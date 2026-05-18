"""Schema validation tests for the portfolio's ``prefect.yaml`` shape."""
from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from statdesk_prefect_tools.preflight.yaml_schema import parse_prefect_yaml

from tests.conftest import CLEAN_PREFECT_YAML


def test_clean_yaml_validates() -> None:
    parsed = parse_prefect_yaml(CLEAN_PREFECT_YAML)
    assert parsed.name == "smoke-scraper"
    assert len(parsed.deployments) == 1
    assert parsed.deployments[0].work_pool.name == "default"
    assert parsed.deployments[0].job_variables.env.PGDATABASE == "smoke_db"


def test_pull_must_start_with_git_clone() -> None:
    bad = textwrap.dedent(
        """\
        name: smoke
        pull:
          - prefect.deployments.steps.run_shell_script:
              script: echo hi
        deployments: []
        """
    )
    with pytest.raises(ValidationError, match="git_clone"):
        parse_prefect_yaml(bad)


def test_pull_must_include_pip_install() -> None:
    bad = textwrap.dedent(
        """\
        name: smoke
        pull:
          - prefect.deployments.steps.git_clone:
              repository: x
              branch: main
              access_token: "{{ prefect.blocks.secret.github-pat }}"
        deployments: []
        """
    )
    with pytest.raises(ValidationError, match="pip install"):
        parse_prefect_yaml(bad)


def test_access_token_must_reference_secret_block() -> None:
    bad = textwrap.dedent(
        """\
        name: smoke
        pull:
          - prefect.deployments.steps.git_clone:
              repository: x
              branch: main
              access_token: ghp_PLAINTEXT
          - prefect.deployments.steps.run_shell_script:
              script: pip install --no-cache-dir .
        deployments: []
        """
    )
    with pytest.raises(ValidationError, match="Secret block"):
        parse_prefect_yaml(bad)


def test_work_pool_must_be_default() -> None:
    bad = CLEAN_PREFECT_YAML.replace("name: default", "name: custom-pool")
    with pytest.raises(ValidationError):
        parse_prefect_yaml(bad)


def test_entrypoint_requires_colon() -> None:
    bad = CLEAN_PREFECT_YAML.replace(
        "entrypoint: pipeline_flow.py:smoke_flow",
        "entrypoint: pipeline_flow.py",
    )
    with pytest.raises(ValidationError, match="entrypoint"):
        parse_prefect_yaml(bad)


def test_pgdatabase_required_in_env() -> None:
    bad = CLEAN_PREFECT_YAML.replace(
        "PGDATABASE: smoke_db",
        "OTHER_VAR: x",
    )
    with pytest.raises(ValidationError, match="PGDATABASE"):
        parse_prefect_yaml(bad)
