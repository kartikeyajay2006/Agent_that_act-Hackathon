from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run_setup(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ensure-env.py"), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_new_demo_env_generates_private_local_secrets_and_only_reports_names(tmp_path: Path):
    (tmp_path / ".env.example").write_bytes((REPO / ".env.example").read_bytes())

    result = run_setup(tmp_path)
    contents = (tmp_path / ".env").read_text()

    assert result.returncode == 0
    assert "Generated local credentials: FORGESRE_MCP_TOKEN, POSTGRES_PASSWORD, MONITOR_PASSWORD" in result.stdout
    assert "MODEL_API_KEY" in result.stdout
    assert "change-me" not in contents
    assert all(len(contents.split(f"{name}=", 1)[1].splitlines()[0]) >= 48 for name in (
        "FORGESRE_MCP_TOKEN", "POSTGRES_PASSWORD", "MONITOR_PASSWORD"
    ))
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    assert not any(secret in result.stdout for secret in (
        contents.split("FORGESRE_MCP_TOKEN=", 1)[1].splitlines()[0],
        contents.split("POSTGRES_PASSWORD=", 1)[1].splitlines()[0],
    ))


def test_existing_env_values_are_preserved_and_existing_database_is_not_rotated(tmp_path: Path):
    (tmp_path / ".env.example").write_bytes((REPO / ".env.example").read_bytes())
    (tmp_path / ".env").write_text(
        "FORGESRE_MCP_TOKEN=local-token-kept\nPOSTGRES_PASSWORD=change-me\n"
        "MONITOR_PASSWORD=monitor-password-kept\nMODEL_API_KEY=\nCUSTOM_SETTING=keep-me\n"
    )

    result = run_setup(tmp_path, "--database-exists")
    contents = (tmp_path / ".env").read_text()

    assert result.returncode == 2
    assert "FORGESRE_MCP_TOKEN=local-token-kept" in contents
    assert "POSTGRES_PASSWORD=change-me" in contents
    assert "MONITOR_PASSWORD=monitor-password-kept" in contents
    assert "CUSTOM_SETTING=keep-me" in contents
    assert "MODEL_API_KEY" in result.stdout
    assert "POSTGRES_PASSWORD" in result.stdout
    assert "local-token-kept" not in result.stdout
    assert "monitor-password-kept" not in result.stdout
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_custom_model_reports_missing_base_url_by_name(tmp_path: Path):
    (tmp_path / ".env.example").write_text(
        "FORGESRE_MCP_TOKEN=\nPOSTGRES_PASSWORD=\nMONITOR_PASSWORD=\n"
        "MODEL_PROVIDER=custom\nMODEL_API_KEY=\nMODEL_BASE_URL=\n"
    )
    result = run_setup(tmp_path)
    assert result.returncode == 0
    assert "MODEL_API_KEY" in result.stdout
    assert "MODEL_BASE_URL" in result.stdout


def test_env_is_gitignored():
    result = subprocess.run(
        ["git", "check-ignore", ".env"], cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
