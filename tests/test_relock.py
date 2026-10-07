"""fleet relock against real git repos with real (bare) remotes; only `uv` is faked."""

import subprocess
from pathlib import Path

import pytest

from fleet_cli import relock as rl
from fleet_cli.repos import Repo

OLD = "a" * 40
NEW = "b" * 40
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def _lock(sha: str) -> str:
    return (
        'version = 1\n\n[[package]]\nname = "local-first-common"\nversion = "0.1.0"\n'
        f'source = {{ git = "https://github.com/jamalhansen/local-first-common.git?branch=main#{sha}" }}\n'
    )


def _repo(tmp: Path, name: str, path_deps: tuple[str, ...] = ()) -> Repo:
    remote = tmp / "remotes" / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    path = tmp / "work" / name
    subprocess.run(["git", "clone", "-q", str(remote), str(path)], check=True, capture_output=True)
    sources = "".join(f'{d} = {{ path = "../{d}" }}\n' for d in path_deps)
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\ndependencies = ["local-first-common"]\n'
        f'[tool.uv.sources]\nlocal-first-common = {{ git = "x" }}\n{sources}'
    )
    (path / "uv.lock").write_text(_lock(OLD))
    subprocess.run([*GIT, "add", "-A"], cwd=path, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "init"], cwd=path, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=path, check=True, capture_output=True)
    return Repo(path)


def fake_uv(cmd, cwd):
    """`uv lock` moves the pin to NEW; `uv sync` does nothing; git runs for real."""
    if cmd[0] == "uv":
        if cmd[1] == "lock":
            (cwd / "uv.lock").write_text(_lock(NEW))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    if cmd[0] == "git" and cmd[1] == "commit":
        cmd = [*GIT, *cmd[1:]]
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, check=False)


def _remote_head(tmp: Path, name: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", "main"], cwd=tmp / "remotes" / f"{name}.git", capture_output=True, text=True, check=True
    ).stdout.strip()


def test_pinned_commit_reads_the_lock(tmp_path):
    assert rl.pinned_commit(_repo(tmp_path, "tool")) == OLD


def test_push_order_puts_path_dependencies_first(tmp_path):
    a, b, c = _repo(tmp_path, "a", ("c",)), _repo(tmp_path, "b"), _repo(tmp_path, "c")
    assert [r.name for r in rl.push_order([a, b, c])] == ["c", "a", "b"]


def test_push_order_rejects_a_cycle(tmp_path):
    a, b = _repo(tmp_path, "a", ("b",)), _repo(tmp_path, "b", ("a",))
    with pytest.raises(ValueError, match="cycle"):
        rl.push_order([a, b])


def test_current_pin_is_left_alone(tmp_path):
    [r] = rl.relock_all([_repo(tmp_path, "tool")], push=True, dry_run=False, run=fake_uv, target=OLD)
    assert r.status == "current"


def test_dirty_repo_is_skipped(tmp_path):
    repo = _repo(tmp_path, "tool")
    (repo.path / "wip.py").write_text("x = 1\n")
    [r] = rl.relock_all([repo], push=True, dry_run=False, run=fake_uv, target=NEW)
    assert (r.status, r.detail) == ("skipped", "uncommitted changes")


def test_dry_run_changes_nothing(tmp_path):
    repo = _repo(tmp_path, "tool")
    [r] = rl.relock_all([repo], push=True, dry_run=True, run=fake_uv, target=NEW)
    assert r.status == "would-update"
    assert rl.pinned_commit(repo) == OLD


def test_repin_commits_and_pushes(tmp_path):
    repo = _repo(tmp_path, "tool")
    [r] = rl.relock_all([repo], push=True, dry_run=False, run=fake_uv, target=NEW)
    assert (r.status, r.old, r.new) == ("pushed", OLD, NEW)
    assert (
        _remote_head(tmp_path, "tool")
        == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo.path, text=True).strip()
    )


def test_hook_rejecting_the_pin_is_a_failure_and_restores_the_lock(tmp_path):
    repo = _repo(tmp_path, "tool")
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'tests failed'\nexit 1\n")
    hook.chmod(0o755)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo.path, text=True)
    [r] = rl.relock_all([repo], push=True, dry_run=False, run=fake_uv, target=NEW)
    assert r.status == "failed" and "tests failed" in r.detail
    assert rl.pinned_commit(repo) == OLD
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo.path, text=True) == head
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=repo.path, text=True).strip()


def test_not_pushed_while_a_path_sibling_has_unpushed_work(tmp_path):
    sib = _repo(tmp_path, "sib")
    (sib.path / "x.py").write_text("x = 1\n")
    subprocess.run([*GIT, "add", "-A"], cwd=sib.path, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "local only"], cwd=sib.path, check=True)
    user = _repo(tmp_path, "user", ("sib",))
    results = {r.repo: r for r in rl.relock_all([user, sib], push=True, dry_run=False, run=fake_uv, target=NEW)}
    assert results["sib"].status == "skipped"  # its own unpushed commit
    assert results["user"].status == "committed"
    assert "unpushed work in sib" in results["user"].detail
