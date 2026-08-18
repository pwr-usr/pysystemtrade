# CLAUDE.md

Fork of [pysystemtrade](https://github.com/robcarver17/pysystemtrade) whose
purpose is backtesting/trading **Chinese futures only**, sourced from Tushare.
It is deliberately optimized as a small personal production fork while
preserving the repository's native storage and simulation interfaces.

## Environment

- Python via **uv** (`uv run python …`, `uv run pytest …`); deps in
  `pyproject.toml`, Tushare SDK is the `tushare` extra. Format with
  `uv run black` (pinned 23.11.0).
- Storage config belongs in `private/private_config.yaml`; use the standard
  `parquet_store` and Mongo keys rather than adding source-specific storage.
- Tushare credentials may come from `TUSHARE_TOKEN` or the private-config
  `tushare_token` key. Never print or commit them.
- Tests: `uv run pytest -q`; add `PYSYSTEMTRADE_RUN_MONGO_TESTS=1` to include
  the storage workflow tests (needs mongo up).

## Tushare integration — context per part

| Part | Read |
|---|---|
| Architecture, design decisions (FINAL=close, CNH FX, naming, roll methodology), all CLI entry points and workflows, failure model | [docs/tushare_chinese_futures.md](docs/tushare_chinese_futures.md) |
| Inspecting every data stage (vendor API → catalogue → parquet/mongo → calendars → multiple/adjusted → sim), with runnable snippets and health checks | [docs/tushare_data_inspection.md](docs/tushare_data_inspection.md) |
| Historical evidence behind the currently accepted roll parameters | `private/evidence/tushare_roll_evidence.csv` |
| Upstream data/production concepts (storage objects, roll calendars, sim) | [docs/data.md](docs/data.md), [docs/production.md](docs/production.md) |

Key entry points: seed `python -m sysinit.futures.seed_price_data_from_tushare`,
historical rebuild `python -m sysinit.futures.rebuild_tushare_multiple_adjusted`,
daily collection `python -m sysproduction.update_tushare_futures`, daily
multiple/adjusted update `python -m sysproduction.run_daily_update_multiple_adjusted_prices`,
and live rolls `python -m sysproduction.interactive_update_roll_status`.

## Conventions to preserve

- Tushare is a **vendor** (`sysdata/tushare/`), never a broker; the broker
  seam stays free for a future real CN execution broker.
- FINAL is the traded close, never settlement. Daily rows stamp at 23:00
  naive. Instruments are `EXCHANGE_CODE` (`SHFE_CU`), Currency=CNH.
- `rollconfig.csv` is manually maintained policy. Build a calendar for one
  instrument into a temporary directory with the native builder, inspect and
  validate it, then deliberately replace the reviewed CSV. Never generate or
  replace every calendar automatically.
- Calendar CSVs bootstrap historical multiple prices. Daily updates and live
  rolls take current/forward/carry identities from the final multiple-price row;
  live rolling does not update the calendar CSV.
- Keep the config invariants green (`sysdata/tests/test_tushare_futures_config.py`)
  when touching the manifest or csvconfig rows.
