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

    # Working Directory
    WORKING_DIR = Path(os.getenv("WORKING_DIR", "./workspace"))
    WORKING_DIR.mkdir(exist_ok=True)

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
