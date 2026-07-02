"""Application Insights client for querying errors."""
from datetime import datetime, timedelta
from typing import List, Dict
from config import Config

class AppInsightsClient:
    """Client for querying Application Insights via Azure Monitor."""

    def __init__(self):
        """Initialize the client."""
        self.connection_string = Config.AZURE_APP_INSIGHTS_CONNECTION_STRING
        self.workspace_id = Config.AZURE_WORKSPACE_ID
        self.instrumentation_key = Config.AZURE_APP_INSIGHTS_INSTRUMENTATION_KEY

    def fetch_recent_errors(self, time_window_minutes: int = 10) -> List[Dict]:
        """
        Fetch recent errors from Application Insights.

        Args:
            time_window_minutes: Time window to query

        Returns:
            List of error events
        """
        # For Day 1 MVP: Simulate errors (replace with real query later)
        # TODO: Replace with real Azure Monitor query using:
        #   from azure.monitor.query import LogsQueryClient
        #   from azure.identity import DefaultAzureCredential
        #   client = LogsQueryClient(DefaultAzureCredential())
        #   query = "exceptions | where timestamp > ago(10m) | summarize count() by type, problemId"
        return self._simulate_errors()

    def _simulate_errors(self) -> List[Dict]:
        """Simulate errors for Day 1 testing."""
        return [
            {
                "type": "System.Collections.Generic.KeyNotFoundException",
                "problemId": "error-001",
                "count": 5,
                "sample_message": "The given key '999' was not present in the dictionary.",
                "sample_stack": """at AutoDebugDemo.Functions.GetUserFunction.Run(HttpRequestData req, String userId) in /src/GetUserFunction.cs:line 23""",
                "first_seen": datetime.now() - timedelta(minutes=5),
                "last_seen": datetime.now()
            }
        ]
