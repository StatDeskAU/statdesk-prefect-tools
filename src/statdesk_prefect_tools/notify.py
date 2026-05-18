"""Slack notify helpers — used by the CI deploy workflow's notification step.

Reads `SLACK_WEBHOOK_URL` from env. Graceful no-op when unset (so CI runs
in forks / dev clones don't fail on missing secrets).
"""
from __future__ import annotations

import os
import sys

import requests


def send(message: str, *, is_error: bool = False) -> bool:
    """Post `message` to the Slack incoming webhook. Returns True on success.

    No-op (returns False) when SLACK_WEBHOOK_URL is unset — never raises.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        print(f"[notify] SLACK_WEBHOOK_URL unset — skipping ({message[:80]!r})", file=sys.stderr)
        return False
    try:
        resp = requests.post(url, json={"text": message}, timeout=10)
        if resp.status_code != 200:
            print(f"[notify] Slack returned {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            return False
        return True
    except requests.RequestException as exc:
        print(f"[notify] Slack POST failed: {exc}", file=sys.stderr)
        return False


def deploy_result(status: str, *, repo: str | None = None, run_url: str | None = None) -> bool:
    """Format + send a deploy-result Slack message.

    Called from `.github/workflows/deploy.yml`'s `if: always()` notify step.

    `status` is the GH Actions `job.status` value: 'success' / 'failure' / 'cancelled'.
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY", "<unknown-repo>")
    run_url = run_url or _build_run_url()

    if status == "success":
        icon = ":white_check_mark:"
        verb = "Prefect deploy SUCCESS"
    elif status == "cancelled":
        icon = ":grey_exclamation:"
        verb = "Prefect deploy CANCELLED"
    else:
        icon = ":x:"
        verb = "Prefect deploy FAILED"

    line = f"{icon} *{verb}*  `{repo}`"
    if run_url:
        line += f"  <{run_url}|view run>"
    return send(line, is_error=status != "success")


def _build_run_url() -> str | None:
    """Construct the GH Actions run URL from the standard env vars."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (server and repo and run_id):
        return None
    return f"{server}/{repo}/actions/runs/{run_id}"
