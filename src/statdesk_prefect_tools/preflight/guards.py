"""Pre-flight guards — the 6 historical onboarding lessons, mechanically.

Each `guard_*` function takes the repo root as its only argument and returns
a list of :class:`GuardFailure` (empty if clean). The runner collects all
guards' results and exits non-zero if any guard returned at least one
failure.

Adding a new guard:
  1. Write `def guard_<name>(repo_root: Path) -> list[GuardFailure]`.
  2. Add it to the `ALL_GUARDS` list at the bottom of this module.
  3. Add a positive + negative test in `tests/test_guards.py`.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Iterable

import yaml

from statdesk_prefect_tools.preflight.models import GuardFailure
from statdesk_prefect_tools.preflight.yaml_schema import parse_prefect_yaml, ValidationError


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _find_workflow_files(repo_root: Path) -> list[Path]:
    """Locate `.github/workflows/*.yml` and `*.yaml` files."""
    wf_dir = repo_root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")])


def _git(repo_root: Path, *args: str) -> str:
    """Run a git command in repo_root. Returns stdout (stripped). Empty on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip()
    except (FileNotFoundError, OSError):
        return ""


def _tracked_files(repo_root: Path) -> set[str]:
    """Return the set of files git tracks at HEAD (forward-slash paths)."""
    out = _git(repo_root, "ls-tree", "-r", "HEAD", "--name-only")
    return set(line.strip() for line in out.splitlines() if line.strip())


# --------------------------------------------------------------------------
# Guard 1 — workflow references PREFECT_API_AUTH_STRING
# --------------------------------------------------------------------------

def guard_workflow_references_auth_string(repo_root: Path) -> list[GuardFailure]:
    """Workflow must read PREFECT_API_AUTH_STRING from GH secrets.

    Without basic auth, `prefect deploy` returns 401 against the central
    server. This is the first onboarding gotcha. We enforce that the
    workflow declares the env var (mechanically) so removing it requires
    a deliberate code change.
    """
    failures: list[GuardFailure] = []
    workflows = _find_workflow_files(repo_root)
    deploy_workflows = [w for w in workflows if "deploy" in w.name.lower()]
    if not deploy_workflows:
        return [GuardFailure(
            guard="guard_workflow_references_auth_string",
            message="No `.github/workflows/deploy.yml` found — every scraper needs an auto-deploy workflow.",
            file_hint=repo_root / ".github" / "workflows" / "deploy.yml",
        )]
    for wf in deploy_workflows:
        text = _read(wf)
        if "PREFECT_API_AUTH_STRING" not in text:
            failures.append(GuardFailure(
                guard="guard_workflow_references_auth_string",
                message=(
                    f"{wf.name} does not reference PREFECT_API_AUTH_STRING. "
                    "Add it to the deploy job's `env:` block "
                    "(value comes from the GH org-level secret of the same name)."
                ),
                file_hint=wf,
            ))
    return failures


# --------------------------------------------------------------------------
# Guard 2 — no bash multi-line env-var prefix in workflow run steps
# --------------------------------------------------------------------------

_BASH_ENV_PREFIX_RE = re.compile(r"^[A-Z_][A-Z0-9_]*=.+$", re.MULTILINE)


def guard_no_bash_env_prefix_in_workflow(repo_root: Path) -> list[GuardFailure]:
    """Reject workflow `run:` blocks that set env vars on one line and
    invoke the command on the next.

    In bash, this:
        VAR=value
        do_something
    is two separate commands — `VAR=value` sets nothing useful (no
    command to attach to), and `do_something` runs without `VAR`.

    GitHub Actions workflows should use the step's `env:` block instead.
    """
    failures: list[GuardFailure] = []
    for wf in _find_workflow_files(repo_root):
        try:
            data = yaml.safe_load(_read(wf)) or {}
        except yaml.YAMLError:
            continue
        for job_name, job in (data.get("jobs") or {}).items():
            for idx, step in enumerate(job.get("steps") or []):
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                lines = run.splitlines()
                for i, line in enumerate(lines[:-1]):
                    if _BASH_ENV_PREFIX_RE.match(line.strip()):
                        # Next non-blank line is what would have inherited
                        next_lines = [ln for ln in lines[i + 1:] if ln.strip()]
                        if next_lines and not _BASH_ENV_PREFIX_RE.match(next_lines[0].strip()):
                            failures.append(GuardFailure(
                                guard="guard_no_bash_env_prefix_in_workflow",
                                message=(
                                    f"{wf.name}:jobs.{job_name}.steps[{idx}]: "
                                    f"bash env-var prefix on its own line ({line.strip()!r}) "
                                    "won't carry to the next command. "
                                    "Use the step's `env:` block instead, or put "
                                    "the env var and command on a single bash line."
                                ),
                                file_hint=wf,
                            ))
                            break
    return failures


