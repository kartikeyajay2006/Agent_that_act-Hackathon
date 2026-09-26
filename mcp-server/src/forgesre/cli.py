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
import subprocess
import sys
import time

from .config import get_settings
from .deployment import release_metadata
from .ops import VERSIONED, Ops
from .probes import probe, wait_ready
from .results import ToolError
from .synthetic import run_checkout_probes
from .trueforge import (
    AGENT_NAME,
    APPROVAL_GATED_TOOLS,
    MCP_SERVER_NAME,
    READ_ONLY_AGENT_NAME,
    SKILL_NAME,
    TrueForgeError,
    from_env,
    model_params,
)

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


def _skill_can_install(env) -> bool:
    """Git-backed skills are cloned inside the sandbox. TrueForge's Linux local sandbox can only read
    /usr/lib*, /usr/local, /usr/bin…, so git's https helper must live there (Debian yes, Fedora no)."""
    mode = env.get("FORGESRE_ATTACH_SKILL", "auto")
    if mode in ("always", "never"):
        return mode == "always"
    if env.get("DAYTONA_API_KEY"):
        return True
    try:
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    readable = (  # TrueForge local-sandbox read roots: Linux, then macOS (Xcode CLT, Homebrew)
        "/usr/lib/", "/usr/lib64/", "/usr/local/", "/usr/bin", "/bin", "/lib/", "/lib64/",
        "/Library/", "/opt/homebrew/",
    )  # fmt: skip
    return exec_path.startswith(readable)


def cmd_trueforge_setup(ops: Ops, *, read_only: bool = True) -> int:
    tf = from_env()
    env = os.environ
    provider = env.get("MODEL_PROVIDER", "anthropic")
    model_id = env.get("MODEL_ID", "")
    api_key = env.get("MODEL_API_KEY", "")
    token = env.get("FORGESRE_MCP_TOKEN", "")

    mcp_url = env.get("FORGESRE_MCP_URL") or (
        f"http://{env.get('FORGESRE_MCP_HOST', ops.settings.mcp_host)}:"
        f"{env.get('FORGESRE_MCP_PORT', ops.settings.mcp_port)}/mcp"
    )
    try:
        transports = tf.mcp_transport_types()
        if tf._is_loopback_url(mcp_url):
            if "stdio" in transports:
                raise TrueForgeError(
                    "TrueForge advertises stdio MCP, but this integration does not know its required "
                    "stdio manifest shape; refusing to substitute loopback HTTP"
                )
            raise TrueForgeError(
                "installed TrueForge supports MCP transports " + ", ".join(sorted(transports))
                + " but not stdio; refusing loopback HTTP. Set FORGESRE_MCP_URL to a real non-loopback endpoint."
            )
        if "remote" not in transports:
            raise TrueForgeError("installed TrueForge schema does not support remote MCP endpoints")
    except TrueForgeError as exc:
        print(f"ERROR TrueForge MCP transport preflight: {exc}", file=sys.stderr)
        return 2

    missing = [name for name, value in (
        ("MODEL_ID", model_id),
        ("MODEL_API_KEY", api_key if provider != "custom" else "configured-or-optional"),
        ("MODEL_BASE_URL", env.get("MODEL_BASE_URL", "") if provider in ("custom", "truefoundry") else "configured"),
        ("FORGESRE_MCP_TOKEN", token),
    ) if not value]
    if missing:
        print("Missing required configuration variables: " + ", ".join(missing), file=sys.stderr)
        return 2

    fqn = tf.configure_model(
        provider=provider, model_id=model_id, api_key=api_key, base_url=env.get("MODEL_BASE_URL") or None
    )
    print(f"agent model: {fqn}  params {model_params(fqn)}")

    # Optional second provider (e.g. your own OpenAI key) so the model can be switched in the TrueForge UI.
    if env.get("OPENAI_API_KEY") and provider != "openai":
        ids = [m.strip() for m in env.get("OPENAI_MODEL_IDS", "gpt-4.1-mini").split(",") if m.strip()]
        extra = tf.configure_provider(provider="openai", model_ids=ids, api_key=env["OPENAI_API_KEY"], base_url=None)
        print(f"fallback provider: openai {extra} (selectable in the TrueForge model picker)")

    tf.configure_mcp(url=mcp_url, token=token)
    tools = tf.list_mcp_tools()
    print(f"MCP connector '{MCP_SERVER_NAME}' -> {mcp_url}: TrueForge sees {len(tools)} tools")
    gated = [t.get("name") for t in tools if (t.get("annotations") or {}).get("destructiveHint")]
    print(f"  destructive per annotations: {gated}")

    daytona = False
    with_skill = False
    if read_only:
        print("sandbox and skills disabled for the read-only investigation profile")
    else:
        if env.get("DAYTONA_API_KEY"):
            if tf.sandbox_provider() != "daytona":
                print("sandbox provider: configuring Daytona (first time builds a snapshot; can take a few minutes)…")
            try:
                tf.configure_daytona(env["DAYTONA_API_KEY"])
                daytona = True
                print("sandbox provider: Daytona configured")
            except TrueForgeError as exc:
                print(f"WARNING: Daytona not configured — {exc}")
                print("         falling back to TrueForge's local sandbox (fix the key's permissions and re-run)")
        if not daytona:
            print("sandbox provider: TrueForge local sandbox (standalone mode)")

        env_for_skill = dict(env) if daytona else {k: v for k, v in env.items() if k != "DAYTONA_API_KEY"}
        with_skill = _skill_can_install(env_for_skill)
        repo = env.get("FORGESRE_SKILL_REPO", "https://github.com/kartikeyajay2006/Agent_that_act-Hackathon")
        ref = env.get("FORGESRE_SKILL_REF", "main")
    if not read_only and with_skill:
        try:
            tf.configure_skill(repo_url=repo, ref=ref, path="skills/incident-diagnostics")
            print(f"skill '{SKILL_NAME}' registered from {repo} and attached")
        except TrueForgeError as exc:
            with_skill = False
            print(f"WARNING: skill not registered ({exc})")
    if read_only:
        agent_name = READ_ONLY_AGENT_NAME
        description = "Read-only production incident investigator; no probe, sandbox, or remediation tools"
        instructions = (ops.settings.root / "agent" / "forgesre-investigator.system.md").read_text()
    else:
        agent_name = AGENT_NAME
        description = "Autonomous production reliability agent (investigate, diagnose, act, verify, report)"
        if with_skill:
            source = (
                "Load the attached `incident-diagnostics` skill and run its analyzer: "
                "`python <skills dir>/incident-diagnostics/scripts/diagnose.py --window 15` "
                "(skills directory from your "
                "sandbox instructions)."
            )
        else:
            source = (
                "Fetch the reference analyzer through the MCP bridge from inside the sandbox (no internet needed) and "
                "run it: `mcp-client call-tool forgesre get_reference_analyzer '{}' | python3 -c \"import json,sys; "
                "open('diagnose.py','w').write(json.load(sys.stdin)['source'])\" && python diagnose.py --window 15`."
            )
            print("skill not attached (sandbox cannot clone git skills here); analyzer delivered via the MCP bridge")
        instructions = (
            (ops.settings.root / "agent" / "forgesre.system.md").read_text().replace("{{DIAGNOSTICS_SOURCE}}", source)
        )
    manifest = tf.agent_manifest(
        model_fqn=fqn, instructions=instructions, with_skill=with_skill, read_only=read_only
    )
    agent = tf.upsert_agent(manifest, name=agent_name, description=description)
    if read_only:
        enabled_tools = tf.validate_read_only_agent(tf.get_agent(READ_ONLY_AGENT_NAME))
        print(f"read-only agent '{agent_name}' saved (id {agent.get('id')})")
        print(f"enabled tools: {enabled_tools}")
    else:
        print(f"agent '{agent_name}' saved (id {agent.get('id')}); approval required for {APPROVAL_GATED_TOOLS}")
    print(f"open {tf.base_url} -> Agents -> {agent_name} -> Try")
    return 0


