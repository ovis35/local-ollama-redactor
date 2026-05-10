# local-ollama-redactor

Windows 本地端文件脫敏 CLI。讀取 `.txt` / `.md`，依序執行：

1. **Regex 第一層** — deterministic 高確定性脫敏（email / 電話 / 身分證 / API key / token / JWT / 信用卡 / 銀行帳號 / 地址 …）
2. **A 模型 (redactor)** — 強制呼叫本地 Ollama 模型做語意敏感資訊審查並套用替換
3. **Regex 第二層** — 對 A 模型結果再做一次 deterministic 檢查
4. **B 模型 (reviewer)** — 強制呼叫本地 Ollama 模型，比對「原始檔」vs「候選脫敏檔」，判定是否仍有敏感資訊或過度改寫
5. **僅當 B 模型 verdict = pass 時**，才寫出正式 `.sanitized.md` + `sanitize_report.json`
6. 任何階段失敗 → 只寫 `sanitize_error.json`，**絕不**輸出正式 sanitized 檔

完全在本機執行，不呼叫任何雲端 API、不傳檔案出本機。

## 安裝

需要 Python 3.11+ 與本地 [Ollama](https://ollama.com/) 服務。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 確認 Ollama 已啟動

```powershell
# 服務應監聽 http://localhost:11434
curl http://localhost:11434/api/tags

# 列出本地模型
ollama list
```

若清單裡沒有你想用的模型，先 `ollama pull <model>`，例如：

```powershell
ollama pull qwen3:14b
ollama pull gpt-oss:20b
```

## 使用方式

最小範例：

```powershell
python .\sanitize_file.py `
  --input ".\samples\sample.md" `
  --redactor-model "qwen3:14b" `
  --reviewer-model "gpt-oss:20b"
```

指定 output 路徑：

```powershell
python .\sanitize_file.py `
  --input ".\note.md" `
  --output ".\note.sanitized.md" `
  --redactor-model "qwen3:14b" `
  --reviewer-model "gpt-oss:20b"
```

調整 reviewer 自動修補的最大重試次數：

```powershell
python .\sanitize_file.py `
  --input "D:\Brain\logs\test.md" `
  --redactor-model "qwen3:14b" `
  --reviewer-model "gpt-oss:20b" `
  --max-review-fixes 2
```

### 必填參數

| 參數 | 說明 |
| --- | --- |
| `--input` | 輸入檔，必須是 `.txt` 或 `.md` |
| `--redactor-model` | A 模型 (語意脫敏) 名稱 |
| `--reviewer-model` | B 模型 (脫敏稽核) 名稱 |

### 選填參數

| 參數 | 預設 | 說明 |
| --- | --- | --- |
| `--output` | `<stem>.sanitized<ext>` | 輸出檔；不得與 input 相同 |
| `--max-review-fixes` | `2` | reviewer fail 時自動套用 `required_fixes` 重試的最大次數 |
| `--chunk-size` | `3000` | 送進模型的字元分段大小 |
| `--ollama-url` | `http://localhost:11434` | 本地 Ollama URL |
| `--ollama-timeout` | `900` | 單次 Ollama 呼叫的 timeout 秒數；thinking 模型 (e.g. gpt-oss) 冷載 + 推理較慢時可調高 |

## 輸出檔案

對於 `note.md`，工具會產生：

- `note.sanitized.md` — 正式脫敏結果（僅 B 模型 pass 才會產生）
- `note.sanitize_report.json` — 成功報告
- `note.sanitize_error.json` — 失敗報告（成功時不會產生）

## 成功輸出範例

`note.sanitize_report.json`：

```json
{
  "status": "success",
  "source_file": "D:\\Brain\\logs\\note.md",
  "output_file": "D:\\Brain\\logs\\note.sanitized.md",
  "redactor_model": "qwen3:14b",
  "reviewer_model": "gpt-oss:20b",
  "regex_redactions": {
    "EMAIL": 1,
    "TAIWAN_PHONE": 1,
    "TAIWAN_ID": 1,
    "API_KEY": 1
  },
  "redactor_review": {
    "chunks": 1,
    "items_found": 3,
    "items_applied": 3
  },
  "final_review": {
    "verdict": "pass",
    "rounds": 1,
    "risk_level": "low",
    "remaining_sensitive_items": [],
    "over_redaction_issues": [],
    "structure_issues": []
  }
}
```

## 失敗輸出範例

`note.sanitize_error.json`：

```json
{
  "status": "failed",
  "source_file": "D:\\Brain\\logs\\note.md",
  "failed_stage": "ollama_check",
  "reason": "無法連線到 Ollama (http://localhost:11434): ...",
  "redactor_model": "qwen3:14b",
  "reviewer_model": "gpt-oss:20b",
  "details": {}
}
```

`failed_stage` 可能值：

- `ollama_check` — Ollama 未啟動或模型不存在
- `read_input` — 讀檔失敗
- `regex_redaction` — Regex 脫敏例外
- `redactor_review` — A 模型呼叫失敗或回傳非 JSON
- `final_review` — B 模型呼叫失敗、回傳非 JSON、或重試後仍 fail
- `write_output` — 寫出檔案失敗

## 設計原則

- **不覆蓋原檔**：output 與 input 同路徑會直接報錯。
- **Ollama 強制**：沒有 `--no-llm`，沒有讓模型變可選的旁路；Ollama 不可用就直接失敗。
- **雙模型審查**：redactor 找出敏感片段並替換；reviewer 比對原始 vs 候選，把關殘留與過度脫敏。
- **失敗不產生正式 sanitized 檔**：只要任何階段失敗（含 reviewer 重試後仍 fail），只寫 `sanitize_error.json`。
- **完全本地**：僅與 `http://localhost:11434` 通訊，不向任何外部服務送出檔案內容。
- **JSON 嚴格**：模型回傳必須是合法 JSON，否則整批失敗；JSON 前後若混入雜訊會嘗試擷取第一個平衡的 `{...}` 區塊。
