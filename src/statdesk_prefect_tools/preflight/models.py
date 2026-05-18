"""Shared data types for preflight guards."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuardFailure:
    """One violation found by a preflight guard.

    A guard returning a list of these (possibly empty) signals zero or more
    independent violations. The CLI runner aggregates failures across guards
    and exits non-zero if any guard returned at least one.
    """

    guard: str          # e.g. "guard_supabase_prepare_threshold"
    message: str        # human-readable explanation, suitable for CI step summary
    file_hint: Path | None = None   # path the operator should look at first

    def format(self, *, repo_root: Path | None = None) -> str:
        location = ""
        if self.file_hint is not None:
            rel = self.file_hint
            if repo_root is not None:
                try:
                    rel = self.file_hint.relative_to(repo_root)
                except ValueError:
                    pass
            location = f"  ({rel})"
        return f"[{self.guard}] {self.message}{location}"
