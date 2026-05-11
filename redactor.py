"""可選 LLM 旁路審查：掃過 regex 結果，找出規則以外的疑似機敏。"""
from __future__ import annotations

from typing import Any

from ollama_client import OllamaError, generate_json

# 注意：LLM 看的是 regex 處理「之後」的文字，目的是找 regex 沒抓到的東西。
LLM_CHECK_PROMPT_TEMPLATE = """你是一個本地文件脫敏稽核器。下面這段文字已經被 Regex 第一層處理過，
明顯的 email、電話、身分證、API key、token、JWT、信用卡、IP、銀行帳號、台灣地址等
已經被替換為 [EMAIL]、[PHONE]、[TAIWAN_ID]、[API_KEY]、[TOKEN]、[JWT]、[CREDIT_CARD]、
[IP_ADDRESS]、[BANK_ACCOUNT]、[ADDRESS] 之類的標籤。

你的任務：
**只找出 Regex 規則以外、仍可能洩漏隱私或機密的片段**，例如：
- 個人姓名、暱稱、可識別私人關係
- 公司內部專案代號、未公開合作對象、未公開產品名稱
- 醫療、法律、財務的敏感描述
- 看起來像帳號或代號但 regex 沒抓到的字串
- 其他你判斷該被脫敏的具體片段

請忽略已被標籤化（[XXX]）的部分。
你不得摘要原文。
你不得改寫整篇文章。
你只能指出需要替換的精確片段。
你必須只輸出 JSON，不要輸出 Markdown，不要輸出說明文字。

輸出格式：

{{
  "risk_level": "low|medium|high",
  "items": [
    {{
      "type": "person|business_secret|medical|legal|financial|other",
      "exact_text": "需要替換的原文片段",
      "replacement": "[合適標籤]",
      "reason": "為什麼疑似敏感"
    }}
  ]
}}

如果沒有發現規則以外的敏感資訊，請輸出：

{{
  "risk_level": "low",
  "items": []
}}

文字如下：
---
{content}
---
"""


def chunk_text(text: str, chunk_size: int) -> list[str]:
    """依段落 (\\n\\n) 切，盡量逼近 chunk_size 但不破壞段落。"""
    if chunk_size <= 0:
        return [text] if text else []
    if len(text) <= chunk_size:
        return [text] if text else []

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(para), chunk_size):
                chunks.append(para[i : i + chunk_size])
            continue
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > chunk_size and buf:
            chunks.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def _apply_items(chunk: str, items: list[dict[str, Any]]) -> tuple[str, int]:
    applied = 0
    out = chunk
    for item in items:
        if not isinstance(item, dict):
            continue
        exact = item.get("exact_text")
        replacement = item.get("replacement") or "[REDACTED]"
        if not isinstance(exact, str) or not exact:
            continue
        if not isinstance(replacement, str):
            replacement = str(replacement)
        if exact in out:
            out = out.replace(exact, replacement, 1)
            applied += 1
    return out, applied


def run_llm_check(
    text: str,
    model: str,
    url: str,
    chunk_size: int,
    timeout: int = 600,
) -> tuple[str, dict[str, Any]]:
    """掃過 regex 後文字，套用 LLM 找到的額外脫敏。

    回傳 (套用後文字, 統計 + 所有 items 的清單)。
    """
    chunks = chunk_text(text, chunk_size)
    new_chunks: list[str] = []
    all_items: list[dict[str, Any]] = []
    applied_total = 0
    risk_levels: list[str] = []

    for idx, chunk in enumerate(chunks):
        prompt = LLM_CHECK_PROMPT_TEMPLATE.format(content=chunk)
        try:
            data = generate_json(url, model, prompt, timeout=timeout)
        except OllamaError as exc:
            raise OllamaError(
                f"LLM check 在 chunk {idx + 1}/{len(chunks)} 失敗：{exc}"
            ) from exc

        items = data.get("items", []) or []
        if not isinstance(items, list):
            raise OllamaError(
                f"LLM check chunk {idx + 1} 回傳 items 不是 list：{items!r}"
            )
        risk = data.get("risk_level", "low")
        if isinstance(risk, str):
            risk_levels.append(risk)

        new_chunk, n_applied = _apply_items(chunk, items)
        new_chunks.append(new_chunk)
        for item in items:
            if isinstance(item, dict):
                all_items.append(
                    {
                        "chunk": idx + 1,
                        "type": item.get("type", "other"),
                        "exact_text": item.get("exact_text", ""),
                        "replacement": item.get("replacement", ""),
                        "reason": item.get("reason", ""),
                    }
                )
        applied_total += n_applied

    risk_order = {"low": 0, "medium": 1, "high": 2}
    overall_risk = "low"
    for r in risk_levels:
        if risk_order.get(r, 0) > risk_order[overall_risk]:
            overall_risk = r

    rejoined = "\n\n".join(new_chunks)
    stats = {
        "chunks": len(chunks),
        "items_found": len(all_items),
        "items_applied": applied_total,
        "overall_risk": overall_risk,
        "items": all_items,
    }
    return rejoined, stats
