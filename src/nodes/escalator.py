"""Escalation agent - creates Azure DevOps work items for human handoff."""
import requests
from base64 import b64encode
from state import DebugState, EscalationInfo, Decision
from config import Config
from datetime import datetime


class EscalationAgent:
    """Creates structured handoff to human developers via Azure DevOps work items."""

    def __init__(self):
        """Initialize escalation agent."""
        self.org = Config.AZURE_DEVOPS_ORG
        self.project = Config.AZURE_DEVOPS_PROJECT
        self.repo = Config.AZURE_DEVOPS_REPO
        self.pat = Config.AZURE_DEVOPS_PAT
        self.headers = {
            'Content-Type': 'application/json-patch+json',
            'Authorization': f'Basic {b64encode(f":{self.pat}".encode()).decode()}'
        }

    def escalate(self, state: DebugState) -> DebugState:
        """Create work item and mark workflow as escalated."""
        print("\n🚨 Escalation Agent: Creating escalation work item...")

        context_summary = self._build_context_summary(state)
        work_item = self._create_work_item(state, context_summary)

        escalation_info: EscalationInfo = {
            "work_item_id": work_item.get("id") if work_item else None,
            "work_item_url": (
                work_item.get("_links", {}).get("html", {}).get("href")
                if work_item else None
            ),
            "reason": state.get("failure_reason") or "Review feedback requires human intervention",
            "assigned_to": None,
            "escalated_at": datetime.now(),
            "context_summary": context_summary
        }

        state["escalation"] = escalation_info
        state["status"] = "escalated"

        # Add comment to PR if one exists
        if state.get("pr_number") and work_item:
            self._add_pr_comment(state["pr_number"], work_item.get("id"))

        decision: Decision = {
            "agent": "escalator",
            "decision_point": "escalation_created",
            "choice": "work_item_created" if work_item else "escalation_failed",
            "reasoning": (
                f"Created work item #{work_item.get('id', 'N/A')}: {escalation_info['reason']}"
                if work_item else f"Failed to create work item: {escalation_info['reason']}"
            ),
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        if work_item:
            print(f"   ✅ Work item #{work_item['id']} created")
            if escalation_info["work_item_url"]:
                print(f"   URL: {escalation_info['work_item_url']}")
        else:
            print("   ⚠️  Could not create work item")

        return state

    def _create_work_item(self, state: DebugState, context: str) -> dict:
        """Create a Bug work item via Azure DevOps REST API."""
        error_type = state["error_event"]["error_type"].split('.')[-1]
        class_name = (
            state["code_context"]["class_name"]
            if state.get("code_context") else "Unknown"
        )

        title = f"[ESCALATED] {error_type} in {class_name} - AI fix unsuccessful"

        # Azure DevOps work items use JSON Patch format
        patch_doc = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": context},
            {"op": "add", "path": "/fields/System.Tags",
             "value": "ai-escalation; auto-debug"},
            {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority",
             "value": 2},
        ]

        url = f"{self.org}/{self.project}/_apis/wit/workitems/$Bug?api-version=7.0"

        try:
            response = requests.post(url, json=patch_doc, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"   Error creating work item: {e}")
            return None

    def _build_context_summary(self, state: DebugState) -> str:
        """Build comprehensive HTML context for the work item description."""
        error = state["error_event"]
        ctx = state.get("code_context") or {}

        html = f"""<h2>Escalated Debugging Issue</h2>
<p>The autonomous debugging agent was unable to resolve this error after \
{state['current_attempt']} attempt(s).</p>

<h3>Error Details</h3>
<ul>
<li><b>Type:</b> {error['error_type']}</li>
<li><b>Message:</b> {error['message']}</li>
<li><b>File:</b> {ctx.get('file_path', 'N/A')}:{ctx.get('line_number', 'N/A')}</li>
<li><b>Frequency:</b> {error['frequency']} occurrences</li>
<li><b>Session ID:</b> {state['session_id']}</li>
</ul>

<h3>Fix Attempts</h3>"""

        for attempt in state.get("fix_attempts", []):
            html += f"""<details>
<summary>Attempt {attempt['attempt_number']}: {attempt['strategy']}</summary>
<pre>{attempt['fixed_code'][:1000]}</pre>
</details>"""

        if state.get("build_errors"):
            html += "<h3>Build Errors</h3>"
            for err in state["build_errors"]:
                html += f"<pre>{err['error_output'][:500]}</pre>"

        if state.get("parsed_feedback"):
            fb = state["parsed_feedback"]
            html += f"""<h3>Reviewer Feedback</h3>
<p><b>Status:</b> {fb['approval_status']}</p>
<p><b>Sentiment:</b> {fb['sentiment']}</p>
<p><b>Summary:</b> {fb['overall_summary']}</p>
<ul>"""
            for req in fb.get("change_requests", []):
                html += f"<li>{req}</li>"
            html += "</ul>"

        html += "<h3>Decision Trail</h3><ul>"
        for d in state.get("decisions", []):
            html += f"<li><b>{d['agent']}:</b> {d['reasoning']}</li>"
        html += "</ul>"

        return html

    def _add_pr_comment(self, pr_number: int, work_item_id: int) -> None:
        """Add escalation comment to the PR."""
        comment_headers = {
            'Content-Type': 'application/json',
            'Authorization': self.headers['Authorization']
        }
        thread_data = {
            "comments": [{
                "parentCommentId": 0,
                "content": (
                    f"⚠️ This PR has been **escalated** to a human developer. "
                    f"See work item #{work_item_id} for full context.\n\n"
                    f"The AI agent was unable to resolve the reviewer's feedback."
                ),
                "commentType": 1
            }],
            "status": 1
        }
        url = (
            f"{self.org}/{self.project}/_apis/git/repositories/{self.repo}"
            f"/pullRequests/{pr_number}/threads?api-version=7.0"
        )
        try:
            requests.post(url, json=thread_data, headers=comment_headers)
            print(f"   Added escalation comment to PR #{pr_number}")
        except Exception as e:
            print(f"   ⚠️  Could not add PR comment: {e}")
