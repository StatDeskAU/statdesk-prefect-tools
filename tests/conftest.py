"""Shared fixtures: synthetic scraper repos used by guard tests.

Each helper produces a tmp_path-rooted directory that looks enough like a
StatDeskAU scraper repo for guards to run against it. The `clean_repo`
fixture is the baseline that passes every guard; individual tests then
mutate one file to make the targeted guard fire.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest


CLEAN_PREFECT_YAML = textwrap.dedent(
    """\
    name: smoke-scraper
    prefect-version: 3.x

    pull:
      - prefect.deployments.steps.git_clone:
          id: clone
          repository: https://github.com/StatDeskAU/smoke-scraper.git
          branch: main
          access_token: "{{ prefect.blocks.secret.github-pat }}"
      - prefect.deployments.steps.run_shell_script:
          id: install
          script: pip install --no-cache-dir .
          directory: "{{ clone.directory }}"

    deployments:
      - name: smoke-primary
        entrypoint: pipeline_flow.py:smoke_flow
        work_pool:
          name: default
        tags: [smoke]
        concurrency_limit: 1
        paused: true
        schedule:
          cron: "0 1 * * *"
          timezone: Australia/Sydney
        job_variables:
          env:
            PGDATABASE: smoke_db
    """
)


CLEAN_PIPELINE_FLOW = textwrap.dedent(
    """\
    from __future__ import annotations

    from prefect import flow


    @flow(name="smoke-flow")
    def smoke_flow() -> int:
        return 1
    """
)


CLEAN_MIRROR = textwrap.dedent(
    """\
    from __future__ import annotations

    import os

    import psycopg
    from psycopg.conninfo import make_conninfo


    def _open_supabase():
        conninfo = make_conninfo(
            host=os.environ["SUPABASE_PG_HOST"],
            port=int(os.environ.get("SUPABASE_PG_PORT", "6543")),
            dbname=os.environ["SUPABASE_PG_DATABASE"],
            user=os.environ["SUPABASE_PG_USER"],
            password=os.environ["SUPABASE_PG_PASSWORD"],
        )
        return psycopg.connect(conninfo, autocommit=False, prepare_threshold=None)
    """
)


CLEAN_MIRROR_TEST = textwrap.dedent(
    """\
    def test_module_imports():
        import pipeline_warehouse_mirror  # noqa: F401
    """
)


CLEAN_DEPLOY_WORKFLOW = textwrap.dedent(
    """\
    name: deploy
    on:
      push:
        branches: [main]
      workflow_dispatch: {}

    jobs:
      deploy:
        runs-on: ubuntu-latest
        env:
          PREFECT_API_URL: ${{ vars.PREFECT_API_URL }}
          PREFECT_API_AUTH_STRING: ${{ secrets.PREFECT_API_AUTH_STRING }}
        steps:
          - uses: actions/checkout@v4
            with:
              fetch-depth: 0
          - uses: actions/setup-python@v5
            with:
              python-version: '3.12'
          - run: pip install -e .[dev]
          - run: statdesk-preflight .
          - run: prefect deploy --all --prefect-file prefect.yaml
    """
)


def _git_init_and_commit(repo_root: Path) -> None:
    """Initialise the synthetic repo as a real git repo and commit everything.

    Guards 4, 4b, and 6 hit `git`; without a real repo they short-circuit
    (which is OK for those guards' "no failures" path) but tracked-file
    coverage needs an actual commit to be meaningful.
    """
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "test")
    run("add", "-A")
    run("commit", "-q", "-m", "synthetic")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    """A synthetic scraper repo that passes every guard.

    Mutate one file to make a single guard fire — the rest stay green.
    """
    _write(tmp_path / "prefect.yaml", CLEAN_PREFECT_YAML)
    _write(tmp_path / "pipeline_flow.py", CLEAN_PIPELINE_FLOW)
    _write(tmp_path / "pipeline_warehouse_mirror.py", CLEAN_MIRROR)
    _write(tmp_path / "tests" / "test_warehouse_mirror.py", CLEAN_MIRROR_TEST)
    _write(tmp_path / ".github" / "workflows" / "deploy.yml", CLEAN_DEPLOY_WORKFLOW)
    # Minimal pyproject so `tracked` reflects realistic shape
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "smoke"\nversion = "0.0.0"\n',
    )
    _git_init_and_commit(tmp_path)
    return tmp_path


@pytest.fixture
def repo_without_git(tmp_path: Path) -> Path:
    """Same content as `clean_repo` but without a git init.

    Useful for asserting guards that need git tolerate non-repo callers.
    """
    _write(tmp_path / "prefect.yaml", CLEAN_PREFECT_YAML)
    _write(tmp_path / "pipeline_flow.py", CLEAN_PIPELINE_FLOW)
    _write(tmp_path / ".github" / "workflows" / "deploy.yml", CLEAN_DEPLOY_WORKFLOW)
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "smoke"\nversion = "0.0.0"\n',
    )
    return tmp_path


__all__ = [
    "clean_repo",
    "repo_without_git",
    "CLEAN_PREFECT_YAML",
    "CLEAN_PIPELINE_FLOW",
    "CLEAN_MIRROR",
    "CLEAN_MIRROR_TEST",
    "CLEAN_DEPLOY_WORKFLOW",
]
