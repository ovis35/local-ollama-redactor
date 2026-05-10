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
    redactor_model: str,
    reviewer_model: str,
    regex_redactions: dict[str, int],
    redactor_stats: dict[str, Any],
    reviewer_review: dict[str, Any],
    rounds: int,
) -> dict[str, Any]:
    return {
        "status": "success",
        "source_file": str(source_file),
        "output_file": str(output_file),
        "redactor_model": redactor_model,
        "reviewer_model": reviewer_model,
        "regex_redactions": regex_redactions,
        "redactor_review": {
            "chunks": redactor_stats.get("chunks", 0),
            "items_found": redactor_stats.get("items_found", 0),
            "items_applied": redactor_stats.get("items_applied", 0),
        },
        "final_review": {
            "verdict": reviewer_review.get("verdict", "fail"),
            "rounds": rounds,
            "risk_level": reviewer_review.get("risk_level", "low"),
            "remaining_sensitive_items": reviewer_review.get(
                "remaining_sensitive_items", []
            ),
            "over_redaction_issues": reviewer_review.get(
                "over_redaction_issues", []
            ),
            "structure_issues": reviewer_review.get("structure_issues", []),
        },
    }


def build_error_report(
    source_file: Path,
    redactor_model: str,
    reviewer_model: str,
    failed_stage: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "source_file": str(source_file),
        "failed_stage": failed_stage,
        "reason": reason,
        "redactor_model": redactor_model,
        "reviewer_model": reviewer_model,
        "details": details or {},
    }
