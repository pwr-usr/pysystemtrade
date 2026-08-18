# Tushare Chinese futures data

Related documents:
[inspecting each data stage](/docs/tushare_data_inspection.md) ·
[XTQuant Chinese-futures broker assessment](/docs/xtquant_chinese_futures_assessment.md)

This integration uses [Tushare](https://tushare.pro) as a read-only
historical-data source for Chinese futures across all six exchanges (CFFEX,
DCE, CZCE, SHFE, INE, GFEX). It is a data vendor, not a broker: it is not
registered in `broker_factory_func`, and the broker seat stays free for a real
execution broker later.

Scope: every concrete outright contract Tushare lists (`fut_basic`,
`fut_type=1`), including expired products and the catalogue-only
monthly-average (`*_F`) and TAS families, plus `CNHUSD` spot FX. Daily bars
only. Tushare options, minute bars, and provider-stitched "main" contracts are
out of scope — continuous series are stitched locally from individual
contracts with the repository's own roll calendars.

## What is stored where

| Data | Canonical storage | Why |
|---|---|---|
| Daily concrete-contract prices (`Day@` + merged) | Parquet | The repository's native futures-price store |
| `CNHUSD` spot FX | Parquet | Native `fxPricesData` interface |
| Exact contract expiries and sampling state | MongoDB | Mutable operational metadata, as for any broker source |
| Vendor mapping, specification eras, minimum ticks | `sysdata/tushare/config/futures_instruments.csv` | Reviewed human policy, version controlled |
| Instrument metadata (point size, currency, class) | `data/futures/csvconfig/instrumentconfig.csv` | Repository convention |
| Half-spread costs | `data/futures/csvconfig/spreadcosts.csv` (imported to Mongo for production) | Repository convention |
| Roll policy | `data/futures/csvconfig/rollconfig.csv` + `data/futures/roll_calendars_csv/` | Manually maintained policy and reviewed historical calendars |
| Token | `TUSHARE_TOKEN` env var (or `tushare_token` in `private_config.yaml`) | Never committed, printed, or passed on a command line |

Storage locations come from `private/private_config.yaml` (`parquet_store`,
`mongo_host`, `mongo_port`, `mongo_db`) — the standard repository mechanism.
There is no raw Tushare lake and no duplicate provider-continuous dataset.

For a contract such as SHFE copper September 2026 the store contains:

```text
<parquet_store>/futures_contract_prices/Day@SHFE_CU#20260900.parquet
<parquet_store>/futures_contract_prices/SHFE_CU#20260900.parquet   # merged
```

Tushare is daily-only, so the merged (MIXED) file is byte-identical to the
`Day@` file; it exists because simulation and stitching read merged prices.
Parquet writes go to a same-directory temporary file and are replaced
atomically.

## Instrument identity

- Internal instrument codes are exchange-qualified: `SHFE_CU`, `CFFEX_IF`,
  `CZCE_TA`, `DCE_M`, `INE_SC`, `GFEX_LC`. Renamed products are separate
  instruments linked by the manifest `Predecessor` column (`CZCE_ME` →
  `CZCE_MA`, `CZCE_TC` → `CZCE_ZC`, `CZCE_RO` → `CZCE_OI`, `CZCE_WS` →
  `CZCE_WH`, `CZCE_WT` → `CZCE_PM`, `CZCE_ER` → `CZCE_RI`, `DCE_FB_OLD` →
  `DCE_FB`).
- Contract dates are `YYYYMM00`, taken from `fut_basic.d_month` — never parsed
  from the symbol. This makes the CZCE three-digit convention (`TA405`) and
  special families (`SC2406TAS`, `L2602F`) unambiguous; the symbol is
  cross-checked against `d_month` and any inconsistency aborts the run.
- The DCE fibreboard 2019 respecification is handled with manifest validity
  windows: one vendor contract crossing the boundary is downloaded once and
  sliced into a `DCE_FB_OLD` record (through 2019-11-29, 500-sheet spec) and a
  `DCE_FB` record (from 2019-12-02, new spec). Both are ordinary instruments
  with their own point sizes.
- Four families are `catalog_only` (`DCE_L_F`, `DCE_PP_F`, `DCE_V_F`,
  `INE_SCTAS`): they get price data but never a roll policy.

## Price policy

- `FINAL` is the **traded close**, never the settlement price. Settlement is a
  daily VWAP-like mark used for margining — not a price a backtest can fill
  at. A missing close stays `NaN`; it is deliberately never backfilled with
  settlement. `OPEN`/`HIGH`/`LOW`/`VOLUME` map from the vendor fields
  directly (volume in lots).
- Daily rows are timestamped at the repository's notional close, 23:00 naive
  local, matching every other daily source.
- The repository's price cleaning applies (zero-volume rows dropped, so a
  contract's series starts when it actually trades; zero/negative prices
  dropped; spikes checked on update). One deviation: `ignore_future_prices`
  is disabled, because an evening update must not discard the already-complete
  Chinese session stamped at 23:00.
