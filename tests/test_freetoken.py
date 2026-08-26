"""FreeToken: an edge MoE server, discovered and driven like any OpenAI API."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from hubbleflow import agent, models
from hubbleflow.config import FREETOKEN, Config
from hubbleflow.permissions import PermissionPolicy

SERVED = ["Qwen3.6-35B-A3B", "GLM-5.2", "DeepSeek-V4-Flash", "bge-m3-embed"]


class _Server(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list", "data": [
                {"id": m, "object": "model", "context_length": 262144} for m in SERVED
            ]})
        else:
            self.send_error(404)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self._json({
            "id": "ft-1", "object": "chat.completion", "created": 0,
            "model": request.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": f"served {request.get('model')}"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        })


@pytest.fixture
def server(monkeypatch):
    """A stub FreeToken server on an ephemeral port."""
    httpd = HTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("FREETOKEN_URL", f"http://127.0.0.1:{httpd.server_port}/v1")
    models.forget_local_models()
    yield httpd
    httpd.shutdown()
    models.forget_local_models()


def test_models_are_discovered_from_the_server(server):
    found = models.list_freetoken()
    assert "Qwen3.6-35B-A3B" in found
    assert "bge-m3-embed" not in found, "embedding models aren't chat models"


def test_the_advertised_context_length_is_kept(server):
    assert dict(models.list_freetoken_details())["GLM-5.2"] == 262144


def test_a_bare_name_resolves_to_freetoken_when_it_is_served(server):
    assert models.resolve("GLM-5.2") == f"{FREETOKEN}:GLM-5.2"


def test_an_unserved_name_is_not_claimed(server):
    assert not models.resolve("gemini-3.5-flash").startswith(f"{FREETOKEN}:")


def test_nothing_running_means_no_models_and_no_error(monkeypatch):
    monkeypatch.setenv("FREETOKEN_URL", "http://127.0.0.1:9/v1")
    models.forget_local_models()
    assert models.list_freetoken() == []


def test_a_freetoken_session_needs_no_api_key(server):
    config = Config.load(model=f"{FREETOKEN}:GLM-5.2")
    assert config.provider == FREETOKEN
    assert config.is_local, "your own GPU isn't billed per token"


def test_it_appears_in_the_catalogue(server):
    assert dict(models.catalogue())[FREETOKEN]


async def test_the_agent_runs_against_the_server(server, tmp_path):
    config = Config.load(model=f"{FREETOKEN}:GLM-5.2", workspace=tmp_path, auto_approve=True)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        harness = await agent.build(config, checkpointer, PermissionPolicy(auto_approve=True))
        result = await harness.graph.ainvoke(
            {"messages": [HumanMessage(content="hello freetoken")]},
            {"configurable": {"thread_id": "ft"}, "recursion_limit": 8},
        )
    assert "GLM-5.2" in str(result["messages"][-1].content)


def test_a_local_session_compacts_inside_its_window(server, tmp_path):
    """FreeToken is local, so it inherits the small-window compaction rule."""
    from hubbleflow import config as config_module

    config = Config.load(model=f"{FREETOKEN}:GLM-5.2", workspace=tmp_path)
    assert agent._summarize_after(config) < config_module.context_window()
