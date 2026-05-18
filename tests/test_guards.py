"""Guard-by-guard tests: clean-repo passes, targeted mutation makes guard fire."""
from __future__ import annotations

import textwrap
from pathlib import Path

from statdesk_prefect_tools.preflight.guards import (
    ALL_GUARDS,
    guard_branch_in_sync_with_remote,
    guard_entrypoint_committed,
    guard_entrypoint_imports_committed,
    guard_mirror_test_present,
    guard_no_bash_env_prefix_in_workflow,
    guard_no_long_inline_base64,
    guard_prefect_yaml_schema,
    guard_supabase_conninfo_keyword_form,
    guard_supabase_prepare_threshold,
    guard_workflow_references_auth_string,
)


# --------------------------------------------------------------------------
# Clean-repo baseline — every guard returns empty
# --------------------------------------------------------------------------

def test_clean_repo_passes_all_guards(clean_repo: Path) -> None:
    for guard in ALL_GUARDS:
        failures = guard(clean_repo)
        assert failures == [], f"{guard.__name__} fired on clean repo: {failures}"


# --------------------------------------------------------------------------
# Guard 1 — workflow references PREFECT_API_AUTH_STRING
# --------------------------------------------------------------------------

def test_guard_1_missing_auth_string(clean_repo: Path) -> None:
    wf = clean_repo / ".github" / "workflows" / "deploy.yml"
    wf.write_text(
        wf.read_text(encoding="utf-8").replace("PREFECT_API_AUTH_STRING", "SOME_OTHER_VAR"),
        encoding="utf-8",
    )
    failures = guard_workflow_references_auth_string(clean_repo)
    assert len(failures) == 1
    assert "PREFECT_API_AUTH_STRING" in failures[0].message


def test_guard_1_no_deploy_workflow(clean_repo: Path) -> None:
    (clean_repo / ".github" / "workflows" / "deploy.yml").unlink()
    failures = guard_workflow_references_auth_string(clean_repo)
    assert len(failures) == 1
    assert "deploy.yml" in failures[0].message


# --------------------------------------------------------------------------
# Guard 2 — no multi-line bash env-var prefix in workflow run blocks
# --------------------------------------------------------------------------

def test_guard_2_multiline_env_prefix_fires(clean_repo: Path) -> None:
    wf = clean_repo / ".github" / "workflows" / "deploy.yml"
    # Inject a bad multi-line step: env var on its own line followed by command
    bad = textwrap.dedent(
        """\
        name: deploy
        on: { workflow_dispatch: {} }
        jobs:
          deploy:
            runs-on: ubuntu-latest
            env:
              PREFECT_API_AUTH_STRING: x
            steps:
              - run: |
                  FOO=bar
                  echo "$FOO"
        """
    )
    wf.write_text(bad, encoding="utf-8")
    failures = guard_no_bash_env_prefix_in_workflow(clean_repo)
    assert len(failures) == 1
    assert "FOO=bar" in failures[0].message


# --------------------------------------------------------------------------
# Guard 3 — Supabase psycopg.connect missing prepare_threshold=None
# --------------------------------------------------------------------------

def test_guard_3_missing_prepare_threshold(clean_repo: Path) -> None:
    mirror = clean_repo / "pipeline_warehouse_mirror.py"
    mirror.write_text(
        textwrap.dedent(
            """\
            import os
            import psycopg
            from psycopg.conninfo import make_conninfo

            def _open_supabase():
                conninfo = make_conninfo(
                    host=os.environ["SUPABASE_PG_HOST"],
                    user=os.environ["SUPABASE_PG_USER"],
                    password=os.environ["SUPABASE_PG_PASSWORD"],
                )
                return psycopg.connect(conninfo, autocommit=False)
            """
        ),
        encoding="utf-8",
    )
    failures = guard_supabase_prepare_threshold(clean_repo)
    assert len(failures) == 1
    assert "prepare_threshold" in failures[0].message


def test_guard_3_no_mirror_means_no_failure(repo_without_git: Path) -> None:
    # No pipeline_warehouse_mirror.py in repo_without_git fixture
    assert guard_supabase_prepare_threshold(repo_without_git) == []


# --------------------------------------------------------------------------
# Guard 3b — URI conninfo for Supabase
# --------------------------------------------------------------------------

