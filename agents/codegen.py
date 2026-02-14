"""Code generation agent - generates C# fixes using Azure OpenAI."""
import os
from openai import AzureOpenAI
from state import DebugState, FixAttempt, Decision
from config import Config
from datetime import datetime
import re


class CodeGenAgent:
    """Generates C# code fixes using Azure OpenAI."""

    def __init__(self):
        """Initialize code generation agent."""
        self.client = AzureOpenAI(
            api_key=Config.AZURE_OPENAI_API_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT
        )
        self.deployment = Config.AZURE_OPENAI_DEPLOYMENT

    def generate_fix(self, state: DebugState) -> DebugState:
        """Generate a fix for the error."""
        print("\n🛠️  CodeGen Agent: Generating fix...")

        error_event = state["error_event"]
        code_context = state["code_context"]
        strategy = state["fix_strategy"]
        attempt = state["current_attempt"]

        # Read the full file content
        full_file_path = os.path.join(state["repo_path"], code_context["file_path"])
        try:
            with open(full_file_path, 'r') as f:
                full_file_content = f.read()
        except FileNotFoundError:
            full_file_content = code_context["code_snippet"]

        # Build prompt
        prompt = self._build_prompt(error_event, code_context, strategy, attempt, full_file_content)

        # Call Azure OpenAI
        print(f"   Calling Azure OpenAI {self.deployment} (attempt {attempt})...")

        response = self.client.chat.completions.create(
            model=self.deployment,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert C# developer specializing in fixing bugs in Azure Functions. You return the COMPLETE fixed file with all using statements, namespace, class definition, and methods intact. Never return partial code."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_completion_tokens=4000
        )

        response_text = response.choices[0].message.content

        # Extract fixed code
        fixed_code = self._extract_code(response_text)

        # Store fix attempt
        fix_attempt: FixAttempt = {
            "attempt_number": attempt,
            "strategy": strategy,
            "fixed_code": fixed_code,
            "reasoning": response_text
        }
        state["fix_attempts"].append(fix_attempt)
        state["status"] = "generating"

        # Log decision
        decision: Decision = {
            "agent": "codegen",
            "decision_point": "fix_generated",
            "choice": strategy,
            "reasoning": f"Generated fix using {strategy} strategy with {self.deployment}",
            "timestamp": datetime.now()
        }
        state["decisions"].append(decision)

        print(f"   ✅ Fix generated successfully")

        return state

    def _build_prompt(self, error_event, code_context, strategy, attempt, full_file_content) -> str:
        """Build prompt for Azure OpenAI."""

        prompt = f"""You are a C# debugging expert. Fix this error in an Azure Function.

**Error Information:**
- Type: {error_event['error_type']}
- Message: {error_event['message']}
- Location: {code_context['file_path']}:{code_context['line_number']}

**Error context (line {code_context['line_number']} marked with >>>):**
```
{code_context['code_snippet']}
```

**COMPLETE current file:**
```csharp
{full_file_content}
```

**Fix Strategy:** {strategy}
**Attempt:** {attempt}/3

**Instructions:**
1. Fix ONLY the specific error at line {code_context['line_number']}
2. Keep the fix minimal - do NOT change anything else
3. Preserve ALL using statements, namespace, class definition, constructors, and other methods
4. Return the COMPLETE file with the fix applied
5. Do NOT add new using statements unless absolutely required for the fix
6. Do NOT rename or restructure anything

Output the COMPLETE fixed file inside ```csharp code blocks. The output must be a valid, compilable C# file.
"""
        return prompt

    def _extract_code(self, response_text: str) -> str:
        """Extract C# code from response."""
        pattern = r'```csharp\n(.*?)\n```'
        matches = re.findall(pattern, response_text, re.DOTALL)

        if matches:
            # Return the longest match (most likely the full file)
            return max(matches, key=len).strip()

        return response_text.strip()
