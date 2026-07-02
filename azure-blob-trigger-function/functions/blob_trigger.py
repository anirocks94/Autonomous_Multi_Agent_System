"""
functions/blob_trigger.py — F1: Azure Blob Storage Trigger (Blueprint).

RESPONSIBILITY:
  Ingestion only. Fires when a new CSV is uploaded to the exceptions container.
  Delegates ALL business logic to the services/ layer.

FLOW:
  blob upload → csv_parser.parse() → actionability_filter.is_actionable()
             → state_builder.build_and_publish() [→ Service Bus]
             → archive blob to processed/ or skipped/
"""
import logging
import os

import azure.functions as func

from services import csv_parser, actionability_filter, state_builder

logger    = logging.getLogger(__name__)
bp        = func.Blueprint()

_CONTAINER = os.environ.get("AZURE_BLOB_CONTAINER", "exceptions")
_BLOB_CONN = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
_SB_QUEUE  = os.environ.get("SERVICE_BUS_QUEUE_NAME", "debug-jobs")


@bp.blob_trigger(
    arg_name="blob",
    path=f"{_CONTAINER}/{{name}}",
    connection="AZURE_BLOB_CONNECTION_STRING",
)
def exception_blob_trigger(blob: func.InputStream) -> None:
    """
    Triggered by any new blob in the exceptions container.
    Parses the CSV, applies the actionability gate, and publishes
    a DebugState JSON message to the Service Bus queue.
    """
    blob_name = blob.name.split("/")[-1]
    logger.info("🔔 [F1] Blob trigger: %s (%d bytes)", blob_name, blob.length)

    # Skip non-CSV and already-handled blobs
    if not blob_name.endswith(".csv"):
        logger.info("⏭️  [F1] Skipping non-CSV: %s", blob_name)
        return

    if "/processed/" in blob.name or "/skipped/" in blob.name:
        logger.info("⏭️  [F1] Already handled: %s", blob.name)
        return

    # ── 1. Parse ────────────────────────────────────────────────────────
    error_data = csv_parser.parse(blob.read(), blob_name)
    if error_data is None:
        logger.warning("⚠️  [F1] Unparseable CSV: %s", blob_name)
        _move_blob(blob_name, "skipped")
        return

    logger.info("✅ [F1] Parsed: %s (count=%d)", error_data["type"], error_data["count"])

    # ── 2. Actionability gate ────────────────────────────────────────────
    if not actionability_filter.is_actionable(error_data):
        logger.info("⏭️  [F1] Non-actionable — skipping")
        _move_blob(blob_name, "skipped")
        return

    # ── 3. Build DebugState + publish to Service Bus ─────────────────────
    session_id = state_builder.build_and_publish(error_data)
    logger.info("📨 [F1→SB] Published session=%s to queue='%s'", session_id, _SB_QUEUE)

    # ── 4. Archive blob ──────────────────────────────────────────────────
    _move_blob(blob_name, "processed")


def _move_blob(blob_name: str, prefix: str) -> None:
    """Copy blob to <prefix>/<blob_name> and delete the original."""
    if not _BLOB_CONN:
        logger.warning("AZURE_BLOB_CONNECTION_STRING not set — cannot move blob.")
        return
    if os.environ.get("LOCAL_DEV", "false").lower() == "true":
        logger.info("LOCAL_DEV: skipping blob move for %s → %s/", blob_name, prefix)
        return
    try:
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(_BLOB_CONN)
        cnt = svc.get_container_client(_CONTAINER)
        src = cnt.get_blob_client(blob_name)
        dst = cnt.get_blob_client(f"{prefix}/{blob_name}")
        dst.upload_blob(src.download_blob().readall(), overwrite=True)
        src.delete_blob()
        logger.info("📦 Moved %s → %s/%s", blob_name, prefix, blob_name)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("⚠️  Could not move blob '%s': %s", blob_name, exc)
