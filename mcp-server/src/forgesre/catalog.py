"""Resolves service / instance names against config/services.yaml.

Every tool funnels user-supplied names through here, so an unknown or
malformed name never reaches Docker, Prometheus, or the filesystem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .results import ToolError

_NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_VERSION = re.compile(r"^v[0-9]{1,3}$")


@dataclass(frozen=True)
class Instance:
    """One running unit: a single service, or one version of a versioned service."""

    name: str
    service: str
    version: str | None
    container: str
    base_url: str | None
    compose_service: str | None
    spec: dict[str, Any]


def validate_name(value: str, field: str = "service") -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ToolError("INVALID_INPUT", f"{field} must match {_NAME.pattern}", field=field, value=str(value)[:80])
    return value


def validate_version(value: str, field: str = "version") -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ToolError("INVALID_INPUT", f"{field} must look like v1, v2, ...", field=field, value=str(value)[:80])
    return value


class Catalog:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.services: dict[str, Any] = settings.service_map

    def service(self, name: str) -> dict[str, Any]:
        validate_name(name)
        if name not in self.services:
            raise ToolError("UNKNOWN_SERVICE", f"'{name}' is not a known service", known_services=sorted(self.services))
        return self.services[name]

    def versions(self, service: str) -> dict[str, Any]:
        spec = self.service(service)
        if spec.get("kind") != "versioned":
            raise ToolError("NOT_VERSIONED", f"'{service}' is not a versioned service")
        return spec["versions"]

    def instance_for_version(self, service: str, version: str) -> Instance:
        validate_version(version)
        versions = self.versions(service)
        if version not in versions:
            raise ToolError("UNKNOWN_VERSION", f"{service} has no release '{version}'", known_versions=sorted(versions))
        v = versions[version]
        return Instance(
            name=v["instance"],
            service=service,
            version=version,
            container=v["container"],
            base_url=v.get("base_url"),
            compose_service=v.get("compose_service"),
            spec=v,
        )

    def instances(self) -> list[Instance]:
        out: list[Instance] = []
        for name, spec in self.services.items():
            if spec.get("kind") == "versioned":
                out.extend(self.instance_for_version(name, v) for v in spec["versions"])
            else:
                out.append(
                    Instance(
                        name=name,
                        service=name,
                        version=None,
                        container=spec["container"],
                        base_url=spec.get("base_url"),
                        compose_service=None,
                        spec=spec,
                    )
                )
        return out

    def instance(self, name: str) -> Instance:
        """Accepts an instance name (payment-service-v2) or a single-instance service name."""
        validate_name(name)
        for inst in self.instances():
            if inst.name == name:
                return inst
        if name in self.services and self.services[name].get("kind") == "versioned":
            raise ToolError(
                "AMBIGUOUS_SERVICE",
                f"'{name}' has several versions; name the instance explicitly",
                instances=[i.name for i in self.instances() if i.service == name],
            )
        raise ToolError(
            "UNKNOWN_SERVICE",
            f"'{name}' is not a known service or instance",
            known=sorted({i.name for i in self.instances()} | set(self.services)),
        )

    def dependents(self, service: str) -> list[str]:
        spec = self.service(service)
        direct = set(spec.get("dependents", []))
        direct |= {n for n, s in self.services.items() if service in s.get("depends_on", [])}
        return sorted(direct)
