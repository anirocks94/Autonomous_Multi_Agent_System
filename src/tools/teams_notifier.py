"""
teams_notifier.py
=================

WHAT THIS FILE DOES
-------------------
Provides the `TeamsNotifier` class, a lightweight notification adapter that sends
formatted alert cards to a **Microsoft Teams channel** via an Incoming Webhook URL.
It is used by the autonomous debugging workflow to keep engineering teams informed
at two critical milestones:

  1. When a new exception is first detected and the agent begins its investigation.
  2. When the agent has successfully authored a fix and raised a pull request.

HOW IT WORKS
------------
1. **Webhook-based delivery** — Microsoft Teams supports *Incoming Webhooks*, which
   accept HTTP POST requests with a JSON payload following the *MessageCard* schema.
   No OAuth token or registered Azure AD app is required; only the webhook URL
   (stored in `Config.TEAMS_WEBHOOK_URL`) is needed.

2. **MessageCard schema** — Each notification is serialised as a legacy Teams
   *MessageCard* JSON object (`@type: "MessageCard"`).  The card includes:
   - A `themeColor` (red for errors, green for success) shown as a coloured stripe.
   - An `activityTitle` and `activitySubtitle` for at-a-glance context.
   - A `facts` list of key-value pairs (error type, message, PR URL, etc.).
   - An optional `potentialAction` block with a one-click "Review PR" button.

3. **Fire-and-forget safety** — All HTTP calls are wrapped in a broad `try/except`
   inside `_post()`.  A failed Teams ping is logged to stdout but never re-raised,
   ensuring that a transient network issue or misconfigured webhook cannot crash the
   autonomous agent workflow.

4. **Config guard** — Every public method checks `Config.TEAMS_WEBHOOK_URL` first
   and silently returns if it is empty, making Teams integration entirely optional
   — no environment variable, no notifications.

PUBLIC INTERFACE
----------------
TeamsNotifier.notify_exception_received(session_id: str, error_event: dict) -> None
    Sends a red "Exception Detected" alert card to Teams.
    `error_event` is the raw event dict from the monitoring agent and is expected
    to contain keys: `error_type`, `message`, and `frequency`.

TeamsNotifier.notify_pr_generated(session_id: str, pr_url: str, fix_description: str) -> None
    Sends a green "PR Created Successfully" card with a clickable link to the PR
    and a brief summary of the fix applied by the Fixer Agent.

TeamsNotifier._post(payload: dict) -> None  [internal]
    Posts the JSON payload to `Config.TEAMS_WEBHOOK_URL` via `requests.post()`.
    Swallows all exceptions to preserve workflow stability.
"""
import json
import traceback
import requests
try:
    from config import Config          # Azure Functions path (src/ added to sys.path)
except ImportError:
    from src.config import Config      # Direct run from repo root


class TeamsNotifier:
    """Tool class for sending alerts to a Microsoft Teams channel via Webhook."""

    @classmethod
    def notify_exception_received(cls, session_id: str, error_event: dict):
        """Send a notification when an exception is first detected by the monitoring agent."""
        if not Config.TEAMS_WEBHOOK_URL:
            # If no webhook is configured, silently skip.
            return

        error_type = error_event.get("error_type", "Unknown Error")
        message = error_event.get("message", "No message provided")
        frequency = error_event.get("frequency", 1)
        
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "FF0000",
            "summary": f"New Exception Detected: {error_type}",
            "sections": [{
                "activityTitle": f"🚨 Exception Detected: {error_type}",
                "activitySubtitle": f"Session: {session_id}",
                "facts": [
                    {"name": "Error Type:", "value": error_type},
                    {"name": "Message:", "value": message},
                    {"name": "Occurrences:", "value": str(frequency)}
                ],
                "markdown": True,
                "text": "The autonomous agent has started its investigation workflow."
            }]
        }

        cls._post(payload)

    @classmethod
    def notify_pr_generated(cls, session_id: str, pr_url: str, fix_description: str):
        """Send a notification when the agent has successfully authored a PR code fix."""
        if not Config.TEAMS_WEBHOOK_URL:
            return

        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "00FF00",
            "summary": f"Fix Generated and PR Created",
            "sections": [{
                "activityTitle": f"✅ PR Created Successfully",
                "activitySubtitle": f"Session: {session_id}",
                "facts": [
                    {"name": "PR Link:", "value": f"[{pr_url}]({pr_url})"},
                ],
                "markdown": True,
                "text": f"**Fix Summary:**\\n{fix_description}\\n\\nPlease review the PR in Azure DevOps."
            }],
            "potentialAction": [
                {
                    "@type": "OpenUri",
                    "name": "Review PR",
                    "targets": [
                        {"os": "default", "uri": pr_url}
                    ]
                }
            ]
        }

        cls._post(payload)

    @classmethod
    def _post(cls, payload: dict):
        """Internal helper to post the webhock to Teams."""
        try:
            response = requests.post(
                Config.TEAMS_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            response.raise_for_status()
        except Exception as e:
            # Log failure but do not crash the agent workflow.
            print(f"⚠️  Failed to send Teams notification: {e}")
            pass
