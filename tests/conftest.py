"""A scripted chat model, so the whole harness can be exercised without a key."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr


class ScriptedModel(BaseChatModel):
    """Replays a fixed list of AI messages, one per call."""

    script: list[AIMessage]
    _calls: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ):
        message = self._next()
        chunk = AIMessageChunk(
            content=message.content,
            tool_call_chunks=[
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls or [])
            ],
            id=message.id,
            usage_metadata=message.usage_metadata,
        )
        if run_manager and isinstance(message.content, str):
            run_manager.on_llm_new_token(message.content, chunk=ChatGenerationChunk(message=chunk))
        yield ChatGenerationChunk(message=chunk)

    def _next(self) -> AIMessage:
        index = min(self._calls, len(self.script) - 1)
        self._calls += 1
        return self.script[index].model_copy()


@pytest.fixture(autouse=True)
def headless_terminal():
    """Run every test without a real terminal attached.

    prompt_toolkit reaches for the console it expects to be running in, and a
    CI runner doesn't have one -- on Windows that is a hard error rather than a
    degraded output. Binding a pipe and a dummy output makes the suite headless
    everywhere, which is what it already claimed to be.
    """
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        yield


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "hello.txt").write_text("hello from the workspace\n")
    return tmp_path
