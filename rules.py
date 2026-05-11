"""第一層 deterministic Regex 脫敏規則。"""
from __future__ import annotations

import re
from typing import Pattern

# 各規則：(編譯後 pattern, 替換 token)
PATTERNS: dict[str, tuple[Pattern[str], str]] = {
    "EMAIL": (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL]",
    ),
    "TAIWAN_PHONE": (
        re.compile(r"\b09\d{2}[-\s]?\d{3}[-\s]?\d{3}\b"),
        "[PHONE]",
    ),
    "TAIWAN_ID": (
        re.compile(r"\b[A-Z][12]\d{8}\b"),
        "[TAIWAN_ID]",
    ),
    "JWT": (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "[JWT]",
    ),
    "BEARER_TOKEN": (
        re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"),
        "Bearer [BEARER_TOKEN]",
    ),
    "CREDIT_CARD": (
        re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
        "[CREDIT_CARD]",
    ),
    "IP_ADDRESS": (
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "[IP_ADDRESS]",
    ),
    # key-value 風格的 secrets：保留 key 與分隔符，只替換 value
    "API_KEY": (
        re.compile(r"(?i)\b(api[_-]?key)\s*([:=])\s*([A-Za-z0-9._\-]+)"),
        r"\1\2 [API_KEY]",
    ),
    "PASSWORD": (
        re.compile(r"(?i)\b(password|passwd|pwd)\s*([:=])\s*([A-Za-z0-9._\-!@#$%^&*+]+)"),
        r"\1\2 [PASSWORD]",
    ),
    "SECRET": (
        re.compile(r"(?i)\b(secret)\s*([:=])\s*([A-Za-z0-9._\-!@#$%^&*+]+)"),
        r"\1\2 [SECRET]",
    ),
    "TOKEN": (
        re.compile(r"(?i)\b(token)\s*([:=])\s*([A-Za-z0-9._\-!@#$%^&*+]+)"),
        r"\1\2 [TOKEN]",
    ),
    # 銀行帳號提示：行庫名稱或關鍵字附近的長數字串
    "BANK_ACCOUNT_HINT": (
        re.compile(
            r"(?i)(銀行|帳號|account|acct|iban)[^\n]{0,12}\b(\d[\d\- ]{7,18}\d)\b"
        ),
        r"\1 [BANK_ACCOUNT]",
    ),
    # 台灣地址提示：縣市 + 路/街 + 號 (允許中間有區/巷/弄/樓)
    "TAIWAN_ADDRESS_HINT": (
        re.compile(
            r"(?:台北|臺北|新北|桃園|台中|臺中|台南|臺南|高雄|基隆|新竹|嘉義|苗栗|彰化|南投|雲林|屏東|宜蘭|花蓮|台東|臺東|澎湖|金門|連江)"
            r"[市縣]?[一-鿿]{0,8}?[路街道](?:[一二三四五六七八九十百千0-9]{1,4}段)?"
            r"[一-鿿0-9\- ]{0,20}?\d+號"
            r"(?:[^\n，。;；]{0,20}?(?:巷|弄|樓|室|F))?"
        ),
        "[ADDRESS]",
    ),
}


def apply_regex_redactions(text: str) -> tuple[str, dict[str, int]]:
    """套用所有 regex 規則。回傳 (脫敏後文字, 各規則命中次數)。"""
    counts: dict[str, int] = {}
    redacted = text
    for name, (pattern, replacement) in PATTERNS.items():
        redacted, n = pattern.subn(replacement, redacted)
        counts[name] = n
    return redacted, counts
