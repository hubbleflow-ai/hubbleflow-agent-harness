"""The one core tool DeepAgents doesn't ship: a real shell.

Filesystem reads/writes/greps come from `FilesystemMiddleware`; this covers
everything else a coding agent needs -- running tests, git, build tools.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from langchain_core.tools import tool

WINDOWS = sys.platform == "win32"

MAX_OUTPUT_CHARS = 30_000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600

# Emitted after every command so the tool can carry the working directory
# across calls the way an interactive shell would. The two shells spell the
# same idea differently, and `create_subprocess_shell` picks cmd.exe on Windows,
# so PowerShell is invoked explicitly rather than assumed.
_CWD_MARKER = "__HUBBLEFLOW_CWD__"

_POSIX_EPILOGUE = f"\nprintf '\\n{_CWD_MARKER}%s' \"$PWD\""
_WINDOWS_EPILOGUE = f"\nWrite-Output \"`n{_CWD_MARKER}$($PWD.Path)\""

# Keeping colour and pagers out of tool output matters on both platforms.
_QUIET_ENV = {"TERM": "dumb", "NO_COLOR": "1", "GIT_PAGER": "cat", "PAGER": "cat"}


class ShellSession:
    """Runs commands in the workspace, remembering `cd` between calls."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.cwd = workspace

    async def run(self, command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        timeout = max(1, min(int(timeout), MAX_TIMEOUT))
        proc = await self._spawn(command)
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Command timed out after {timeout}s and was killed:\n$ {command}"

        output = raw.decode("utf-8", errors="replace")
        output = self._absorb_cwd(output)
        return _format(command, output, proc.returncode or 0)

    async def _spawn(self, command: str):
        """Start the command in the platform's shell, asking it for its cwd."""
        common = {
            "cwd": str(self.cwd),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
            "env": {**os.environ, **_QUIET_ENV},
        }
        argv = windows_argv(command) if WINDOWS else None
        if argv is not None:
            return await asyncio.create_subprocess_exec(*argv, **common)
        return await asyncio.create_subprocess_shell(posix_command(command), **common)

    def _absorb_cwd(self, output: str) -> str:
        head, marker, tail = output.rpartition(_CWD_MARKER)
        if not marker:
            return output
        candidate = Path(tail.strip())
        # Only follow the shell out of the workspace if it actually still exists.
        if candidate.is_dir():
            self.cwd = candidate
        return head.rstrip("\n")


def posix_command(command: str) -> str:
    """The command as handed to `sh`, with the cwd probe appended."""
    return command + _POSIX_EPILOGUE


def windows_argv(command: str) -> list[str]:
    """The argv used on Windows.

    `create_subprocess_shell` would pick cmd.exe, which can't do the things an
    agent writes; PowerShell is invoked explicitly instead.
    """
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command + _WINDOWS_EPILOGUE]


def _format(command: str, output: str, code: int) -> str:
    body = output.strip() or "(no output)"
    if len(body) > MAX_OUTPUT_CHARS:
        cut = len(body) - MAX_OUTPUT_CHARS
        body = f"{body[:MAX_OUTPUT_CHARS]}\n... [{cut} characters truncated]"
    if code != 0:
        return f"exit code {code}\n{body}"
    return body


def make_shell_tool(session: ShellSession):
    """Build the `bash` tool bound to a workspace-scoped shell session."""

    @tool("bash", parse_docstring=True)
    async def bash(command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        """Run a shell command and return its combined stdout and stderr.

        Runs in the system shell -- POSIX sh on macOS and Linux, PowerShell on
        Windows -- so write commands for whichever platform you are on. The
        working directory persists between calls, so `cd` sticks. Prefer the
        dedicated file tools (read_file, write_file, edit_file, grep, glob) for
        reading and editing files -- use this for running tests, git, package
        managers, and other real work.

        Args:
            command: The shell command to run.
            timeout: Seconds to wait before killing the command (max 600).
        """
        return await session.run(command, timeout)

    return bash