# --------------------------------------------------------------------------
# Guard 3 — Supabase pooler trips on prepared statements
# --------------------------------------------------------------------------

def _ast_walk_calls(tree: ast.AST) -> Iterable[ast.Call]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _call_name(call: ast.Call) -> str:
    """Best-effort dotted name extraction for a Call's func."""
    parts: list[str] = []
    node: ast.AST | None = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _file_mentions_supabase(text: str) -> bool:
    """Heuristic: does this file talk to Supabase at all?

    We check the file text (not the individual Call expression) because the
    canonical pattern hoists the connection string into a local var:

        conninfo = make_conninfo(host=os.environ["SUPABASE_PG_HOST"], ...)
        return psycopg.connect(conninfo, prepare_threshold=None)

    The connect Call alone gives no signal. The file context does.
    """
    return "supabase" in text.lower()


def guard_supabase_prepare_threshold(repo_root: Path) -> list[GuardFailure]:
    """Every `psycopg.connect(...)` in a Supabase-targeting mirror must set
    `prepare_threshold=None`.

    Supabase's transaction-mode pooler (Supavisor) rejects session-level
    prepared statements with `DuplicatePreparedStatement: prepared
    statement "_pg3_0" already exists` on the second invocation. psycopg3's
    automatic statement caching trips this on any `cursor.executemany(...)`.

    Scope: only `pipeline_warehouse_mirror.py`, and only when that file
    mentions Supabase. Other psycopg.connect calls (laptop PG, worker PG)
    are unaffected.
    """
    failures: list[GuardFailure] = []
    mirror = repo_root / "pipeline_warehouse_mirror.py"
    if not mirror.is_file():
        return failures  # no mirror, no risk
    text = _read(mirror)
    if not _file_mentions_supabase(text):
        return failures  # mirror exists but doesn't talk to Supabase
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [GuardFailure(
            guard="guard_supabase_prepare_threshold",
            message="pipeline_warehouse_mirror.py has a syntax error — cannot AST-parse",
            file_hint=mirror,
        )]
    for call in _ast_walk_calls(tree):
        name = _call_name(call)
        if not name.endswith("psycopg.connect") and name != "connect":
            continue
        kw_names = {kw.arg for kw in call.keywords if kw.arg is not None}
        if "prepare_threshold" not in kw_names:
            failures.append(GuardFailure(
                guard="guard_supabase_prepare_threshold",
                message=(
                    f"pipeline_warehouse_mirror.py:{call.lineno}: "
                    "psycopg.connect() to Supabase is missing "
                    "`prepare_threshold=None`. Supavisor will reject "
                    "prepared statements on the second executemany() call."
                ),
                file_hint=mirror,
            ))
    return failures


# --------------------------------------------------------------------------
# Guard 3b — Supabase conninfo must use keyword form, not URI
# --------------------------------------------------------------------------

_URI_LITERAL_RE = re.compile(r"postgres(?:ql)?://", re.IGNORECASE)


def guard_supabase_conninfo_keyword_form(repo_root: Path) -> list[GuardFailure]:
    """Reject `psycopg.connect("postgresql://...supabase...")` patterns.

    The bundled libpq in `psycopg[binary]` mis-parses dotted usernames
    (the Supabase pooler format `postgres.<project-ref>`) in URI form,
    silently stripping the tenant suffix and producing a "FATAL: user
    postgres does not exist" error. `make_conninfo(host=..., user=...,
    password=...)` builds a keyword-form connection string that
    preserves dots literally.
    """
    failures: list[GuardFailure] = []
    mirror = repo_root / "pipeline_warehouse_mirror.py"
    if not mirror.is_file():
        return failures
    text = _read(mirror)
    for m in _URI_LITERAL_RE.finditer(text):
        line_no = text[: m.start()].count("\n") + 1
        # Heuristic — was 'supabase' mentioned within ~200 chars of the URI literal?
        window = text[max(0, m.start() - 200): m.start() + 200]
        if "supabase" not in window.lower():
            continue
        failures.append(GuardFailure(
            guard="guard_supabase_conninfo_keyword_form",
            message=(
                f"pipeline_warehouse_mirror.py:{line_no}: "
                "Supabase connection string in URI form ('postgresql://...'). "
                "Use psycopg.conninfo.make_conninfo(host=..., user=..., password=...) "
                "instead — URI form mis-parses dotted usernames."
            ),
            file_hint=mirror,
        ))
    return failures


# --------------------------------------------------------------------------
# Guard 4 — entrypoint files are committed to git
# --------------------------------------------------------------------------

