"""Unexpected data: compare each store with its copy in the latest backup.

Integrity checks say a store opens; they can't say it still holds what it held. These
compare against the backup: a table that lost rows (the fleet's stores are append-mostly,
so a drop is worth a look), a table that disappeared, a vault with noticeably fewer
notes. Near-empty and long-unwritten stores are noted as info -- often just an idle
tool, sometimes a pipeline that quietly stopped.
"""
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

import duckdb

STALE_DAYS = 30
NEAR_EMPTY_ROWS = 5
VAULT_DROP_RATIO = 0.02


def table_counts(path: Path, kind: str) -> dict[str, int] | None:
    """Row count per table, opened read-only; None if the store can't be read right now."""
    try:
        if kind == "sqlite":
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                names = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
                return {n: conn.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
            finally:
                conn.close()
        if kind == "duckdb":
            conn = duckdb.connect(str(path), read_only=True)
            try:
                tables = conn.execute(
                    "SELECT table_schema, table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
                ).fetchall()
                return {
                    (t if s == "main" else f"{s}.{t}"): conn.execute(f'SELECT count(*) FROM "{s}"."{t}"').fetchone()[0]
                    for s, t in tables
                }
            finally:
                conn.close()
    except (sqlite3.Error, duckdb.Error, OSError):
        return None
    return None


def compare_tables(now: dict[str, int], before: dict[str, int]) -> list[str]:
    """Warnings for tables that shrank or vanished since the backup."""
    notes = []
    for table, then in sorted(before.items()):
        if table not in now:
            notes.append(f"table {table} is gone (had {then} rows in backup)")
        elif now[table] < then:
            pct = (then - now[table]) / then * 100 if then else 0
            notes.append(f"{table}: {then} -> {now[table]} rows since backup (-{pct:.0f}%)")
    return notes


def database_notes(path: Path, kind: str, backup_copy: Path | None, now: datetime) -> tuple[list[str], list[str]]:
    """(warnings, info) for one database."""
    warnings: list[str] = []
    info: list[str] = []
    counts = table_counts(path, kind)
    if counts is None:
        return warnings, info
    total = sum(counts.values())
    if not counts or total == 0:
        warnings.append("empty: no rows in any table")
    elif total < NEAR_EMPTY_ROWS:
        info.append(f"only {total} row(s)")
    if backup_copy is not None and backup_copy.exists() and backup_copy.stat().st_size > 0:
        before = table_counts(backup_copy, kind)
        if before is not None:
            warnings += compare_tables(counts, before)
    age = (now - datetime.fromtimestamp(path.stat().st_mtime).astimezone()).days
    if age > STALE_DAYS:
        info.append(f"no writes in {age} days")
    return warnings, info


def _note_count_on_disk(vault: Path) -> int:
    return sum(1 for p in vault.rglob("*.md") if ".obsidian" not in p.parts and ".trash" not in p.parts)


def _note_count_in_zip(zip_path: Path) -> int | None:
    try:
        with zipfile.ZipFile(zip_path) as z:
            return sum(
                1 for n in z.namelist()
                if n.endswith(".md") and "/.obsidian/" not in n and "/.trash/" not in n
            )
    except (zipfile.BadZipFile, OSError):
        return None


def vault_notes(vault: Path, backup_zip: Path | None) -> list[str]:
    """Warn when a vault has noticeably fewer notes than its backup."""
    if backup_zip is None or not backup_zip.exists():
        return []
    then = _note_count_in_zip(backup_zip)
    now = _note_count_on_disk(vault)
    if then and now < then * (1 - VAULT_DROP_RATIO):
        return [f"{then} -> {now} notes since backup (-{(then - now) / then:.0%})"]
    return []
