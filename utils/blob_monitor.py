"""Azure Blob Storage monitor for exception CSV files."""
import csv
import io
import json
from datetime import datetime
from typing import List, Dict, Optional
from azure.storage.blob import BlobServiceClient
from config import Config


class BlobMonitor:
    """Monitors Azure Blob Storage for new exception CSV files."""

    def __init__(self):
        """Initialize blob monitor."""
        self.blob_service = BlobServiceClient.from_connection_string(
            Config.AZURE_BLOB_CONNECTION_STRING
        )
        self.container_name = Config.AZURE_BLOB_CONTAINER
        self.container_client = self.blob_service.get_container_client(self.container_name)
        self._processed_blobs: set = set()

    def check_for_new_files(self) -> Optional[Dict]:
        """
        Check for new exception CSV files in blob storage.

        Returns:
            Parsed error data from the first unprocessed CSV, or None
        """
        blobs = self.container_client.list_blobs()

        for blob in blobs:
            if blob.name.endswith(".csv") and blob.name not in self._processed_blobs:
                if blob.name.startswith("processed/"):
                    continue
                print(f"   Found new exception file: {blob.name}")
                error_data = self._download_and_parse(blob.name)
                self._processed_blobs.add(blob.name)
                if error_data:
                    return error_data

        return None

    def _download_and_parse(self, blob_name: str) -> Optional[Dict]:
        """Download CSV blob and parse exception data."""
        blob_client = self.container_client.get_blob_client(blob_name)
        content = blob_client.download_blob().readall().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        rows = [r for r in reader if r.get("type")]

        if not rows:
            print(f"   Empty CSV file: {blob_name}")
            return None

        # Detect format: App Insights export vs simple format
        if "details" in rows[0] or "problemId" in rows[0]:
            return self._parse_app_insights_format(rows, blob_name)
        else:
            return self._parse_simple_format(rows, blob_name)

    def _parse_app_insights_format(self, rows: List[Dict], blob_name: str) -> Optional[Dict]:
        """Parse Application Insights CSV export format."""
        # Find the best row (one with method info and details)
        best_row = None
        for row in rows:
            if row.get("method") and row.get("details"):
                best_row = row
                break
        if not best_row:
            best_row = rows[0]

        # Extract stack trace from details JSON
        stack_trace = ""
        message = best_row.get("innermostMessage") or best_row.get("message", "")
        details = best_row.get("details", "")

        if details:
            try:
                details_json = json.loads(details)
                if isinstance(details_json, list):
                    for entry in details_json:
                        raw_stack = entry.get("rawStack", "")
                        if raw_stack:
                            stack_trace = raw_stack
                            break
                        parsed_stack = entry.get("parsedStack", [])
                        if parsed_stack:
                            stack_lines = []
                            for frame in parsed_stack:
                                method = frame.get("method", "")
                                assembly = frame.get("assembly", "")
                                filename = frame.get("fileName", "")
                                line = frame.get("line", 0)
                                if filename and line:
                                    stack_lines.append(f"   at {method} in {filename}:line {line}")
                                else:
                                    stack_lines.append(f"   at {method}")
                            stack_trace = "\n".join(stack_lines)
                            break
                        if not message:
                            message = entry.get("message", "")
            except (json.JSONDecodeError, TypeError):
                pass

        if not stack_trace:
            stack_trace = best_row.get("stack", "") or best_row.get("method", "")

        error_type = best_row.get("type", "UnknownException")

        return {
            "type": error_type,
            "problemId": blob_name.replace(".csv", ""),
            "count": len(rows),
            "sample_message": message,
            "sample_stack": stack_trace,
            "first_seen": datetime.now(),
            "last_seen": datetime.now(),
            "source_file": blob_name,
            "total_errors": len(rows)
        }

    def _parse_simple_format(self, rows: List[Dict], blob_name: str) -> Optional[Dict]:
        """Parse simple CSV format (error_type, message, stack_trace, frequency)."""
        row = rows[0]
        return {
            "type": row.get("error_type", row.get("type", "UnknownException")),
            "problemId": blob_name.replace(".csv", ""),
            "count": int(row.get("frequency", row.get("count", "1"))),
            "sample_message": row.get("message", row.get("sample_message", "")),
            "sample_stack": row.get("stack_trace", row.get("sample_stack", "")),
            "first_seen": datetime.now(),
            "last_seen": datetime.now(),
            "source_file": blob_name,
            "total_errors": len(rows)
        }

    def mark_processed(self, blob_name: str) -> None:
        """Move processed file to a 'processed' folder."""
        try:
            source_blob = self.container_client.get_blob_client(blob_name)
            content = source_blob.download_blob().readall()

            dest_name = f"processed/{blob_name}"
            dest_blob = self.container_client.get_blob_client(dest_name)
            dest_blob.upload_blob(content, overwrite=True)

            source_blob.delete_blob()
            print(f"   Moved {blob_name} -> {dest_name}")
        except Exception as e:
            print(f"   Warning: Could not move blob: {e}")
