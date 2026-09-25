"""Mechanical checks of each repo against local-first-common/STANDARDS.md."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

from fleet_cli.repos import LIBRARY, Repo

Status = Literal["pass", "fail", "skip", "info"]
_REGISTER_CALL = re.compile(r"\bregister_tool\s*\(")


@dataclass(frozen=True)
class Finding:
    check: str
    status: Status
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def check_tracking(repo: Repo) -> Finding:
    if repo.is_library:
        return Finding("tracking", "skip", "the library itself")
    # A regex, not the literal call text: the fleet's pre-commit scan counts files containing it.
    files = [
        str(f.relative_to(repo.path)) for f in repo.source_files
        if _REGISTER_CALL.search(f.read_text(encoding="utf-8", errors="ignore"))
    ]
    if len(files) > 1:
        return Finding("tracking", "fail", f"registered in {len(files)} files, must be 1: {', '.join(files)}")
    if files:
        return Finding("tracking", "pass")
    extra = " (timed_run alone doesn't register the tool)" if "timed_run(" in repo.source_text else ""
    return Finding("tracking", "fail", f"never calls register_tool{extra}")


def check_entry_points(repo: Repo) -> Finding:
    if not repo.entry_points:
        return Finding("entry-points", "skip", "no [project.scripts]")
    off = [
        f"{k} = {v}" for k, v in repo.entry_points.items()
        if not v.endswith((".cli:app", ".server:main"))  # server:main = a long-running service
    ]
    if off:
        return Finding("entry-points", "fail", "not <pkg>.cli:app: " + ", ".join(off))
    return Finding("entry-points", "pass")


def check_layout(repo: Repo) -> Finding:
    if (repo.path / "src").is_dir():
        return Finding("layout", "pass")
    return Finding("layout", "fail", "flat package layout; standard is src/<package>/")


def check_no_logic_py(repo: Repo) -> Finding:
    hits = [str(p.relative_to(repo.path)) for p in repo.source_files if p.name == "logic.py"]
    if hits:
        return Finding("no-logic-py", "fail", "deprecated module name: " + ", ".join(hits))
    return Finding("no-logic-py", "pass")


def check_portable_source(repo: Repo) -> Finding:
    if repo.is_library or not repo.depends_on_library:
        return Finding("portable-source", "skip")
    src = repo.uv_sources.get(LIBRARY, {})
    if isinstance(src, dict) and "path" in src:
        return Finding("portable-source", "fail", f"{LIBRARY} is a local path source; run `make use-github`")
    return Finding("portable-source", "pass")


def check_tests(repo: Repo) -> Finding:
    n = len(repo.test_files)
    return Finding("tests", "pass", f"{n} files") if n else Finding("tests", "fail", "no tests/test_*.py")


def check_remote(repo: Repo) -> Finding:
    return Finding("remote", "pass") if repo.remote else Finding("remote", "fail", "no git remote")


def check_json_flag(repo: Repo) -> Finding:
    if not repo.entry_points:
        return Finding("json-flag", "skip")
    text = repo.source_text
    if "--json" in text or "json_option(" in text:
        return Finding("json-flag", "pass")
    return Finding("json-flag", "info", "no --json output")


CHECKS: dict[str, Callable[[Repo], Finding]] = {
    "tracking": check_tracking,
    "entry-points": check_entry_points,
    "layout": check_layout,
    "no-logic-py": check_no_logic_py,
    "portable-source": check_portable_source,
    "tests": check_tests,
    "remote": check_remote,
    "json-flag": check_json_flag,
}


def exemptions(repo: Repo) -> dict[str, str]:
    """``[tool.fleet.exempt]`` in the repo's pyproject: check name -> documented reason."""
    return repo.pyproject.get("tool", {}).get("fleet", {}).get("exempt", {})


def audit_repo(repo: Repo, only: set[str] | None = None) -> list[Finding]:
    exempt = exemptions(repo)
    return [
        Finding(name, "skip", f"exempt: {exempt[name]}") if name in exempt else check(repo)
        for name, check in CHECKS.items()
        if only is None or name in only
    ]


def audit(repos: list[Repo], only: set[str] | None = None) -> dict[str, list[Finding]]:
    return {r.name: audit_repo(r, only) for r in repos}


def failures(results: dict[str, list[Finding]]) -> int:
    return sum(f.status == "fail" for findings in results.values() for f in findings)
