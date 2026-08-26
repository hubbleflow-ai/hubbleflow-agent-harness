"""A Gemini harness profile.

DeepAgents ships built-in `HarnessProfile`s for Anthropic's frontier models and
OpenAI Codex, but none for Gemini -- so the loop runs unturned against Google's
models. This registers one at the `google_genai` provider key, where it applies
to every Gemini spec the harness might be pointed at.

Each section targets a behaviour Gemini shows in an agentic coding loop that the
Anthropic-tuned defaults don't correct for.
"""

from __future__ import annotations

from deepagents import HarnessProfile, register_harness_profile

_PROVIDER_KEYS = ("google_genai", "google_vertexai")

_SYSTEM_PROMPT_SUFFIX = """\
<parallel_tool_calls>
When several tool calls are independent, issue them together in a single turn rather than one at a time. Reading four files is four calls in one turn, not four turns. Only serialise calls when a later call genuinely needs an earlier call's result as a parameter. Never invent a parameter value to make a call parallelisable.
</parallel_tool_calls>

<ground_every_claim>
Open the file before you describe it. Do not answer questions about this codebase from memory, from the file name, or from what a project of this shape usually contains -- read the actual code first. If you have not run the test, do not say it passes. If you have not read the function, do not say what it returns. Saying "let me check" and then checking is always better than a confident guess.
</ground_every_claim>

<edit_dont_reprint>
To change a file, call edit_file with the smallest string that identifies the change, or write_file when creating something new. Do not print a whole revised file into your reply and ask the user to paste it, and do not reprint a file you just edited to show what changed. Read a file before you edit it.
</edit_dont_reprint>

<terminal_register>
Your output renders in a terminal. Answer in prose at the length the question deserves, and skip the scaffolding: no "Great question!", no restating the request before doing it, no summary of what you are about to do, no recap of what you just did when the tool output already showed it. Do not narrate tool calls in prose -- the harness already displays them. When a task is done, say what changed and stop.
</terminal_register>

<finish_the_job>
Work through the whole request before reporting back. If a task has several parts, use the todo tool to track them and keep going until each one is genuinely done -- not until the first part works. When you hit something you cannot do, finish everything else and say plainly which part you left and why.
</finish_the_job>"""

_TOOL_DESCRIPTION_OVERRIDES = {
    "write_file": (
        "Write content to a file, creating it or replacing it entirely. Use this "
        "for new files. To change part of an existing file use edit_file instead, "
        "and read the file before you touch it."
    ),
    "edit_file": (
        "Replace an exact string in a file. Read the file first so the string you "
        "match is the string that is actually there, including its indentation. "
        "Choose the shortest snippet that appears exactly once; the edit fails if "
        "it matches zero times or more than once."
    ),
}


def register_gemini_profile() -> None:
    """Register the Gemini harness profile. Safe to call more than once."""
    profile = HarnessProfile(
        system_prompt_suffix=_SYSTEM_PROMPT_SUFFIX,
        tool_description_overrides=_TOOL_DESCRIPTION_OVERRIDES,
    )
    for key in _PROVIDER_KEYS:
        register_harness_profile(key, profile)
