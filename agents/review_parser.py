"""Review parser agent - fetches and parses PR review comments."""
import json
import time
import requests
from base64 import b64encode
from typing import List
from openai import AzureOpenAI
from state import DebugState, ReviewComment, ParsedReviewFeedback, Decision
from config import Config
from datetime import datetime


class ReviewParserAgent:
    """Fetches PR comments from Azure DevOps and parses them with AI."""

    def __init__(self):
        """Initialize review parser agent."""
        self.org = Config.AZURE_DEVOPS_ORG
        self.project = Config.AZURE_DEVOPS_PROJECT
        self.repo = Config.AZURE_DEVOPS_REPO
        self.pat = Config.AZURE_DEVOPS_PAT
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Basic {b64encode(f":{self.pat}".encode()).decode()}'
        }
        self.client = AzureOpenAI(
            api_key=Config.AZURE_OPENAI_API_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT
        )
        self.deployment = Config.AZURE_OPENAI_DEPLOYMENT

    def poll_and_parse(self, state: DebugState) -> DebugState:
        """Fetch PR comments and parse into structured feedback."""
        print("\n📬 Review Parser Agent: Checking for PR review comments...")

        pr_number = state.get("pr_number")
        if not pr_number:
            state["status"] = "failed"
            state["failure_reason"] = "No PR number to poll"
            return state

        # Fetch comment threads from Azure DevOps
        raw_threads = self._fetch_pr_threads(pr_number)
        human_comments = self._extract_human_comments(raw_threads)

        if not human_comments:
            # No comments yet — increment poll count and wait
            state["review_poll_count"] = state.get("review_poll_count", 0) + 1
            state["status"] = "awaiting_review"
            poll_count = state["review_poll_count"]
            max_polls = state.get("max_review_polls", Config.MAX_REVIEW_POLLS)
            print(f"   No comments yet (poll {poll_count}/{max_polls})")
            print(f"   Waiting {Config.REVIEW_POLL_INTERVAL_SECONDS}s before next poll...")
            time.sleep(Config.REVIEW_POLL_INTERVAL_SECONDS)
            return state

        # Parse comments with LLM
        print(f"   Found {len(human_comments)} review comment(s)")
        state["review_comments"] = human_comments
        parsed = self._parse_comments_with_llm(human_comments, state)
        state["parsed_feedback"] = parsed
        state["status"] = "awaiting_review"

        decision: Decision = {
            "agent": "review_parser",
            "decision_point": "comments_parsed",
            "choice": parsed["approval_status"],
            "reasoning": f"Parsed {len(human_comments)} comment(s): {parsed['overall_summary']}",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        print(f"   ✅ Feedback parsed: {parsed['approval_status']} ({parsed['sentiment']})")
        print(f"   Summary: {parsed['overall_summary']}")

        return state

    def _fetch_pr_threads(self, pr_number: int) -> List[dict]:
        """Fetch PR comment threads from Azure DevOps REST API."""
        url = (
            f"{self.org}/{self.project}/_apis/git/repositories/{self.repo}"
            f"/pullRequests/{pr_number}/threads?api-version=7.0"
        )
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json().get("value", [])
        except Exception as e:
            print(f"   Error fetching PR threads: {e}")
            return []

    def _extract_human_comments(self, threads: List[dict]) -> List[ReviewComment]:
        """Extract human-authored comments from thread data."""
        comments = []
        for thread in threads:
            for comment in thread.get("comments", []):
                # Skip system comments
                if comment.get("commentType") == "system":
                    continue
                if comment.get("isDeleted"):
                    continue

                author = comment.get("author", {}).get("displayName", "Unknown")
                content = comment.get("content", "")
                if not content.strip():
                    continue

                # Extract file/line context from thread
                thread_context = thread.get("threadContext") or {}
                file_path = thread_context.get("filePath")
                line_number = None
                right_file_start = thread_context.get("rightFileStart") or {}
                if right_file_start:
                    line_number = right_file_start.get("line")

                review_comment: ReviewComment = {
                    "comment_id": comment.get("id", 0),
                    "author": author,
                    "content": content,
                    "file_path": file_path,
                    "line_number": line_number,
                    "thread_status": thread.get("status", "unknown"),
                    "created_date": comment.get("publishedDate", "")
                }
                comments.append(review_comment)

        return comments

    def _parse_comments_with_llm(self, comments: List[ReviewComment],
                                  state: DebugState) -> ParsedReviewFeedback:
        """Use Azure OpenAI to parse unstructured comments into structured feedback."""
        print("   Parsing comments with Azure OpenAI...")

        comments_text = "\n\n".join([
            f"[{c['author']}] ({c.get('file_path') or 'general'}:"
            f"{c.get('line_number') or 'N/A'}): {c['content']}"
            for c in comments
        ])

        prompt = f"""Analyze these code review comments on a PR that fixes a {state['error_event']['error_type']} error.

**Review Comments:**
{comments_text}

Return ONLY valid JSON (no markdown, no code fences):
{{
    "approval_status": "approved|changes_requested|rejected|pending",
    "sentiment": "positive|neutral|negative|critical",
    "change_requests": ["specific change 1", "specific change 2"],
    "affected_lines": [line_numbers_mentioned_as_integers],
    "affected_files": ["file/paths/mentioned"],
    "overall_summary": "one sentence summary of reviewer intent"
}}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {
                        "role": "system",
                        "content": "You parse code review comments into structured feedback. Return ONLY valid JSON, no markdown."
                    },
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=1000
            )

            response_text = response.choices[0].message.content.strip()

            # Strip markdown fences if present
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                response_text = "\n".join(lines)

            result = json.loads(response_text)
            return {
                "approval_status": result.get("approval_status", "pending"),
                "sentiment": result.get("sentiment", "neutral"),
                "change_requests": result.get("change_requests", []),
                "affected_lines": result.get("affected_lines", []),
                "affected_files": result.get("affected_files", []),
                "overall_summary": result.get("overall_summary", ""),
                "raw_comments": comments
            }
        except Exception as e:
            print(f"   ⚠️  LLM parsing failed ({e}), using fallback")
            return self._fallback_parse(comments)

    def _fallback_parse(self, comments: List[ReviewComment]) -> ParsedReviewFeedback:
        """Rule-based fallback for comment parsing."""
        all_text = " ".join(c["content"].lower() for c in comments)

        if any(w in all_text for w in ["lgtm", "approve", "looks good", "ship it"]):
            status = "approved"
            sentiment = "positive"
        elif any(w in all_text for w in ["reject", "do not merge", "wrong approach", "not suitable"]):
            status = "rejected"
            sentiment = "critical"
        else:
            status = "changes_requested"
            sentiment = "neutral"

        return {
            "approval_status": status,
            "sentiment": sentiment,
            "change_requests": [c["content"] for c in comments],
            "affected_lines": [c["line_number"] for c in comments if c.get("line_number")],
            "affected_files": [c["file_path"] for c in comments if c.get("file_path")],
            "overall_summary": f"Fallback parse: {status}",
            "raw_comments": comments
        }