def cmd_investigate(prompt: str) -> int:
    from .investigation import InvestigationUnavailable, investigate

    try:
        result = investigate(prompt, on_event=lambda event: print(f"event={event.type}", flush=True))
    except InvestigationUnavailable as exc:
        print(f"ERROR TrueForge: {exc}", file=sys.stderr)
        return 2
    print(f"session_id={result.session_id}")
    print(f"status={result.status}")
    print(f"approval_required={bool(result.approval_events)}")
    if result.output:
        print("\n--- investigation ---\n" + result.output)
    if result.approval_events:
        print("Approval event preserved; no approval was submitted.", file=sys.stderr)
        return 2
    return 0 if result.status == "done" else 1


def cmd_investigator_preflight() -> int:
    tf = from_env()
    try:
        tools = tf.validate_read_only_agent(tf.get_agent(READ_ONLY_AGENT_NAME))
    except TrueForgeError as exc:
        print(f"ERROR TrueForge preflight: {exc}", file=sys.stderr)
        return 2
    print(f"preflight=ok agent={READ_ONLY_AGENT_NAME} observation_tools={len(tools)}")
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
    setup_tf = sub.add_parser("trueforge-setup")
    e = sub.add_parser("eval", help="score the agent on scenarios through TrueForge")
    e.add_argument("--scenario", default="approve,deny,healthy", help="comma list: approve, deny, healthy")
    e.add_argument("--runs", type=int, default=1)
    profile = setup_tf.add_mutually_exclusive_group()
    profile.add_argument("--read-only", dest="read_only", action="store_true", default=True)
    profile.add_argument("--full", dest="read_only", action="store_false")
    inv = sub.add_parser("investigate", help="run a read-only incident investigation through TrueForge")
    inv.add_argument("--prompt", required=True)
    sub.add_parser("investigator-preflight", help="verify saved investigator and observation-only tool list")
    a = sub.add_parser("agent-run")
    a.add_argument("--prompt", required=True)
    g = a.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true", help="approve approval-gated calls (testing)")
    g.add_argument("--deny", action="store_true", help="deny approval-gated calls (testing)")
    args = parser.parse_args(argv)
    if args.cmd == "eval":
        from .evaluate import main as run_eval

        scenarios = [x.strip() for x in args.scenario.split(",") if x.strip()]
        bad = [x for x in scenarios if x not in ("approve", "deny", "healthy")]
        if bad:
            parser.error(f"unknown scenario(s): {bad}")
        return run_eval(get_settings(), scenarios, args.runs)
    if args.cmd == "agent-run":
        from .agent_driver import main as run_agent

        return run_agent(args.prompt, "approve" if args.approve else "deny" if args.deny else "ask")
    if args.cmd == "investigate":
        return cmd_investigate(args.prompt)
    if args.cmd == "investigator-preflight":
        return cmd_investigator_preflight()
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
            return cmd_trueforge_setup(ops, read_only=args.read_only)
        return cmd_check(ops, args.expect, args.timeout)
    except ToolError as exc:
        print(f"ERROR {exc.code}: {exc.message} {exc.details or ''}", file=sys.stderr)
        return 2
    except TrueForgeError as exc:
        print(f"ERROR TrueForge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
