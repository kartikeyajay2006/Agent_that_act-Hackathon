"""Implementation of every ForgeSRE tool as plain, testable methods.

server.py only adapts these to MCP. All inputs are validated here, all
mutations are checked against config/policy.yaml here, and every action and
verification is written to the audit log here.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from . import logs as logmod
from .audit import AuditLog, now_iso
from .catalog import Catalog, Instance, validate_name, validate_version
from .config import Settings
from .database import database_health
from .deployment import DeploymentStore, release_metadata
from .dockerctl import DockerControl
from .probes import probe, wait_ready
from .prom import SIGNALS, Prometheus, render, summarize_series
from .results import ToolError, ok, partial
from .synthetic import run_checkout_probes
from .trueforge import TrueForgeError, from_env

VERSIONED = "payment-service"


def r4(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


def clamp(value: int | float, lo: int | float, hi: int | float) -> int | float:
    return max(lo, min(hi, value))


def _criterion(name: str, observed: Any, op: str, threshold: Any) -> dict[str, Any]:
    if observed is None:
        passed = False  # missing data never counts as healthy
    elif op == "<=":
        passed = observed <= threshold
    elif op == ">=":
        passed = observed >= threshold
    else:
        passed = observed == threshold
    return {"criterion": name, "observed": observed, "operator": op, "threshold": threshold, "passed": passed}


def evaluate_recovery(
    *, ready: bool, synthetic_ratio: float | None, signals: dict[str, Any], thresholds: dict[str, Any]
) -> list[dict[str, Any]]:
    """Pure check of post-action signals against config/verification.yaml thresholds."""
    th = thresholds
    return [
        _criterion("active_version_ready", ready, "==", th["active_version_ready"]),
        _criterion("synthetic_success_ratio", synthetic_ratio, ">=", th["synthetic_success_ratio_min"]),
        _criterion("checkout_error_rate", signals.get("checkout_error_rate"), "<=", th["checkout_error_rate_max"]),
        _criterion(
            "checkout_latency_p95_seconds",
            signals.get("checkout_latency_p95_seconds"),
            "<=",
            th["checkout_latency_p95_seconds_max"],
        ),
        _criterion(
            "db_connection_utilization",
            signals.get("db_connection_utilization"),
            "<=",
            th["db_connection_utilization_max"],
        ),
    ]


class Ops:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog = Catalog(settings)
        self.docker = DockerControl(settings)
        self.prom = Prometheus(settings.prometheus_url)
        self.deploy = DeploymentStore(settings)
        self.audit = AuditLog(settings)
        self.vcfg = settings.verification
        self.trueforge = from_env()

    # ------------------------------------------------------------------ helpers
    def _gateway(self) -> Instance:
        return self.catalog.instance("api-gateway")

    def _instances_for(self, name: str) -> list[Instance]:
        validate_name(name)
        spec = self.settings.service_map.get(name)
        if spec and spec.get("kind") == "versioned":
            return [self.catalog.instance_for_version(name, v) for v in spec["versions"]]
        return [self.catalog.instance(name)]

    def _scalar(self, signal: str, window: str) -> float | None:
        try:
            return self.prom.scalar(render(signal, window))
        except ToolError:
            return None

    def _by_version(self, signal: str, window: str) -> dict[str, float | None]:
        try:
            rows = self.prom.signal_now(signal, window)
        except ToolError:
            return {}
        return {r["labels"].get("version", "?"): r4(r["value"]) for r in rows}

    def snapshot(self, window: str = "30s") -> dict[str, Any]:
        """Key production signals right now. Used for before/after comparisons."""
        return {
            "at": now_iso(),
            "rate_window": window,
            "active_version": self.deploy.active_version(VERSIONED),
            "checkout_error_rate": r4(self._scalar("checkout_error_rate", window)),
            "checkout_latency_p95_seconds": r4(self._scalar("checkout_latency_p95", window)),
            "checkout_requests_per_second": r4(self._scalar("checkout_request_rate", window)),
            "db_connections_total": r4(self._scalar("db_connections_total", window)),
            "db_connection_utilization": r4(self._scalar("db_connection_utilization", window)),
            "db_pool_utilization_by_version": self._by_version("db_pool_utilization", window),
            "db_pool_active_by_version": self._by_version("db_pool_active_connections", window),
        }

    def _alerts(self) -> list[dict[str, Any]]:
        out = []
        for a in self.prom.alerts():
            labels = a.get("labels", {})
            out.append(
                {
                    "alert": labels.get("alertname"),
                    "state": a.get("state"),
                    "severity": labels.get("severity"),
                    "service": labels.get("service"),
                    "summary": a.get("annotations", {}).get("summary"),
                    "active_since": a.get("activeAt"),
                    "value": r4(float(a["value"])) if a.get("value") not in (None, "") else None,
                }
            )
        return sorted(out, key=lambda a: (a["state"] != "firing", a["alert"] or ""))

    def _ensure_incident(self) -> dict[str, Any] | None:
        inc = self.audit.incident()
        if inc and inc.get("status") == "open":
            return inc
        firing = [a for a in self._alerts() if a["state"] == "firing"]
        if not firing:
            return None
        title = "Production alert: " + ", ".join(sorted({a["alert"] for a in firing}))
        return self.audit.open_incident(title, firing, self.snapshot())

    # ------------------------------------------------------------------ read tools
    def list_services(self) -> dict[str, Any]:
        active = self.deploy.active_version(VERSIONED)
        rows = []
        for inst in self.catalog.instances():
            st = self.docker.state(inst)
            rows.append(
                {
                    "instance": inst.name,
                    "service": inst.service,
                    "version": inst.version,
                    "tier": self.settings.service_map[inst.service].get("tier"),
                    "container_status": st.get("status"),
                    "receiving_traffic": (inst.version == active) if inst.version else None,
                    "restart_allowed": inst.name in self._restart_targets(),
                }
            )
        return ok(environment=self.settings.environment, active_versions={VERSIONED: active}, services=rows)

    def incident_context(self) -> dict[str, Any]:
        alerts = self._alerts()
        inc = self._ensure_incident()
        return ok(
            incident=inc and {k: inc[k] for k in ("incident_id", "title", "status", "opened_at") if k in inc},
            alerts=alerts,
            firing_count=sum(a["state"] == "firing" for a in alerts),
            current_signals=self.snapshot(),
        )

    def service_health(self, service: str) -> dict[str, Any]:
        active = self.deploy.active_version(VERSIONED)
        rows = []
        for inst in self._instances_for(service):
            state = self.docker.state(inst)
            row: dict[str, Any] = {"instance": inst.name, "version": inst.version, "container": state}
            if inst.version:
                row["receiving_traffic"] = inst.version == active
            if state.get("running") and inst.base_url:
                row["liveness"] = probe(inst, "/health")
                row["readiness"] = probe(inst, "/ready")
            rows.append(row)
        overall = "healthy"
        serving = [r for r in rows if r.get("receiving_traffic", True) and r["container"].get("exists")]
        if any(not r["container"].get("running") for r in serving):
            overall = "down"
        elif any(r.get("readiness", {"ok": True}).get("ok") is False for r in serving):
            overall = "degraded"
        return ok(
            service=service, overall=overall, active_version=active if service == VERSIONED else None, instances=rows
        )

    def service_logs(
        self, service: str, since_minutes: int, level: str | None, query: str | None, limit: int
    ) -> dict[str, Any]:
        lc = self.vcfg["logs"]
        since_minutes = int(clamp(since_minutes, 1, lc["max_since_minutes"]))
        limit = int(clamp(limit, 1, lc["max_limit"]))
        if level and level.upper() not in logmod.LEVELS:
            raise ToolError("INVALID_INPUT", "level must be DEBUG, INFO, WARN or ERROR", field="level")
        if query and len(query) > 120:
            raise ToolError("INVALID_INPUT", "query must be at most 120 characters", field="query")
        since = datetime.now(UTC) - timedelta(minutes=since_minutes)
        records: list[dict[str, Any]] = []
        for inst in self._instances_for(service):
            for raw in self.docker.logs(inst, since=since):
                rec = logmod.parse_line(raw, inst.name)
                if logmod.matches(rec, level, query):
                    records.append(rec)
        records.sort(key=lambda r: str(r["timestamp"]))
        recent = [logmod.bound_record(r, lc["max_message_chars"]) for r in records[-limit:]]
        return ok(
            service=service,
            since_minutes=since_minutes,
            filter={"level": level, "query": query},
            matched=len(records),
            returned=len(recent),
            truncated=len(records) > limit,
            **logmod.summarize(records),
            records=recent,
        )

    def query_metrics(self, signal: str, window_minutes: int, rate_window: str) -> dict[str, Any]:
        window_minutes = int(clamp(window_minutes, 1, self.vcfg["evidence"]["max_window_minutes"]))
        step = max(2, int(window_minutes * 60 / 60))
        series = self.prom.signal_range(signal, window_minutes, step, rate_window)
        current = self.prom.signal_now(signal, rate_window)
        cur_map = {tuple(sorted(r["labels"].items())): r4(r["value"]) for r in current}
        return ok(
            signal=signal,
            description=SIGNALS[signal]["description"],
            unit=SIGNALS[signal]["unit"],
            window_minutes=window_minutes,
            rate_window=rate_window,
            step_seconds=step,
            series=[
                {
                    "labels": s["labels"],
                    "current": cur_map.get(tuple(sorted(s["labels"].items()))),
                    **summarize_series(s["points"]),
                }
                for s in series[:12]
            ],
        )

    def db_health(self) -> dict[str, Any]:
        return ok(**database_health(self.settings.database_monitor_dsn))

    def recent_deployments(self, service: str | None, limit: int) -> dict[str, Any]:
        if service:
            self.catalog.service(service)
        return ok(deployments=self.deploy.history(service, int(clamp(limit, 1, 50))))

    def active_deployment(self, service: str) -> dict[str, Any]:
        self.catalog.versions(service)
        state = self.deploy.read().get("services", {}).get(service)
        if not state:
            raise ToolError("NO_DEPLOYMENT_STATE", f"no deployment state recorded for {service}")
        gw = probe(self._gateway(), "/route")
        return ok(
            service=service,
            **state,
            gateway_observed_version=(gw.get("body") or {}).get("active_version") if gw.get("ok") else None,
            available_versions=sorted(self.catalog.versions(service)),
        )

    async def synthetic_check(self, requests: int) -> dict[str, Any]:
        cfg = self.vcfg["synthetic"]
        n = int(clamp(requests, 1, cfg["max_requests"]))
        result = await run_checkout_probes(self._gateway().base_url or "", n, float(cfg["timeout_seconds"]))
        self.audit.record("synthetic_check", "run_synthetic_check", "completed", result)
        return ok(**result)

    def collect_evidence(self, window_minutes: int) -> dict[str, Any]:
        ecfg = self.vcfg["evidence"]
        window_minutes = int(clamp(window_minutes, 2, ecfg["max_window_minutes"]))
        step = int(ecfg["step_seconds"])
        end = time.time()
        start = end - window_minutes * 60
        series: dict[str, Any] = {}
        for sig in (
            "checkout_error_rate",
            "checkout_latency_p95",
            "checkout_request_rate",
            "traffic_by_upstream_version",
            "checkout_errors_by_reason",
            "payment_errors_by_type",
            "db_pool_active_connections",
            "db_pool_utilization",
            "db_connections_total",
            "db_connection_utilization",
        ):
            rows = self.prom.range(render(sig, "30s"), start, end, step)
            series[sig] = [
                {"labels": r["labels"], "points": [[int(t), r4(v)] for t, v in r["points"]]} for r in rows[:8]
            ]
        since = datetime.fromtimestamp(start, UTC)
        records: list[dict[str, Any]] = []
        lifecycle: dict[str, Any] = {}
        for inst in self.catalog.instances():
            if inst.service not in ("api-gateway", VERSIONED):
                continue
            lifecycle[inst.name] = self.docker.state(inst)
            records.extend(logmod.parse_line(raw, inst.name) for raw in self.docker.logs(inst, since=since))
        deployments = [
            d
            for d in self.deploy.history(VERSIONED, 50)
            if d.get("deployed_at") and datetime.fromisoformat(d["deployed_at"]).timestamp() >= start - 3600
        ]
        evidence = {
            "generated_at": now_iso(),
            "window": {"start_unix": int(start), "end_unix": int(end), "step_seconds": step, "rate_window": "30s"},
            "units": {k: SIGNALS[k]["unit"] for k in series},
            "metrics": series,
            "log_event_counts": logmod.bucket_counts(records, bucket_s=step),
            "log_event_totals": logmod.summarize([r for r in records if logmod.LEVELS.get(r["level"], 20) >= 30])[
                "event_counts"
            ],
            "deployments": deployments,
            "container_lifecycle": lifecycle,
            "alerts": self._alerts(),
            "restart_actions": [
                {"target": e["data"].get("target"), "completed_at": e["timestamp"]}
                for e in self.audit.events(types={"action_completed"})
                if e["source"] == "restart_service"
            ][-10:],
        }
        self.audit.record(
            "evidence_collected",
            "collect_incident_evidence",
            "completed",
            {"window_minutes": window_minutes, "series": len(series), "log_records": len(records)},
        )
        return ok(**evidence)

    # ------------------------------------------------------------------ risk
    def _restart_targets(self) -> list[str]:
        return list(self.settings.action_policy("restart_service").get("allowed_targets", []))

    def _recent_restarts(self, target: str, minutes: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
        return sum(
            1
            for e in self.audit.events(types={"action_completed"})
            if e["source"] == "restart_service"
            and e["data"].get("target") == target
            and datetime.fromisoformat(e["timestamp"]) >= cutoff
        )

    def assess_risk(self, action: str, service: str, target_version: str | None) -> dict[str, Any]:
        policy = self.settings.action_policy(action)
        if action not in ("restart_service", "rollback_deployment"):
            raise ToolError("INVALID_INPUT", "action must be restart_service or rollback_deployment", field="action")
        klass = policy.get("class", "red")
        rps = self._scalar("checkout_request_rate", "1m") or 0.0
        err = self._scalar("checkout_error_rate", "30s")
        blockers: list[str] = []
        detail: dict[str, Any] = {}

        if action == "restart_service":
            inst = self.catalog.instance(service)
            active = self.deploy.active_version(inst.service) if inst.version else None
            serving = inst.version is None or inst.version == active
            window = int(policy.get("window_minutes", 10))
            used = self._recent_restarts(inst.name, window)
            limit = int(policy.get("max_per_target_per_window", 2))
            if inst.name not in self._restart_targets():
                blockers.append(f"{inst.name} is not in the restart allowlist")
            if used >= limit:
                blockers.append(f"restart budget exhausted ({used}/{limit} in {window} min)")
            prior = [
                e["data"].get("duration_seconds")
                for e in self.audit.events(types={"action_completed"})
                if e["source"] == "restart_service" and e["data"].get("target") == inst.name
            ]
            est = prior[-1] if prior and prior[-1] else policy.get("estimated_disruption_seconds", 8)
            affected = self.catalog.dependents(inst.service) if inst.service in self.settings.service_map else []
            detail = {
                "target": inst.name,
                "receives_traffic": serving,
                "restart_budget": {"used": used, "limit": limit, "window_minutes": window},
                "expected_disruption_seconds": est,
                "expected_failed_requests": round(rps * est) if serving else 0,
                "dependents": affected,
                "reversible": True,
                "recovery_strategy": "container returns to the same image and config; no state change",
            }
        else:
            validate_name(service)
            if service not in policy.get("allowed_services", []):
                blockers.append(f"{service} is not eligible for rollback")
            versions = self.catalog.versions(service)
            current = self.deploy.active_version(service)
            if target_version is None:
                raise ToolError("INVALID_INPUT", "target_version is required for rollback_deployment")
            validate_version(target_version, "target_version")
            if target_version not in versions:
                raise ToolError("UNKNOWN_VERSION", f"{service} has no release {target_version}")
            if target_version == current:
                blockers.append(f"{service} is already running {target_version}")
            target = self.catalog.instance_for_version(service, target_version)
            tstate = self.docker.state(target)
            treadiness = probe(target, "/ready") if tstate.get("running") else {"ok": None, "reason": "not running"}
            if tstate.get("running") and not treadiness.get("ok"):
                blockers.append(f"target {target.name} is running but not ready")
            held = self._by_version("db_pool_active_connections", "30s").get(current or "")
            drain = float(policy.get("drain_seconds", 2))
            detail = {
                "current_version": current,
                "target_version": target_version,
                "target_instance": target.name,
                "target_container": {k: tstate.get(k) for k in ("status", "uptime_seconds", "image_id")},
                "target_readiness": treadiness,
                "target_will_be_started": not tstate.get("running"),
                "switch_mechanism": "atomic route switch at api-gateway, then stop previous version",
                "expected_disruption_seconds": drain,
                "expected_failed_requests": round(rps * drain * (err or 0)),
                "db_connections_released": held,
                "dependents": self.catalog.dependents(service),
                "reversible": True,
                "recovery_strategy": f"roll forward by redeploying {current} if {target_version} misbehaves",
            }
        level = {"green": "LOW", "yellow": "MEDIUM", "red": "HIGH"}.get(klass, "HIGH")
        result = {
            "action": action,
            "service": service,
            "policy_class": klass,
            "risk_level": "BLOCKED" if blockers else level,
            "approval_required": bool(policy.get("approval_required", False)),
            "approval_enforced_by": "TrueForge require_approval_for_tools" if policy.get("approval_required") else None,
            "environment": self.settings.environment,
            "active_requests_per_minute": round(rps * 60),
            "current_checkout_error_rate": r4(err),
            "blockers": blockers,
            **detail,
        }
        self.audit.record("risk_assessed", "assess_action_risk", "completed", result)
        return ok(**result)

    # ------------------------------------------------------------------ actions
    def restart(self, service: str, reason: str) -> dict[str, Any]:
        policy = self.settings.action_policy("restart_service")
        inst = self.catalog.instance(service)
        if inst.name not in self._restart_targets():
            raise ToolError(
                "ACTION_NOT_ALLOWED",
                f"restart of {inst.name} is not permitted by policy",
                allowed_targets=self._restart_targets(),
            )
        window = int(policy.get("window_minutes", 10))
        limit = int(policy.get("max_per_target_per_window", 2))
        used = self._recent_restarts(inst.name, window)
        if used >= limit:
            raise ToolError(
                "RATE_LIMITED",
                f"{inst.name} was already restarted {used} times in {window} minutes; escalate instead",
            )
        if not self.docker.state(inst).get("exists"):
            raise ToolError("NOT_DEPLOYED", f"{inst.name} is not deployed")
        action_id = f"act-{uuid.uuid4().hex[:8]}"
        before = self.snapshot()
        self.audit.record(
            "action_started",
            "restart_service",
            "started",
            {"action_id": action_id, "target": inst.name, "reason": reason[:300], "before": before},
        )
        started = time.monotonic()
        self.docker.restart(inst)
        running = self.docker.wait_running(inst, 15)
        timeout = float(policy.get("readiness_timeout_seconds", 30))
        live = wait_ready(inst, timeout, "/health") if inst.base_url else {"ok": running}
        ready = probe(inst, "/ready") if inst.base_url and live.get("ok") else None
        duration = round(time.monotonic() - started, 1)
        status = "success" if running and live.get("ok") else "failed"
        self.audit.record(
            "action_completed",
            "restart_service",
            status,
            {"action_id": action_id, "target": inst.name, "duration_seconds": duration, "readiness": ready},
        )
        result = {
            "action_id": action_id,
            "target": inst.name,
            "container_running": running,
            "liveness": live,
            "readiness_immediately_after": ready,
            "duration_seconds": duration,
            "container": self.docker.state(inst),
            "note": "The restart completed. That does not mean the incident is resolved: verify with verify_recovery.",
        }
        if status != "success":
            return partial(f"{inst.name} did not become live within {timeout}s", **result)
        return ok(**result)

    def rollback(self, service: str, from_version: str, to_version: str, reason: str) -> dict[str, Any]:
        policy = self.settings.action_policy("rollback_deployment")
        validate_name(service)
        validate_version(from_version, "from_version")
        validate_version(to_version, "to_version")
        if service not in policy.get("allowed_services", []):
            raise ToolError("ACTION_NOT_ALLOWED", f"rollback is not permitted for {service}")
        versions = self.catalog.versions(service)
        for v in (from_version, to_version):
            if v not in versions:
                raise ToolError("UNKNOWN_VERSION", f"{service} has no release {v}", known_versions=sorted(versions))
        current = self.deploy.active_version(service)
        if to_version == current:
            return ok(
                result="ALREADY_AT_TARGET",
                message=f"{service} is already serving {to_version}; nothing changed",
                active_version=current,
            )
        if from_version != current:
            raise ToolError(
                "VERSION_MISMATCH",
                f"from_version {from_version} is not the active version ({current})",
                active_version=current,
            )
        if policy.get("require_active_incident", True) and not self._ensure_incident():
            raise ToolError(
                "NO_ACTIVE_INCIDENT", "rollback requires an open incident or firing alert for this environment"
            )

        attestation = self._attest_approval(service, from_version, to_version, policy)
        action_id = f"act-{uuid.uuid4().hex[:8]}"
        before = self.snapshot()
        self.audit.record(
            "action_started",
            "rollback_deployment",
            "started",
            {
                "action_id": action_id,
                "service": service,
                "from_version": from_version,
                "to_version": to_version,
                "reason": reason[:500],
                "before": before,
                "approval": {
                    "required": True,
                    "enforced_by": "TrueForge require_approval_for_tools",
                    "attestation": attestation,
                },
            },
        )
        started = time.monotonic()
        target = self.catalog.instance_for_version(service, to_version)
        source = self.catalog.instance_for_version(service, from_version)
        started_how = self.docker.ensure_running(target)
        readiness = wait_ready(target, float(policy.get("target_readiness_timeout_seconds", 45)))
        if not readiness.get("ok"):
            self.audit.record(
                "action_completed",
                "rollback_deployment",
                "failed",
                {"action_id": action_id, "error_code": "TARGET_NOT_READY", "readiness": readiness},
            )
            raise ToolError(
                "TARGET_NOT_READY",
                f"{target.name} did not become ready; traffic was NOT switched",
                readiness=readiness,
            )
        entry = self.deploy.record_switch(
            service=service,
            to_version=to_version,
            change_type="rollback",
            actor=(
                f"forgesre-agent, human-approved in TrueForge session {attestation['session_id']}"
                if attestation
                else "forgesre-agent (TrueForge attestation disabled by policy)"
            ),
            metadata={
                "reason": reason[:300],
                "rolled_back_from": from_version,
                **release_metadata(
                    self.settings, target.spec.get("source_dir"), self.docker.state(target).get("image_id")
                ),
            },
        )
        gateway_version = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            gw = probe(self._gateway(), "/route")
            gateway_version = (gw.get("body") or {}).get("active_version") if gw.get("ok") else None
            if gateway_version == to_version:
                break
            time.sleep(0.25)
        time.sleep(float(policy.get("drain_seconds", 2)))
        stopped = self.docker.stop(source)
        duration = round(time.monotonic() - started, 1)
        switched = gateway_version == to_version
        self.audit.record(
            "action_completed",
            "rollback_deployment",
            "success" if switched else "partial",
            {
                "action_id": action_id,
                "service": service,
                "from_version": from_version,
                "to_version": to_version,
                "deployment_id": entry["deployment_id"],
                "gateway_observed_version": gateway_version,
                "previous_version_stopped": stopped,
                "duration_seconds": duration,
            },
        )
        result = {
            "action_id": action_id,
            "service": service,
            "from_version": from_version,
            "to_version": to_version,
            "deployment_id": entry["deployment_id"],
            "target_start": started_how,
            "target_readiness": readiness,
            "gateway_observed_version": gateway_version,
            "previous_version_container_stopped": stopped,
            "duration_seconds": duration,
            "note": "Traffic switched. Recovery is not confirmed until verify_recovery passes.",
        }
        if not switched:
            return partial("route written but gateway did not confirm the switch within 10s", **result)
        return ok(**result)

    def _attest_approval(
        self, service: str, from_version: str, to_version: str, policy: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Confirm in TrueForge's session events that a human allowed this exact call, once."""
        if not policy.get("require_trueforge_attestation", True):
            return None
        wanted = {"service": service, "from_version": from_version, "to_version": to_version}
        consumed = {
            (e["data"].get("approval") or {}).get("attestation", {}).get("tool_call_id")
            for e in self.audit.events(types={"action_started"})
            if e["source"] == "rollback_deployment"
        }
        found = None
        for _ in range(10):  # the approval event is persisted just before dispatch; allow a short lag
            try:
                found = self.trueforge.find_approval(tool="rollback_deployment", arguments=wanted)
            except TrueForgeError as exc:
                raise ToolError("APPROVAL_UNVERIFIABLE", f"cannot verify approval with TrueForge: {exc}") from exc
            if found and found["tool_call_id"] not in consumed:
                return found
            time.sleep(0.5)
        if found:
            raise ToolError("APPROVAL_ALREADY_USED", "that human approval was already used for a rollback")
        self.audit.record("approval_missing", "rollback_deployment", "refused", {"requested": wanted})
        raise ToolError(
            "APPROVAL_NOT_FOUND",
            "no human approval for this exact rollback was found in TrueForge; nothing was changed",
            requested=wanted,
        )

    # ------------------------------------------------------------------ verification
    async def verify(self, settle_seconds: int | None) -> dict[str, Any]:
        rc = self.vcfg["recovery"]
        th = rc["thresholds"]
        settle = int(
            clamp(settle_seconds if settle_seconds is not None else rc["settle_seconds"], 0, rc["max_settle_seconds"])
        )
        last_action = next(
            (e for e in reversed(self.audit.events(types={"action_completed"}))),
            None,
        )
        waited = 0.0
        if last_action:
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(last_action["timestamp"])).total_seconds()
            waited = max(0.0, settle - elapsed)
        self.audit.record(
            "verification_started",
            "verify_recovery",
            "started",
            {"settle_seconds": settle, "waiting_seconds": round(waited, 1)},
        )
        if waited:
            await asyncio.sleep(waited)

        active = self.deploy.active_version(VERSIONED)
        active_inst = self.catalog.instance_for_version(VERSIONED, active) if active else None
        readiness = await asyncio.to_thread(probe, active_inst, "/ready") if active_inst else {"ok": False}
        synth = await run_checkout_probes(
            self._gateway().base_url or "",
            int(rc["synthetic_requests"]),
            float(self.vcfg["synthetic"]["timeout_seconds"]),
        )
        window = rc["metric_window"]
        snap = await asyncio.to_thread(self.snapshot, window)
        pool_util = (snap["db_pool_utilization_by_version"] or {}).get(active or "")

        criteria = evaluate_recovery(
            ready=bool(readiness.get("ok")), synthetic_ratio=synth["success_ratio"], signals=snap, thresholds=th
        )
        recovered = all(c["passed"] for c in criteria)
        verdict = "RECOVERED" if recovered else "NOT_RECOVERED"
        result = {
            "verdict": verdict,
            "criteria": criteria,
            "failed_criteria": [c["criterion"] for c in criteria if not c["passed"]],
            "active_version": active,
            "active_version_pool_utilization": pool_util,
            "synthetic": synth,
            "signals": snap,
            "waited_seconds": round(waited, 1),
            "last_action": last_action and {"source": last_action["source"], "at": last_action["timestamp"]},
        }
        self.audit.record("verification_completed", "verify_recovery", verdict, result)
        if recovered:
            self.audit.update_incident(verified_recovered_at=now_iso())
        return ok(**result)

    # ------------------------------------------------------------------ timeline
    def timeline(self) -> dict[str, Any]:
        inc = self.audit.incident()
        events = self.audit.events(inc["incident_id"]) if inc else []
        compact = []
        for e in events:
            d = e["data"]
            item = {"at": e["timestamp"], "type": e["type"], "source": e["source"], "status": e["status"]}
            for k in ("target", "action_id", "from_version", "to_version", "verdict", "failed_criteria", "title"):
                if k in d:
                    item[k] = d[k]
            compact.append(item)
        return ok(incident=inc, events=compact[-60:])


async def gateway_route(settings: Settings) -> str | None:
    """Tiny helper used by the dashboard."""
    url = Catalog(settings).instance("api-gateway").base_url
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{url}/route")
            return r.json().get("active_version")
    except (httpx.HTTPError, ValueError):
        return None
