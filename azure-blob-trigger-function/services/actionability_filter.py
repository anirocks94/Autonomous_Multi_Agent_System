"""
services/actionability_filter.py — Decide if a detected error is within the
agent's capability to fix autonomously.

WHY AN ALLOWLIST?
  Attempting to generate a fix for an unknown or infrastructure-level error
  (e.g. OutOfMemoryException, network timeouts) would produce unreliable
  patches. Silently skipping unrecognised types is safer than a bad fix.

PUBLIC API:
    is_actionable(error_data: dict) -> bool
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)

# .NET exception types the agent knows how to fix autonomously
_ACTIONABLE_TYPES = frozenset([
    # Fully-qualified names (from App Insights export)
    "System.Collections.Generic.KeyNotFoundException",
    "System.NullReferenceException",
    "System.IndexOutOfRangeException",
    "System.DivideByZeroException",
    "System.ArgumentNullException",
    "System.FormatException",
    "System.InvalidOperationException",
    "System.ArgumentException",
    "System.IO.FileNotFoundException",
    "MimeKit.ParseException",
    # Short names (from simple CSV format)
    "KeyNotFoundException",
    "NullReferenceException",
    "IndexOutOfRangeException",
    "DivideByZeroException",
    "ArgumentNullException",
    "FormatException",
    "ParseException",
    "InvalidOperationException",
])


def is_actionable(error_data: Dict) -> bool:
    """
    Return True if the error type is in the supported allowlist.

    Args:
        error_data: Parsed error dict from services.csv_parser.parse().

    Returns:
        True  — the agent should attempt to fix this error.
        False — skip; log and move blob to 'skipped/' prefix.
    """
    error_type: str = error_data.get("type", "")
    result = any(allowed in error_type for allowed in _ACTIONABLE_TYPES)

    if result:
        logger.info("✅ Actionable: %s", error_type)
    else:
        logger.info("⏭️  Non-actionable, skipping: %s", error_type)

    return result
