# statdesk-prefect-tools

Portfolio-shared Prefect deployment automation for the StatDeskAU scraper portfolio. Eliminates the manual gymnastics that the first three scraper onboardings (Scraper_0021 SQM Research, Scraper_0056 NSW VG, Scraper_0062 JobsAndSkills) hit — auth-string discovery, bash multi-line traps, Supabase pooler bugs, untracked-entrypoint failures, etc.

Pinned in every scraper's `[dev]` deps so the CI workflow can call its CLIs.

## Console scripts

| Command | Purpose |
|---|---|
| `statdesk-preflight <repo>` | Runs all 10 deploy-time guards. Hard-fails on any violation. Called from each scraper's `.github/workflows/deploy.yml` before `prefect deploy`. |
| `statdesk-register-github-pat` | Idempotent. Saves a GitHub PAT as the `github-pat` Prefect Secret block so the worker's git_clone pull step can authenticate. One-time operator action. |
| `statdesk-provision-worker-env --config portfolio_env.yaml` | (Phase 4) Updates the prefect-worker container's env vars on Dokploy via API. Replaces the UI clicks. |

## Preflight guards

Each guard codifies one historical lesson from the `prefect-worker` README's "Onboarding gotchas". Documentation doesn't stop the next operator from re-hitting them; mechanical guards do.

| # | Lesson | Guard |
|---|---|---|
| 1 | `prefect deploy` needs `PREFECT_API_AUTH_STRING` | `guard_workflow_references_auth_string` |
| 2 | Bash multi-line env-var prefix silently loses values | `guard_no_bash_env_prefix_in_workflow` |
| 3 | Supabase pooler trips on prepared statements | `guard_supabase_prepare_threshold` |
| 3b | URI-form Supabase conninfo silently truncates dotted usernames | `guard_supabase_conninfo_keyword_form` |
| 4 | Worker only sees committed code | `guard_entrypoint_committed` |
| 4b | Imports of entrypoint also need to be committed | `guard_entrypoint_imports_committed` |
| 5 | Long base64 strings corrupt on PuTTY/terminal paste | `guard_no_long_inline_base64` |
| 6 | Commits pushed after PR merge get stranded | `guard_branch_in_sync_with_remote` |
| extra | Mirror file must have a test | `guard_mirror_test_present` |
| extra | `prefect.yaml` must match portfolio schema | `guard_prefect_yaml_schema` |

## Install

```bash
pip install "statdesk-prefect-tools @ git+https://github.com/StatDeskAU/statdesk-prefect-tools.git@v0.1.0"
```

Pinned by version in each scraper's `pyproject.toml` so updates don't surprise existing repos.

## Usage in a scraper repo

```bash
# Once, manually
statdesk-register-github-pat   # one-time setup (reads PREFECT_API_AUTH_STRING + GITHUB_PAT from env)

# Every push to main, automatically (via .github/workflows/deploy.yml)
statdesk-preflight .           # blocks on any violation
prefect deploy --all           # the actual deploy
```

## Repo layout

```
statdesk-prefect-tools/
  src/statdesk_prefect_tools/
    preflight/
      runner.py        # statdesk-preflight CLI
      guards.py        # the 10 guards
      yaml_schema.py   # Pydantic model for prefect.yaml
      models.py        # GuardFailure dataclass + utilities
    dokploy/
      client.py        # Minimal Dokploy HTTP API wrapper
      apply_env.py     # statdesk-provision-worker-env CLI (Phase 4)
    secrets/
      register_github_pat.py    # statdesk-register-github-pat CLI
    notify.py          # CI Slack notify for the deploy workflow
  tests/
    test_guards.py
    test_yaml_schema.py
    fixtures/          # synthetic-repo fixtures for guard tests
```
