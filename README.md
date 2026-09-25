# fleet-cli

One front door for the local-first repos and the data they keep.

```bash
uv tool install ~/projects/local-first/fleet-cli
fleet status      # repo health, launchd jobs, degraded tools, data stores; exit 1 if anything needs attention
fleet audit       # every fleet repo against local-first-common/STANDARDS.md; exit 1 on failures
fleet audit --failures            # just the failing checks, with details
fleet audit -r vault-tools -c tracking
fleet data        # SQLite/DuckDB integrity + whether each store is in the latest iCloud backup
fleet catalog -o ~/projects/repo-catalog.md   # regenerate the catalog; **Note lines are kept
fleet repos [--all]
```

Every command takes `--json`.

## What counts as the fleet

Python repos in `~/projects/local-first/`, plus top-level `~/projects/*` repos that depend on
local-first-common (vault-tools, vault-log, vault-semantic-search, resume-manager). The top-level
ones stay where they are because vault hooks, synced to other machines, call them by path.
`personal-infra/scripts/repo-health-run` uses the same rule.

## Audit checks

| check | passes when |
|---|---|
| tracking | source calls `register_tool()` |
| entry-points | every `[project.scripts]` target is `<pkg>.cli:app` (or `<pkg>.server:main` for a service) |
| layout | `src/<package>/` layout |
| no-logic-py | no module named `logic.py` |
| portable-source | local-first-common comes from git, not a local path |
| tests | `tests/test_*.py` exist |
| remote | the repo has a git remote |
| json-flag | (info only) the CLI offers `--json` |

A deliberate exception is documented in the repo itself and reported as n/a with the reason:

```toml
[tool.fleet.exempt]
tracking = "read-only dashboard over the tracking DB"
```

## Data stores

`fleet data` checks every `*.db`/`*.duckdb` under `~/sync` (the same set `backup-local-first`
copies), each vault under `~/vaults`, and `EXTRA_STORES` in `data.py` for data that lives
elsewhere (pebble's journal in `~/Documents/pebble`). A store warns when it's missing from the
latest backup or the latest backup is more than 17 days old, and fails when it can't be read.
A DuckDB file another process holds open reports `in use`, not a failure.

## status

Reads what other tools already produce: `~/sync/local-first/repo-health-latest.json`
(from `repo-health-run`), `job-health --json`, and process-doctor's
`process-doctor-data-health-state.json`, plus `fleet data`.
