"""Operator CLI used by scripts/*.sh. Shares code with the MCP tools.

forgesre init-state                 # write baseline route (v1) if none exists
forgesre deploy payment-service v2  # release pipeline: start, wait ready, switch traffic
forgesre reset                      # back to the known-good baseline
forgesre status                     # JSON snapshot of the environment
forgesre check --expect healthy|incident [--timeout 120]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from .config import get_settings
from .deployment import release_metadata
from .ops import VERSIONED, Ops
from .probes import probe, wait_ready
from .results import ToolError
from .synthetic import run_checkout_probes
from .trueforge import AGENT_NAME, APPROVAL_GATED_TOOLS, MCP_SERVER_NAME, TrueForgeError, from_env

SCRAPE_INTERVAL_S = 2
MIN_BASELINE_S = 45


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_init_state(ops: Ops) -> int:
    if ops.deploy.active_version(VERSIONED):
        print(f"deployment state present: {VERSIONED} -> {ops.deploy.active_version(VERSIONED)}")
        return 0
    return _write_baseline(ops)


def _baseline_version(ops: Ops) -> str:
    return sorted(ops.catalog.versions(VERSIONED))[0]


def _write_baseline(ops: Ops) -> int:
    version = _baseline_version(ops)
    inst = ops.catalog.instance_for_version(VERSIONED, version)
    entry = ops.deploy.record_switch(
        service=VERSIONED,
        to_version=version,
        change_type="deploy",
        actor="release-pipeline",
        metadata=release_metadata(ops.settings, inst.spec.get("source_dir"), ops.docker.state(inst).get("image_id")),
        reset=True,
    )
    print(f"baseline route written: {VERSIONED} -> {version} ({entry['deployment_id']})")
    return 0


def cmd_deploy(ops: Ops, service: str, version: str) -> int:
    target = ops.catalog.instance_for_version(service, version)
    current = ops.deploy.active_version(service)
    if current == version:
        print(f"{service} already at {version}")
        return 0
    print(f"deploying {target.name} ({ops.docker.ensure_running(target)})")
    ready = wait_ready(target, 60)
    if not ready.get("ok"):
        print(f"ERROR: {target.name} never became ready: {ready}", file=sys.stderr)
        return 1
    entry = ops.deploy.record_switch(
        service=service,
        to_version=version,
        change_type="deploy",
        actor="release-pipeline",
        metadata=release_metadata(
            ops.settings, target.spec.get("source_dir"), ops.docker.state(target).get("image_id")
        ),
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        gw = probe(ops.catalog.instance("api-gateway"), "/route")
        if (gw.get("body") or {}).get("active_version") == version:
            print(f"traffic switched: {service} {current} -> {version} ({entry['deployment_id']})")
            return 0
        time.sleep(0.25)
    print("ERROR: gateway did not pick up the new route", file=sys.stderr)
    return 1


def cmd_reset(ops: Ops) -> int:
    baseline = _baseline_version(ops)
    for v in ops.catalog.versions(VERSIONED):
        if v != baseline:
            inst = ops.catalog.instance_for_version(VERSIONED, v)
            if ops.docker.remove(inst):
                print(f"removed {inst.name}")
    base = ops.catalog.instance_for_version(VERSIONED, baseline)
    ops.docker.ensure_running(base)
    _write_baseline(ops)
    ops.audit.clear()
    print("incident state and audit log cleared (previous log archived)")
    return 0


def cmd_status(ops: Ops) -> int:
    snap = ops.snapshot()
    try:
        alerts = ops._alerts()
    except ToolError as exc:
        alerts = [{"error": exc.message}]
    _print({"signals": snap, "alerts": alerts, "incident": ops.audit.incident()})
    return 0


def _check_once(ops: Ops, expect: str) -> tuple[bool, dict]:
    gw = ops.catalog.instance("api-gateway")
    synth = asyncio.run(run_checkout_probes(gw.base_url or "", 10, 5))
    err = ops._scalar("checkout_error_rate", "30s")
    rps = ops._scalar("checkout_request_rate", "30s")
    firing = [a["alert"] for a in ops._alerts() if a["state"] == "firing"]
    samples = ops.prom.scalar('count_over_time(up{job="api-gateway"}[10m])') or 0
    history_s = samples * SCRAPE_INTERVAL_S
    facts = {
        "metrics_history_seconds": history_s,
        "active_version": ops.deploy.active_version(VERSIONED),
        "synthetic_success_ratio": synth["success_ratio"],
        "checkout_error_rate_30s": err,
        "checkout_rps_30s": rps,
        "firing_alerts": firing,
    }
    if expect == "healthy":
        passed = (
            synth["success_ratio"] >= 0.95
            and err is not None
            and err <= 0.05
            and not firing
            and history_s >= MIN_BASELINE_S
        )
    else:
        passed = synth["success_ratio"] <= 0.5 and err is not None and err >= 0.2 and "CheckoutErrorRateHigh" in firing
    return passed, facts


def cmd_check(ops: Ops, expect: str, timeout: int) -> int:
    deadline = time.monotonic() + timeout
    while True:
        try:
            passed, facts = _check_once(ops, expect)
        except ToolError as exc:
            passed, facts = False, {"error": exc.code, "message": exc.message}
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] expect={expect} passed={passed} {json.dumps(facts, default=str)}", flush=True)
        if passed:
            return 0
        if time.monotonic() >= deadline:
            return 1
        time.sleep(5)


def cmd_trueforge_setup(ops: Ops) -> int:
    tf = from_env()
    env = os.environ
    provider = env.get("MODEL_PROVIDER", "anthropic")
    model_id = env.get("MODEL_ID", "")
    api_key = env.get("MODEL_API_KEY", "")
    if not model_id:
        print("ERROR: set MODEL_ID in .env", file=sys.stderr)
        return 2
    if provider not in ("custom",) and not api_key:
        print(f"ERROR: MODEL_API_KEY is empty — add your {provider} API key to .env", file=sys.stderr)
        return 2
    token = env.get("FORGESRE_MCP_TOKEN", "")
    if not token:
        print("ERROR: FORGESRE_MCP_TOKEN missing from .env (run scripts/setup.sh)", file=sys.stderr)
        return 2

    fqn = tf.configure_model(
        provider=provider, model_id=model_id, api_key=api_key, base_url=env.get("MODEL_BASE_URL") or None
    )
    print(f"model provider configured: {fqn}")

    mcp_url = f"http://{ops.settings.mcp_host}:{ops.settings.mcp_port}/mcp"
    tf.configure_mcp(url=mcp_url, token=token)
    tools = tf.list_mcp_tools()
    print(f"MCP connector '{MCP_SERVER_NAME}' -> {mcp_url}: TrueForge sees {len(tools)} tools")
    gated = [t.get("name") for t in tools if (t.get("annotations") or {}).get("destructiveHint")]
    print(f"  destructive per annotations: {gated}")

    if env.get("DAYTONA_API_KEY"):
        tf.configure_daytona(env["DAYTONA_API_KEY"])
        print("sandbox provider: Daytona configured")
    else:
        print("sandbox provider: TrueForge local sandbox (standalone mode)")

    instructions = (ops.settings.root / "agent" / "forgesre.system.md").read_text()
    agent = tf.upsert_agent(tf.agent_manifest(model_fqn=fqn, instructions=instructions))
    print(f"agent '{AGENT_NAME}' saved (id {agent.get('id')}); approval required for {APPROVAL_GATED_TOOLS}")
    print(f"open {tf.base_url} -> Agents -> {AGENT_NAME} -> Try")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forgesre")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-state")
    d = sub.add_parser("deploy")
    d.add_argument("service")
    d.add_argument("version")
    sub.add_parser("reset")
    sub.add_parser("status")
    c = sub.add_parser("check")
    c.add_argument("--expect", choices=["healthy", "incident"], required=True)
    c.add_argument("--timeout", type=int, default=120)
    sub.add_parser("trueforge-setup")
    a = sub.add_parser("agent-run")
    a.add_argument("--prompt", required=True)
    g = a.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true", help="approve approval-gated calls (testing)")
    g.add_argument("--deny", action="store_true", help="deny approval-gated calls (testing)")
    args = parser.parse_args(argv)
    if args.cmd == "agent-run":
        from .agent_driver import main as run_agent

        return run_agent(args.prompt, "approve" if args.approve else "deny" if args.deny else "ask")
    ops = Ops(get_settings())
    try:
        if args.cmd == "init-state":
            return cmd_init_state(ops)
        if args.cmd == "deploy":
            return cmd_deploy(ops, args.service, args.version)
        if args.cmd == "reset":
            return cmd_reset(ops)
        if args.cmd == "status":
            return cmd_status(ops)
        if args.cmd == "trueforge-setup":
            return cmd_trueforge_setup(ops)
        return cmd_check(ops, args.expect, args.timeout)
    except ToolError as exc:
        print(f"ERROR {exc.code}: {exc.message} {exc.details or ''}", file=sys.stderr)
        return 2
    except TrueForgeError as exc:
        print(f"ERROR TrueForge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
