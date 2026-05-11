"""local-ollama-redactor CLI 入口。

流程：
1. Regex 第一層脫敏（必跑、deterministic）
2. (可選) 本地 Ollama LLM 旁路審查 — 掃過 regex 結果，找出規則以外的疑似機敏並套用
3. 寫出 sanitized 檔 + report

設計：regex 是骨幹、LLM 是旁路。LLM 失敗只會在 report 留 warning，不影響 sanitized 檔產出。
只有讀檔 / regex / 寫檔等 deterministic 階段失敗才會中止並寫 error report。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Any

from ollama_client import (
    OllamaError,
    assert_model_exists,
    check_ollama_available,
)
from redactor import run_llm_check
from report import build_error_report, build_success_report, write_json_report
from rules import apply_regex_redactions

ALLOWED_SUFFIXES = {".txt", ".md"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sanitize_file.py",
        description=(
            "本地文件脫敏工具。Regex 第一層必跑、輸出永遠是 deterministic 結果；"
            "若指定 --llm-model，則額外用本地 Ollama 模型快速掃過 regex 結果，"
            "找出規則以外的疑似機敏並套用。LLM 是旁路、可選；失敗只發 warning。"
        ),
    )
    parser.add_argument("--input", required=True, help="輸入檔案 (.txt 或 .md)")
    parser.add_argument(
        "--llm-model",
        default=None,
        help=(
            "可選：本地 Ollama 模型名稱 (e.g. qwen3:14b)。"
            "提供時會在 regex 後跑一輪 LLM 旁路審查。未提供則只跑 regex。"
        ),
    )
    parser.add_argument("--output", default=None, help="輸出檔案路徑 (可選)")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=8000,
        help="LLM 旁路審查的分段大小 (預設 8000 字元)；只在啟用 LLM 時有意義",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://localhost:11434",
        help="本地 Ollama URL (預設 http://localhost:11434)",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=int,
        default=900,
        help="單次 Ollama 呼叫的 timeout 秒數 (預設 900)",
    )
    parser.add_argument(
        "--strict-llm",
        action="store_true",
        help=(
            "嚴格模式：若 LLM 階段失敗（連線、模型不存在、JSON 解析等）則整體失敗、"
            "不寫 sanitized 檔。預設為非嚴格 — LLM 失敗只在 report 留 warning。"
        ),
    )
    return parser.parse_args(argv)


def _resolve_paths(
    input_arg: str, output_arg: str | None
) -> tuple[Path, Path, Path, Path]:
    input_path = Path(input_arg).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"找不到輸入檔：{input_path}")
    if input_path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"只支援 .txt 或 .md，輸入檔副檔名：{input_path.suffix}"
        )

    if output_arg:
        output_path = Path(output_arg).expanduser().resolve()
    else:
        output_path = input_path.with_name(
            f"{input_path.stem}.sanitized{input_path.suffix}"
        )

    if output_path == input_path:
        raise ValueError("output 路徑不得與 input 相同 (禁止覆蓋原檔)")

    report_path = input_path.with_name(
        f"{input_path.stem}.sanitize_report.json"
    )
    error_path = input_path.with_name(
        f"{input_path.stem}.sanitize_error.json"
    )
    return input_path, output_path, report_path, error_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # ---- 路徑解析 ----
    try:
        input_path, output_path, report_path, error_path = _resolve_paths(
            args.input, args.output
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    def _fail(stage: str, reason: str, details: dict[str, Any] | None = None) -> int:
        err = build_error_report(
            source_file=input_path,
            failed_stage=stage,
            reason=reason,
            details=details,
        )
        try:
            write_json_report(error_path, err)
        except OSError as write_exc:
            print(
                f"[ERROR] 連 error report 都寫不出來：{write_exc}",
                file=sys.stderr,
            )
        print(f"[FAILED] stage={stage} reason={reason}", file=sys.stderr)
        print(f"[FAILED] error report: {error_path}", file=sys.stderr)
        return 1

    # ---- 讀檔 ----
    try:
        source_text = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _fail("read_input", f"讀檔失敗：{exc}")

    # ---- Regex 第一層 (必跑) ----
    try:
        regex_text, regex_counts = apply_regex_redactions(source_text)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "regex_redaction",
            f"regex 失敗：{exc}",
            {"trace": traceback.format_exc()},
        )

    final_text = regex_text
    llm_stats: dict[str, Any] | None = None
    llm_warning: str | None = None

    # ---- LLM 旁路審查 (可選) ----
    if args.llm_model:
        try:
            check_ollama_available(args.ollama_url)
            assert_model_exists(args.ollama_url, args.llm_model)
            final_text, llm_stats = run_llm_check(
                regex_text,
                model=args.llm_model,
                url=args.ollama_url,
                chunk_size=args.chunk_size,
                timeout=args.ollama_timeout,
            )
        except OllamaError as exc:
            if args.strict_llm:
                return _fail("llm_check", str(exc))
            llm_warning = f"LLM 旁路審查失敗，輸出僅含 regex 結果：{exc}"
            print(f"[WARN] {llm_warning}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            if args.strict_llm:
                return _fail(
                    "llm_check",
                    f"LLM 旁路審查異常：{exc}",
                    {"trace": traceback.format_exc()},
                )
            llm_warning = f"LLM 旁路審查異常，輸出僅含 regex 結果：{exc}"
            print(f"[WARN] {llm_warning}", file=sys.stderr)

    # ---- 寫出 sanitized + report ----
    try:
        output_path.write_text(final_text, encoding="utf-8")
    except OSError as exc:
        return _fail("write_output", f"寫出 sanitized 檔失敗：{exc}")

    success = build_success_report(
        source_file=input_path,
        output_file=output_path,
        regex_redactions=regex_counts,
        llm_check=llm_stats,
        llm_warning=llm_warning,
    )
    try:
        write_json_report(report_path, success)
    except OSError as exc:
        return _fail(
            "write_output",
            f"寫出 success report 失敗 (sanitized 已寫出)：{exc}",
        )

    print(f"[OK] sanitized → {output_path}")
    print(f"[OK] report    → {report_path}")
    if llm_warning:
        print(f"[OK] (LLM warning recorded in report)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
