"""success / error report 寫出。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json_report(path: Path, data: dict[str, Any]) -> None:
    """以 UTF-8 + ensure_ascii=False 寫出 JSON 報告。"""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_success_report(
    source_file: Path,
    output_file: Path,
    regex_redactions: dict[str, int],
    llm_check: dict[str, Any] | None,
    llm_warning: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "success",
        "source_file": str(source_file),
        "output_file": str(output_file),
        "regex_redactions": regex_redactions,
        "llm_check": llm_check,
        "llm_warning": llm_warning,
    }


def build_error_report(
    source_file: Path,
    failed_stage: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "source_file": str(source_file),
        "failed_stage": failed_stage,
        "reason": reason,
        "details": details or {},
    }
