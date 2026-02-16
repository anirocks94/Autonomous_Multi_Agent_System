"""Configuration management for the autonomous debugging agent."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Application configuration."""

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

    # Azure DevOps
    AZURE_DEVOPS_ORG = os.getenv("AZURE_DEVOPS_ORG")
    AZURE_DEVOPS_PROJECT = os.getenv("AZURE_DEVOPS_PROJECT")
    AZURE_DEVOPS_REPO = os.getenv("AZURE_DEVOPS_REPO")
    AZURE_DEVOPS_PAT = os.getenv("AZURE_DEVOPS_PAT")

    # Azure Blob Storage
    AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "exceptions")

    # Agent Settings
    MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
    CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.6"))
    POLLING_INTERVAL_SECONDS = int(os.getenv("POLLING_INTERVAL_SECONDS", "300"))
    ERROR_FREQUENCY_THRESHOLD = int(os.getenv("ERROR_FREQUENCY_THRESHOLD", "3"))

    # Review & Escalation Settings (Stage 3)
    MAX_REVIEW_POLLS = int(os.getenv("MAX_REVIEW_POLLS", "20"))
    REVIEW_POLL_INTERVAL_SECONDS = int(os.getenv("REVIEW_POLL_INTERVAL_SECONDS", "30"))

    # Working Directory
    WORKING_DIR = Path(os.getenv("WORKING_DIR", "./workspace"))
    WORKING_DIR.mkdir(exist_ok=True)

    # LangSmith Tracing
    LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false")
    LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "")
    LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "autonomous-debug-agent")
    LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")

    @classmethod
    def validate(cls):
        """Validate required configuration."""
        required = [
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_KEY",
            "AZURE_DEVOPS_ORG",
            "AZURE_DEVOPS_PAT",
            "AZURE_BLOB_CONNECTION_STRING"
        ]

        missing = [key for key in required if not getattr(cls, key)]
        if missing:
            raise ValueError(f"Missing required config: {', '.join(missing)}")

    @classmethod
    def get_llm(cls):
        """Return an AzureChatOpenAI instance for use with LangGraph agents."""
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_endpoint=cls.AZURE_OPENAI_ENDPOINT,
            api_key=cls.AZURE_OPENAI_API_KEY,
            azure_deployment=cls.AZURE_OPENAI_DEPLOYMENT,
            api_version=cls.AZURE_OPENAI_API_VERSION,
        )

    @classmethod
    def setup_langsmith(cls):
        """Configure LangSmith tracing environment variables."""
        if cls.LANGSMITH_TRACING.lower() == "true" and cls.LANGSMITH_API_KEY:
            os.environ["LANGSMITH_TRACING"] = "true"
            os.environ["LANGSMITH_API_KEY"] = cls.LANGSMITH_API_KEY
            os.environ["LANGSMITH_PROJECT"] = cls.LANGSMITH_PROJECT
            os.environ["LANGSMITH_ENDPOINT"] = cls.LANGSMITH_ENDPOINT
            print(f"   LangSmith tracing enabled (project: {cls.LANGSMITH_PROJECT})")
        else:
            print("   LangSmith tracing disabled (set LANGSMITH_TRACING=true to enable)")
