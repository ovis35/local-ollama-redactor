# local-ollama-redactor

Windows 本地端文件脫敏 CLI。讀取 `.txt` / `.md`，以 **Regex 為骨幹**做 deterministic 脫敏，
並可**選擇性**啟用本地 Ollama 模型快速旁路審查，找出 regex 規則以外的疑似機敏。

## 設計原則

- **Regex 為主**：永遠跑、輸出永遠 deterministic、永遠寫得出 sanitized 檔。
- **LLM 為輔**：可選旁路。提供 `--llm-model` 才會跑；任務只是「快速瀏覽 regex 結果，找規則外的疑似機敏」並套用替換。
- **失敗策略**：
  - Regex / 讀檔 / 寫檔失敗 → 中止、寫 `sanitize_error.json`、不寫 sanitized。
  - LLM 失敗（Ollama 連不上、模型不存在、JSON 解析失敗等）→ 預設只 warning + 寫出 regex 結果；加 `--strict-llm` 才會升級為失敗。
- **不覆蓋原檔**：output 不能與 input 同路徑。
- **完全本地**：只與 `http://localhost:11434` 通訊，不向任何外部服務送出檔案內容。

## 安裝

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

需要 Python 3.11+。LLM 旁路審查需要本地 [Ollama](https://ollama.com/) 服務。

```powershell
# 確認 Ollama 服務
curl http://localhost:11434/api/tags
ollama list

# 沒有想用的模型先 pull
ollama pull qwen3:14b
ollama pull gemma3:4b
```

## 使用

### 純 Regex（最快、最穩）

```powershell
python .\sanitize_file.py --input ".\samples\sample.md"
```

### 加上 LLM 旁路審查

```powershell
python .\sanitize_file.py --input ".\samples\sample.md" --llm-model "qwen3:14b"
```

### 嚴格模式：LLM 失敗就整體失敗

```powershell
python .\sanitize_file.py --input ".\samples\sample.md" --llm-model "qwen3:14b" --strict-llm
```

### 大檔案 + 自訂 chunk / timeout

```powershell
python .\sanitize_file.py `
  --input "D:\Brain\logs\big.md" `
  --llm-model "gemma3:4b" `
  --chunk-size 8000 `
  --ollama-timeout 1800
```

## CLI 參數

| 參數 | 必填 | 預設 | 說明 |
| --- | :---: | --- | --- |
| `--input` | ✅ | — | 輸入檔，必須是 `.txt` 或 `.md` |
| `--llm-model` |   | `None` | 本地 Ollama 模型名稱；給了才跑 LLM 旁路審查 |
| `--output` |   | `<stem>.sanitized<ext>` | 輸出檔；不得與 input 相同 |
| `--chunk-size` |   | `8000` | LLM 分段大小（字元） |
| `--ollama-url` |   | `http://localhost:11434` | 本地 Ollama URL |
| `--ollama-timeout` |   | `900` | 單次 Ollama 呼叫 timeout 秒數 |
| `--strict-llm` |   | off | 開啟後 LLM 失敗整體失敗、不寫 sanitized |

## Regex 規則（rules.py）

| 類型 | 替換 |
| --- | --- |
| EMAIL | `[EMAIL]` |
| TAIWAN_PHONE | `[PHONE]` |
| TAIWAN_ID | `[TAIWAN_ID]` |
| CREDIT_CARD | `[CREDIT_CARD]` |
| IP_ADDRESS | `[IP_ADDRESS]` |
| JWT | `[JWT]` |
| BEARER_TOKEN | `Bearer [BEARER_TOKEN]` |
| API_KEY / PASSWORD / SECRET / TOKEN | `<key>= [API_KEY]` 等（保留 key 與分隔符） |
| BANK_ACCOUNT_HINT | `[BANK_ACCOUNT]` |
| TAIWAN_ADDRESS_HINT | `[ADDRESS]` |

## 輸出檔案

對 `note.md`：

- `note.sanitized.md` — sanitized 結果（regex 永遠寫得出；LLM 啟用時包含 LLM 套用結果）
- `note.sanitize_report.json` — 成功報告
- `note.sanitize_error.json` — 失敗報告（成功時不會產生）

## 成功 report 範例

```json
{
  "status": "success",
  "source_file": "...\\note.md",
  "output_file": "...\\note.sanitized.md",
  "regex_redactions": {
    "EMAIL": 1,
    "TAIWAN_PHONE": 1,
    "TAIWAN_ID": 1,
    "API_KEY": 1
  },
  "llm_check": {
    "chunks": 1,
    "items_found": 2,
    "items_applied": 2,
    "overall_risk": "medium",
    "items": [
      {
        "chunk": 1,
        "type": "person",
        "exact_text": "王小明",
        "replacement": "[姓名]",
        "reason": "個人姓名"
      }
    ]
  },
  "llm_warning": null
}
```

`llm_check` 為 `null` 表示沒啟用 LLM；`llm_warning` 非 null 表示 LLM 失敗、輸出僅包含 regex 結果。

## 失敗 report 範例

```json
{
  "status": "failed",
  "source_file": "...\\note.md",
  "failed_stage": "regex_redaction",
  "reason": "...",
  "details": {}
}
```

`failed_stage` 可能值：`read_input` / `regex_redaction` / `llm_check`（僅 `--strict-llm`）/ `write_output`。
