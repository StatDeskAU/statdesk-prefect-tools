"""Preflight guards for scraper repos.

Each guard codifies one historical onboarding lesson — see
`statdesk_prefect_tools.preflight.guards` for the full list. The CLI
runner at `statdesk_prefect_tools.preflight.runner.main` is the
entry point invoked by every scraper's `.github/workflows/deploy.yml`.
"""
from statdesk_prefect_tools.preflight.models import GuardFailure

__all__ = ["GuardFailure"]
