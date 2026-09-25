"""Shared fixtures: a throwaway ~/projects tree with small fake repos."""

import subprocess
from pathlib import Path

import pytest
from local_first_common.testing import isolate_tracking_db  # noqa: F401

LIB_PYPROJECT = """\
[project]
name = "local-first-common"
description = "Shared library"
dependencies = ["duckdb>=1", "typer"]
[project.scripts]
local-first = "local_first_common.cli:app"
"""

TOOL_PYPROJECT = """\
[project]
name = "{name}"
description = "{description}"
dependencies = ["local-first-common", "rich>=14"]
[project.scripts]
{name} = "{entry}"
[tool.uv.sources]
local-first-common = {source}
{extra}
"""

GIT_SOURCE = '{ git = "https://github.com/jamalhansen/local-first-common.git", branch = "main" }'


def make_repo(root: Path, rel: str, files: dict[str, str], remote: str | None = None) -> Path:
    path = root / rel
    for name, text in files.items():
        f = path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run([*git, "add", "-A"], cwd=path, check=True)
    subprocess.run([*git, "commit", "-q", "--no-verify", "-m", "first commit"], cwd=path, check=True)
    if remote:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=path, check=True)
    return path


def tool_files(name: str, pkg: str, *, entry: str | None = None, source: str = GIT_SOURCE,
               extra: str = "", description: str = "A tool", cli: str | None = None) -> dict[str, str]:
    return {
        "pyproject.toml": TOOL_PYPROJECT.format(
            name=name, description=description, entry=entry or f"{pkg}.cli:app", source=source, extra=extra
        ),
        f"src/{pkg}/__init__.py": "",
        f"src/{pkg}/cli.py": cli if cli is not None else (
            "from local_first_common.tracking import register_tool\n"
            "from local_first_common import obsidian\n"
            f'_TOOL = register_tool("{name}")\n'
            'JSON_FLAG = "--json"\n'
        ),
        "tests/test_it.py": "def test_ok():\n    assert True\n",
    }


@pytest.fixture
def projects(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    make_repo(root, "local-first/local-first-common", {
        "pyproject.toml": LIB_PYPROJECT,
        "src/local_first_common/__init__.py": "",
        "src/local_first_common/obsidian.py": "",
        "src/local_first_common/tracking.py": "",
        "src/local_first_common/providers/__init__.py": "",
        "tests/test_lib.py": "",
    }, remote="git@github.com:j/local-first-common.git")
    make_repo(root, "local-first/good-tool", tool_files("good-tool", "good_tool"),
              remote="git@github.com:j/good-tool.git")
    make_repo(root, "local-first/drifty-tool", {
        **tool_files(
            "drifty-tool", "drifty",
            entry="drifty.main:main",
            source='{ path = "../local-first-common", editable = true }',
            cli="from local_first_common.tracking import timed_run\nimport good_tool\n\nwith timed_run('d', None):\n    pass\n",
        ),
        "src/drifty/logic.py": "from .cli import *\n",
    })
    make_repo(root, "top-level-tool", {
        "pyproject.toml": TOOL_PYPROJECT.format(
            name="top-level-tool", description="Flat", entry="top_level.cli:main",
            source=GIT_SOURCE, extra='[tool.fleet.exempt]\nlayout = "hooks call it by path"',
        ),
        "top_level/__init__.py": "",
        "top_level/cli.py": 'from local_first_common.tracking import register_tool\n_T = register_tool("t")\n',
    }, remote="git@github.com:j/top-level-tool.git")
    make_repo(root, "blog", {"README.md": "not python"}, remote="git@github.com:j/blog.git")
    return root
