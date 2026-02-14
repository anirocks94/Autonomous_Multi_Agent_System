"""State definitions for the debugging workflow."""
from typing import TypedDict, Optional, List, Literal
from datetime import datetime

class ErrorEvent(TypedDict):
    """Application Insights error event."""
    error_id: str
    error_type: str
    message: str
    stack_trace: str
    timestamp: datetime
    frequency: int

class CodeContext(TypedDict):
    """Code context around the error."""
    file_path: str
    line_number: int
    code_snippet: str
    method_name: str
    class_name: str

class FixAttempt(TypedDict):
    """A single fix attempt."""
    attempt_number: int
    strategy: str
    fixed_code: str
    reasoning: str

class TestResults(TypedDict):
    """Test execution results."""
    total: int
    passed: int
    failed: int
    failed_tests: List[str]

class Decision(TypedDict):
    """Agent decision log entry."""
    agent: str
    decision_point: str
    choice: str
    reasoning: str
    timestamp: datetime

class DebugState(TypedDict):
    """Complete state for the debugging workflow."""
    # Input
    session_id: str
    error_event: ErrorEvent

    # Repo
    repo_path: Optional[str]
    branch_name: Optional[str]

    # Analysis
    code_context: Optional[CodeContext]
    error_category: Optional[str]
    fix_strategy: Optional[str]
    confidence: float

    # Generation
    fix_attempts: List[FixAttempt]
    current_attempt: int
    max_attempts: int

    # Testing
    test_results: Optional[TestResults]

    # Output
    pr_url: Optional[str]
    pr_number: Optional[int]

    # Tracking
    decisions: List[Decision]
    status: Literal['detecting', 'analyzing', 'generating', 'testing', 'pr_created', 'failed']
    failure_reason: Optional[str]
