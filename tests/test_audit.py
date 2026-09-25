from fleet_cli.audit import CHECKS, audit, audit_repo, failures
from fleet_cli.repos import Repo


def statuses(repo: Repo) -> dict[str, str]:
    return {f.check: f.status for f in audit_repo(repo)}


def test_conforming_tool_passes_everything(projects):
    s = statuses(Repo(projects / "local-first" / "good-tool"))
    assert set(s.values()) == {"pass"}


def test_drift_is_reported(projects):
    findings = {f.check: f for f in audit_repo(Repo(projects / "local-first" / "drifty-tool"))}
    assert findings["tracking"].status == "fail"
    assert "timed_run alone" in findings["tracking"].detail
    assert findings["entry-points"].status == "fail"
    assert findings["no-logic-py"].status == "fail"
    assert findings["portable-source"].status == "fail"
    assert findings["remote"].status == "fail"
    assert findings["json-flag"].status == "info"


def test_exemption_in_pyproject_turns_a_check_into_skip(projects):
    findings = {f.check: f for f in audit_repo(Repo(projects / "top-level-tool"))}
    assert findings["layout"].status == "skip"
    assert findings["layout"].detail == "exempt: hooks call it by path"
    assert findings["entry-points"].status == "fail"


def test_library_skips_tool_only_checks(projects):
    s = statuses(Repo(projects / "local-first" / "local-first-common"))
    assert s["tracking"] == "skip"
    assert s["portable-source"] == "skip"


def test_server_entry_point_is_allowed(projects, tmp_path):
    from conftest import make_repo, tool_files

    path = make_repo(tmp_path, "svc", tool_files("svc", "svc", entry="svc.server:main"), remote="x")
    assert statuses(Repo(path))["entry-points"] == "pass"


def test_only_filter_and_failure_count(projects):
    repos = [Repo(projects / "local-first" / "drifty-tool")]
    results = audit(repos, only={"tracking", "tests"})
    assert [f.check for f in results["drifty-tool"]] == ["tracking", "tests"]
    assert failures(results) == 1


def test_every_check_is_named_consistently(projects):
    repo = Repo(projects / "local-first" / "good-tool")
    assert all(check(repo).check == name for name, check in CHECKS.items())


def test_registration_in_more_than_one_file_fails(tmp_path):
    from conftest import make_repo, tool_files

    files = tool_files("multi", "multi")
    files["src/multi/other.py"] = 'from local_first_common.tracking import register_tool\n_T = register_tool("multi")\n'
    finding = audit_repo(Repo(make_repo(tmp_path, "multi", files, remote="x")), {"tracking"})[0]
    assert finding.status == "fail"
    assert "registered in 2 files" in finding.detail
