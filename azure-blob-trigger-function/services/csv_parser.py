"""
services/csv_parser.py — Parse exception CSV blobs into a normalised error_data dict.

Supports two formats:
  1. Application Insights CSV export  (columns: type, problemId, details, method, …)
  2. Simple CSV format                (columns: error_type, message, stack_trace, frequency)

The blob trigger binding passes raw content bytes directly, so there is NO
second network call needed to download the blob — the bytes arrive via the
trigger binding itself.

Returns:
    dict with keys:
        type, problemId, count, sample_message, sample_stack,
        first_seen, last_seen, source_file, total_errors
    or None if the CSV is empty / unparseable.
"""
import csv
import io
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def parse(blob_content: bytes, blob_name: str) -> Optional[Dict]:
    """
    Parse raw blob content into a normalised error_data dict.

    Args:
        blob_content: Raw bytes of the CSV file (from the trigger binding).
        blob_name:    Name of the blob (used as problemId fallback).

    Returns:
        Parsed error dict, or None if the file is empty / unrecognised.
    """
    try:
        content = blob_content.decode("utf-8")
    except UnicodeDecodeError:
        content = blob_content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(content))
    rows = [r for r in reader if r.get("type") or r.get("error_type")]

    if not rows:
        logger.warning("Empty or unrecognised CSV: %s", blob_name)
        return None

    first_row = rows[0]
    if "details" in first_row or "problemId" in first_row:
        logger.info("Detected App Insights format: %s", blob_name)
        return _parse_app_insights_format(rows, blob_name)
    else:
        logger.info("Detected simple format: %s", blob_name)
        return _parse_simple_format(rows, blob_name)


def _parse_app_insights_format(rows: List[Dict], blob_name: str) -> Optional[Dict]:
    """Parse Application Insights CSV export format."""
    best_row = next(
        (r for r in rows if r.get("method") and r.get("details")),
        rows[0]
    )
    message     = best_row.get("innermostMessage") or best_row.get("message", "")
    stack_trace = _extract_stack(best_row)
    # App Insights exports use 'innermostType' for the actual .NET exception class name
    # (e.g. System.NullReferenceException). 'type' is a generic category field.
    error_type  = (
        best_row.get("innermostType")
        or best_row.get("type")
        or "UnknownException"
    )

    return {
        "type":          error_type,
        "problemId":     blob_name.replace(".csv", ""),
        "count":         len(rows),
        "sample_message": message,
        "sample_stack":  stack_trace,
        "first_seen":    datetime.utcnow(),
        "last_seen":     datetime.utcnow(),
        "source_file":   blob_name,
        "total_errors":  len(rows),
    }


def _extract_stack(row: Dict) -> str:
    """Extract stack trace string from App Insights 'details' JSON column."""
    details_raw = row.get("details", "")
    if not details_raw:
        return row.get("stack", "") or row.get("method", "")

    try:
        details = json.loads(details_raw)
        if not isinstance(details, list):
            return ""
        for entry in details:
            raw_stack = entry.get("rawStack", "")
            if raw_stack:
                return raw_stack
            parsed = entry.get("parsedStack", [])
            if parsed:
                lines = []
                for frame in parsed:
                    method   = frame.get("method", "")
                    filename = frame.get("fileName", "")
                    line     = frame.get("line", 0)
                    lines.append(
                        f"   at {method} in {filename}:line {line}"
                        if filename and line else f"   at {method}"
                    )
                return "\n".join(lines)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.debug("Could not parse details JSON: %s", exc)

    return row.get("stack", "") or row.get("method", "")


def _parse_simple_format(rows: List[Dict], blob_name: str) -> Optional[Dict]:
    """Parse simple CSV format: error_type, message, stack_trace, frequency."""
    row = rows[0]
    return {
        "type":          row.get("error_type") or row.get("type", "UnknownException"),
        "problemId":     blob_name.replace(".csv", ""),
        "count":         int(row.get("frequency") or row.get("count") or 1),
        "sample_message": row.get("message") or row.get("sample_message", ""),
        "sample_stack":  row.get("stack_trace") or row.get("sample_stack", ""),
        "first_seen":    datetime.utcnow(),
        "last_seen":     datetime.utcnow(),
        "source_file":   blob_name,
        "total_errors":  len(rows),
    }
