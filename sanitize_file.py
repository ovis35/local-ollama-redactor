"""local-ollama-redactor CLI 入口。

流程：regex → A 模型 (redactor) → 二次 regex → B 模型 (reviewer) → 寫出。
任何階段失敗 → 寫 sanitize_error.json，不寫正式 sanitized 檔。
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
from redactor import run_redactor
from report import build_error_report, build_success_report, write_json_report
from reviewer import run_reviewer_with_retries
from rules import apply_regex_redactions

ALLOWED_SUFFIXES = {".txt", ".md"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sanitize_file.py",
        description=(
            "本地 Ollama 雙模型文件脫敏工具。"
            "Regex → redactor (A 模型) → reviewer (B 模型)，"
            "B 模型 pass 才輸出正式 sanitized 檔。"
        ),
    )
    parser.add_argument("--input", required=True, help="輸入檔案 (.txt 或 .md)")
    parser.add_argument(
        "--redactor-model",
        required=True,
        help="A 模型 (語意脫敏) 名稱，例如 qwen3:14b",
    )
    parser.add_argument(
        "--reviewer-model",
        required=True,
        help="B 模型 (脫敏稽核) 名稱，例如 gpt-oss:20b",
    )
    parser.add_argument("--output", default=None, help="輸出檔案路徑 (可選)")
    parser.add_argument(
        "--max-review-fixes",
        type=int,
        default=2,
        help="reviewer fail 時自動套用 required_fixes 的最大重試次數 (預設 2)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=3000,
        help="送進模型的分段大小 (預設 3000 字元)",
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


def _merge_counts(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    keys = set(a) | set(b)
    return {k: a.get(k, 0) + b.get(k, 0) for k in sorted(keys)}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    redactor_model = args.redactor_model
    reviewer_model = args.reviewer_model

    # ---- 路徑解析 ----
    try:
        input_path, output_path, report_path, error_path = _resolve_paths(
            args.input, args.output
        )
    except (FileNotFoundError, ValueError) as exc:
        # 路徑/副檔名問題 — 沒有 input_path 可以放 error 檔，直接印 stderr
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    def _fail(stage: str, reason: str, details: dict[str, Any] | None = None) -> int:
        err = build_error_report(
            source_file=input_path,
            redactor_model=redactor_model,
            reviewer_model=reviewer_model,
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

    # ---- Ollama 強制檢查 ----
    try:
        check_ollama_available(args.ollama_url)
        assert_model_exists(args.ollama_url, redactor_model)
        assert_model_exists(args.ollama_url, reviewer_model)
    except OllamaError as exc:
        return _fail("ollama_check", str(exc))

    # ---- 讀檔 ----
    try:
        source_text = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _fail("read_input", f"讀檔失敗：{exc}")

    # ---- Regex 第一層 ----
    try:
        first_pass, regex_counts_1 = apply_regex_redactions(source_text)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "regex_redaction",
            f"第一層 regex 失敗：{exc}",
            {"trace": traceback.format_exc()},
        )

    # ---- A 模型 redactor ----
    try:
        after_redactor, redactor_stats = run_redactor(
            first_pass,
            model=redactor_model,
            url=args.ollama_url,
            chunk_size=args.chunk_size,
            timeout=args.ollama_timeout,
        )
    except OllamaError as exc:
        return _fail("redactor_review", str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "redactor_review",
            f"redactor 流程異常：{exc}",
            {"trace": traceback.format_exc()},
        )

    # ---- Regex 第二層 ----
    try:
        candidate, regex_counts_2 = apply_regex_redactions(after_redactor)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "regex_redaction",
            f"第二層 regex 失敗：{exc}",
            {"trace": traceback.format_exc()},
        )

    regex_counts = _merge_counts(regex_counts_1, regex_counts_2)

    # ---- B 模型 reviewer ----
    try:
        final_text, review, rounds = run_reviewer_with_retries(
            source=source_text,
            candidate=candidate,
            model=reviewer_model,
            url=args.ollama_url,
            chunk_size=args.chunk_size,
            max_fixes=args.max_review_fixes,
            timeout=args.ollama_timeout,
        )
    except OllamaError as exc:
        return _fail("final_review", str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "final_review",
            f"reviewer 流程異常：{exc}",
            {"trace": traceback.format_exc()},
        )

    if review.get("verdict") != "pass":
        return _fail(
            "final_review",
            f"reviewer 經 {rounds} 輪審查仍 fail",
            {
                "rounds": rounds,
                "review": review,
                "regex_redactions": regex_counts,
            },
        )

    # ---- 寫出 sanitized + report ----
    try:
        output_path.write_text(final_text, encoding="utf-8")
    except OSError as exc:
        return _fail("write_output", f"寫出 sanitized 檔失敗：{exc}")

    success = build_success_report(
        source_file=input_path,
        output_file=output_path,
        redactor_model=redactor_model,
        reviewer_model=reviewer_model,
        regex_redactions=regex_counts,
        redactor_stats=redactor_stats,
        reviewer_review=review,
        rounds=rounds,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
