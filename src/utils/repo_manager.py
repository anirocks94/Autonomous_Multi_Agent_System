"""Azure DevOps repository management."""
import shutil
from pathlib import Path
from git import Repo
from config import Config

class RepoManager:
    """Manages cloning and operations on Azure DevOps repo."""

    def __init__(self):
        """Initialize repo manager."""
        self.org = Config.AZURE_DEVOPS_ORG
        self.project = Config.AZURE_DEVOPS_PROJECT
        self.repo_name = Config.AZURE_DEVOPS_REPO
        self.pat = Config.AZURE_DEVOPS_PAT
        self.working_dir = Config.WORKING_DIR

    def clone_repo(self, session_id: str) -> str:
        """
        Clone the Azure DevOps repository.

        Args:
            session_id: Unique session identifier

        Returns:
            Path to cloned repository
        """
        # Build clone URL with PAT
        clone_url = f"{self.org}/{self.project}/_git/{self.repo_name}"
        auth_url = clone_url.replace("https://", f"https://{self.pat}@")

        # Create session-specific directory
        repo_path = self.working_dir / f"repo-{session_id}"

        # Remove if exists
        if repo_path.exists():
            shutil.rmtree(repo_path)

        # Clone
        print(f"Cloning repository to {repo_path}...")
        Repo.clone_from(auth_url, repo_path)

        return str(repo_path)

    def create_branch(self, repo_path: str, branch_name: str) -> None:
        """Create a new branch for the fix."""
        repo = Repo(repo_path)
        repo.git.checkout('-b', branch_name)

    def commit_changes(self, repo_path: str, file_path: str, commit_message: str) -> str:
        """Commit changes to the repository."""
        repo = Repo(repo_path)
        repo.index.add([file_path])
        commit = repo.index.commit(commit_message)
        return commit.hexsha

    def push_branch(self, repo_path: str, branch_name: str) -> None:
        """Push branch to remote."""
        repo = Repo(repo_path)
        origin = repo.remote('origin')
        origin.push(branch_name)
