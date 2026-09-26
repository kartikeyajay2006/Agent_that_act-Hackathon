from __future__ import annotations

from types import SimpleNamespace

import pytest
from trueforge_sdk import TurnStateError

from forgesre.investigation import InvestigationUnavailable, investigate
from forgesre.trueforge import READ_ONLY_AGENT_NAME, READ_ONLY_TOOLS, TrueForge, TrueForgeError


def test_read_only_manifest_has_only_observation_tools_and_no_sandbox():
    manifest = TrueForge("http://trueforge.invalid").agent_manifest(
        model_fqn="provider/model", instructions="read only", read_only=True, with_skill=True
    )
    connector = manifest["mcp_servers"][0]

    assert connector["enable_tools"] == READ_ONLY_TOOLS
    assert "@all" not in connector["enable_tools"]
    assert connector["require_approval_for_tools"] == []
    assert manifest["skills"] == []
    assert manifest["config"]["sandbox"]["enabled"] is False
    assert manifest["config"]["dynamic_sub_agents"]["enabled"] is False


def test_read_only_profile_is_saved_under_a_separate_agent_name():
    client = TrueForge("http://trueforge.invalid")
    calls = []

    def request(method, path, body=None, params=None):
        calls.append((method, path, body))
        if method == "GET" and path == "/api/v1/agents":
            return {"data": []}
        return {"data": {"id": "agent-1"}}

    client._req = request
    saved = client.upsert_agent(
        {"mcp_servers": [{"enable_tools": READ_ONLY_TOOLS}]},
        name=READ_ONLY_AGENT_NAME,
        description="Read-only investigator",
    )

    assert saved["id"] == "agent-1"
    assert calls[1][0:2] == ("POST", "/api/v1/agents")
    assert calls[1][2]["name"] == READ_ONLY_AGENT_NAME


def test_investigation_creates_saved_agent_streams_and_preserves_approval():
    events = [
        SimpleNamespace(type="turn.created"),
        SimpleNamespace(type="tool.approval_required", tool_calls=[{"id": "call-1"}]),
        SimpleNamespace(
            type="turn.done",
            state=SimpleNamespace(status="paused", output=None),
        ),
    ]

    class Sessions:
        def __init__(self):
            self.created_agent = None
            self.inputs = []

        def create(self, *, agent):
            self.created_agent = agent
            return SimpleNamespace(data=SimpleNamespace(id="session-1"))

        def create_turn_stream(self, *, session_id, input):
            self.inputs.append(input)
            return iter(events)

    sessions = Sessions()

    class FakeClient:
        def __init__(self, *, base_url, timeout):
            assert base_url == "http://tf.test"
            assert timeout == 900
            self.sessions = sessions

    class AdminClient:
        def __init__(self, base_url):
            assert base_url == "http://tf.test"

        def get_agent(self, name):
            return {
                "name": name,
                "manifest": {
                    "mcp_servers": [{"name": "forgesre", "enable_tools": READ_ONLY_TOOLS}],
                    "config": {"sandbox": {"enabled": False}},
                    "skills": [],
                },
            }

    streamed = []
    result = investigate(
        "Checkout latency is elevated", base_url="http://tf.test", client_factory=FakeClient,
        admin_factory=AdminClient, on_event=streamed.append
    )

    assert sessions.created_agent == {"name": READ_ONLY_AGENT_NAME}
    assert "read-only" in sessions.inputs[0][0]["content"].lower()
    assert result.session_id == "session-1"
    assert result.status == "paused"
    assert result.approval_events == [events[1]]
    assert streamed == events
    assert len(sessions.inputs) == 1
    assert all(item["type"] != "user.tool_approval" for item in sessions.inputs[0])


def test_unavailable_server_or_saved_agent_is_reported():
    class FailingClient:
        def __init__(self, **kwargs):
            self.sessions = self

        def create(self, **kwargs):
            raise ConnectionError("connection refused")

    with pytest.raises(InvestigationUnavailable, match=READ_ONLY_AGENT_NAME):
        investigate(
            "Investigate checkout failures", client_factory=FailingClient,
            admin_factory=lambda base_url: SimpleNamespace(
                get_agent=lambda name: {
                    "name": name,
                    "manifest": {
                        "mcp_servers": [{"name": "forgesre", "enable_tools": READ_ONLY_TOOLS}],
                        "config": {"sandbox": {"enabled": False}},
                        "skills": [],
                    },
                }
            ),
        )


def test_turn_state_error_reports_trueforge_message_instead_of_accessing_output():
    server_message = "Model provider returned an upstream error"
    events = [
        SimpleNamespace(
            type="turn.done",
            state=TurnStateError(completed_at="2026-09-26T00:00:00Z", message=server_message),
        )
    ]

    class Sessions:
        def create(self, *, agent):
            return SimpleNamespace(data=SimpleNamespace(id="session-error"))

        def create_turn_stream(self, *, session_id, input):
            return iter(events)

    class FakeClient:
        def __init__(self, *, base_url, timeout):
            self.sessions = Sessions()

    class AdminClient:
        def __init__(self, base_url):
            pass

        def get_agent(self, name):
            return {
                "name": name,
                "manifest": {
                    "mcp_servers": [{"name": "forgesre", "enable_tools": READ_ONLY_TOOLS}],
                    "config": {"sandbox": {"enabled": False}},
                    "skills": [],
                },
            }

    streamed = []
    with pytest.raises(InvestigationUnavailable, match=server_message) as exc_info:
        investigate(
            "Investigate checkout failures",
            base_url="http://tf.test",
            client_factory=FakeClient,
            admin_factory=AdminClient,
            on_event=streamed.append,
        )

    assert "'TurnStateError' object has no attribute 'output'" not in str(exc_info.value)
    assert streamed == events


@pytest.mark.parametrize(
    "agent",
    [
        None,
        {"name": "forgesre", "manifest": {}},
        {"name": READ_ONLY_AGENT_NAME, "manifest": {"mcp_servers": [{"name": "forgesre", "enable_tools": ["@all"]}]}},
        {
            "name": READ_ONLY_AGENT_NAME,
            "manifest": {
                "mcp_servers": [{"name": "forgesre", "enable_tools": READ_ONLY_TOOLS + ["restart_service"]}],
                "config": {"sandbox": {"enabled": False}}, "skills": [],
            },
        },
        {
            "name": READ_ONLY_AGENT_NAME,
            "manifest": {
                "mcp_servers": [{"name": "forgesre", "enable_tools": READ_ONLY_TOOLS}],
                "config": {"sandbox": {"enabled": True}}, "skills": [],
            },
        },
    ],
)
def test_preflight_rejects_missing_or_unsafe_saved_profiles(agent):
    with pytest.raises(TrueForgeError):
        TrueForge.validate_read_only_agent(agent)


def test_preflight_accepts_exact_saved_observation_allowlist():
    agent = {
        "name": READ_ONLY_AGENT_NAME,
        "manifest": {
            "mcp_servers": [{"name": "forgesre", "enable_tools": READ_ONLY_TOOLS}],
            "config": {"sandbox": {"enabled": False}}, "skills": [],
        },
    }
    assert TrueForge.validate_read_only_agent(agent) == READ_ONLY_TOOLS


def test_blank_request_is_rejected_before_connecting():
    with pytest.raises(ValueError, match="must not be empty"):
        investigate("   ", client_factory=lambda **kwargs: pytest.fail("network should not be used"))
