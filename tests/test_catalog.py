from datetime import date

from fleet_cli.catalog import (
    build_entries,
    extract_notes,
    library_module_names,
    library_modules_used,
    render_markdown,
)
from fleet_cli.repos import all_repos


def test_library_module_names(projects):
    assert library_module_names(projects / "local-first" / "local-first-common") == {
        "obsidian", "tracking", "providers",
    }


def test_library_modules_used_handles_dotted_and_from_imports():
    source = (
        "from local_first_common.tracking import register_tool\n"
        "from local_first_common import obsidian, not_a_module\n"
        "from local_first_common import (\n    text,\n    cli,\n)\n"
        "import local_first_common.providers.base\n"
    )
    known = {"obsidian", "tracking", "providers", "text", "cli"}
    assert library_modules_used(source, known) == ["cli", "obsidian", "providers", "text", "tracking"]


def test_entries_and_couplings(projects):
    entries = {e.name: e for e in build_entries(all_repos(projects), projects)}
    assert entries["good-tool"].library_modules == ["obsidian", "tracking"]
    assert entries["good-tool"].dependencies == ["rich"]
    assert entries["drifty-tool"].couplings == ["good-tool"]
    assert entries["good-tool"].couplings == []
    assert entries["blog"].kind == "other"


def test_extract_notes_only_keeps_note_lines_under_their_repo():
    md = (
        "## Group\n\n### alpha\n**Purpose:** x\n**Note (verified):** keep me\n\n"
        "### beta\n**Note:** beta note\n\n## Cross-repo\n**Note:** not a repo\n"
    )
    assert extract_notes(md) == {"alpha": ["**Note (verified):** keep me"], "beta": ["**Note:** beta note"]}


def test_render_carries_notes_and_groups(projects):
    entries = build_entries(all_repos(projects), projects)
    text = render_markdown(entries, {"good-tool": ["**Note:** hand-verified"]}, date(2026, 9, 25))
    assert "Generated: 2026-09-25. 5 repos." in text
    assert text.index("## Shared library") < text.index("## Top-level repos") < text.index("## `local-first/` repos")
    good = text[text.index("### good-tool"):]
    assert good.split("\n\n")[0].endswith("**Note:** hand-verified")
    assert "- drifty-tool → good-tool" in text
    assert "**Remote:** **none**" in text