- FX: `CNHUSD` is the inverted `USDCNH.FXCM` closing bid/ask midpoint
  (offshore CNH as a proxy for onshore CNY), available from 2012-02-18.
  Instruments are configured with `Currency=CNH`, so USD-based accounts
  convert through this series; a CNH/CNY-based account sees FX = 1.

## Manual roll policy

`data/futures/csvconfig/rollconfig.csv` is the source of roll parameters and
is edited manually. The column semantics are the native repository semantics
documented in [data.md](/docs/data.md): held and priced delivery-month cycles,
the desired day relative to approximate expiry, the carry-contract offset,
and the approximate expiry offset. Volume and expiry research may inform a
change, but no command automatically infers or applies policy.

`data/futures/roll_calendars_csv/<INSTRUMENT>.csv` records the reviewed
historical roll timestamps and current/next/carry contract chain. It is a
bootstrap and recovery artifact: it initializes historical multiple prices,
but it is not consulted to choose live rolls after the multiple-price series
exists.

Four reviewed calendars deliberately exclude early dead or disconnected
history. Preserve these lower raw-contract bounds when manually rebuilding
them: `SHFE_RU` from `19990100`, `SHFE_FU` from `20190100`, and `CZCE_SF` and
`CZCE_SM` from `20170100`. Earlier concrete-contract data remains stored; it
is intentionally outside the continuous series.

## Setup

```bash
uv pip install -e '.[tushare]'        # or: pip install -e '.[tushare]'
export TUSHARE_TOKEN=...              # needs >= 2000 Tushare points
```

`private/private_config.yaml`:

```yaml
parquet_store: '/path/to/pysystemtrade/parquet'
mongo_host: 127.0.0.1
mongo_port: 27017
mongo_db: 'production'
```

MongoDB can be any instance; a simple option is Docker:

```bash
docker run -d --name pysystemtrade-mongo --restart unless-stopped \
  -p 127.0.0.1:27017:27017 \
  -v /path/to/pysystemtrade/mongo:/data/db mongo:6.0
```

## Workflows

### One-off: seed the full history

```bash
python -m sysinit.futures.seed_price_data_from_tushare --dry-run   # catalogue + config check
python -m sysinit.futures.seed_price_data_from_tushare --yes
```

One `fut_daily` request per vendor contract (~11k requests at 180/min ≈ 1.5h).
The parquet checkpoints are the resume state: a re-run validates finalized
expired contracts (canonical columns, 23:00 stamps, monotonic, inside their
effective window) and skips them. Active contracts are always refreshed, so
a checkpoint made during an interrupted seed cannot freeze partial history.
A finalized daily series with a missing/mismatched merged file is repaired
without re-downloading. `--instrument SHFE_CU` / `--contract 202609`
restrict the scope — with `--no-resume` this is also the repair path when
Tushare corrects history. Progress prints every 200 vendor contracts. Exit
code is non-zero if any contract ends in an explicit failure.

Import spreads into Mongo for production use (answer `n` = bulk add):

```bash
python sysinit/futures/repocsv_spread_costs.py
```

### One-off: initialize or change a roll policy

First edit one row in `data/futures/csvconfig/rollconfig.csv`. Then generate a
candidate calendar for only that instrument in a temporary directory; the
native builder uses the configured policy and the stored contract prices to
find dates where the required contracts overlap:

```bash
mkdir -p /tmp/pysystemtrade-roll-calendars
uv run python - <<'PY'
from sysinit.futures.rollcalendars_from_db_prices_to_csv import (
    build_and_write_roll_calendar,
)

build_and_write_roll_calendar(
    "SHFE_RB",
    output_datapath="/tmp/pysystemtrade-roll-calendars",
    check_before_writing=False,
)
PY
```

Inspect and, if necessary, hand-edit
`/tmp/pysystemtrade-roll-calendars/SHFE_RB.csv`. A row's timestamp is the
last observation assigned to `current_contract`; the following retained
observation is assigned to `next_contract`. Both contracts must have genuine
prices on the row timestamp so the Panama roll adjustment is defined.

Validate the edited candidate against concrete-contract prices:

```bash
uv run python - <<'PY'
from sysinit.futures.rollcalendars_from_db_prices_to_csv import (
    check_saved_roll_calendar,
)

check_saved_roll_calendar(
    "SHFE_RB", input_datapath="/tmp/pysystemtrade-roll-calendars"
)
PY
```

Only after inspection and successful validation, deliberately replace the
reviewed file and rebuild that instrument:

```bash
cp /tmp/pysystemtrade-roll-calendars/SHFE_RB.csv \
  data/futures/roll_calendars_csv/SHFE_RB.csv
uv run python -m sysinit.futures.rebuild_tushare_multiple_adjusted \
  --instrument SHFE_RB
```

For a first initialization after every calendar has been reviewed and copied,
the thin universe adapter can rebuild all configured Tushare instruments:

