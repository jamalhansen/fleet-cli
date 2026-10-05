"""Are the fleet's data stores readable, and are they in the latest backup?

Stores are the SQLite/DuckDB files under ~/sync, the vaults under ~/vaults, and
EXTRA_STORES (data that lives elsewhere). Backup layout matches
personal-infra/scripts/backup-local-first: <backups>/<YYYY-MM-DD>/{databases,vaults}/.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import duckdb

from fleet_cli.anomalies import STALE_DAYS, database_notes, vault_notes

HOME = Path.home()
SYNC_DIR = HOME / "sync"
VAULTS_DIR = HOME / "vaults"
BACKUP_DIR = HOME / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Backups" / "local-first"
EXTRA_STORES = {"pebble-journal": HOME / "Documents" / "pebble"}
BACKUP_MAX_AGE_DAYS = 17  # backups run on the 1st and 15th; job-health uses the same threshold

Level = Literal["ok", "warn", "fail"]


@dataclass
class StoreReport:
    name: str
    kind: str  # sqlite | duckdb | vault | directory | unknown
    path: str
    size_bytes: int
    modified: str
    integrity: str  # ok | in use | <error>
    backup: str  # "2026-09-15" | "missing" | "not covered"
    level: Level
    warnings: list[str] = field(default_factory=list)  # unexpected data, e.g. rows lost since backup
    info: list[str] = field(default_factory=list)  # worth knowing, not a problem (idle, near-empty)

    def to_dict(self) -> dict:
        return asdict(self)


def discover_databases(sync_dir: Path = SYNC_DIR) -> list[Path]:
    """Same discovery rule as backup-local-first."""
    found = [*sync_dir.glob("**/*.db"), *sync_dir.glob("**/*.duckdb")]
    return sorted(p for p in found if p.is_file() and ".sync-conflict" not in p.name and not p.name.startswith("."))


def detect_kind(path: Path) -> str:
    with path.open("rb") as f:
        header = f.read(16)
    if header.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if header[8:12] == b"DUCK":
        return "duckdb"
    return "unknown"


def check_sqlite(path: Path) -> str:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        return "in use" if "locked" in str(e) else str(e)
    except sqlite3.DatabaseError as e:
        return str(e)
    return "ok" if result == "ok" else result


def check_duckdb(path: Path) -> str:
    try:
        conn = duckdb.connect(str(path), read_only=True)
        try:
            conn.execute("SELECT count(*) FROM duckdb_tables()").fetchone()
        finally:
            conn.close()
    except duckdb.IOException as e:
        return "in use" if "lock" in str(e).lower() else str(e).splitlines()[0]
    except duckdb.Error as e:
        return str(e).splitlines()[0]
    return "ok"


def latest_backup(backup_dir: Path = BACKUP_DIR) -> Path | None:
    dated = []
    for d in backup_dir.glob("????-??-??") if backup_dir.exists() else []:
        try:
            dated.append((date.fromisoformat(d.name), d))
        except ValueError:
            continue
    return max(dated)[1] if dated else None


def _level(integrity: str, backup: str, today: date) -> Level:
    if integrity not in ("ok", "in use"):
        return "fail"
    if backup in ("missing", "not covered"):
        return "warn"
    if (today - date.fromisoformat(backup)).days > BACKUP_MAX_AGE_DAYS:
        return "warn"
    return "ok"


def _with_warnings(report: StoreReport) -> StoreReport:
    if report.warnings and report.level == "ok":
        report.level = "warn"
    return report


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="minutes")


def check_stores(
    sync_dir: Path = SYNC_DIR,
    vaults_dir: Path = VAULTS_DIR,
    backup_dir: Path = BACKUP_DIR,
    extra: dict[str, Path] | None = None,
    today: date | None = None,
    now: datetime | None = None,
) -> list[StoreReport]:
    today = today or datetime.now().astimezone().date()
    now = now or datetime.now().astimezone()
    backup = latest_backup(backup_dir)
    backup_date = backup.name if backup else None
    reports = []

    for db in discover_databases(sync_dir):
        kind = detect_kind(db)
        integrity = {"sqlite": check_sqlite, "duckdb": check_duckdb}.get(kind, lambda _: "unrecognized format")(db)
        in_backup = backup is not None and (backup / "databases" / db.relative_to(sync_dir)).exists()
        status = backup_date if in_backup else "missing"
        report = StoreReport(
            str(db.relative_to(sync_dir)),
            kind,
            str(db),
            db.stat().st_size,
            _mtime(db),
            integrity,
            status,
            _level(integrity, status, today),
        )
        if integrity == "ok":
            copy = backup / "databases" / db.relative_to(sync_dir) if in_backup else None
            report.warnings, report.info = database_notes(db, kind, copy, now)
        reports.append(_with_warnings(report))

    vaults = (
        sorted(d for d in vaults_dir.iterdir() if d.is_dir() and not d.name.startswith("."))
        if vaults_dir.exists()
        else []
    )
    for v in vaults:
        in_backup = backup is not None and (backup / "vaults" / f"{v.name}.zip").exists()
        status = backup_date if in_backup else "missing"
        report = StoreReport(
            f"vault:{v.name}",
            "vault",
            str(v),
            _dir_size(v),
            _mtime(v),
            "ok",
            status,
            _level("ok", status, today),
        )
        report.warnings = vault_notes(v, backup / "vaults" / f"{v.name}.zip" if in_backup else None)
        reports.append(_with_warnings(report))

    for name, path in (EXTRA_STORES if extra is None else extra).items():
        if not path.exists():
            reports.append(StoreReport(name, "directory", str(path), 0, "", "missing", "not covered", "fail"))
            continue
        report = StoreReport(
            name,
            "directory",
            str(path),
            _dir_size(path),
            _mtime(path),
            "ok",
            "not covered",
            _level("ok", "not covered", today),
        )
        newest = max((f.stat().st_mtime for f in path.rglob("*") if f.is_file()), default=None)
        if newest is not None and (age := (now - datetime.fromtimestamp(newest).astimezone()).days) > STALE_DAYS:
            report.info.append(f"no new files in {age} days")
        reports.append(report)
    return reports
