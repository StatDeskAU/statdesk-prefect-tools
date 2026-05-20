"""`statdesk-provision-worker-env` CLI -- idempotent Dokploy env push.

Workflow:
  1. Load the YAML manifest (keys + metadata only).
  2. Read each declared env-var from the caller's shell env.
  3. GET the current Dokploy compose env.
  4. Compute the merged result (unmanaged keys preserved by default).
  5. Diff against current state -- mask sensitive values.
  6. If --apply, POST compose.update; optionally compose.deploy.
     Otherwise (the default), print the diff and exit 0.

Safety rails:
  - `--apply` is required to mutate anything. Default is dry-run.
  - Sensitive values are masked in stdout (length + first/last 2 chars).
  - Required keys missing from the caller's env -> hard fail before any mutation.
  - `$ -> $$` escaping is applied per-key based on the manifest's
    `dollar_escape` flag, NOT a blanket transform.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from statdesk_prefect_tools.dokploy.client import DokployClient, DokployError
from statdesk_prefect_tools.dokploy.manifest import EnvKey, Manifest, load_manifest


# ---------------------------------------------------------------------------
# Resolution -- manifest + shell env -> desired Dokploy env state
# ---------------------------------------------------------------------------


def _resolve_compose_id(manifest: Manifest) -> str:
    if manifest.service.compose_id:
        return manifest.service.compose_id
    if manifest.service.compose_id_env:
        val = os.environ.get(manifest.service.compose_id_env, "").strip()
        if not val:
            raise click.ClickException(
                f"Manifest references compose_id_env={manifest.service.compose_id_env!r} "
                "but that env var is empty or unset."
            )
        return val
    raise click.ClickException(
        "Manifest must specify either `service.compose_id` or `service.compose_id_env`."
    )


def _resolve_value(key: EnvKey) -> str | None:
    """Resolve one env-key's value from the caller's shell env.

    Returns None when the var is unset and key.required=False -- the caller
    should then leave the existing Dokploy value alone for that key.
    """
    raw = os.environ.get(key.name, "")
    if not raw:
        if key.required:
            raise click.ClickException(
                f"Required env var {key.name!r} is not set in the caller's environment."
            )
        return None
    if key.dollar_escape:
        raw = raw.replace("$", "$$")
    return raw


def _build_desired_env(
    manifest: Manifest, current_env: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Return (merged_env, managed_keys).

    `merged_env` preserves the order of `current_env`, then appends any
    new keys from the manifest. `managed_keys` lists which keys the
    manifest controls (used by the diff display).
    """
    managed = [k.name for k in manifest.env]
    merged: dict[str, str] = dict(current_env)  # start with everything currently there
    for key in manifest.env:
        resolved = _resolve_value(key)
        if resolved is None:
            continue  # leave existing value alone
        merged[key.name] = resolved
    return merged, managed


# ---------------------------------------------------------------------------
# Diff display
# ---------------------------------------------------------------------------


def _mask(value: str) -> str:
    if not value:
        return "(empty)"
    n = len(value)
    if n <= 4:
        return f"<{n} chars>"
    return f"<{n} chars: {value[:2]}...{value[-2:]}>"


def _format_value(value: str, key: EnvKey | None) -> str:
    if key is not None and key.sensitive:
        return _mask(value)
    if len(value) > 80:
        return f"{value[:77]}..."
    return value


def _diff_lines(
    current: dict[str, str], desired: dict[str, str], keys_by_name: dict[str, EnvKey]
) -> list[str]:
    """Yield human-readable diff lines for stdout."""
    lines: list[str] = []
    for name in desired:
        key = keys_by_name.get(name)
        new_v = desired[name]
        old_v = current.get(name)
        if old_v is None:
            lines.append(f"  + {name}={_format_value(new_v, key)}")
        elif old_v != new_v:
            lines.append(f"  ~ {name}: {_format_value(old_v, key)} -> {_format_value(new_v, key)}")
    for name in current:
        if name not in desired:
            key = keys_by_name.get(name)
            lines.append(f"  - {name}={_format_value(current[name], key)}")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.argument(
    "manifest_path",
    type=click.Path(file_okay=True, dir_okay=False, exists=True, path_type=Path),
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Apply the diff to Dokploy. Default: dry-run (print and exit).",
)
@click.option(
    "--deploy",
    is_flag=True,
    help="After --apply, also trigger compose.deploy so the new env propagates.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Remove keys present in current Dokploy env but missing from the manifest. "
    "Default: preserve unmanaged keys (safer).",
)
@click.option("--api-key", envvar="DOKPLOY_API_KEY", help="Dokploy API key (or set $DOKPLOY_API_KEY).")
def main(
    manifest_path: Path,
    do_apply: bool,
    deploy: bool,
    strict: bool,
    api_key: str,
) -> None:
    """Provision a Dokploy compose service's env from a YAML manifest."""
    manifest = load_manifest(manifest_path)
    compose_id = _resolve_compose_id(manifest)
    keys_by_name = {k.name: k for k in manifest.env}

    try:
        client = DokployClient(api_base=manifest.service.api_base, api_key=api_key)
        current_env = client.get_compose_env(compose_id)
    except DokployError as exc:
        raise click.ClickException(str(exc))

    desired_env, managed_keys = _build_desired_env(manifest, current_env)

    if strict:
        unmanaged = [k for k in current_env if k not in managed_keys]
        for k in unmanaged:
            desired_env.pop(k, None)
        if unmanaged:
            click.echo(f"--strict: removing {len(unmanaged)} unmanaged key(s): {', '.join(unmanaged)}", err=True)

    diff = _diff_lines(current_env, desired_env, keys_by_name)

    click.echo(f"Manifest: {manifest_path}", err=True)
    click.echo(f"Service:  compose_id={compose_id}", err=True)
    click.echo(f"API:      {manifest.service.api_base}", err=True)
    click.echo(f"Managed:  {len(managed_keys)} key(s)", err=True)
    click.echo("", err=True)

    if not diff:
        click.echo("No changes -- desired state matches current Dokploy env.", err=True)
        return

    click.echo(f"Planned changes ({len(diff)}):", err=True)
    for line in diff:
        click.echo(line)
    click.echo("", err=True)

    if not do_apply:
        click.echo("(dry-run -- pass --apply to push to Dokploy)", err=True)
        return

    click.echo("Applying...", err=True)
    try:
        client.update_compose_env(compose_id, desired_env)
    except DokployError as exc:
        raise click.ClickException(f"compose.update failed: {exc}")
    click.echo("compose.update OK", err=True)

    if deploy:
        click.echo("Triggering redeploy...", err=True)
        try:
            client.deploy_compose(
                compose_id,
                title="env provisioning",
                description=f"Applied from {manifest_path.name}",
            )
        except DokployError as exc:
            raise click.ClickException(f"compose.deploy failed: {exc}")
        click.echo("compose.deploy OK -- container will restart shortly", err=True)
    else:
        click.echo("(env updated, container NOT restarted; pass --deploy to redeploy now)", err=True)


if __name__ == "__main__":
    main()
