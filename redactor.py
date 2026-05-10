"""第二層：呼叫 A 模型 (redactor) 做語意脫敏。"""
from __future__ import annotations

from typing import Any

from ollama_client import OllamaError, generate_json

REDACTOR_PROMPT_TEMPLATE = """你是一個本地文件脫敏器。你的任務是檢查文字中是否仍包含個人資料、帳號密碼、電話、email、地址、身分證字號、API key、token、金流資訊、銀行帳號、信用卡、公司內部機密、未公開合作對象、可識別私人關係、醫療或法律敏感資訊。

你不得摘要。
你不得改寫整篇文章。
你只能指出需要替換的精確片段。
你必須只輸出 JSON，不要輸出 Markdown，不要輸出說明文字。

輸出格式：

{{
  "risk_level": "low|medium|high",
  "items": [
    {{
      "type": "person|phone|email|address|credential|financial|business_secret|medical|legal|other",
      "exact_text": "需要替換的原文片段",
      "replacement": "[合適標籤]",
      "reason": "為什麼疑似敏感"
    }}
  ]
}}

如果沒有發現敏感資訊，請輸出：

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
        # 單段就超過 chunk_size：硬切
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
    """將 A 模型回傳 items 的 exact_text 替換為 replacement。回傳 (新字串, 套用數)。"""
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


def run_redactor(
    text: str,
    model: str,
    url: str,
    chunk_size: int,
    timeout: int = 600,
) -> tuple[str, dict[str, Any]]:
    """跑完整 redactor 流程。回傳 (脫敏後文字, 統計資訊)。"""
    chunks = chunk_text(text, chunk_size)
    new_chunks: list[str] = []
    total_items = 0
    applied_items = 0
    risk_levels: list[str] = []

    for idx, chunk in enumerate(chunks):
        prompt = REDACTOR_PROMPT_TEMPLATE.format(content=chunk)
        try:
            data = generate_json(url, model, prompt, timeout=timeout)
        except OllamaError as exc:
            raise OllamaError(
                f"redactor 在 chunk {idx + 1}/{len(chunks)} 失敗：{exc}"
            ) from exc

        items = data.get("items", []) or []
        if not isinstance(items, list):
            raise OllamaError(
                f"redactor chunk {idx + 1} 回傳 items 不是 list：{items!r}"
            )
        risk = data.get("risk_level", "low")
        if isinstance(risk, str):
            risk_levels.append(risk)

        new_chunk, n_applied = _apply_items(chunk, items)
        new_chunks.append(new_chunk)
        total_items += len(items)
        applied_items += n_applied

    rejoined = "\n\n".join(new_chunks)
    stats = {
        "chunks": len(chunks),
        "items_found": total_items,
        "items_applied": applied_items,
        "risk_levels": risk_levels,
    }
    return rejoined, stats