def guard_entrypoint_committed(repo_root: Path) -> list[GuardFailure]:
    """The file referenced by each deployment's `entrypoint` must be tracked.

    The worker does a fresh `git clone` per fire. Anything not on the
    deployed branch is invisible. Cost of finding this out at fire time
    is ~10 minutes of confused debugging — cheaper to fail at preflight.
    """
    failures: list[GuardFailure] = []
    yaml_path = repo_root / "prefect.yaml"
    if not yaml_path.is_file():
        return [GuardFailure(
            guard="guard_entrypoint_committed",
            message="prefect.yaml not found at repo root",
            file_hint=yaml_path,
        )]
    try:
        data = yaml.safe_load(_read(yaml_path)) or {}
    except yaml.YAMLError as exc:
        return [GuardFailure(
            guard="guard_entrypoint_committed",
            message=f"prefect.yaml unparseable: {exc}",
            file_hint=yaml_path,
        )]
    tracked = _tracked_files(repo_root)
    if not tracked:
        # Not a git repo, or no commits yet — let other guards complain
        return failures
    for dep in data.get("deployments") or []:
        ep = dep.get("entrypoint", "")
        if ":" not in ep:
            continue
        file_part = ep.rsplit(":", 1)[0]
        # Normalise to forward-slash for git ls-tree comparison
        file_norm = file_part.replace("\\", "/")
        if file_norm not in tracked:
            failures.append(GuardFailure(
                guard="guard_entrypoint_committed",
                message=(
                    f"Deployment {dep.get('name', '?')!r} entrypoint refers to "
                    f"{file_norm!r}, which is not tracked in HEAD. "
                    "The worker will fail with FileNotFoundError at fire time. "
                    "Run `git add` + commit before deploying."
                ),
                file_hint=repo_root / file_part,
            ))
    return failures


# --------------------------------------------------------------------------
# Guard 4b — same-package imports of the entrypoint are also committed
# --------------------------------------------------------------------------

