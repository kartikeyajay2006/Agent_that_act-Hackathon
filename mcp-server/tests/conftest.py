"""Test fixtures: an isolated Settings/Ops pointing at temp state + artifacts dirs."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from forgesre.config import get_settings  # noqa: E402
from forgesre.ops import Ops  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    base = get_settings()
    return dataclasses.replace(base, state_dir=tmp_path / "state", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def ops(settings):
    o = Ops(settings)
    o._alerts = lambda: []  # no Prometheus in unit tests unless a test overrides it
    return o


@pytest.fixture
def v2_active(ops):
    for v in ("v1", "v2"):
        ops.deploy.record_switch(
            service="payment-service", to_version=v, change_type="deploy", actor="test", metadata={}
        )
    return ops