def test_guard_3b_uri_form_fires(clean_repo: Path) -> None:
    mirror = clean_repo / "pipeline_warehouse_mirror.py"
    mirror.write_text(
        textwrap.dedent(
            """\
            import psycopg
            # Supabase pooler in URI form — mis-parses dotted username
            def _open_supabase():
                return psycopg.connect(
                    "postgresql://postgres.abc:secret@aws-supabase.com:6543/postgres",
                    prepare_threshold=None,
                )
            """
        ),
        encoding="utf-8",
    )
    failures = guard_supabase_conninfo_keyword_form(clean_repo)
    assert len(failures) == 1
    assert "URI form" in failures[0].message


# --------------------------------------------------------------------------
# Guard 4 — entrypoint files committed
# --------------------------------------------------------------------------

def test_guard_4_entrypoint_uncommitted_fires(clean_repo: Path) -> None:
    # Add a new entrypoint file but don't commit
    (clean_repo / "ghost_flow.py").write_text("def f(): pass\n", encoding="utf-8")
    yml = clean_repo / "prefect.yaml"
    yml.write_text(
        yml.read_text(encoding="utf-8").replace(
            "entrypoint: pipeline_flow.py:smoke_flow",
            "entrypoint: ghost_flow.py:f",
        ),
        encoding="utf-8",
    )
    failures = guard_entrypoint_committed(clean_repo)
    assert len(failures) == 1
    assert "ghost_flow.py" in failures[0].message


# --------------------------------------------------------------------------
# Guard 4b — entrypoint imports committed
# --------------------------------------------------------------------------

def test_guard_4b_uncommitted_import_fires(clean_repo: Path) -> None:
    # Create a same-repo package, import it from the entrypoint, don't commit
    pkg = clean_repo / "ghostpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "helper.py").write_text("def x(): return 1\n", encoding="utf-8")
    ep = clean_repo / "pipeline_flow.py"
    ep.write_text(
        textwrap.dedent(
            """\
            from prefect import flow
            from ghostpkg.helper import x


            @flow
            def smoke_flow():
                return x()
            """
        ),
        encoding="utf-8",
    )
    # Note: we DO NOT commit. Tracked-state lookups won't see ghostpkg/.
    failures = guard_entrypoint_imports_committed(clean_repo)
    # At least one failure mentioning ghostpkg
    assert any("ghostpkg" in f.message for f in failures), failures


# --------------------------------------------------------------------------
# Guard 5 — long inline base64 strings
# --------------------------------------------------------------------------

def test_guard_5_long_base64_fires(clean_repo: Path) -> None:
    blob = "A" * 250  # 250 chars of base64-alphabet
    (clean_repo / "embedded_creds.py").write_text(
        f'TOKEN = "{blob}"\n', encoding="utf-8"
    )
    failures = guard_no_long_inline_base64(clean_repo)
    assert len(failures) == 1
    assert "base64" in failures[0].message.lower()


# --------------------------------------------------------------------------
# Guard 6 — local branch in sync with remote
# --------------------------------------------------------------------------

def test_guard_6_no_upstream_returns_clean(clean_repo: Path) -> None:
    # Fresh `git init` has no upstream — guard should be a no-op
    assert guard_branch_in_sync_with_remote(clean_repo) == []


# --------------------------------------------------------------------------
# Mirror test presence
# --------------------------------------------------------------------------

def test_guard_mirror_test_missing(clean_repo: Path) -> None:
    (clean_repo / "tests" / "test_warehouse_mirror.py").unlink()
    failures = guard_mirror_test_present(clean_repo)
    assert len(failures) == 1
    assert "tests/test_warehouse_mirror.py" in failures[0].message


def test_guard_mirror_test_empty(clean_repo: Path) -> None:
    (clean_repo / "tests" / "test_warehouse_mirror.py").write_text(
        "# no test_ functions here\n", encoding="utf-8"
    )
    failures = guard_mirror_test_present(clean_repo)
    assert len(failures) == 1
    assert "no `test_*`" in failures[0].message


# --------------------------------------------------------------------------
# prefect.yaml schema
# --------------------------------------------------------------------------

def test_guard_schema_rejects_bad_yaml(clean_repo: Path) -> None:
    (clean_repo / "prefect.yaml").write_text(
        textwrap.dedent(
            """\
            name: broken
            pull:
              - prefect.deployments.steps.run_shell_script:
                  script: echo no git_clone
            deployments: []
            """
        ),
        encoding="utf-8",
    )
    failures = guard_prefect_yaml_schema(clean_repo)
    assert len(failures) == 1
    assert "git_clone" in failures[0].message


def test_guard_schema_missing_yaml(repo_without_git: Path) -> None:
    (repo_without_git / "prefect.yaml").unlink()
    failures = guard_prefect_yaml_schema(repo_without_git)
    assert len(failures) == 1
    assert "not found" in failures[0].message
