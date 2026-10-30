# Project Rules — Dropshipping Automation (HOANK STORE + Hathoro)

> Rewritten 26 September 2026. The `Final Automation/` stack, `hoank_tracker.db` and
> `unified_tracker.py` this file used to describe no longer exist. `Automations/` ("ZUHRA
> Software") is the live production stack. **Read `AGENTS.md` at the repository root first** —
> it is the authoritative guide — then `Automations/automations_complete_documentation.md`
> Appendix F (the NEVER-DO rules) before changing anything under `Automations/`.

## Architecture (current)
- Live code lives in `Automations/` (engine/, checkout/, platforms/, utils/, scripts/, dashboard/, gui/).
- Master data: `Automations/data/automations_unified.db` (SQLite, WAL); email archive:
  `Automations/data/gmail_archive.db`; team-edited Google Sheet for SKU map / coupons / priority.
- Account and inbox lists come from `Automations/config/accounts.json` only (never hardcode).
- Tokens live in `Automations/data/All_Tokens/` (git-ignored). Never commit secrets, databases or logs.

## Data conventions
- Zoro order ids start with `WB` (phone orders `SO`); Amazon `merchant-order-id` carries the WB id
  or a label (`OOS`, `Discontinued`, `Loss`, `Duplicate`, `20`, …).
- FedEx tracking ids are 12 digits, UPS ids start with `1Z`. Marketplace `ATVPDKIKX0DER`.
- Labels: `Completed` / `Loss` / `Refunded` / `Cancelled` / `Incomplete` (see `closing.py determine_label`).

## Working rules
- Inspect the DB read-only; back up before any purge/VACUUM; placers stopped for VACUUM.
- Never touch a running placer's Chrome (ports 9221–9224).
- Do not weaken the placement lock, the fail-closed duplicate check, the Sheets loads, the
  coupon/priority rules, the label criteria or the messaging guards.
- Prefer incremental work: closing processes only new/changed orders, syncs are incremental —
  keep it that way.
