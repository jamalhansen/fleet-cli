from fleet_cli.repos import Repo, all_repos, fleet_repos


def test_fleet_is_local_first_python_repos_plus_top_level_library_users(projects):
    assert [r.name for r in fleet_repos(projects)] == [
        "drifty-tool", "good-tool", "local-first-common", "top-level-tool",
    ]


def test_all_repos_includes_non_python(projects):
    assert "blog" in [r.name for r in all_repos(projects)]


def test_manifest_facts(projects):
    r = Repo(projects / "local-first" / "good-tool")
    assert r.description == "A tool"
    assert r.dependencies == ["local-first-common", "rich"]
    assert r.entry_points == {"good-tool": "good_tool.cli:app"}
    assert r.depends_on_library
    assert r.packages == ["good_tool"]
    assert r.remote == "git@github.com:j/good-tool.git"
    assert r.last_commit[1] == "first commit"


def test_source_files_skip_tests(projects):
    r = Repo(projects / "local-first" / "good-tool")
    assert all("tests" not in p.parts for p in r.source_files)
    assert len(r.test_files) == 1


def test_path_dependencies_exclude_library(projects):
    assert Repo(projects / "local-first" / "drifty-tool").path_dependencies == []
