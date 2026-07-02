"""
State Definitions — Autonomous Debugging Agent (LangGraph TypedDict Contract).

WHAT THIS FILE DOES:
  Defines every TypedDict that flows through the LangGraph StateGraph.
  In LangGraph, the "state" is the single source of truth shared across all
  nodes; every node receives the full state, mutates only the keys it owns,
  and returns the updated state.  This file is the contract between nodes.

DESIGN PRINCIPLES:
  1. TypedDict (not Pydantic BaseModel) — LangGraph requires plain-dict
     serialisability for its checkpointer.  TypedDict gives us static type
     hints without the Pydantic overhead.

  2. Optional fields represent "not yet computed" stages.  A node should
     never assume a downstream key is populated.

  3. Annotated[List[…], operator.add] — LangGraph's reducer protocol.
     The parallel_fix_attempts list uses operator.add as a merge reducer,
     enabling fan-out/fan-in (Send API) without race conditions.

  4. The Literal type on DebugState.status acts as a compile-time enum,
     documenting every valid lifecycle state of a workflow session:
       detecting → analyzing → generating → testing →
       pr_created / failed / awaiting_approval / rejected /
       awaiting_review / incorporating_feedback / escalated

STAGED EVOLUTION:
  ┌──────────┬─────────────────────────────────────────────────────────┐
  │ Stage 1  │ ErrorEvent, CodeContext, FixAttempt, TestResults        │
  │ Stage 2  │ AnalysisResult, BuildError, parallel_fix_attempts      │
  │ Stage 3  │ ReviewComment, ParsedReviewFeedback, EscalationInfo,   │
  │          │ SupervisorDecision                                      │
  │ Stage 4  │ InvestigationOutput, FixOutput (ReAct agent outputs)   │
  │ Stage 5  │ rag_context (injected from ChromaDB retrieval)         │
  └──────────┴─────────────────────────────────────────────────────────┘

KEY TYPES FOR INTERVIEW DISCUSSION:
  DebugState  — top-level graph state; all nodes share this dict
  Decision    — immutable audit-log entry appended by every node
  SupervisorDecision — routing log entry appended by the Supervisor
"""
import operator
from typing import TypedDict, Optional, List, Literal, Annotated
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


class AnalysisResult(TypedDict):
    """AI-powered error analysis result."""
    category: str
    strategy: str
    confidence: float
    reasoning: str
    alternative_strategies: List[str]


class BuildError(TypedDict):
    """Build error from a failed fix attempt."""
    error_output: str
    failed_code: str
    attempt_number: int


class ApprovalInfo(TypedDict):
    """Human approval information."""
    status: Literal['pending', 'approved', 'rejected', 'changes_requested']
    reviewer_feedback: Optional[str]
    reviewed_at: Optional[datetime]


class ReviewComment(TypedDict):
    """A single parsed review comment from a PR."""
    comment_id: int
    author: str
    content: str
    file_path: Optional[str]
    line_number: Optional[int]
    thread_status: Optional[str]
    created_date: str


class ParsedReviewFeedback(TypedDict):
    """Structured feedback extracted from PR review comments."""
    approval_status: Literal['approved', 'changes_requested', 'rejected', 'pending']
    sentiment: Literal['positive', 'neutral', 'negative', 'critical']
    change_requests: List[str]
    affected_lines: List[int]
    affected_files: List[str]
    overall_summary: str
    raw_comments: List[ReviewComment]


class EscalationInfo(TypedDict):
    """Information about an escalation to human developers."""
    work_item_id: Optional[int]
    work_item_url: Optional[str]
    reason: str
    assigned_to: Optional[str]
    escalated_at: datetime
    context_summary: str


class SupervisorDecision(TypedDict):
    """A routing decision made by the supervisor."""
    decision_point: str
    available_routes: List[str]
    chosen_route: str
    reasoning: str
    used_llm: bool
    timestamp: datetime


class InvestigationOutput(TypedDict):
    """Structured investigation results from the Investigator Agent (Stage 4)."""
    root_cause: str
    error_category: str
    file_path: str
    line_number: int
    method_name: str
    class_name: str
    code_snippet: str
    fix_strategy: str
    confidence: float
    additional_context: str
    affected_files: List[str]


class FixOutput(TypedDict):
    """Structured fix results from the Fixer Agent (Stage 4)."""
    fixed_file_path: str
    strategy_used: str
    fix_description: str
    build_passed: bool
    attempts_made: int
    final_code: str


class TriageOutput(TypedDict):
    decision: Literal['FIXABLE', 'DUPLICATE', 'UNFIXABLE']
    reasoning: str

class DebugState(TypedDict):
    """Complete state for the debugging workflow."""
    # Input
    session_id: str
    error_event: ErrorEvent

    # Triage (Stage 1.5)
    triage_output: Optional[TriageOutput]

    # Repo
    repo_path: Optional[str]
    branch_name: Optional[str]

    # Analysis
    code_context: Optional[CodeContext]
    error_category: Optional[str]
    fix_strategy: Optional[str]
    confidence: float

    # AI Analysis (Stage 2)
    analysis_result: Optional[AnalysisResult]
    parallel_strategies: List[str]

    # Generation
    fix_attempts: List[FixAttempt]
    current_attempt: int
    max_attempts: int

    # Parallel fix attempts (Stage 2 - Send API reducer)
    parallel_fix_attempts: Annotated[List[FixAttempt], operator.add]
    best_fix_index: Optional[int]

    # Build errors for self-correction (Stage 2)
    build_errors: List[BuildError]

    # Testing
    test_results: Optional[TestResults]

    # Approval (Stage 2)
    approval: Optional[ApprovalInfo]

    # Review feedback (Stage 3)
    review_comments: List[ReviewComment]
    parsed_feedback: Optional[ParsedReviewFeedback]
    review_poll_count: int
    max_review_polls: int
    reviewer_feedback_context: Optional[str]

    # Escalation (Stage 3)
    escalation: Optional[EscalationInfo]

    # Supervisor (Stage 3)
    supervisor_decisions: List[SupervisorDecision]

    # Agentic investigation & fix (Stage 4)
    investigation_output: Optional[InvestigationOutput]
    fix_output: Optional[FixOutput]

    # RAG context (Stage 5)
    rag_context: Optional[str]

    # Output
    pr_url: Optional[str]
    pr_number: Optional[int]

    # Tracking
    decisions: List[Decision]
    status: Literal[
        'detecting', 'analyzing', 'generating', 'testing',
        'pr_created', 'failed', 'awaiting_approval', 'rejected',
        'awaiting_review', 'incorporating_feedback', 'escalated'
    ]
    failure_reason: Optional[str]