def guard_entrypoint_imports_committed(repo_root: Path) -> list[GuardFailure]:
    """AST-parse the entrypoint file; verify each `from <pkg> import <x>`
    that resolves to a file inside this repo is tracked in HEAD.

    Catches the Scraper_0062 trap where prefect_flow.py was tracked but
    notify.py (which it imports) was not.
    """
    failures: list[GuardFailure] = []
    yaml_path = repo_root / "prefect.yaml"
    if not yaml_path.is_file():
        return failures
    try:
        data = yaml.safe_load(_read(yaml_path)) or {}
    except yaml.YAMLError:
        return failures
    tracked = _tracked_files(repo_root)
    if not tracked:
        return failures

    seen_failures: set[tuple[str, str]] = set()
    for dep in data.get("deployments") or []:
        ep = dep.get("entrypoint", "")
        if ":" not in ep:
            continue
        file_part = ep.rsplit(":", 1)[0].replace("\\", "/")
        ep_path = repo_root / file_part
        if not ep_path.is_file():
            continue
        try:
            tree = ast.parse(_read(ep_path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    modules.append(node.module)
            for mod in modules:
                # Only consider modules whose top-level package exists in repo
                top = mod.split(".")[0]
                # Try common layouts: src/<top>/ and ./<top>/
                candidate_dirs = [repo_root / "src" / top, repo_root / top]
                if not any(p.is_dir() for p in candidate_dirs):
                    continue
                # Module path within the package
                rel_path = mod.replace(".", "/") + ".py"
                possible = [f"src/{rel_path}", rel_path, f"src/{rel_path.replace('.py', '/__init__.py')}", rel_path.replace(".py", "/__init__.py")]
                if not any(p in tracked for p in possible):
                    key = (file_part, mod)
                    if key in seen_failures:
                        continue
                    seen_failures.add(key)
                    failures.append(GuardFailure(
                        guard="guard_entrypoint_imports_committed",
                        message=(
                            f"{file_part} imports {mod!r} but no file matching "
                            f"that module is tracked in HEAD. Worker clone will fail."
                        ),
                        file_hint=ep_path,
                    ))
    return failures


# --------------------------------------------------------------------------
# Guard 5 — no long inline base64 strings in source
# --------------------------------------------------------------------------

_BASE64_LITERAL_RE = re.compile(
    r"(['\"])([A-Za-z0-9+/=]{200,})\1"
)


def guard_no_long_inline_base64(repo_root: Path) -> list[GuardFailure]:
    """Reject string literals > 200 chars of pure base64.

    Long base64 strings corrupt on PuTTY paste / terminal wrap. If the
    operator needs to pass an encoded value, it should travel through
    an env var (`*_B64`) instead, not a code literal.
    """
    failures: list[GuardFailure] = []
    for py in repo_root.rglob("*.py"):
        # Skip venv/tests-fixtures so synthetic fixtures don't trip the
        # guard when we run our own tests.
        if any(part in {".venv", "venv", "node_modules", ".pytest_cache"} for part in py.parts):
            continue
        text = _read(py)
        for m in _BASE64_LITERAL_RE.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            failures.append(GuardFailure(
                guard="guard_no_long_inline_base64",
                message=(
                    f"{py.name}:{line_no}: long base64 literal ({len(m.group(2))} chars) in source. "
                    "Pass through an env var like `*_B64` instead — long base64 "
                    "strings corrupt on PuTTY paste / terminal wrap."
                ),
                file_hint=py,
            ))
    return failures


# --------------------------------------------------------------------------
# Guard 6 — local branch is in sync with remote
# --------------------------------------------------------------------------

def guard_branch_in_sync_with_remote(repo_root: Path) -> list[GuardFailure]:
    """Detect locally-merged-but-unpushed commits.

    Specifically catches the case where PR #N merged on GitHub before the
    operator pushed later commits to the feat branch — those later
    commits never reach the deployed branch.

    In CI the checkout is always synced so this is a no-op. On a laptop
    `statdesk-preflight` run it surfaces drift.
    """
    failures: list[GuardFailure] = []
    branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        return failures  # detached HEAD (e.g. CI's `actions/checkout`) — skip
    remote_ref = _git(repo_root, "rev-parse", "--abbrev-ref", f"{branch}@{{u}}")
    if not remote_ref:
        return failures  # no upstream — skip
    ahead = _git(repo_root, "rev-list", "--count", f"{remote_ref}..HEAD")
    try:
        n = int(ahead) if ahead else 0
    except ValueError:
        n = 0
    if n > 0:
        failures.append(GuardFailure(
            guard="guard_branch_in_sync_with_remote",
            message=(
                f"Local branch {branch!r} is {n} commit(s) ahead of {remote_ref!r}. "
                "Push them before deploying — otherwise the worker will clone "
                "the older remote state."
            ),
        ))
    return failures


# --------------------------------------------------------------------------
# Extra — mirror file must have at least one test
# --------------------------------------------------------------------------

def guard_mirror_test_present(repo_root: Path) -> list[GuardFailure]:
    """If `pipeline_warehouse_mirror.py` exists, `tests/test_warehouse_mirror.py`
    must exist and contain at least one `test_` function.

    Catches "we built a mirror but never tested it" scenarios — common in
    fast onboarding paths.
    """
    failures: list[GuardFailure] = []
    mirror = repo_root / "pipeline_warehouse_mirror.py"
    if not mirror.is_file():
        return failures
    test_file = repo_root / "tests" / "test_warehouse_mirror.py"
    if not test_file.is_file():
        return [GuardFailure(
            guard="guard_mirror_test_present",
            message=(
                "pipeline_warehouse_mirror.py exists but tests/test_warehouse_mirror.py "
                "is missing. Add at least one test for the mirror flow before deploying."
            ),
            file_hint=test_file,
        )]
    text = _read(test_file)
    if not re.search(r"^\s*def\s+test_", text, re.MULTILINE):
        failures.append(GuardFailure(
            guard="guard_mirror_test_present",
            message=(
                "tests/test_warehouse_mirror.py exists but contains no `test_*` "
                "functions. Add at least one before deploying."
            ),
            file_hint=test_file,
        ))
    return failures


# --------------------------------------------------------------------------
# Extra — prefect.yaml matches the portfolio schema
# --------------------------------------------------------------------------

def guard_prefect_yaml_schema(repo_root: Path) -> list[GuardFailure]:
    """Validate `prefect.yaml` against the Pydantic schema in
    `statdesk_prefect_tools.preflight.yaml_schema`.

    Enforces work_pool=default, pull-step shape, deployment shape,
    job_variables.env.PGDATABASE presence, etc.
    """
    yaml_path = repo_root / "prefect.yaml"
    if not yaml_path.is_file():
        return [GuardFailure(
            guard="guard_prefect_yaml_schema",
            message="prefect.yaml not found at repo root",
            file_hint=yaml_path,
        )]
    try:
        parse_prefect_yaml(_read(yaml_path))
    except ValidationError as exc:
        return [GuardFailure(
            guard="guard_prefect_yaml_schema",
            message=f"prefect.yaml fails schema validation:\n{exc}",
            file_hint=yaml_path,
        )]
    except Exception as exc:  # noqa: BLE001
        return [GuardFailure(
            guard="guard_prefect_yaml_schema",
            message=f"prefect.yaml load failed: {exc}",
            file_hint=yaml_path,
        )]
    return []


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

ALL_GUARDS = [
    guard_workflow_references_auth_string,
    guard_no_bash_env_prefix_in_workflow,
    guard_supabase_prepare_threshold,
    guard_supabase_conninfo_keyword_form,
    guard_entrypoint_committed,
    guard_entrypoint_imports_committed,
    guard_no_long_inline_base64,
    guard_branch_in_sync_with_remote,
    guard_mirror_test_present,
    guard_prefect_yaml_schema,
]
