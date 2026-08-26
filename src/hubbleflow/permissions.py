"""Which tool calls need a human to say yes.

Everything that writes or executes pauses by default. Two things narrow that
down so the harness isn't asking permission to run `ls`: a built-in read-only
allowlist, and whatever the user approves with "don't ask again" this session.
"""

from __future__ import annotations

import re
import shlex
from urllib.parse import urlparse
from dataclasses import dataclass, field

# Commands that only look. Anything not on this list prompts.
_READ_ONLY = frozenset(
    {
        "awk", "basename", "cat", "cut", "date", "diff", "dirname", "du", "echo",
        "env", "file", "find", "grep", "head", "hostname", "id", "jq", "less",
        "ls", "man", "md5sum", "printenv", "printf", "ps", "pwd", "readlink",
        "realpath", "rg", "sed", "sort", "stat", "tail", "tree", "type", "uname",
        "uniq", "wc", "whereis", "which", "who", "whoami", "yq",
        # PowerShell equivalents, so a Windows session isn't prompting for `dir`.
        "dir", "gc", "gci", "gcm", "gi", "gl", "gp", "ls-l", "measure-object",
        "select-object", "select-string", "sort-object", "type", "where-object",
        "get-childitem", "get-content", "get-command", "get-date", "get-item",
        "get-location", "get-process", "resolve-path", "test-path", "write-host",
        "write-output",
    }
)

# Subcommands of otherwise-dangerous tools that are safe to run unattended.
_READ_ONLY_SUB = {
    "git": frozenset({"blame", "branch", "config", "describe", "diff", "log", "ls-files",
                      "remote", "shortlog", "show", "status", "tag"}),
    "uv": frozenset({"tree", "pip", "python", "version"}),
    "npm": frozenset({"ls", "list", "view", "outdated"}),
    "docker": frozenset({"ps", "images", "logs", "inspect"}),
    "kubectl": frozenset({"get", "describe", "logs", "explain", "version"}),
}

# Tools whose allowlist entry is narrower than the tool itself.
_SCOPED_TOOLS = frozenset({"bash", "execute", "web_fetch"})

# `sed -i`, `git config --global x y`, and friends look read-only but aren't.
_MUTATING_FLAGS = re.compile(r"(^|\s)(-i\b|--in-place\b|--global\s+\S+\s+\S+)")

# Shell syntax that can smuggle a write past a read-only-looking head command.
_UNSAFE_SHELL = re.compile(r"(>>?|\||;|&&|\|\||\$\(|`|\bxargs\b|\bsudo\b)")


@dataclass(slots=True)
class PermissionPolicy:
    """Session-scoped record of what may run without asking."""

    auto_approve: bool = False
    always_allow_tools: set[str] = field(default_factory=set)
    always_allow_commands: set[str] = field(default_factory=set)

    def needs_review(self, tool_name: str, args: dict) -> bool:
        """True when this call should pause for a human decision."""
        if self.auto_approve or tool_name in self.always_allow_tools:
            return False
        if self.signature_for(tool_name, args) in self.always_allow_commands:
            return False
        if tool_name in {"bash", "execute"}:
            return not is_read_only(str(args.get("command", "")))
        return True

    def allow_always(self, tool_name: str, args: dict) -> None:
        """Record a 'don't ask again' decision for the rest of the session."""
        if tool_name in _SCOPED_TOOLS:
            self.always_allow_commands.add(self.signature_for(tool_name, args))
        else:
            self.always_allow_tools.add(tool_name)

    @staticmethod
    def signature_for(tool_name: str, args: dict) -> str:
        """What an allowlist entry for this call would cover.

        A shell call is scoped by its command, a fetch by its host -- saying yes
        to one page on a domain is a reasonable yes to the rest of it, and is
        how the agent reads documentation without a prompt per link.
        """
        if tool_name in {"bash", "execute"}:
            return PermissionPolicy.signature(str(args.get("command", "")))
        if tool_name == "web_fetch":
            return host_of(str(args.get("url", "")))
        return tool_name

    @staticmethod
    def signature(command: str) -> str:
        """The part of a command an allowlist entry covers: `git commit`, `pytest`.

        A command that pipes, redirects or chains gets its whole text as the
        signature -- allowlisting `rm` off the back of `rm -rf build && uv build`
        would hand over far more than the user agreed to.
        """
        normalised = " ".join(command.split())
        if _UNSAFE_SHELL.search(normalised):
            return normalised
        head = _head_tokens(normalised, limit=2)
        if not head:
            return normalised
        if len(head) > 1 and head[0] in _READ_ONLY_SUB:
            return " ".join(head[:2])
        return head[0]


def is_read_only(command: str) -> bool:
    """Whether a shell command is safe to run without asking."""
    if not command.strip() or _UNSAFE_SHELL.search(command) or _MUTATING_FLAGS.search(command):
        return False

    tokens = _head_tokens(command, limit=2)
    if not tokens:
        return False

    # PowerShell cmdlets are case-insensitive, and so is the allowlist for them.
    head = tokens[0].lower()
    if head in _READ_ONLY_SUB:
        return len(tokens) > 1 and tokens[1] in _READ_ONLY_SUB[head]
    return head in _READ_ONLY


def host_of(url: str) -> str:
    """The hostname a fetch would hit, used as its permission scope."""
    parsed = urlparse(url if "//" in url else f"https://{url}")
    return parsed.netloc or url


def _head_tokens(command: str, *, limit: int) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    # Skip leading VAR=value assignments so `FOO=1 ls` still reads as `ls`.
    while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
        tokens = tokens[1:]
    return tokens[:limit]
