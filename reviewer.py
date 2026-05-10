"""第三層：呼叫 B 模型 (reviewer) 比對原始 vs 候選脫敏檔。"""
from __future__ import annotations

from typing import Any

from ollama_client import OllamaError, generate_json
from redactor import chunk_text

REVIEWER_PROMPT_TEMPLATE = """你是一個本地文件脫敏稽核器。你的任務不是改寫文章，而是比對「原始檔」與「候選脫敏檔」，判斷候選脫敏檔是否仍殘留敏感資訊，或是否過度改寫破壞原文結構。

請檢查以下類型：
個人姓名、電話、email、地址、身分證字號、帳號密碼、API key、token、金流資訊、銀行帳號、信用卡、公司內部機密、未公開合作對象、可識別私人關係、醫療或法律敏感資訊。

你必須只輸出 JSON，不要輸出 Markdown，不要輸出說明文字。

輸出格式：

{{
  "verdict": "pass|fail",
  "risk_level": "low|medium|high",
  "remaining_sensitive_items": [
    {{
      "type": "person|phone|email|address|credential|financial|business_secret|medical|legal|other",
      "text_excerpt": "候選脫敏檔中仍殘留的片段，最多 30 字",
      "location_hint": "用段落、標題或附近文字描述大概位置",
      "reason": "為什麼仍可識別或敏感"
    }}
  ],
  "over_redaction_issues": [
    {{
      "location_hint": "用段落、標題或附近文字描述大概位置",
      "reason": "哪裡被過度刪改，導致原意受損"
    }}
  ],
  "structure_issues": [
    {{
      "location_hint": "用段落、標題或附近文字描述大概位置",
      "reason": "Markdown、段落或標題結構是否被破壞"
    }}
  ],
  "required_fixes": [
    {{
      "exact_text": "候選脫敏檔中應替換的原文",
      "replacement": "[合適標籤]"
    }}
  ]
}}

如果候選脫敏檔合格，請輸出：

{{
  "verdict": "pass",
  "risk_level": "low",
  "remaining_sensitive_items": [],
  "over_redaction_issues": [],
  "structure_issues": [],
  "required_fixes": []
}}

原始檔：
---
{source_chunk}
---

候選脫敏檔：
---
{sanitized_chunk}
---
"""


def pair_chunks(
    source: str,
    candidate: str,
    chunk_size: int,
) -> list[tuple[str, str]]:
    """分別切後 zip，長度不同則 pad 空字串。"""
    src_chunks = chunk_text(source, chunk_size) or [""]
    cand_chunks = chunk_text(candidate, chunk_size) or [""]
    n = max(len(src_chunks), len(cand_chunks))
    src_chunks += [""] * (n - len(src_chunks))
    cand_chunks += [""] * (n - len(cand_chunks))
    return list(zip(src_chunks, cand_chunks))


def _merge_lists(target: dict[str, list], data: dict[str, Any], key: str) -> None:
    val = data.get(key, []) or []
    if isinstance(val, list):
        target[key].extend(val)


def run_review_round(
    source: str,
    candidate: str,
    model: str,
    url: str,
    chunk_size: int,
    timeout: int = 600,
) -> dict[str, Any]:
    """跑一輪 reviewer。回傳合併後的審查結果。"""
    pairs = pair_chunks(source, candidate, chunk_size)
    merged: dict[str, Any] = {
        "verdict": "pass",
        "risk_level": "low",
        "remaining_sensitive_items": [],
        "over_redaction_issues": [],
        "structure_issues": [],
        "required_fixes": [],
    }
    risk_order = {"low": 0, "medium": 1, "high": 2}

    for idx, (src_chunk, cand_chunk) in enumerate(pairs):
        prompt = REVIEWER_PROMPT_TEMPLATE.format(
            source_chunk=src_chunk,
            sanitized_chunk=cand_chunk,
        )
        try:
            data = generate_json(url, model, prompt, timeout=timeout)
        except OllamaError as exc:
            raise OllamaError(
                f"reviewer 在 chunk {idx + 1}/{len(pairs)} 失敗：{exc}"
            ) from exc

        verdict = data.get("verdict", "fail")
        if verdict != "pass":
            merged["verdict"] = "fail"
        risk = data.get("risk_level", "low")
        if isinstance(risk, str) and risk in risk_order:
            if risk_order[risk] > risk_order[merged["risk_level"]]:
                merged["risk_level"] = risk
        for key in (
            "remaining_sensitive_items",
            "over_redaction_issues",
            "structure_issues",
            "required_fixes",
        ):
            _merge_lists(merged, data, key)

    return merged


def _apply_required_fixes(
    candidate: str, fixes: list[dict[str, Any]]
) -> tuple[str, int]:
    applied = 0
    out = candidate
    for fix in fixes:
        if not isinstance(fix, dict):
            continue
        exact = fix.get("exact_text")
        replacement = fix.get("replacement") or "[REDACTED]"
        if not isinstance(exact, str) or not exact:
            continue
        if not isinstance(replacement, str):
            replacement = str(replacement)
        if exact in out:
            out = out.replace(exact, replacement, 1)
            applied += 1
    return out, applied


def run_reviewer_with_retries(
    source: str,
    candidate: str,
    model: str,
    url: str,
    chunk_size: int,
    max_fixes: int,
    timeout: int = 600,
) -> tuple[str, dict[str, Any], int]:
    """跑 reviewer，fail 時自動套 required_fixes 重試。

    回傳 (最終 candidate, 最終 review dict, 實際輪數)。
    """
    rounds = 0
    review: dict[str, Any] = {}
    current = candidate
    total_attempts = max_fixes + 1
    for attempt in range(total_attempts):
        rounds += 1
        review = run_review_round(
            source, current, model, url, chunk_size, timeout=timeout
        )
        if review.get("verdict") == "pass":
            return current, review, rounds
        if attempt == total_attempts - 1:
            break
        fixes = review.get("required_fixes", []) or []
        if not fixes:
            # 無修正建議卻 fail，無法自動修，提早結束
            break
        current, _ = _apply_required_fixes(current, fixes)
    return current, review, rounds
