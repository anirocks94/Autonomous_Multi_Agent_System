"""
Triage Agent — Stage 1.5 (Three-Layer Reliability Design).

WHAT THIS FILE DOES:
  Acts as a "bouncer" before the repository is cloned. Evaluates whether
  an incoming error is worth the cost of cloning the repo and spinning up
  the full Investigator + Fixer pipeline.

THREE-LAYER TRIAGE (ordered by reliability, fail-safe first):

  ┌─────────────────────────────────────────────────────────────────────┐
  │ Layer 1: Deterministic Rules (O(1), no LLM)                        │
  │   Hard blocks known infrastructure errors by type name.            │
  │   100% reliable — never wrong, never skips real code bugs.         │
  ├─────────────────────────────────────────────────────────────────────┤
  │ Layer 2: RAG Duplicate Detection (vector similarity, no LLM)       │
  │   Checks if we already fixed this exact error recently.            │
  │   Uses a similarity threshold + status check to avoid false hits.  │
  ├─────────────────────────────────────────────────────────────────────┤
  │ Layer 3: LLM Advisory (non-blocking, soft signal)                  │
  │   LLM provides categorisation guidance on ambiguous errors.        │
  │   IMPORTANT: If the LLM says UNFIXABLE but the error type is in    │
  │   KNOWN_CODE_ERRORS, the LLM decision is OVERRIDDEN by the rule.  │
  │   LLM can never silently drop a confirmed code-level error.        │
  └─────────────────────────────────────────────────────────────────────┘

DESIGN RATIONALE:
  LLMs are non-deterministic and can sometimes misclassify. Since the
  Triage is the first (and only) gate before cloning, a false UNFIXABLE
  means the bug is silently ignored forever. The rule-based overrides in
  Layer 1 and Layer 3 ensure that known code bugs always get through.
"""
from datetime import datetime
from typing import Optional
from config import Config
from state import DebugState, Decision, TriageOutput
from rag.memory import DebugMemory

# ── Layer 1: Hard infrastructure error blocklist ─────────────────────────────
# These errors are NOT fixable by writing C# code. The AI cannot resolve
# OOM, thread aborts, or network-level failures by patching application code.
_INFRA_ERROR_TYPES: frozenset = frozenset([
    # Memory / runtime
    "OutOfMemoryException",
    "StackOverflowException",
    "ThreadAbortException",
    "ExecutionEngineException",
    "AccessViolationException",

    # Network / Azure infrastructure
    "SocketException",
    "HttpRequestException",
    "WebException",
    "TimeoutException",
    "OperationCanceledException",
    "TaskCanceledException",

    # Deployment / environment
    "TypeLoadException",
    "DllNotFoundException",
    "BadImageFormatException",
    "PlatformNotSupportedException",

    # Auth / cloud tokens
    "AuthenticationException",
    "SecurityException",
    "UnauthorizedAccessException",
])

# ── Layer 3: Override guard — never let LLM skip these code-level types ───────
# If the error type contains any of these, LLM UNFIXABLE decision is overridden.
_KNOWN_CODE_ERRORS: frozenset = frozenset([
    "NullReferenceException",
    "KeyNotFoundException",
    "IndexOutOfRangeException",
    "ArgumentNullException",
    "ArgumentException",
    "FormatException",
    "ParseException",
    "InvalidOperationException",
    "DivideByZeroException",
    "FileNotFoundException",
    "NotImplementedException",
    "NotSupportedException",
    "OverflowException",
    "InvalidCastException",
])

# Minimum ChromaDB similarity to treat a past fix as a DUPLICATE hit
_DUPLICATE_SIMILARITY_THRESHOLD: float = 0.92


