"""Loads config/*.yaml and .env into one immutable settings object."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _repo_root() -> Path:
    env = os.environ.get("FORGESRE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[3]


def load_dotenv(path: Path) -> None:
    """Minimal .env reader; real environment variables always win."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    return _interpolate(yaml.safe_load(path.read_text()) or {})


@dataclass(frozen=True)
class Settings:
    root: Path
    services: dict[str, Any]
    policy: dict[str, Any]
    verification: dict[str, Any]
    prometheus_url: str
    database_monitor_dsn: str
    state_dir: Path
    artifacts_dir: Path
    compose_file: Path
    mcp_host: str
    mcp_port: int

    @property
    def environment(self) -> str:
        return self.services.get("environment", "unknown")

    @property
    def service_map(self) -> dict[str, Any]:
        return self.services["services"]

    def action_policy(self, action: str) -> dict[str, Any]:
        return self.policy.get("actions", {}).get(action, {})


REQUIRED_THRESHOLDS = {
    "synthetic_success_ratio_min",
    "checkout_error_rate_max",
    "checkout_latency_p95_seconds_max",
    "db_connection_utilization_max",
    "active_version_ready",
}


def validate(settings: Settings) -> None:
    """Fail at startup, not mid-incident, when a config file is incomplete."""
    rec = settings.verification.get("recovery", {})
    missing = REQUIRED_THRESHOLDS - set(rec.get("thresholds", {}))
    for key in ("settle_seconds", "max_settle_seconds", "metric_window", "synthetic_requests"):
        if key not in rec:
            missing.add(f"recovery.{key}")
    for section in ("synthetic", "evidence", "logs"):
        if section not in settings.verification:
            missing.add(section)
    if missing:
        raise ValueError(f"config/verification.yaml is missing: {sorted(missing)}")
    for action in ("restart_service", "rollback_deployment"):
        if action not in settings.policy.get("actions", {}):
            raise ValueError(f"config/policy.yaml has no policy for {action}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root = _repo_root()
    load_dotenv(root / ".env")
    config_dir = Path(os.environ.get("FORGESRE_CONFIG_DIR", root / "config"))
    services = _load_yaml(config_dir / "services.yaml")
    prom_port = os.environ.get("PROMETHEUS_HOST_PORT", "19090")
    pg_port = os.environ.get("PG_HOST_PORT", "15432")
    monitor_pw = os.environ.get("MONITOR_PASSWORD", "")
    settings = Settings(
        root=root,
        services=services,
        policy=_load_yaml(config_dir / "policy.yaml"),
        verification=_load_yaml(config_dir / "verification.yaml"),
        prometheus_url=os.environ.get("PROMETHEUS_URL", f"http://127.0.0.1:{prom_port}"),
        database_monitor_dsn=os.environ.get(
            "DATABASE_MONITOR_DSN",
            f"postgresql://forgesre_monitor:{monitor_pw}@127.0.0.1:{pg_port}/payments?connect_timeout=3",
        ),
        state_dir=Path(os.environ.get("FORGESRE_STATE_DIR", root / "state")),
        artifacts_dir=Path(os.environ.get("FORGESRE_ARTIFACTS_DIR", root / "artifacts")),
        compose_file=root / "docker-compose.yml",
        mcp_host=os.environ.get("FORGESRE_MCP_HOST", "127.0.0.1"),
        mcp_port=int(os.environ.get("FORGESRE_MCP_PORT", "18900")),
    )
    validate(settings)
    return settings
