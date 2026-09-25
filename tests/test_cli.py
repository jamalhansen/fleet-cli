import json

import pytest
from typer.testing import CliRunner

from fleet_cli import cli
from fleet_cli.repos import all_repos, fleet_repos

runner = CliRunner()


@pytest.fixture
def fake_projects(projects, monkeypatch):
    monkeypatch.setattr(cli, "fleet_repos", lambda: fleet_repos(projects))
    monkeypatch.setattr(cli, "all_repos", lambda: all_repos(projects))
    monkeypatch.setattr(cli, "PROJECTS", projects)
    real_build = cli.catalog_mod.build_entries
    monkeypatch.setattr(cli.catalog_mod, "build_entries", lambda repos: real_build(repos, projects))
    return projects


def test_audit_exits_1_on_failures_and_filters(fake_projects):
    result = runner.invoke(cli.app, ["audit", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert {f["check"]: f["status"] for f in data["good-tool"]}["tracking"] == "pass"

    result = runner.invoke(cli.app, ["audit", "--repo", "good-tool"])
    assert result.exit_code == 0


def test_audit_rejects_unknown_check(fake_projects):
    result = runner.invoke(cli.app, ["audit", "--check", "nope"])
    assert result.exit_code != 0
    assert "unknown check" in result.output


def test_catalog_write_keeps_notes_and_dry_run_does_not_write(fake_projects, tmp_path):
    out = tmp_path / "catalog.md"
    out.write_text("### good-tool\n**Note:** checked by hand\n")

    result = runner.invoke(cli.app, ["catalog", "-o", str(out), "--dry-run"])
    assert result.exit_code == 0
    assert out.read_text() == "### good-tool\n**Note:** checked by hand\n"

    result = runner.invoke(cli.app, ["catalog", "-o", str(out)])
    assert result.exit_code == 0
    text = out.read_text()
    assert "**Note:** checked by hand" in text
    assert "### drifty-tool" in text


def test_repos_lists_fleet(fake_projects):
    result = runner.invoke(cli.app, ["repos", "--json"])
    assert [r["name"] for r in json.loads(result.output)] == [
        "drifty-tool", "good-tool", "local-first-common", "top-level-tool",
    ]
