# scripts/archive

One-off backfill / repair / probe scripts that already did their job. Nothing in the live stack
imports or runs them; they are kept for reference (moved here 31 Oct 2026).

They find the project by going one folder up from `scripts/`, so from here that path is wrong:
to run one again, move it back to `Automations/scripts/` first (and read it — several write to
`data/automations_unified.db`; back the DB up and stop the placers before running anything that
writes). `backfill_from_archive.py` and `backfill_walmart_zoro.py` also hard-code old home paths.
