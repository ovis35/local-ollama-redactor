"""本地 Ollama HTTP 客戶端。"""
from __future__ import annotations

import json
from typing import Any

import requests


class OllamaError(Exception):
    """Ollama 相關錯誤基底。"""


class OllamaUnavailableError(OllamaError):
    """無法連線到 Ollama 服務。"""


class ModelNotFoundError(OllamaError):
    """指定模型不存在於本地 Ollama。"""


class OllamaJSONError(OllamaError):
    """模型回傳內容無法解析為 JSON。"""


def check_ollama_available(url: str, timeout: int = 5) -> None:
    """檢查 Ollama 服務可用。"""
    try:
        resp = requests.get(f"{url.rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaUnavailableError(
            f"無法連線到 Ollama ({url})：{exc}"
        ) from exc


def list_models(url: str, timeout: int = 10) -> list[str]:
    """列出本地已安裝的模型名稱。"""
    try:
        resp = requests.get(f"{url.rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaUnavailableError(f"無法取得模型列表：{exc}") from exc
    data = resp.json()
    return [m.get("name", "") for m in data.get("models", [])]


def assert_model_exists(url: str, model: str) -> None:
    """若模型不存在於本地，raise ModelNotFoundError。"""
    available = list_models(url)
    if model in available:
        return
    # 容忍 ":latest" 隱含 tag
    bare = {m.split(":", 1)[0] for m in available}
    if model.split(":", 1)[0] in bare and ":" not in model:
        return
    raise ModelNotFoundError(
        f"本地 Ollama 找不到模型 '{model}'。已安裝：{available}"
    )


def _extract_first_json_object(text: str) -> str | None:
    """從含雜訊的字串中擷取第一個平衡的 {...} 區塊。"""
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    return text[start : i + 1]
    return None


def generate_json(
    url: str,
    model: str,
    prompt: str,
    timeout: int = 600,
) -> dict[str, Any]:
    """呼叫 /api/generate 並回傳解析後的 JSON dict。"""
    # 走 /api/chat，對 thinking 模型 (e.g. gpt-oss) 的 harmony 格式服從性較好；
    # 加上強制 JSON-only 的 system prompt + format:"json" 雙保險。
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You output ONLY a single valid JSON object that matches the "
                    "schema requested by the user. No prose, no explanation, no "
                    "markdown, no code fence. Do not write any analysis. Output "
                    "JSON and nothing else."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
        "think": False,
    }
    try:
        resp = requests.post(
            f"{url.rstrip('/')}/api/chat",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(
            f"呼叫 Ollama chat 失敗 (model={model})：{exc}"
        ) from exc

    body = resp.json()
    message = body.get("message", {}) if isinstance(body, dict) else {}
    raw = message.get("content", "") if isinstance(message, dict) else ""
    # Fallback：thinking 模型可能把 JSON 放在 thinking 欄位
    if not isinstance(raw, str) or not raw.strip():
        thinking = (
            message.get("thinking", "") if isinstance(message, dict) else ""
        )
        if not isinstance(thinking, str) or not thinking.strip():
            thinking = body.get("thinking", "") if isinstance(body, dict) else ""
        if isinstance(thinking, str) and thinking.strip():
            raw = thinking
    if not isinstance(raw, str) or not raw.strip():
        raise OllamaJSONError(
            f"Ollama 回傳空 content 與 thinking (model={model})。"
            f"done_reason={body.get('done_reason')!r}"
        )

    # 第一輪：直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 第二輪：嘗試擷取第一個 JSON object
    extracted = _extract_first_json_object(raw)
    if extracted is not None:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError as exc:
            raise OllamaJSONError(
                f"模型 {model} 回傳 JSON 解析失敗："
                f"{exc}。原始回應前 500 字：{raw[:500]!r}"
            ) from exc

    raise OllamaJSONError(
        f"模型 {model} 回傳內容不是合法 JSON。"
        f"原始回應前 500 字：{raw[:500]!r}"
    )
