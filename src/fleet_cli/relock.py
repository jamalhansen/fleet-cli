"""Re-pin fleet repos to local-first-common's current main.

Each repo pins the library to a commit in uv.lock, so a library change reaches a
repo only when that repo is re-pinned. This does it the way it was done by hand on
2026-10-06, keeping what that taught:

- a commit counts only if HEAD actually moved (a pre-commit hook that rejects the
  new pin must read as a failure, not a success), and the lock is restored then;
- path-dependency siblings go first, and a repo is never pushed while a sibling it
  depends on has unpushed work -- its CI clones the sibling from GitHub, so it
  would test against the old sibling (calibration and model-comparison-harness
  went red exactly that way);
- a push counts only once the remote has HEAD.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from fleet_cli.repos import LIBRARY, Repo

Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[str]"]

_PIN = re.compile(r'name = "local-first-common"\n(?:[^\n]+\n)*?source = \{ git = "[^"#]*#([0-9a-f]{7,40})"')


def _run(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, check=False)


@dataclass
class RelockResult:
    repo: str
    status: str  # pushed | committed | would-update | current | skipped | failed
    detail: str = ""
    old: str = ""
    new: str = ""


def pinned_commit(repo: Repo) -> str | None:
    """The local-first-common commit repo's uv.lock pins, or None if it pins none."""
    lock = repo.path / "uv.lock"
    if not lock.exists():
        return None
    m = _PIN.search(lock.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def push_order(repos: Sequence[Repo]) -> list[Repo]:
    """Repos ordered so each comes after the fleet siblings it depends on by path."""
    by_name = {r.name: r for r in repos}
    ordered: list[Repo] = []
    state: dict[str, str] = {}

    def visit(r: Repo, chain: tuple[str, ...]) -> None:
        if state.get(r.name) == "done":
            return
        if state.get(r.name) == "visiting":
            raise ValueError("path-dependency cycle: " + " -> ".join((*chain, r.name)))
        state[r.name] = "visiting"
        for dep in r.path_dependencies:
            if dep in by_name:
                visit(by_name[dep], (*chain, r.name))
        state[r.name] = "done"
        ordered.append(r)

    for r in sorted(repos, key=lambda r: r.name):
        visit(r, ())
    return ordered


def _has_unpushed(repo: Repo, run: Runner) -> bool:
    out = run(["git", "log", "@{u}..HEAD", "--oneline"], repo.path)
    return out.returncode != 0 or bool(out.stdout.strip())


def _is_dirty(repo: Repo, run: Runner) -> bool:
    return bool(run(["git", "status", "--porcelain"], repo.path).stdout.strip())


def _head(repo: Repo, run: Runner) -> str:
    return run(["git", "rev-parse", "HEAD"], repo.path).stdout.strip()


def relock_repo(
    repo: Repo, *, push: bool, dry_run: bool, target: str, siblings: dict[str, Repo], run: Runner = _run
) -> RelockResult:
    old = pinned_commit(repo)
    if old is None or repo.is_library:
        return RelockResult(repo.name, "skipped", "no local-first-common pin")
    if _is_dirty(repo, run):
        return RelockResult(repo.name, "skipped", "uncommitted changes", old)
    if _has_unpushed(repo, run):
        return RelockResult(repo.name, "skipped", "unpushed commits (or no upstream)", old)
    if target and target.startswith(old):
        return RelockResult(repo.name, "current", "", old, old)
    if dry_run:
        return RelockResult(repo.name, "would-update", "", old, target[: len(old)] if target else "?")

    lock = run(["uv", "lock", "--upgrade-package", LIBRARY], repo.path)
    new = pinned_commit(repo)
    if lock.returncode != 0 or new is None:
        run(["git", "checkout", "--", "uv.lock"], repo.path)
        return RelockResult(repo.name, "failed", "uv lock: " + _tail(lock.stderr), old)
    if new == old:
        return RelockResult(repo.name, "current", "", old, new)

    run(["uv", "sync"], repo.path)
    before = _head(repo, run)
    run(["git", "add", "uv.lock"], repo.path)
    commit = run(
        ["git", "commit", "-m", f"Bump {LIBRARY} {old} -> {new}\n\nRe-pinned by `fleet relock`."],
        repo.path,
    )
    if commit.returncode != 0 or _head(repo, run) == before:
        run(["git", "reset", "-q", "HEAD", "--", "uv.lock"], repo.path)
        run(["git", "checkout", "--", "uv.lock"], repo.path)
        run(["uv", "sync"], repo.path)
        return RelockResult(repo.name, "failed", "commit blocked: " + _tail(commit.stdout + commit.stderr), old, new)

    if not push:
        return RelockResult(repo.name, "committed", "", old, new)
    waiting = [d for d in repo.path_dependencies if d in siblings and _has_unpushed(siblings[d], run)]
    if waiting:
        return RelockResult(repo.name, "committed", "not pushed: unpushed work in " + ", ".join(waiting), old, new)
    pushed = run(["git", "push", "-q"], repo.path)
    if pushed.returncode != 0 or _has_unpushed(repo, run):
        return RelockResult(repo.name, "committed", "push failed: " + _tail(pushed.stderr), old, new)
    return RelockResult(repo.name, "pushed", "", old, new)


def library_head(run: Runner = _run, cwd: Path | None = None) -> str:
    """local-first-common's main on GitHub (what `uv lock --upgrade-package` resolves)."""
    out = run(["git", "ls-remote", "https://github.com/jamalhansen/local-first-common.git", "main"], cwd or Path.home())
    return out.stdout.split()[0] if out.returncode == 0 and out.stdout.strip() else ""


def relock_all(
    repos: Sequence[Repo], *, push: bool, dry_run: bool, run: Runner = _run, target: str | None = None
) -> list[RelockResult]:
    target = library_head(run) if target is None else target
    siblings = {r.name: r for r in repos}
    return [
        relock_repo(r, push=push, dry_run=dry_run, target=target, siblings=siblings, run=run) for r in push_order(repos)
    ]


def _tail(text: str, lines: int = 3) -> str:
    return " | ".join(line.strip() for line in text.strip().splitlines()[-lines:] if line.strip())
