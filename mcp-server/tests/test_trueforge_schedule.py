from types import SimpleNamespace

import pytest

from forgesre import cli
from forgesre.trueforge import READ_ONLY_AGENT_NAME, READ_ONLY_TOOLS, TrueForge, TrueForgeError


def _investigator():
    return {
        "name": READ_ONLY_AGENT_NAME,
        "manifest": {
            "mcp_servers": [{"name": "forgesre", "enable_tools": READ_ONLY_TOOLS}],
            "config": {"sandbox": {"enabled": False}},
            "skills": [],
        },
    }


class ScheduleAPI:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.created = []
        self.updated = []

    def list(self):
        return iter(self.existing)

    def create(self, *, agent_name, name, manifest):
        self.created.append((agent_name, name, manifest))
        return SimpleNamespace(data=SimpleNamespace(id="schedule-new", name=name, agent_name=agent_name))

    def update(self, *, schedule_id, name, manifest):
        self.updated.append((schedule_id, name, manifest))
        return SimpleNamespace(data=SimpleNamespace(id=schedule_id, name=name, agent_name=READ_ONLY_AGENT_NAME))


def _sdk_factory(schedules, calls):
    def factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(schedules=schedules)

    return factory


def _upsert(client, sdk_factory, **kwargs):
    return client.upsert_read_only_schedule(
        name="local-oncall",
        cron="0 * * * *",
        timezone="Etc/UTC",
        task="Inspect current signals and report findings only.",
        sdk_factory=sdk_factory,
        **kwargs,
    )


def test_schedule_is_created_paused_for_validated_read_only_agent(monkeypatch):
    client = TrueForge("http://tf.test")
    client.get_agent = lambda name: _investigator()
    schedules, constructor_calls = ScheduleAPI(), []

    result = _upsert(client, _sdk_factory(schedules, constructor_calls))

    assert result == {
        "operation": "created",
        "id": "schedule-new",
        "name": "local-oncall",
        "agent_name": READ_ONLY_AGENT_NAME,
        "status": "paused",
    }
    assert schedules.created[0][0] == READ_ONLY_AGENT_NAME
    assert schedules.created[0][2].status.value == "paused"
    assert constructor_calls[0]["base_url"] == "http://tf.test"
    assert constructor_calls[0]["timeout"] >= 600


def test_activation_requires_explicit_flag_and_updates_matching_schedule():
    client = TrueForge("http://tf.test")
    client.get_agent = lambda name: _investigator()
    existing = SimpleNamespace(id="schedule-existing", name="local-oncall", agent_name=READ_ONLY_AGENT_NAME)
    schedules, constructor_calls = ScheduleAPI([existing]), []

    result = _upsert(client, _sdk_factory(schedules, constructor_calls), activate=True)

    assert result["operation"] == "updated"
    assert result["status"] == "active"
    assert schedules.updated[0][0] == "schedule-existing"
    assert schedules.updated[0][2].status.value == "active"
    assert not schedules.created


def test_unsafe_or_conflicting_targets_are_rejected_before_schedule_write():
    client = TrueForge("http://tf.test")
    unsafe_agent = _investigator()
    unsafe_agent["manifest"]["mcp_servers"][0]["enable_tools"] = ["restart_service"]
    client.get_agent = lambda name: unsafe_agent
    schedules, calls = ScheduleAPI(), []

    with pytest.raises(TrueForgeError, match="allowlist mismatch"):
        _upsert(client, _sdk_factory(schedules, calls))
    assert not calls
    assert not schedules.created

    client.get_agent = lambda name: _investigator()
    collision = SimpleNamespace(id="other", name="local-oncall", agent_name="action-agent")
    schedules = ScheduleAPI([collision])
    with pytest.raises(TrueForgeError, match="different agent"):
        _upsert(client, _sdk_factory(schedules, []))
    assert not schedules.updated


@pytest.mark.parametrize("cron", ["", "@hourly", "0 * * * * *"])
def test_invalid_cron_is_rejected_before_sdk_call(cron):
    client = TrueForge("http://tf.test")
    client.get_agent = lambda name: pytest.fail("invalid schedule should be rejected before connecting")
    with pytest.raises(TrueForgeError, match="cron"):
        client.upsert_read_only_schedule(
            name="local-oncall",
            cron=cron,
            timezone="Etc/UTC",
            task="Observe only",
            sdk_factory=lambda **kwargs: pytest.fail("invalid schedule should be rejected before connecting"),
        )


def test_cli_reports_names_of_missing_schedule_settings_without_connecting(monkeypatch, capsys):
    for name in (
        "TRUEFORGE_SCHEDULE_NAME",
        "TRUEFORGE_SCHEDULE_CRON",
        "TRUEFORGE_SCHEDULE_TIMEZONE",
        "TRUEFORGE_SCHEDULE_TASK",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "from_env", lambda: pytest.fail("incomplete config must not connect"))

    assert cli.cmd_trueforge_schedule_setup() == 2
    output = capsys.readouterr()
    assert "TRUEFORGE_SCHEDULE_NAME" in output.err
    assert "TRUEFORGE_SCHEDULE_TASK" in output.err
