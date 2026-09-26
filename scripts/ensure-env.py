#!/usr/bin/env python3
"""Create a private local .env and fill only missing local credentials."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path

SECRET_BYTES = {
    "FORGESRE_MCP_TOKEN": 32,
    "POSTGRES_PASSWORD": 24,
    "MONITOR_PASSWORD": 24,
}
ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*?)(\r?\n)?$")


def _is_placeholder(value: str | None) -> bool:
    return not value or value.startswith("change-me")


def ensure_env(root: Path, *, database_exists: bool) -> tuple[list[str], list[str]]:
    example = root / ".env.example"
    env_file = root / ".env"
    if not example.is_file():
        raise FileNotFoundError(f"Missing {example}")

    if not env_file.exists():
        data = example.read_bytes()
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)

    text = env_file.read_text()
    lines = text.splitlines(keepends=True)
    values: dict[str, str] = {}
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = ASSIGNMENT.match(line)
        if match:
            key, value = match.group(1), match.group(2)
            values[key] = value
            positions[key] = index

    generated: list[str] = []
    additions: list[str] = []
    for key, nbytes in SECRET_BYTES.items():
        if key in {"POSTGRES_PASSWORD", "MONITOR_PASSWORD"} and database_exists:
            continue
        if _is_placeholder(values.get(key)):
            value = secrets.token_hex(nbytes)
            if key in positions:
                old_line = lines[positions[key]]
                newline = "\r\n" if old_line.endswith("\r\n") else "\n" if old_line.endswith("\n") else ""
                lines[positions[key]] = f"{key}={value}{newline}"
            else:
                additions.append(f"{key}={value}\n")
            values[key] = value
            generated.append(key)

    missing: list[str] = []
    if database_exists:
        missing.extend(
            key for key in ("POSTGRES_PASSWORD", "MONITOR_PASSWORD") if _is_placeholder(values.get(key))
        )
    provider = values.get("MODEL_PROVIDER", "anthropic")
    required_external = ["MODEL_API_KEY"]
    if provider in {"custom", "truefoundry"}:
        required_external.append("MODEL_BASE_URL")
    missing.extend(key for key in required_external if _is_placeholder(values.get(key)))

    updated = "".join(lines) + "".join(additions)
    if updated != text:
        fd, temporary = tempfile.mkstemp(prefix=".env.", dir=root)
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w") as stream:
                stream.write(updated)
            os.replace(temporary, env_file)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            Path(temporary).unlink(missing_ok=True)
            raise

    os.chmod(env_file, stat.S_IRUSR | stat.S_IWUSR)
    return generated, sorted(set(missing))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--database-exists", action="store_true")
    args = parser.parse_args(argv)

    try:
        generated, missing = ensure_env(args.root, database_exists=args.database_exists)
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not initialize local .env ({type(exc).__name__})", file=sys.stderr)
        return 2

    print("Local .env is present with owner-only permissions.")
    if generated:
        print("Generated local credentials: " + ", ".join(generated))
    if missing:
        print("Missing variables to configure locally: " + ", ".join(missing))
    return 2 if any(name in {"POSTGRES_PASSWORD", "MONITOR_PASSWORD"} for name in missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
