"""One summary over the fleet's existing health signals.

Reads what other tools already produce rather than re-checking: repo-health-run's
snapshot, `job-health --json`, and process-doctor's degraded-tool state. Adds the
data-store check from fleet_cli.data.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from fleet_cli.data import StoreReport

SYNC = Path.home() / "sync" / "local-first"
REPO_HEALTH_FILE = SYNC / "repo-health-latest.json"
DEGRADED_FILE = SYNC / "process-doctor-data-health-state.json"


def _unhealthy_reasons(r: dict) -> list[str]:
    reasons = []
    if not r["lint"]["ok"]:
        reasons.append(f"lint ({r['lint'].get('error_count', '?')} errors)")
    if not r["tests"]["ok"]:
        reasons.append(f"tests ({r['tests'].get('failed', '?')} failed)")
    if not r["hooks"]["ok"]:
        reasons.append("hooks not installed")
    if not r["git"]["has_remote"]:
        reasons.append("no remote")
    if not r["install"]["ok"]:
        reasons.append(f"installed copy stale ({r['install'].get('stale_files', '?')} files)")
    return reasons


def repo_health(path: Path = REPO_HEALTH_FILE) -> dict:
    if not path.exists():
        return {"error": f"{path} not found; run repo-health-run"}
    snap = json.loads(path.read_text(encoding="utf-8"))
    unhealthy = {
        name: reasons for name, r in snap["repos"].items() if (reasons := _unhealthy_reasons(r))
    }
    unpushed = {name: r["git"]["unpushed"] for name, r in snap["repos"].items() if r["git"]["unpushed"]}
    return {
        "generated_at": snap["generated_at"],
        "healthy": snap["healthy"],
        "total": snap["total"],
        "unhealthy": unhealthy,
        "unpushed": unpushed,
    }


def jobs(command: str | None = None) -> dict:
    exe = command or shutil.which("job-health") or str(Path.home() / "bin" / "job-health")
    try:
        proc = subprocess.run([exe, "--json"], capture_output=True, text=True, timeout=60, check=False)
        results = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"error": f"job-health failed: {e}"}
    failing = {}
    for j in results:
        if j.get("ok"):
            continue
        if j.get("stale"):
            why = f"stale (last run {j.get('last_run') or 'never'})"
        elif not j.get("loaded"):
            why = "not loaded"
        elif j.get("missing_first_run"):
            why = "never run"
        else:
            why = f"exit {j.get('last_exit_status')}"
        failing[j["label"]] = why
    return {"ok": len(results) - len(failing), "total": len(results), "failing": failing}


def degraded_tools(path: Path = DEGRADED_FILE) -> list[str]:
    if not path.exists():
        return []
    return sorted(json.loads(path.read_text(encoding="utf-8")))


def data_summary(reports: list[StoreReport]) -> dict:
    return {
        "ok": sum(r.level == "ok" for r in reports),
        "total": len(reports),
        "problems": {
            r.name: "; ".join([f"{r.level}: integrity {r.integrity}, backup {r.backup}", *r.warnings])
            for r in reports if r.level != "ok"
        },
    }


def overall_ok(summary: dict) -> bool:
    return (
        not summary["repos"].get("unhealthy")
        and "error" not in summary["repos"]
        and not summary["jobs"].get("failing")
        and "error" not in summary["jobs"]
        and not summary["degraded_tools"]
        and not summary["data"]["problems"]
    )
