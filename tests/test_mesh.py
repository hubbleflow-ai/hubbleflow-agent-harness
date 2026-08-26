"""Mesh LLM: an OpenAI-compatible node, discovered and driven like any provider."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from hubbleflow import agent, models
from hubbleflow.config import MESH, Config
from hubbleflow.permissions import PermissionPolicy

SERVED = ["GLM-4.7-Flash-Q4_K_M", "Qwen3-30B-A3B-Q5_K_M", "bge-m3-embed"]


class _Node(BaseHTTPRequestHandler):
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
        path = self.path.rstrip("/")
        if path.endswith("/models"):
            self._json({"object": "list", "data": [{"id": m, "object": "model"} for m in SERVED]})
        elif path.endswith("/api/status"):
            # A consumer: standby, publishing nothing, offering neither models nor VRAM.
            self._json({
                "node_status": "standby", "publication_state": "private",
                "serving_models": [], "hosted_models": [], "my_vram_gb": 0, "peers": [],
                "runtime": {"capabilities": {"local_serving": False}},
            })
        else:
            self.send_error(404)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self._json({
            "id": "mesh-1", "object": "chat.completion", "created": 0,
            "model": request.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": f"served {request.get('model')}"}}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 8, "total_tokens": 50},
        })


@pytest.fixture
def node(monkeypatch):
    """A stub mesh node on an ephemeral port.

    The console gets pointed at the stub too. It is derived from the API url by
    swapping the port, so leaving it alone sends `mesh_posture` to whatever real
    node is listening on this machine -- and a developer running `mesh-llm serve`
    would watch these tests fail on the consume-only guard.
    """
    server = HTTPServer(("127.0.0.1", 0), _Node)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("MESH_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("MESH_LLM_CONSOLE_PORT", str(server.server_port))
    models.forget_local_models()
    yield server
    server.shutdown()
    models.forget_local_models()


async def test_models_are_discovered_from_the_node(node):
    found = models.list_mesh()
    assert "GLM-4.7-Flash-Q4_K_M" in found
    assert "bge-m3-embed" not in found, "embedding models aren't chat models"


async def test_a_bare_name_resolves_to_the_mesh_when_the_node_serves_it(node):
    assert models.resolve("GLM-4.7-Flash-Q4_K_M") == f"{MESH}:GLM-4.7-Flash-Q4_K_M"


async def test_an_unserved_name_does_not_get_claimed_by_the_mesh(node):
    assert not models.resolve("gemini-2.5-pro").startswith(f"{MESH}:")


async def test_a_mesh_session_needs_no_api_key(node):
    config = Config.load(model=f"{MESH}:GLM-4.7-Flash-Q4_K_M")
    assert config.provider == MESH
    assert config.is_local, "self-hosted compute isn't billed per token"


async def test_the_agent_runs_against_the_node(node, tmp_path):
    config = Config.load(model=f"{MESH}:GLM-4.7-Flash-Q4_K_M", workspace=tmp_path, auto_approve=True)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        harness = await agent.build(config, checkpointer, PermissionPolicy(auto_approve=True))
        result = await harness.graph.ainvoke(
            {"messages": [HumanMessage(content="hello mesh")]},
            {"configurable": {"thread_id": "mesh"}, "recursion_limit": 8},
        )

    assert "GLM-4.7-Flash-Q4_K_M" in str(result["messages"][-1].content)


# --------------------------------------------------------------------------
# client-only enforcement
# --------------------------------------------------------------------------

def _posture(**overrides):
    from hubbleflow.models import MeshPosture

    return MeshPosture(**{"reachable": True, **overrides})


def test_a_client_only_node_is_not_contributing():
    assert not _posture().contributing
    assert _posture().why() == "client-only"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("serving", ("some-model",)),
        ("hosting", ("some-model",)),
        ("published", True),
        ("local_serving", True),
        ("vram_offered", 18.0),
    ],
)
def test_any_sign_of_giving_compute_counts_as_contributing(field, value):
    assert _posture(**{field: value}).contributing


async def test_a_contributing_node_offers_no_models(node, monkeypatch):
    """The default is consume-only, so a host node is skipped rather than used."""
    monkeypatch.setattr(models, "mesh_posture", lambda: _posture(serving=("shared-model",)))
    assert models.list_mesh() == []


async def test_the_opt_out_lets_a_contributing_node_be_used(node, monkeypatch):
    monkeypatch.setattr(models, "mesh_posture", lambda: _posture(serving=("shared-model",)))
    monkeypatch.setenv("HUBBLEFLOW_MESH_ALLOW_HOST", "1")
    assert "GLM-4.7-Flash-Q4_K_M" in models.list_mesh()


async def test_an_unreachable_console_does_not_block_the_mesh(node, monkeypatch):
    """No console answer shouldn't be read as 'contributing' and lock you out."""
    monkeypatch.setattr(models, "mesh_posture", lambda: models.MeshPosture())
    assert "GLM-4.7-Flash-Q4_K_M" in models.list_mesh()
