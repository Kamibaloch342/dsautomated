# scripts/archive

One-off backfill / repair / probe scripts that already did their job, and starters that something
else replaced. Nothing in the live stack imports or runs them; they are kept for reference.

They find the project by going one folder up from `scripts/`, so from here that path is wrong:
to run one again, move it back to `Automations/scripts/` first (and read it — several write to
`data/automations_unified.db`; back the DB up and stop the placers before running anything that
writes). `backfill_from_archive.py` and `backfill_walmart_zoro.py` also hard-code old home paths.

## Moved here 31 Oct 2026

The first 20: backfills, repairs and probes from the summer.

## Moved here 10 Nov 2026 (48 files, owner's request after the 10 Nov audit)

Each was checked first: no live module, config, LaunchAgent or running process names it.

| Group | Files |
|---|---|
| June DB repairs (16) | `backfill_closing_from_archive`, `backfill_zoro_base`, `fill_ww_earnings`, `ingest_nonwb_orders`, `migrate_unified_db`, `patch_derived`, `rebuild_orders_view`, `recompute_split_costs`, `verify_split_costs`, `verify_sync`, `refresh_order_status`, `sync_ship_from_log`, `tag_manual_review`, `tag_placed_via`, `update_order`, `review_manual_orders` |
| August backfills and probes (9) | `fetch_account_earnings`, `fetch_account_tracking`, `fetch_account_zoro`, `fetch_orders_earnings`, `fetch_orders_zoro`, `fetch_grainger_costs`, `detect_mislabeled_wb`, `dump_confirm_shipment`, `dump_zoro_pdp` |
| Sep–Oct Zoro Excel reports (10) | `generate_zoro_returns_excel`, `generate_all_accounts_zoro_returns_excel`, `generate_individual_account_excels`, `generate_single_workbook_with_top_summaries`, `generate_zoro_tax_refund_excels`, `generate_zoro_tax_refund_full_2026`, `update_individual_workbooks`, `generate_undelivered_orders_excel`, `fetch_salesmercentile_returns`, `fetch_all_accounts_returns` |
| November one-offs (3) | `backfill_emails`, `rename_labels_to_kamran`, `whatsapp_backlog` |
| Replaced starters (10) | `dashboard_keeper`, `install_mac_autostart`, `localtunnel.sh`, `public_tunnel.sh`, `start_tunnel.sh`, `setup_shared_dashboard.sh` (the cloud agent keeps the dashboard and the tunnels up now), `install_zoro_schedule.sh`, `populate_queue`, `sync_queue`, `run_repricer_check` (the dashboard has its own repricer schedule) |

Not moved, although they were on the same list: `start_dashboard.py`, `dashboard_watchdog.py` and
`install_dashboard_autostart.py`. They are the Windows PC's dashboard starter — `travel_mode.py`
still tells the Windows user to run the first, and a Scheduled Task there may run the second.
Move them once the Windows PC no longer runs the dashboard.

`localtunnel.sh` and `start_tunnel.sh` used to carry the team dashboard password as a fallback
value. It was taken out on 10 Nov 2026; it is still in the git history, so that password has to be
changed.
