"""fleet deploy: the same stale-install rule as repo-health-run, and the deploy call."""

import shutil
from pathlib import Path

from fleet_cli import deploy as dp
from fleet_cli.repos import Repo


def _install(tools: Path, repo: Repo, editable: bool = False) -> Path:
    """Fake a uv tool install of repo: a receipt plus a byte copy of its package into site-packages."""
    name = dp.tool_name(repo)
    tool = tools / name
    (tool / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    src = repo.path / "src"
    for pkg in repo.packages:
        shutil.copytree(src / pkg, tool / "lib" / "python3.12" / "site-packages" / pkg)
    receipt = f'[tool]\nrequirements = [{{ name = "{name}", directory = "{repo.path}"{", editable = true" if editable else ""} }}]\n'
    (tool / "uv-receipt.toml").write_text(receipt)
    return tool / "lib" / "python3.12" / "site-packages"


def test_not_installed_is_fine(projects, tmp_path):
    st = dp.install_state(Repo(projects / "local-first" / "good-tool"), tmp_path / "tools")
    assert (st.installed, st.stale, st.summary) == (False, False, "not installed")


def test_editable_install_is_always_live(projects, tmp_path):
    repo = Repo(projects / "local-first" / "good-tool")
    _install(tmp_path / "tools", repo, editable=True)
    (repo.path / "src" / "good_tool" / "cli.py").write_text("# changed\n")
    st = dp.install_state(repo, tmp_path / "tools")
    assert st.editable and not st.stale and st.summary == "editable (always live)"


def test_identical_install_is_current_and_changed_or_missing_files_count_as_stale(projects, tmp_path):
    repo = Repo(projects / "local-first" / "good-tool")
    site = _install(tmp_path / "tools", repo)
    assert dp.install_state(repo, tmp_path / "tools").summary == "current"
    (repo.path / "src" / "good_tool" / "cli.py").write_text("# a fix committed after the install\n")
    (repo.path / "src" / "good_tool" / "new_module.py").write_text("x = 1\n")
    st = dp.install_state(repo, tmp_path / "tools")
    assert st.stale and st.stale_files == 2 and st.summary == "2 stale files"
    assert (site / "good_tool" / "cli.py").exists()  # the install itself is untouched by checking


def test_tool_name_comes_from_pyproject_not_the_directory(projects):
    repo = Repo(projects / "local-first" / "good-tool")
    (repo.path / "pyproject.toml").write_text('[project]\nname = "renamed-tool"\ndependencies = []\n')
    assert dp.tool_name(Repo(repo.path)) == "renamed-tool"


def test_deploy_runs_the_library_script_from_the_workspace(projects):
    calls = []

    def fake_run(cmd, cwd):
        calls.append((list(cmd), cwd))
        import subprocess

        return subprocess.CompletedProcess(
            cmd, 0, "reinstalled uv tool: good-tool\nsmoke ok: import good_tool.cli\n", ""
        )

    r = dp.deploy(Repo(projects / "local-first" / "good-tool"), run=fake_run, projects=projects)
    assert r.ok and r.detail == "reinstalled uv tool: good-tool | smoke ok: import good_tool.cli"
    assert calls[0][0] == [str(projects / "local-first" / "local-first-common" / "scripts" / "deploy.sh"), "good-tool"]
    assert calls[0][1] == projects / "local-first"


def test_deploy_refuses_a_repo_outside_the_workspace(projects):
    r = dp.deploy(Repo(projects / "top-level-tool"), run=lambda c, d: None, projects=projects)  # type: ignore[arg-type,return-value]
    assert not r.ok and "deploy by hand" in r.detail