class TriageAgent:
    """Three-layer Triage agent that evaluates errors before cloning the repo."""

    def __init__(self):
        self.llm = Config.get_llm()
        self.structured_llm = self.llm.with_structured_output(TriageOutput)
        self.memory = DebugMemory.get_instance()

    # ── Public entry point ────────────────────────────────────────────────────

    def triage(self, state: DebugState) -> DebugState:
        """Evaluate the incoming error across three layers and set triage_output."""
        print("\n🚦 Triage Agent: Evaluating error...")
        error = state["error_event"]
        error_type: str = error.get("error_type", "")

        if "decisions" not in state:
            state["decisions"] = []

        # ── Layer 1: Deterministic infrastructure block ───────────────────────
        infra_decision = self._check_infrastructure(error_type)
        if infra_decision:
            print(f"   🔴 Layer 1 (Rule): {infra_decision['decision']} — {infra_decision['reasoning']}")
            state["triage_output"] = infra_decision
            state["status"] = "rejected"
            state["failure_reason"] = infra_decision["reasoning"]
            self._record_decision(state, infra_decision, layer=1)
            return state

        # ── Layer 2: RAG duplicate detection ─────────────────────────────────
        rag_ctx = self._retrieve_rag_context(error_type, error.get("message", ""))
        state["rag_context"] = rag_ctx or None

        duplicate_decision = self._check_duplicate(error_type, error.get("stack_trace", ""))
        if duplicate_decision:
            print(f"   🟡 Layer 2 (RAG): {duplicate_decision['decision']} — {duplicate_decision['reasoning']}")
            state["triage_output"] = duplicate_decision
            state["status"] = "rejected"
            state["failure_reason"] = duplicate_decision["reasoning"]
            self._record_decision(state, duplicate_decision, layer=2)
            return state

        # ── Layer 3: LLM advisory (non-blocking) ─────────────────────────────
        llm_decision = self._llm_advisory(error, rag_ctx)
        llm_decision = self._apply_code_error_override(llm_decision, error_type)

        print(f"   🔵 Layer 3 (LLM): {llm_decision['decision']} — {llm_decision['reasoning']}")
        state["triage_output"] = llm_decision

        if llm_decision["decision"] != "FIXABLE":
            state["status"] = "rejected"
            state["failure_reason"] = llm_decision["reasoning"]

        self._record_decision(state, llm_decision, layer=3)
        return state

    # ── Layer 1: Infrastructure rule check ───────────────────────────────────

    def _check_infrastructure(self, error_type: str) -> Optional[TriageOutput]:
        """Return UNFIXABLE if the error type is a known infrastructure error."""
        for infra_type in _INFRA_ERROR_TYPES:
            if infra_type in error_type:
                return TriageOutput(
                    decision="UNFIXABLE",
                    reasoning=f"Rule-based: '{infra_type}' is an infrastructure error "
                              f"that cannot be fixed by patching application code."
                )
        return None

    # ── Layer 2: RAG duplicate detection ─────────────────────────────────────

    def _retrieve_rag_context(self, error_type: str, message: str) -> str:
        """Retrieve formatted past context from ChromaDB."""
        try:
            return self.memory.retrieve_context(error_type, message) or ""
        except Exception:
            return ""

    def _check_duplicate(self, error_type: str, stack_trace: str) -> Optional[TriageOutput]:
        """Return DUPLICATE if ChromaDB has a high-similarity resolved past fix."""
        try:
            query = f"Error: {error_type}\nStack: {stack_trace[:500]}"
            results = self.memory.search_memory(query, top_k=1)
            if not results:
                return None

            top = results[0]
            distance = top.get("distance")
            if distance is None:
                return None

            similarity = 1 - distance
            meta = top.get("metadata", {})
            already_fixed = meta.get("status") in ("pr_created", "done")

            if similarity >= _DUPLICATE_SIMILARITY_THRESHOLD and already_fixed:
                return TriageOutput(
                    decision="DUPLICATE",
                    reasoning=(
                        f"RAG: Found a resolved past fix with similarity {similarity:.2f} "
                        f"(session: {meta.get('session_id', 'unknown')}). Skipping to avoid duplicate PR."
                    )
                )
        except Exception as e:
            print(f"   ⚠️  RAG duplicate check failed ({e}), skipping Layer 2.")
        return None

    # ── Layer 3: LLM advisory ────────────────────────────────────────────────

    def _llm_advisory(self, error: dict, rag_ctx: str) -> TriageOutput:
        """Ask the LLM to categorise the error. Defaults to FIXABLE on failure."""
        rag_section = rag_ctx if rag_ctx else "No similar past errors found in memory."
        prompt = f"""You are a triage advisor for an autonomous C# debugging agent.
Classify the incoming error based on whether the agent CAN fix it by modifying C# code.

**Error Type:** {error.get('error_type', 'Unknown')}
**Error Message:** {error.get('message', 'No message')}
**Stack Trace (first 800 chars):**
{str(error.get('stack_trace', ''))[:800]}

**Past Memory Context:**
{rag_section}

RULES:
- Return UNFIXABLE only for pure infrastructure issues (cloud outage, OOM, network, expired secrets).
- Return DUPLICATE only if memory shows this exact error was RECENTLY fixed successfully.
- Return FIXABLE for anything that can be addressed by changing C# source code.

Provide a one-sentence reasoning.
"""
        try:
            result = self.structured_llm.invoke(prompt)
            return result
        except Exception as e:
            print(f"   ⚠️  LLM advisory failed ({e}). Defaulting to FIXABLE.")
            return TriageOutput(
                decision="FIXABLE",
                reasoning="LLM advisory failed — proceeding to clone as a safe default."
            )

    # ── Layer 3 guard: override LLM if it incorrectly skips a code error ─────

    def _apply_code_error_override(
        self, decision: TriageOutput, error_type: str
    ) -> TriageOutput:
        """
        If the LLM returned UNFIXABLE but the error type is a known code-level
        exception, override the decision to FIXABLE. The LLM cannot silently
        discard genuine bug reports.
        """
        if decision["decision"] == "UNFIXABLE":
            for code_type in _KNOWN_CODE_ERRORS:
                if code_type in error_type:
                    print(
                        f"   ⚙️  Override: LLM said UNFIXABLE, but '{code_type}' "
                        f"is a known code error — forcing FIXABLE."
                    )
                    return TriageOutput(
                        decision="FIXABLE",
                        reasoning=(
                            f"LLM overridden by rule: '{code_type}' is a known "
                            f"code-level error that the agent should attempt to fix."
                        )
                    )
        return decision

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _record_decision(self, state: DebugState, output: TriageOutput, layer: int):
        """Record the triage decision in the graph's decision log."""
        decision: Decision = {
            "agent": f"triage_layer_{layer}",
            "decision_point": "initial_triage",
            "choice": output["decision"],
            "reasoning": output["reasoning"],
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)
        print(f"   ✅ Triage complete: {output['decision']}")
