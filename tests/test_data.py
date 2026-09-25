import sqlite3
from datetime import date
from pathlib import Path

import duckdb
import pytest

from fleet_cli.data import check_stores, detect_kind, latest_backup

TODAY = date(2026, 9, 25)


@pytest.fixture
def home(tmp_path: Path) -> dict[str, Path]:
    sync, vaults, backups = tmp_path / "sync", tmp_path / "vaults", tmp_path / "backups"
    (sync / "tool").mkdir(parents=True)
    conn = sqlite3.connect(sync / "tool" / "good.db")
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    ddb = duckdb.connect(str(sync / "tool" / "log.duckdb"))
    ddb.execute("CREATE TABLE runs AS SELECT 1 AS id")
    ddb.close()
    (sync / "tool" / "broken.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    (sync / "tool" / "x.sync-conflict-1.db").write_bytes(b"ignored")
    (vaults / "Notes" / "a.md").parent.mkdir(parents=True)
    (vaults / "Notes" / "a.md").write_text("hi")
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "day.md").write_text("entry")
    return {"sync": sync, "vaults": vaults, "backups": backups, "journal": tmp_path / "journal"}


def backup(home: dict[str, Path], day: str, dbs: list[str], vaults: list[str]) -> None:
    root = home["backups"] / day
    for rel in dbs:
        (root / "databases" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "databases" / rel).write_bytes(b"")
    (root / "vaults").mkdir(parents=True, exist_ok=True)
    for v in vaults:
        (root / "vaults" / f"{v}.zip").write_bytes(b"")


def run(home):
    reports = check_stores(home["sync"], home["vaults"], home["backups"],
                           extra={"journal": home["journal"]}, today=TODAY)
    return {r.name: r for r in reports}


def test_detect_kind(home):
    assert detect_kind(home["sync"] / "tool" / "good.db") == "sqlite"
    assert detect_kind(home["sync"] / "tool" / "log.duckdb") == "duckdb"


def test_integrity_and_backup_levels(home):
    backup(home, "2026-09-01", ["tool/good.db"], [])
    backup(home, "2026-09-24", ["tool/good.db", "tool/log.duckdb"], ["Notes"])
    r = run(home)
    assert set(r) == {"tool/good.db", "tool/log.duckdb", "tool/broken.db", "vault:Notes", "journal"}
    assert (r["tool/good.db"].integrity, r["tool/good.db"].backup, r["tool/good.db"].level) == ("ok", "2026-09-24", "ok")
    assert r["tool/log.duckdb"].level == "ok"
    assert r["tool/broken.db"].level == "fail"
    assert r["tool/broken.db"].integrity != "ok"
    assert r["vault:Notes"].level == "ok"
    assert (r["journal"].backup, r["journal"].level) == ("not covered", "warn")


def test_stale_backup_warns(home):
    backup(home, "2026-09-01", ["tool/good.db"], ["Notes"])
    r = run(home)
    assert r["tool/good.db"].backup == "2026-09-01"
    assert r["tool/good.db"].level == "warn"


def test_no_backups_at_all(home):
    r = run(home)
    assert r["tool/good.db"].backup == "missing"
    assert r["tool/good.db"].level == "warn"


def test_missing_extra_store_fails(home, tmp_path):
    reports = check_stores(home["sync"], home["vaults"], home["backups"],
                           extra={"gone": tmp_path / "nope"}, today=TODAY)
    assert {r.name: r.level for r in reports}["gone"] == "fail"


def test_latest_backup_ignores_non_dates(home):
    backup(home, "2026-09-15", [], [])
    (home["backups"] / "notes").mkdir()
    assert latest_backup(home["backups"]).name == "2026-09-15"
    assert latest_backup(home["backups"] / "absent") is None


class TestUnexpectedData:
    def test_rows_lost_since_backup_warn(self, home):
        import shutil

        backup(home, "2026-09-24", [], ["Notes"])
        copy = home["backups"] / "2026-09-24" / "databases" / "tool" / "good.db"
        copy.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(home["sync"] / "tool" / "good.db")
        conn.execute("DELETE FROM t")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(10)])
        conn.commit()
        conn.close()
        shutil.copy2(home["sync"] / "tool" / "good.db", copy)
        conn = sqlite3.connect(home["sync"] / "tool" / "good.db")
        conn.execute("DELETE FROM t WHERE x < 4")
        conn.commit()
        conn.close()
        r = run(home)["tool/good.db"]
        assert r.level == "warn"
        assert r.warnings == ["t: 10 -> 6 rows since backup (-40%)"]

    def test_empty_and_idle_stores(self, home):
        from datetime import UTC, datetime

        conn = sqlite3.connect(home["sync"] / "tool" / "empty.db")
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
        conn.close()
        reports = check_stores(home["sync"], home["vaults"], home["backups"], extra={"journal": home["journal"]},
                               today=TODAY, now=datetime(2027, 1, 1, tzinfo=UTC))
        r = {x.name: x for x in reports}
        assert r["tool/empty.db"].warnings == ["empty: no rows in any table"]
        assert "only 1 row(s)" in r["tool/good.db"].info
        assert any(i.startswith("no writes in") for i in r["tool/good.db"].info)
        assert any(i.startswith("no new files in") for i in r["journal"].info)

    def test_vault_losing_notes_warns(self, home):
        import zipfile

        backup(home, "2026-09-24", [], [])
        with zipfile.ZipFile(home["backups"] / "2026-09-24" / "vaults" / "Notes.zip", "w") as z:
            for i in range(10):
                z.writestr(f"Notes/n{i}.md", "x")
        r = run(home)["vault:Notes"]
        assert r.level == "warn"
        assert r.warnings == ["10 -> 1 notes since backup (-90%)"]
