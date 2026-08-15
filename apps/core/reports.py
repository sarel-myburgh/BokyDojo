"""Shared secure report export helpers."""

from __future__ import annotations

import csv

from django.http import HttpResponse

from apps.core import audit

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_csv_cell(value):
    """Neutralize spreadsheet formulas while preserving non-string values."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_report_response(*, filename: str, header: list, rows: list, actor) -> HttpResponse:
    """Return an audited CSV; no download is released if its audit write fails."""
    audit.record(
        "export",
        actor=actor,
        subject_type="report",
        subject_id=filename,
        note=f"{len(rows)} row(s)",
        strict=True,
    )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow([safe_csv_cell(value) for value in header])
    writer.writerows([[safe_csv_cell(value) for value in row] for row in rows])
    return response
