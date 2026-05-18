"""`statdesk-preflight` CLI — runs all guards on a repo, exits non-zero on failure.

Invoked from each scraper's `.github/workflows/deploy.yml` before
`prefect deploy --all`. Also runnable on a laptop:

    statdesk-preflight .
    statdesk-preflight /path/to/scraper

GitHub Actions step-summary friendly: when `GITHUB_STEP_SUMMARY` env var
is set, also writes a Markdown summary there so failures surface on the
workflow run page.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from statdesk_prefect_tools.preflight.guards import ALL_GUARDS
from statdesk_prefect_tools.preflight.models import GuardFailure


def _emit_step_summary(failures: list[GuardFailure], repo_root: Path) -> None:
    """Write a GitHub Actions step summary if GITHUB_STEP_SUMMARY is set."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            if not failures:
                f.write("## ✅ Preflight: all guards passed\n\n")
                return
            f.write(f"## ❌ Preflight: {len(failures)} violation(s)\n\n")
            for fail in failures:
                f.write(f"### `{fail.guard}`\n\n")
                f.write(f"{fail.message}\n\n")
                if fail.file_hint:
                    try:
                        rel = fail.file_hint.relative_to(repo_root)
                    except ValueError:
                        rel = fail.file_hint
                    f.write(f"_File: `{rel}`_\n\n")
    except OSError:
        pass  # don't fail the run because we couldn't write the summary


@click.command()
@click.argument("repo_root", type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path), default=".")
@click.option("--guard", "guard_filter", multiple=True, help="Only run guards matching one of these names (substring match).")
@click.option("--verbose", is_flag=True, help="Print one line per guard even on pass.")
def main(repo_root: Path, guard_filter: tuple[str, ...], verbose: bool) -> None:
    """Run preflight guards on a scraper repo.

    Hard-fails on any guard violation. Designed for CI but also useful on a laptop.
    """
    repo_root = repo_root.resolve()
    click.echo(f"Preflight: {repo_root}", err=True)
    click.echo(f"Guards:    {len(ALL_GUARDS)}", err=True)

    all_failures: list[GuardFailure] = []
    guards = ALL_GUARDS
    if guard_filter:
        guards = [g for g in guards if any(f in g.__name__ for f in guard_filter)]
        click.echo(f"Filtered to {len(guards)} guard(s): {', '.join(g.__name__ for g in guards)}", err=True)

    for guard in guards:
        try:
            fails = guard(repo_root)
        except Exception as exc:  # noqa: BLE001
            fails = [GuardFailure(
                guard=guard.__name__,
                message=f"Guard raised an exception: {type(exc).__name__}: {exc}",
            )]
        if fails:
            for f in fails:
                click.echo(f"  FAIL  {f.format(repo_root=repo_root)}")
            all_failures.extend(fails)
        elif verbose:
            click.echo(f"  pass  {guard.__name__}", err=True)

    _emit_step_summary(all_failures, repo_root)

    if all_failures:
        click.echo("", err=True)
        click.echo(f"FAILED: {len(all_failures)} violation(s) across {len(set(f.guard for f in all_failures))} guard(s)", err=True)
        sys.exit(1)
    click.echo("OK: all guards passed", err=True)


if __name__ == "__main__":
    main()
