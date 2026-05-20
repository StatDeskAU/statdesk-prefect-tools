"""Tests for the apply_env CLI flow."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from statdesk_prefect_tools.dokploy.apply_env import (
    _build_desired_env,
    _diff_lines,
    _mask,
    _resolve_compose_id,
    main,
)
from statdesk_prefect_tools.dokploy.manifest import EnvKey, Manifest, Service, load_manifest


# --------------------------------------------------------------------------
# Manifest parsing
# --------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


def test_load_manifest_minimal(tmp_path: Path) -> None:
    p = _write_manifest(
        tmp_path,
        {
            "service": {"type": "compose", "compose_id": "abc"},
            "env": [{"name": "PGHOST", "required": True}],
        },
    )
    m = load_manifest(p)
    assert m.service.compose_id == "abc"
    assert m.env[0].name == "PGHOST"


def test_load_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    p = _write_manifest(
        tmp_path,
        {
            "service": {"type": "compose", "compose_id": "abc"},
            "env": [],
            "stray": "should fail",
        },
    )
    with pytest.raises(Exception, match="(?i)extra|stray"):
        load_manifest(p)


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_resolve_compose_id_prefers_literal() -> None:
    m = Manifest(service=Service(compose_id="literal-id"), env=[])
    assert _resolve_compose_id(m) == "literal-id"


def test_resolve_compose_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_COMPOSE_ID", "from-env-id")
    m = Manifest(service=Service(compose_id_env="MY_COMPOSE_ID"), env=[])
    assert _resolve_compose_id(m) == "from-env-id"


def test_resolve_compose_id_fails_when_neither_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING", raising=False)
    m = Manifest(service=Service(compose_id_env="MISSING"), env=[])
    with pytest.raises(Exception, match="(?i)empty|unset"):
        _resolve_compose_id(m)


# --------------------------------------------------------------------------
# Build desired env
# --------------------------------------------------------------------------


def test_build_desired_preserves_unmanaged_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "newdb")
    m = Manifest(
        service=Service(compose_id="x"),
        env=[EnvKey(name="PGHOST", required=True)],
    )
    current = {"PGHOST": "olddb", "UNMANAGED": "leave-me-alone"}
    merged, managed = _build_desired_env(m, current)

    assert merged == {"PGHOST": "newdb", "UNMANAGED": "leave-me-alone"}
    assert managed == ["PGHOST"]


def test_build_desired_applies_dollar_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGPASSWORD", "secret$value")
    m = Manifest(
        service=Service(compose_id="x"),
        env=[EnvKey(name="PGPASSWORD", required=True, dollar_escape=True, sensitive=True)],
    )
    merged, _ = _build_desired_env(m, {})
    assert merged["PGPASSWORD"] == "secret$$value"


def test_build_desired_skips_optional_unset_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPTIONAL", raising=False)
    m = Manifest(
        service=Service(compose_id="x"),
        env=[EnvKey(name="OPTIONAL", required=False)],
    )
    merged, _ = _build_desired_env(m, {"OPTIONAL": "kept-from-dokploy"})
    assert merged["OPTIONAL"] == "kept-from-dokploy"


def test_build_desired_fails_fast_on_required_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REQUIRED_BUT_MISSING", raising=False)
    m = Manifest(
        service=Service(compose_id="x"),
        env=[EnvKey(name="REQUIRED_BUT_MISSING", required=True)],
    )
    with pytest.raises(Exception, match="(?i)required"):
        _build_desired_env(m, {})


# --------------------------------------------------------------------------
# Diff display
# --------------------------------------------------------------------------


def test_diff_shows_additions_changes_and_deletions() -> None:
    keys = {"A": EnvKey(name="A"), "B": EnvKey(name="B"), "C": EnvKey(name="C")}
    current = {"A": "1", "B": "2", "GONE": "9"}
    desired = {"A": "1", "B": "3", "C": "4"}
    lines = _diff_lines(current, desired, keys)
    assert any(l.startswith("  + C=") for l in lines)
    assert any(l.startswith("  ~ B:") for l in lines)
    assert any(l.startswith("  - GONE=") for l in lines)
    # A unchanged -> no line for it
    assert not any(l.endswith("A=1") for l in lines if l.startswith("  ~"))


def test_diff_masks_sensitive_values() -> None:
    keys = {"PW": EnvKey(name="PW", sensitive=True)}
    current = {"PW": "old-password-123"}
    desired = {"PW": "new-password-456"}
    lines = _diff_lines(current, desired, keys)
    joined = "\n".join(lines)
    assert "old-password-123" not in joined
    assert "new-password-456" not in joined


def test_mask_helper() -> None:
    assert _mask("") == "(empty)"
    assert _mask("ab") == "<2 chars>"
    assert _mask("abcdef") == "<6 chars: ab...ef>"


# --------------------------------------------------------------------------
# CLI end-to-end (dry-run, mocked Dokploy)
# --------------------------------------------------------------------------


def test_cli_dry_run_shows_diff_and_exits_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker
) -> None:
    p = _write_manifest(
        tmp_path,
        {
            "service": {"type": "compose", "compose_id": "abc", "api_base": "https://x.example/api"},
            "env": [{"name": "PGHOST", "required": True}],
        },
    )
    monkeypatch.setenv("DOKPLOY_API_KEY", "k")
    monkeypatch.setenv("PGHOST", "newdb")

    mocker.patch(
        "statdesk_prefect_tools.dokploy.client.DokployClient.get_compose_env",
        return_value={"PGHOST": "olddb"},
    )
    update = mocker.patch(
        "statdesk_prefect_tools.dokploy.client.DokployClient.update_compose_env"
    )

    result = CliRunner().invoke(main, [str(p)])
    assert result.exit_code == 0, result.output
    assert "PGHOST" in result.output
    assert "newdb" in result.output
    # Dry-run must not call update
    update.assert_not_called()


def test_cli_apply_calls_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker
) -> None:
    p = _write_manifest(
        tmp_path,
        {
            "service": {"type": "compose", "compose_id": "abc"},
            "env": [{"name": "PGHOST", "required": True}],
        },
    )
    monkeypatch.setenv("DOKPLOY_API_KEY", "k")
    monkeypatch.setenv("PGHOST", "newdb")

    mocker.patch(
        "statdesk_prefect_tools.dokploy.client.DokployClient.get_compose_env",
        return_value={"PGHOST": "olddb", "UNMANAGED": "kept"},
    )
    update = mocker.patch(
        "statdesk_prefect_tools.dokploy.client.DokployClient.update_compose_env"
    )

    result = CliRunner().invoke(main, [str(p), "--apply"])
    assert result.exit_code == 0, result.output
    update.assert_called_once()
    _, kwargs = update.call_args
    args = update.call_args.args
    # Signature: update_compose_env(compose_id, env, *, title=..., description=...)
    compose_id, env_dict = args[0], args[1]
    assert compose_id == "abc"
    assert env_dict == {"PGHOST": "newdb", "UNMANAGED": "kept"}


def test_cli_strict_removes_unmanaged_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker
) -> None:
    p = _write_manifest(
        tmp_path,
        {
            "service": {"type": "compose", "compose_id": "abc"},
            "env": [{"name": "PGHOST", "required": True}],
        },
    )
    monkeypatch.setenv("DOKPLOY_API_KEY", "k")
    monkeypatch.setenv("PGHOST", "newdb")

    mocker.patch(
        "statdesk_prefect_tools.dokploy.client.DokployClient.get_compose_env",
        return_value={"PGHOST": "olddb", "UNMANAGED": "to-be-removed"},
    )
    update = mocker.patch(
        "statdesk_prefect_tools.dokploy.client.DokployClient.update_compose_env"
    )

    result = CliRunner().invoke(main, [str(p), "--apply", "--strict"])
    assert result.exit_code == 0, result.output
    args = update.call_args.args
    env_dict = args[1]
    assert env_dict == {"PGHOST": "newdb"}
    assert "UNMANAGED" not in env_dict


def test_cli_no_changes_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker
) -> None:
    p = _write_manifest(
        tmp_path,
        {
            "service": {"type": "compose", "compose_id": "abc"},
            "env": [{"name": "PGHOST", "required": True}],
        },
    )
    monkeypatch.setenv("DOKPLOY_API_KEY", "k")
    monkeypatch.setenv("PGHOST", "samedb")

    mocker.patch(
        "statdesk_prefect_tools.dokploy.client.DokployClient.get_compose_env",
        return_value={"PGHOST": "samedb"},
    )
    update = mocker.patch(
        "statdesk_prefect_tools.dokploy.client.DokployClient.update_compose_env"
    )

    result = CliRunner().invoke(main, [str(p), "--apply"])
    assert result.exit_code == 0, result.output
    assert "No changes" in result.output
    update.assert_not_called()
