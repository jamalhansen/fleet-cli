"""fleet: one front door for the local-first repos and the data they keep."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from local_first_common.cli import dry_run_option, json_option
from local_first_common.tracking import register_tool, timed_run
from rich.console import Console
from rich.table import Table

from fleet_cli import audit as audit_mod
from fleet_cli import catalog as catalog_mod
from fleet_cli import data as data_mod
from fleet_cli import status as status_mod
from fleet_cli.repos import PROJECTS, all_repos, fleet_repos

TOOL = "fleet"
_TOOL = register_tool(TOOL)

app = typer.Typer(help=__doc__, no_args_is_help=True)
console = Console()

_MARK = {"pass": "[green]✓[/green]", "fail": "[red]✗[/red]", "skip": "[dim]–[/dim]", "info": "[yellow]·[/yellow]"}
_LEVEL = {"ok": "[green]ok[/green]", "warn": "[yellow]warn[/yellow]", "fail": "[red]fail[/red]"}


def _print_json(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@app.command()
def repos(
    all_: Annotated[bool, typer.Option("--all", "-a", help="Every git repo, not just the fleet.")] = False,
    as_json: Annotated[bool, json_option()] = False,
):
    """List fleet repos: Python repos in local-first/ plus top-level ones using local-first-common."""
    with timed_run(TOOL, None, source_location="repos") as run:
        found = all_repos() if all_ else fleet_repos()
        run.item_count = len(found)
    rows = [{"name": r.name, "location": str(r.path.relative_to(PROJECTS)), "remote": r.remote} for r in found]
    if as_json:
        _print_json(rows)
        return
    table = Table("repo", "location", "remote")
    for row in rows:
        table.add_row(row["name"], row["location"], row["remote"] or "[red]none[/red]")
    console.print(table)
    console.print(f"[dim]{len(rows)} repos[/dim]")


@app.command()
def audit(
    repo: Annotated[list[str] | None, typer.Option("--repo", "-r", help="Only these repos.")] = None,
    check: Annotated[
        list[str] | None,
        typer.Option("--check", "-c", help=f"Only these checks: {', '.join(audit_mod.CHECKS)}."),
    ] = None,
    failures_only: Annotated[bool, typer.Option("--failures", "-f", help="Only list failing checks.")] = False,
    as_json: Annotated[bool, json_option()] = False,
):
    """Check every fleet repo against STANDARDS.md. Exits 1 if any check fails."""
    unknown = set(check or []) - set(audit_mod.CHECKS)
    if unknown:
        raise typer.BadParameter(f"unknown check(s): {', '.join(sorted(unknown))}", param_hint="--check")
    with timed_run(TOOL, None, source_location="audit") as run:
        targets = [r for r in fleet_repos() if not repo or r.name in repo]
        results = audit_mod.audit(targets, set(check) if check else None)
        run.item_count = len(targets)
    n_fail = audit_mod.failures(results)

    if as_json:
        _print_json({name: [f.to_dict() for f in fs] for name, fs in results.items()})
    elif failures_only:
        for name, fs in results.items():
            for f in fs:
                if f.status == "fail":
                    console.print(f"{_MARK['fail']} {name} [bold]{f.check}[/bold]: {f.detail}", soft_wrap=True)
    else:
        names = [f.check for f in next(iter(results.values()), [])]
        table = Table("repo", *names)
        for name, fs in results.items():
            table.add_row(name, *(_MARK[f.status] for f in fs))
        console.print(table)
        console.print(
            f"[dim]{_MARK['fail']} fail  {_MARK['info']} info  {_MARK['skip']} n/a — "
            f"`fleet audit --failures` for details[/dim]"
        )
    if not as_json:
        console.print(f"{len(results)} repos, {n_fail} failing checks")
    if n_fail:
        raise typer.Exit(1)


@app.command()
def catalog(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write here (existing **Note lines are kept). Default: stdout."),
    ] = None,
    dry_run: Annotated[bool, dry_run_option()] = False,
    as_json: Annotated[bool, json_option()] = False,
):
    """Generate the repo catalog from manifests and an import scan."""
    with timed_run(TOOL, None, source_location="catalog") as run:
        entries = catalog_mod.build_entries(all_repos())
        run.item_count = len(entries)
    if as_json:
        _print_json([e.to_dict() for e in entries])
        return
    existing = output.read_text(encoding="utf-8") if output and output.exists() else ""
    notes = catalog_mod.extract_notes(existing)
    text = catalog_mod.render_markdown(entries, notes, datetime.now().astimezone().date())
    if output is None:
        typer.echo(text)
    elif dry_run:
        kept = sum(len(v) for v in notes.values())
        console.print(f"[dry-run] would write {len(entries)} repos to {output}, keeping {kept} note lines")
    else:
        output.write_text(text, encoding="utf-8")
        console.print(f"Wrote {len(entries)} repos to {output}")


@app.command()
def data(as_json: Annotated[bool, json_option()] = False):
    """Integrity and backup coverage of every data store. Exits 1 on a failed integrity check."""
    with timed_run(TOOL, None, source_location="data") as run:
        reports = data_mod.check_stores()
        run.item_count = len(reports)
    if as_json:
        _print_json([r.to_dict() for r in reports])
    else:
        table = Table("store", "kind", "size", "modified", "integrity", "in backup", "", "notes")
        for r in reports:
            notes = "\n".join([*(f"[yellow]{w}[/yellow]" for w in r.warnings), *(f"[dim]{i}[/dim]" for i in r.info)])
            table.add_row(
                r.name,
                r.kind,
                f"{r.size_bytes / 1e6:.1f} MB",
                r.modified[:16].replace("T", " "),
                r.integrity,
                r.backup,
                _LEVEL[r.level],
                notes,
            )
        console.print(table)
        latest = data_mod.latest_backup()
        console.print(f"[dim]latest backup: {latest.name if latest else 'none'}[/dim]")
    if any(r.level == "fail" for r in reports):
        raise typer.Exit(1)


@app.command()
def status(as_json: Annotated[bool, json_option()] = False):
    """Repo health, background jobs, degraded tools and data stores in one view. Exits 1 if anything needs attention."""
    with timed_run(TOOL, None, source_location="status") as run:
        summary = {
            "repos": status_mod.repo_health(),
            "jobs": status_mod.jobs(),
            "degraded_tools": status_mod.degraded_tools(),
            "data": status_mod.data_summary(data_mod.check_stores()),
        }
        run.item_count = 1
    ok = status_mod.overall_ok(summary)
    if as_json:
        _print_json({**summary, "ok": ok})
    else:
        _print_status(summary)
    if not ok:
        raise typer.Exit(1)


def _local(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


def _section(title: str, ok: bool, headline: str, problems: dict[str, str | list[str]]) -> None:
    console.print(f"{'[green]✓[/green]' if ok else '[red]✗[/red]'} [bold]{title}[/bold] {headline}")
    for name, why in problems.items():
        console.print(f"    {name}: {', '.join(why) if isinstance(why, list) else why}")


def _print_status(s: dict) -> None:
    repos_ = s["repos"]
    if "error" in repos_:
        _section("repos", False, repos_["error"], {})
    else:
        _section(
            "repos",
            not repos_["unhealthy"],
            f"{repos_['healthy']}/{repos_['total']} healthy (snapshot {_local(repos_['generated_at'])})",
            repos_["unhealthy"],
        )
        if repos_["unpushed"]:
            console.print(
                "    [dim]unpushed: " + ", ".join(f"{k} ({v})" for k, v in repos_["unpushed"].items()) + "[/dim]"
            )
    jobs_ = s["jobs"]
    if "error" in jobs_:
        _section("jobs", False, jobs_["error"], {})
    else:
        _section("jobs", not jobs_["failing"], f"{jobs_['ok']}/{jobs_['total']} on schedule", jobs_["failing"])
    degraded = s["degraded_tools"]
    _section(
        "tools",
        not degraded,
        "none degraded" if not degraded else f"{len(degraded)} degraded",
        {d: "failing most calls" for d in degraded},
    )
    data_ = s["data"]
    _section("data", not data_["problems"], f"{data_['ok']}/{data_['total']} stores ok", data_["problems"])
