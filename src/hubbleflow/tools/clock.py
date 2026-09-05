"""What time it is, which the model would otherwise have to guess.

A model's sense of "now" is the end of its training data, so asked for the
latest anything it reaches for the last year it saw and searches that. It is not
being careless: nothing in the conversation has ever told it otherwise.

The date is put in the system prompt for that reason, where it cannot be missed.
This tool is for the cases the prompt cannot cover -- a different timezone, or a
long session that has run past midnight since it started.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool

FORMAT = "%A %d %B %Y, %H:%M %Z (UTC%z)"


def local_now() -> datetime:
    """Now, in whatever timezone this machine is set to."""
    return datetime.now().astimezone()


def stamp(when: datetime | None = None) -> str:
    return (when or local_now()).strftime(FORMAT)


def system_prompt_line() -> str:
    """The sentence appended to the system prompt at session start.

    Says the year out loud rather than only the date, because the failure is
    specifically a model reaching for the wrong year -- and says that its
    training cutoff is not now, since that is the belief being corrected.
    """
    now = local_now()
    return (
        f"\n\nToday is {stamp(now)}. Your training data ends well before this, so "
        f"anything you remember as current may not be. When a question asks for the "
        f"latest, the newest, or what is happening now, it means {now:%B %Y} -- search "
        f"for that rather than the last period you have memory of, and use current_time "
        f"if a turn has run long enough that the date may have moved."
    )


def make_clock_tools() -> list:
    @tool("current_time", parse_docstring=True)
    async def current_time(timezone_name: str = "") -> str:
        """The current date and time.

        Use it before answering anything about what is current, recent or
        upcoming, and before searching for the latest of something -- what you
        remember as the present is the end of your training data, not today.

        Args:
            timezone_name: An IANA name like "Asia/Kolkata" or "UTC". Leave it
                empty for the machine's own timezone.
        """
        if not timezone_name.strip():
            return stamp()
        try:
            zone = ZoneInfo(timezone_name.strip())
        except (ZoneInfoNotFoundError, ValueError):
            return (
                f"No timezone called {timezone_name!r}. Use an IANA name such as "
                f'"Asia/Kolkata", "Europe/London" or "UTC". Locally it is {stamp()}.'
            )
        return datetime.now(timezone.utc).astimezone(zone).strftime(FORMAT)

    return [current_time]
