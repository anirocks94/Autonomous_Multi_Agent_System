"""
PR Creator Agent — Automated Pull Request Generation via Azure DevOps REST API.

WHAT THIS FILE DOES:
  Takes the AI-generated code fix from the Fixer agent and converts it into
  a proper engineering artifact: a feature branch, a commit, and a draft PR
  in Azure DevOps, complete with a structured description and a Teams
  notification to the team.

WORKFLOW (deterministic node, no LLM involved):
    1. Derive branch name  →  auto-fix/<ErrorType>-<session_id>
    2. RepoManager.create_branch()  →  git checkout -b
    3. RepoManager.commit_changes() →  git add + git commit
    4. RepoManager.push_branch()    →  git push origin
    5. _create_pr_api()             →  POST /pullrequests (Azure DevOps REST API 7.0)
    6. TeamsNotifier.notify_pr_generated()  →  Teams webhook card

PR DESCRIPTION (self-documenting):
  The PR body includes:
  - Error type, location (file:line), frequency
  - Fix strategy and confidence score
  - LLM model used (transparency for reviewers)
  - Decision trail (append-only audit log from state["decisions"])
  - isDraft: True — always created as draft to require human merge

AZURE DEVOPS AUTHENTICATION:
  Basic auth using a Personal Access Token (PAT) encoded as Base64.
  Header: Authorization: Basic base64(":<PAT>")
  This is the standard pattern for Azure DevOps REST APIs when not
  using Azure AD service principals.

TEAMS INTEGRATION:
  After a successful PR creation, TeamsNotifier.notify_pr_generated() posts
  a green MessageCard with a "Review PR" action button.  Uses TEAMS_WEBHOOK_URL
  from Config (optional — silently skipped if not configured).

INTERVIEW TALKING POINTS:
  - isDraft: True is a deliberate safety measure: AI-generated code should
    never be auto-merged.  A human must explicitly mark it ready.
  - The decision trail in the PR description makes the agent's reasoning
    transparent to reviewers — critical for AI accountability.
  - Branch naming convention (auto-fix/<type>-<session_id>) keeps branches
    identifiable and traceable back to the agent run.
"""
import requests
from base64 import b64encode
from state import DebugState, Decision
from config import Config
from datetime import datetime

class PRCreatorAgent:
    """Creates pull requests in Azure DevOps."""

    def __init__(self):
        """Initialize PR creator agent."""
        self.org = Config.AZURE_DEVOPS_ORG
        self.project = Config.AZURE_DEVOPS_PROJECT
        self.repo = Config.AZURE_DEVOPS_REPO
        self.pat = Config.AZURE_DEVOPS_PAT

        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Basic {b64encode(f":{self.pat}".encode()).decode()}'
        }

    def create_pr(self, state: DebugState) -> DebugState:
        """Create a pull request with the fix."""
        print("\n📝 PR Creator Agent: Creating pull request...")

        error_type = state["error_event"]["error_type"].split('.')[-1]
        branch_name = f"auto-fix/{error_type.lower()}-{state['session_id']}"
        state["branch_name"] = branch_name

        # Commit and push changes
        from utils.repo_manager import RepoManager
        repo_manager = RepoManager()

        print(f"   Creating branch: {branch_name}")
        repo_manager.create_branch(state["repo_path"], branch_name)

        print("   Committing changes...")
        commit_message = self._build_commit_message(state)
        repo_manager.commit_changes(
            state["repo_path"],
            state["code_context"]["file_path"],
            commit_message
        )

        print("   Pushing to remote...")
        repo_manager.push_branch(state["repo_path"], branch_name)

        # Create PR via Azure DevOps API
        print("   Creating pull request...")
        pr_data = self._build_pr_data(state, branch_name)
        pr_response = self._create_pr_api(pr_data)

        if pr_response:
            state["pr_url"] = pr_response.get("url")
            state["pr_number"] = pr_response.get("pullRequestId")
            state["status"] = "pr_created"
            print(f"   ✅ PR created: {state['pr_url']}")
            
            from tools.teams_notifier import TeamsNotifier
            # Pass the reasoning/strategy back as description
            TeamsNotifier.notify_pr_generated(state["session_id"], state["pr_url"], state["fix_strategy"])
        else:
            state["status"] = "failed"
            state["failure_reason"] = "Failed to create PR"
            print("   ❌ Failed to create PR")

        decision: Decision = {
            "agent": "pr_creator",
            "decision_point": "pr_created",
            "choice": "success" if pr_response else "failed",
            "reasoning": f"Created PR #{state.get('pr_number', 'N/A')}",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        return state

    def _build_commit_message(self, state: DebugState) -> str:
        """Build commit message."""
        error_type = state["error_event"]["error_type"]
        location = f"{state['code_context']['file_path']}:{state['code_context']['line_number']}"

        return f"""Auto-fix: {error_type}

Location: {location}
Strategy: {state['fix_strategy']}
Model: {Config.AZURE_OPENAI_DEPLOYMENT}
Session: {state['session_id']}

Generated by Autonomous Debugging Agent"""

    def _build_pr_data(self, state: DebugState, branch_name: str) -> dict:
        """Build PR request data."""
        error_event = state["error_event"]
        test_results = state["test_results"]

        title = f"[AUTO-FIX] {error_event['error_type'].split('.')[-1]} in {state['code_context']['class_name']}"

        description = f"""## 🤖 Automated Fix - Human Review Required

**⚠️ This PR was generated by an AI agent using Azure OpenAI ({Config.AZURE_OPENAI_DEPLOYMENT}). Please review carefully before merging.**

### Error Summary
- **Type**: `{error_event['error_type']}`
- **Location**: `{state['code_context']['file_path']}:{state['code_context']['line_number']}`
- **Frequency**: {error_event['frequency']} occurrences
- **Method**: `{state['code_context']['method_name']}`

### Fix Strategy
**Approach**: {state['fix_strategy']}
**Confidence**: {state['confidence']:.2f}
**Model**: {Config.AZURE_OPENAI_DEPLOYMENT}
**Attempt**: {state['current_attempt']}/{state['max_attempts']}

### Testing Results
✅ **Tests Passed**: {test_results['passed']}/{test_results['total']}
❌ **Tests Failed**: {test_results['failed']}

### Decision Trail
"""
        for decision in state["decisions"]:
            description += f"- **{decision['agent']}**: {decision['reasoning']}\n"

        description += f"""

---
**Session ID**: {state['session_id']}
**Generated by**: Autonomous Debugging Agent (Azure OpenAI)
"""

        return {
            "sourceRefName": f"refs/heads/{branch_name}",
            "targetRefName": "refs/heads/master",
            "title": title,
            "description": description,
            "isDraft": True
        }

    def _create_pr_api(self, pr_data: dict) -> dict:
        """Call Azure DevOps API to create PR."""
        url = f"{self.org}/{self.project}/_apis/git/repositories/{self.repo}/pullrequests?api-version=7.0"

        try:
            response = requests.post(url, json=pr_data, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"   Error creating PR: {e}")
            return None