```bash
uv run python -m sysinit.futures.rebuild_tushare_multiple_adjusted --all
```

The adapter only selects stitchable manifest entries, loads their saved
calendars unchanged, and delegates multiple and additive-Panama construction
to the native builders. It does not create calendars or modify roll policy.

### Daily updates

```bash
python -m sysproduction.update_tushare_futures            # standalone
python -m sysproduction.run_daily_tushare_price_updates   # via syscontrol
python -m sysproduction.run_daily_update_multiple_adjusted_prices
```

The daily update re-fetches the catalogue (6 requests), upserts every
contract's exact expiry and sampling state into Mongo, then for each *listed*
contract appends prices with a 7-day overlap window (`--overlap-days`),
followed by a `CNHUSD` overlap update. It is registered in
`syscontrol/control_config.yaml` as `run_daily_tushare_price_updates` (18:00)
and in the crontab. The wrapper always dispatches so credentials can come
from either `TUSHARE_TOKEN` or private config, and missing credentials fail
visibly. The following native multiple/adjusted process extends each series
using current/forward/carry identities from its final multiple-price row. It
does not read the historical calendar CSV.

### Live rolls

```bash
python -m sysproduction.interactive_update_roll_status
```

The native production workflow uses roll parameters, current contract/expiry
state, positions, and the multiple-price series to decide and execute a live
roll. A completed roll writes the new contract identities into multiple
prices and restitches adjusted prices. It intentionally does not update the
historical roll-calendar CSV; record a permanent historical-calendar change
separately through the reviewed one-instrument workflow above.

The normal path is strict. If construction fails, the interactive command may
ask whether to try its existing forward-fill fallback, explicitly described
there as less accurate. That is an operator-approved live recovery path, not
the historical rebuild policy; inspect the proposed old/new prices before
confirming it.

### Backtesting

No Tushare-specific code — the native `dbFuturesSimData` reads the seeded
stores:

```python
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
data = dbFuturesSimData()
prices = data.get_backadjusted_futures_price("SHFE_RB")
```

## Failure model

- `TushareTransientError` (after bounded retries) and
  `TushareTruncationError` (a response hit the 2000-row `fut_daily` limit)
  fail only the affected contract; the bulk run continues and reports them.
- Historical-mutation, merge, and spike failures remain isolated to the
  affected contract. Every other error (bad token/points, manifest problems,
  schema or identity drift, storage failures, and unknown programming errors)
  is critically logged and aborts the run rather than producing 11k failures.
- If Tushare lists a **new product family**, the daily update still refreshes
  all known instruments but exits non-zero naming the unmapped family; add a
  manifest row (+ instrument config, spread) to accept it.
- If Tushare **changes already-stored history** inside the overlap window,
  the update refuses to write for that futures contract or CNHUSD and reports
  the changed dates; accept futures history deliberately by re-seeding it
  (`seed ... --instrument X --contract YYYYMM --no-resume`).
- Price spikes (vol-normalised move > `max_price_spike`) block the write for
  that contract, as with any broker source.
- The token is scrubbed from all error text.

## Code map

| File | Role |
|---|---|
| `sysdata/tushare/client.py` | Rate-limited (180/min), retrying SDK wrapper; token resolution |
| `sysdata/tushare/manifest.py` | Reviewed product→instrument mapping with validity windows |
| `sysdata/tushare/source.py` | Catalogue fetch + validation; per-vendor-contract price fetch; `HistoricalFuturesContract`; CNHUSD |
| `sysdata/tushare/transforms.py` | Pure vendor-frame → repository-object transforms |
| `sysproduction/update_tushare_futures.py` | Daily update + shared seed helpers (upsert, checkpoints, mutation guard) |
| `sysproduction/run_daily_tushare_price_updates.py` | syscontrol wrapper |
| `sysinit/futures/seed_price_data_from_tushare.py` | Resumable full-history bootstrap |
| `sysinit/futures/rollcalendars_from_db_prices_to_csv.py` | Native one-instrument calendar build and validation |
| `sysinit/futures/multipleprices_from_db_prices_and_csv_calendars_to_db.py` | Native historical multiple-price builder |
| `sysinit/futures/adjustedprices_from_db_multiple_to_db.py` | Native additive-Panama builder |
| `sysinit/futures/rebuild_tushare_multiple_adjusted.py` | Thin batch selector over the native price builders |

## Limitations

- Daily bars only (minute data is a separate Tushare permission; the source
  raises for any non-daily frequency rather than pretending).
- `CNHUSD` starts 2012-02-18; earlier P&L conversion to USD forward-fills
  from the series start. Chinese onshore CNY is proxied by offshore CNH.
- Very early history (1990s CZCE/SHFE predecessors) is only as good as the
  vendor's records; zero-volume and zero-price rows are dropped rather than
  repaired.
- Open interest is validated when present but not persisted (the repository
  price schema has no OI column); manual roll research can still use volume.
