"""Idempotently (re-)register the Prefect ``github-pat`` Secret block.

Replaces the one-off ``Scraper_0021_SQM_Research/scripts/register_github_pat.py``.
Reads the GitHub PAT from ``$GITHUB_PAT`` if set (CI-friendly); otherwise prompts
via ``getpass`` so it never lands in shell history.

Reads ``$PREFECT_API_URL`` and ``$PREFECT_API_AUTH_STRING`` from env (the laptop
operator exports them from their secrets manager; CI gets them from GH secrets).
Falls back to ``https://prefect.statdesk.com.au/api`` for the URL only — never
hardcodes the auth string.

Usage:

    # CI
    GITHUB_PAT=$GITHUB_PAT \
    PREFECT_API_URL=$PREFECT_API_URL \
    PREFECT_API_AUTH_STRING=$PREFECT_API_AUTH_STRING \
        statdesk-register-github-pat

    # Laptop
    export PREFECT_API_URL=https://prefect.statdesk.com.au/api
    export PREFECT_API_AUTH_STRING="admin:..."   # from your secrets manager
    statdesk-register-github-pat                  # prompts for PAT
"""
from __future__ import annotations

import getpass
import os
import sys

import click


def _resolve_pat(non_interactive: bool) -> str:
    """Return the PAT from $GITHUB_PAT, else prompt via getpass."""
    env_pat = os.environ.get("GITHUB_PAT", "").strip()
    if env_pat:
        return env_pat
    if non_interactive:
        click.echo(
            "ERROR: $GITHUB_PAT is unset and --non-interactive was passed. "
            "Set GITHUB_PAT or drop --non-interactive to prompt.",
            err=True,
        )
        sys.exit(2)
    return getpass.getpass("Paste GitHub PAT (input is hidden): ").strip()


def _ensure_prefect_env() -> None:
    """Validate Prefect env vars; default the URL only, never the auth string."""
    if not os.environ.get("PREFECT_API_URL"):
        os.environ["PREFECT_API_URL"] = "https://prefect.statdesk.com.au/api"
    if not os.environ.get("PREFECT_API_AUTH_STRING"):
        click.echo(
            "ERROR: $PREFECT_API_AUTH_STRING is unset. The StatDesk Prefect "
            "server requires basic auth. Export it from your secrets manager "
            "(the 'Prefect basic auth' value) and retry.",
            err=True,
        )
        sys.exit(3)


@click.command()
@click.option(
    "--block-name",
    default="github-pat",
    show_default=True,
    help="Name of the Prefect Secret block to write.",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    help="Fail fast if $GITHUB_PAT is unset (no terminal prompt).",
)
def main(block_name: str, non_interactive: bool) -> None:
    """Idempotently register the Prefect ``github-pat`` Secret block."""
    _ensure_prefect_env()
    pat = _resolve_pat(non_interactive=non_interactive)
    if not pat:
        click.echo("ERROR: empty PAT - aborting without saving.", err=True)
        sys.exit(1)

    # Import here so module import (and --help) doesn't require Prefect installed.
    from prefect.blocks.system import Secret  # noqa: PLC0415

    Secret(value=pat).save(block_name, overwrite=True)
    click.echo(f"Saved Prefect Secret block: {block_name}")


if __name__ == "__main__":
    main()
