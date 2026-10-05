"""Which repos exist, which of them are the fleet, and what their manifests say."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

PROJECTS = Path.home() / "projects"
LIBRARY = "local-first-common"
EXCLUDE = frozenset({"local-ai-tool-template", "claude-skills", "jamalhansen.com"})
_SKIP_DIRS = frozenset({".venv", "venv", "tests", "test", "build", "dist", "node_modules", "__pycache__", ".git"})


def _requirement_name(spec: str) -> str:
    return re.split(r"[<>=!~;\[\s@]", spec, maxsplit=1)[0].strip().lower()


@dataclass(frozen=True)
class Repo:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @cached_property
    def pyproject(self) -> dict:
        f = self.path / "pyproject.toml"
        return tomllib.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    @cached_property
    def package_json(self) -> dict:
        f = self.path / "package.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    @property
    def is_python(self) -> bool:
        return bool(self.pyproject)

    @property
    def is_library(self) -> bool:
        return self.name == LIBRARY

    @property
    def description(self) -> str:
        return self.pyproject.get("project", {}).get("description") or self.package_json.get("description") or ""

    @property
    def dependencies(self) -> list[str]:
        if self.is_python:
            return [_requirement_name(d) for d in self.pyproject.get("project", {}).get("dependencies", [])]
        return sorted(self.package_json.get("dependencies", {}))

    @property
    def entry_points(self) -> dict[str, str]:
        if self.is_python:
            return dict(self.pyproject.get("project", {}).get("scripts", {}))
        bin_ = self.package_json.get("bin", {})
        return {self.name: bin_} if isinstance(bin_, str) else dict(bin_)

    @property
    def uv_sources(self) -> dict[str, dict]:
        return self.pyproject.get("tool", {}).get("uv", {}).get("sources", {})

    @property
    def depends_on_library(self) -> bool:
        return LIBRARY in self.dependencies or LIBRARY in self.uv_sources

    @property
    def path_dependencies(self) -> list[str]:
        """Sibling repos pulled in as local path dependencies (other than the library)."""
        return sorted(
            name for name, src in self.uv_sources.items() if isinstance(src, dict) and "path" in src and name != LIBRARY
        )

    @cached_property
    def packages(self) -> list[str]:
        """Top-level importable package names the repo ships."""
        root = self.path / "src" if (self.path / "src").is_dir() else self.path
        return sorted(p.parent.name for p in root.glob("*/__init__.py") if p.parent.name not in _SKIP_DIRS)

    @cached_property
    def source_files(self) -> list[Path]:
        return sorted(
            p for p in self.path.rglob("*.py") if not _SKIP_DIRS.intersection(p.relative_to(self.path).parts[:-1])
        )

    @cached_property
    def source_text(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in self.source_files)

    @property
    def test_files(self) -> list[Path]:
        tests = self.path / "tests"
        return sorted(tests.rglob("test_*.py")) if tests.is_dir() else []

    def git(self, *args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    @cached_property
    def remote(self) -> str:
        return self.git("remote", "get-url", "origin") or self.git("remote")

    @cached_property
    def last_commit(self) -> tuple[str, str]:
        """(YYYY-MM-DD, subject), or ("", "") for a repo with no commits."""
        out = self.git("log", "-1", "--format=%cs%x09%s")
        date, _, subject = out.partition("\t")
        return date, subject


def all_repos(root: Path = PROJECTS) -> list[Repo]:
    """Every git repo directly under root and under root/local-first."""
    dirs = [*root.glob("*/.git"), *(root / "local-first").glob("*/.git")]
    return sorted((Repo(d.parent) for d in dirs), key=lambda r: r.name)


def fleet_repos(root: Path = PROJECTS) -> list[Repo]:
    """Python repos in local-first/, plus top-level ones that depend on the library.

    Same rule repo-health-run uses. Top-level tool repos stay where they are because
    vault hooks (synced to other machines) call them by path.
    """

    def ok(p: Path) -> bool:
        return p.is_dir() and (p / "pyproject.toml").exists() and p.name not in EXCLUDE

    repos = [Repo(p) for p in (root / "local-first").iterdir() if ok(p)]
    repos += [r for r in (Repo(p) for p in root.iterdir() if ok(p)) if r.depends_on_library]
    return sorted(repos, key=lambda r: r.name)
