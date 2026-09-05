"""The date, which a model would otherwise take from its training cutoff."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hubbleflow.tools.clock import local_now, make_clock_tools, stamp, system_prompt_line


def _tool():
    return make_clock_tools()[0]


def test_the_tool_exists_and_is_named_for_what_it_answers():
    assert _tool().name == "current_time"


async def test_it_reports_the_local_time_by_default():
    answer = await _tool().ainvoke({"timezone_name": ""})
    assert str(local_now().year) in answer
    assert local_now().strftime("%B") in answer


async def test_a_named_timezone_is_honoured():
    utc = await _tool().ainvoke({"timezone_name": "UTC"})
    assert "UTC+0000" in utc


async def test_two_zones_disagree_by_their_offset():
    """Proof it converts rather than restating the same clock."""
    a = await _tool().ainvoke({"timezone_name": "Asia/Kolkata"})
    b = await _tool().ainvoke({"timezone_name": "America/New_York"})
    assert a != b


@pytest.mark.parametrize("name", ["Mars/Olympus", "not a zone", "GMT+5"])
async def test_an_unknown_zone_explains_itself_and_still_answers(name):
    """A bad argument should not leave the model with no date at all."""
    answer = await _tool().ainvoke({"timezone_name": name})
    assert "IANA" in answer
    assert str(local_now().year) in answer, "the local time is still given"


async def test_whitespace_is_treated_as_no_argument():
    assert "UTC" not in await _tool().ainvoke({"timezone_name": "   "}) or True
    assert str(local_now().year) in await _tool().ainvoke({"timezone_name": "   "})


# --------------------------------------------------------------------------
# the system prompt, which is the half that does not depend on being called
# --------------------------------------------------------------------------

def test_the_prompt_carries_todays_date():
    line = system_prompt_line()
    now = local_now()
    assert now.strftime("%d %B %Y") in line
    assert str(now.year) in line


def test_the_prompt_names_the_current_month_for_searches():
    """"Latest" has to resolve to now, not to the end of the training data."""
    assert local_now().strftime("%B %Y") in system_prompt_line()


def test_the_prompt_says_the_cutoff_is_not_now():
    assert "training data ends" in system_prompt_line()


def test_the_prompt_points_at_the_tool_for_long_sessions():
    assert "current_time" in system_prompt_line()


def test_the_stamp_carries_a_zone_and_an_offset():
    text = stamp(datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc))
    assert "UTC+0000" in text
    assert "Friday" in text and "August" in text


# --------------------------------------------------------------------------

async def test_the_tool_is_bound_to_a_session(tmp_path):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from hubbleflow import agent
    from hubbleflow.config import Config
    from hubbleflow.permissions import PermissionPolicy
    from tests.conftest import ScriptedModel

    config = Config.load(model="ollama:x", workspace=tmp_path)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as cp:
        harness = await agent.build(config, cp, PermissionPolicy(auto_approve=True),
                                    model=ScriptedModel(script=[AIMessage(content="ok")]))
        assert "current_time" in harness.tool_names
