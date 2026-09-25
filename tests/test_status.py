import json
import stat
from pathlib import Path

from fleet_cli.data import StoreReport
from fleet_cli.status import data_summary, degraded_tools, jobs, overall_ok, repo_health


def _repo(lint=True, tests=True, hooks=True, remote=True, install=True, unpushed=0) -> dict:
    return {
        "lint": {"ok": lint, "error_count": 0 if lint else 3},
        "tests": {"ok": tests, "failed": 0 if tests else 2},
        "hooks": {"ok": hooks},
        "git": {"has_remote": remote, "unpushed": unpushed},
        "install": {"ok": install, "stale_files": 0 if install else 4},
    }


def test_repo_health_reasons(tmp_path: Path):
    f = tmp_path / "rh.json"
    f.write_text(json.dumps({
        "generated_at": "2026-09-25T05:00:00+00:00", "healthy": 1, "total": 3,
        "repos": {"a": _repo(), "b": _repo(lint=False, remote=False), "c": _repo(install=False, unpushed=2)},
    }))
    h = repo_health(f)
    assert h["unhealthy"] == {"b": ["lint (3 errors)", "no remote"], "c": ["installed copy stale (4 files)"]}
    assert h["unpushed"] == {"c": 2}


def test_repo_health_missing_file(tmp_path: Path):
    assert "error" in repo_health(tmp_path / "absent.json")


def test_jobs_parses_job_health_json(tmp_path: Path):
    script = tmp_path / "job-health"
    payload = [
        {"label": "ok-job", "ok": True},
        {"label": "stale-job", "ok": False, "stale": True, "loaded": True, "last_run": "2026-09-01T07:00"},
        {"label": "new-job", "ok": False, "stale": False, "loaded": True, "missing_first_run": True},
        {"label": "bad-exit", "ok": False, "stale": False, "loaded": True, "last_exit_status": 1},
    ]
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(payload)}\nEOF\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    j = jobs(str(script))
    assert (j["ok"], j["total"]) == (1, 4)
    assert j["failing"] == {
        "stale-job": "stale (last run 2026-09-01T07:00)",
        "new-job": "never run",
        "bad-exit": "exit 1",
    }


def test_jobs_reports_a_broken_command(tmp_path: Path):
    assert "error" in jobs(str(tmp_path / "missing"))


def test_degraded_tools(tmp_path: Path):
    f = tmp_path / "state.json"
    f.write_text('["processing_log:x", "fetch_log:y"]')
    assert degraded_tools(f) == ["fetch_log:y", "processing_log:x"]
    assert degraded_tools(tmp_path / "absent.json") == []


def test_overall_ok():
    good = {"repos": {"unhealthy": {}}, "jobs": {"failing": {}}, "degraded_tools": [],
            "data": data_summary([StoreReport("s", "sqlite", "/p", 1, "", "ok", "2026-09-24", "ok")])}
    assert overall_ok(good)
    assert not overall_ok({**good, "degraded_tools": ["x"]})
    assert not overall_ok({**good, "jobs": {"error": "boom"}})
