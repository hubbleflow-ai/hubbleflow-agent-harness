"""vLLM, llama.cpp and FreeToken: one shape, three ports.

Each is a different program with its own reason to exist, but the harness only
ever sees a base URL with the OpenAI protocol behind it. These run against a
stub of that protocol, once per server, so adding a fourth costs a table row
rather than a test file.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from hubbleflow import agent, models
from hubbleflow.config import Config
from hubbleflow.permissions import PermissionPolicy

SERVED = ["Qwen3.6-35B-A3B", "GLM-5.2", "bge-m3-embed"]
SERVERS = list(models.LOCAL_SERVERS)
IDS = [s.provider for s in SERVERS]


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
            # vLLM reports max_model_len where FreeToken reports context_length.
            self._json({"object": "list", "data": [
                {"id": m, "object": "model", "max_model_len": 262144} for m in SERVED
            ]})
        else:
            self.send_error(404)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self._json({
            "id": "1", "object": "chat.completion", "created": 0,
            "model": request.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": f"served {request.get('model')}"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        })


@pytest.fixture(params=SERVERS, ids=IDS)
def server(request, monkeypatch):
    """One stub, standing in for whichever server this run is about."""
    spec = request.param
    httpd = HTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv(spec.env, f"http://127.0.0.1:{httpd.server_port}/v1")
    models.forget_local_models()
    yield spec
    httpd.shutdown()
    models.forget_local_models()


def test_models_are_discovered(server):
    found = models.list_server(server)
    assert "Qwen3.6-35B-A3B" in found
    assert "bge-m3-embed" not in found, "embedding models aren't chat models"


def test_the_advertised_context_length_is_kept(server):
    assert dict(models.list_server_details(server))["GLM-5.2"] == 262144


def test_a_bare_name_resolves_to_the_server_that_has_it(server):
    assert models.resolve("GLM-5.2") == f"{server.provider}:GLM-5.2"


def test_an_unserved_name_is_not_claimed(server):
    assert not models.resolve("gemini-3.5-flash").startswith(f"{server.provider}:")


def test_it_appears_in_the_catalogue(server):
    assert dict(models.catalogue())[server.provider]


def test_the_listing_is_its_own_models_not_another_provider_s(server):
    from hubbleflow import commands

    assert [name for name, _ in commands._names_for(server.provider)] == sorted(
        m for m in SERVED if m != "bge-m3-embed"
    )


def test_a_session_needs_no_api_key(server):
    config = Config.load(model=f"{server.provider}:GLM-5.2")
    assert config.provider == server.provider
    assert config.is_local, "your own hardware isn't billed per token"


def test_a_session_compacts_inside_its_window(server, tmp_path):
    from hubbleflow import config as config_module

    config = Config.load(model=f"{server.provider}:GLM-5.2", workspace=tmp_path)
    assert agent._summarize_after(config) < config_module.context_window()


async def test_the_agent_runs_against_it(server, tmp_path):
    config = Config.load(model=f"{server.provider}:GLM-5.2", workspace=tmp_path, auto_approve=True)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        harness = await agent.build(config, checkpointer, PermissionPolicy(auto_approve=True))
        result = await harness.graph.ainvoke(
            {"messages": [HumanMessage(content="hello")]},
            {"configurable": {"thread_id": "t"}, "recursion_limit": 8},
        )
    assert "GLM-5.2" in str(result["messages"][-1].content)


@pytest.mark.parametrize("spec", SERVERS, ids=IDS)
def test_nothing_running_means_no_models_and_no_error(spec, monkeypatch):
    monkeypatch.setenv(spec.env, "http://127.0.0.1:9/v1")
    models.forget_local_models()
    assert models.list_server(spec) == []


def test_each_server_has_its_own_port_and_env_var():
    """Two of them running at once is ordinary; colliding defaults would break it."""
    assert len({s.default_url for s in SERVERS}) == len(SERVERS)
    assert len({s.env for s in SERVERS}) == len(SERVERS)
