"""Which installed tools run code older than their repo, and redeploying them.

`uv tool install` is a snapshot, not a link: a commit is not live until the tool is
rebuilt. After a fleet-wide sweep that is every tool at once -- on 2026-10-07, 32 of
them. repo-health-run has flagged this on the dashboard since 2026-10-04 ("installed
copy stale"); this is the matching fix, using the same rule so the two agree:

    not installed as a uv tool, or installed editable  -> fine
    otherwise every .py in the repo's package(s) must match the installed copy byte
    for byte; `stale_files` counts the ones that don't (missing counts as stale)

The deploy itself is local-first-common/scripts/deploy.sh: reinstall, restart a
KeepAlive service, smoke-import every entry point.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from fleet_cli.repos import LIBRARY, PROJECTS, Repo

UV_TOOLS = Path.home() / ".local" / "share" / "uv" / "tools"
Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[str]"]


def _run(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, check=False)


@dataclass
class InstallState:
    repo: str
    tool: str
    installed: bool
    editable: bool = False
    stale_files: int = 0

    @property
    def stale(self) -> bool:
        return self.installed and not self.editable and self.stale_files > 0

    @property
    def summary(self) -> str:
        if not self.installed:
            return "not installed"
        if self.editable:
            return "editable (always live)"
        return f"{self.stale_files} stale files" if self.stale_files else "current"


def tool_name(repo: Repo) -> str:
    """The uv tool's name: [project].name, which can differ from the directory name."""
    return str(repo.pyproject.get("project", {}).get("name") or repo.name)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_state(repo: Repo, tools_dir: Path = UV_TOOLS) -> InstallState:
    name = tool_name(repo)
    receipt = tools_dir / name / "uv-receipt.toml"
    if not receipt.exists():
        return InstallState(repo.name, name, installed=False)
    if "editable =" in receipt.read_text(encoding="utf-8"):
        return InstallState(repo.name, name, installed=True, editable=True)
    site = next((tools_dir / name / "lib").glob("python*/site-packages"), None)
    src_root = repo.path / "src" if (repo.path / "src").is_dir() else repo.path
    stale = 0
    if site is not None:
        for pkg in repo.packages:
            for src_file in (src_root / pkg).rglob("*.py"):
                installed = site / pkg / src_file.relative_to(src_root / pkg)
                if not installed.exists() or _digest(installed) != _digest(src_file):
                    stale += 1
    return InstallState(repo.name, name, installed=True, stale_files=stale)


def install_states(repos: Sequence[Repo], tools_dir: Path = UV_TOOLS) -> list[InstallState]:
    return [install_state(r, tools_dir) for r in sorted(repos, key=lambda r: r.name)]


@dataclass
class DeployResult:
    repo: str
    ok: bool
    detail: str


def deploy(repo: Repo, run: Runner = _run, projects: Path = PROJECTS) -> DeployResult:
    """Run deploy.sh for one repo. Only repos under local-first/ have the script's layout."""
    workspace = projects / "local-first"
    if repo.path.parent != workspace:
        return DeployResult(repo.name, False, f"not under {workspace}; deploy by hand")
    script = workspace / LIBRARY / "scripts" / "deploy.sh"
    proc = run([str(script), repo.name], workspace)
    lines = [line.strip() for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
    return DeployResult(repo.name, proc.returncode == 0, " | ".join(lines[-3:]))
