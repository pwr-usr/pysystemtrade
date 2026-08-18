# Tushare data: inspecting every stage

Runnable recipes for looking at the data as it moves through the pipeline:

```text
vendor API → catalogue records → per-contract prices (parquet) + contract
state (mongo) + CNHUSD (parquet)

historical initialization: manual rollconfig + reviewed roll calendar (CSV)
    → multiple prices (parquet) → adjusted prices (parquet) → simulation

daily/live continuation: final multiple-price row + new contract prices
    → updated multiple and adjusted prices
```

Prerequisites for anything below: the mongo container is up
(`docker start pysystemtrade-mongo`) and, for stages 1–2 only, the token is
exported (`TUSHARE_TOKEN`). Everything runs with `uv run python`. Storage
paths come from `private/private_config.yaml` (`parquet_store`, `mongo_*`),
so plain `dataBlob()` / `diagPrices()` always point at the right stores.

## 1. Raw vendor responses

```python
from sysdata.tushare.client import TushareClient
client = TushareClient()
print(client.fut_basic(exchange="SHFE").head())            # contract catalogue, one exchange
print(client.fut_daily(ts_code="CU2609.SHF").head())       # raw daily bars, one contract
print(client.fx_daily(ts_code="USDCNH.FXCM", start_date="20260101", end_date="20260131").head())
```

This is the untransformed vendor truth — useful when deciding whether an
oddity is Tushare's or ours. `close` becomes FINAL; `settle` is fetched but
never stored.

## 2. Validated catalogue (vendor contract → internal record)

```python
from sysdata.tushare.client import TushareClient
from sysdata.tushare.source import TushareFuturesPriceSource
source = TushareFuturesPriceSource(TushareClient())
result = source.fetch_contract_catalogue_result()
print(len(result.contracts), "records; unmapped families:", result.unmapped_families)
records = [r for r in result.contracts if r.instrument_code == "DCE_FB"]
print(records[0])   # contract_date, ts_code, exact expiry, era price window
```

Without hitting the API: `python -m sysinit.futures.seed_price_data_from_tushare --dry-run`
prints the vendor/internal counts after full validation.

## 3. Per-contract prices (parquet)

Files live at `<parquet_store>/futures_contract_prices/` keyed
`Day@SHFE_CU#20260900.parquet` (daily) and `SHFE_CU#20260900.parquet`
(merged; identical content for this daily-only source).

```python
from sysdata.data_blob import dataBlob
from sysproduction.data.prices import diagPrices
from sysobjects.contracts import futuresContract
from syscore.dateutils import DAILY_PRICE_FREQ

store = diagPrices(dataBlob()).db_futures_contract_price_data
contract = futuresContract("SHFE_CU", "20260900")
daily = store.get_prices_at_frequency_for_contract_object(contract, frequency=DAILY_PRICE_FREQ)
print(daily.tail())        # OPEN/HIGH/LOW/FINAL/VOLUME, 23:00 naive stamps
merged = store.get_merged_prices_for_contract_object(contract)
assert daily.equals(merged)

# every stored contract for an instrument at once:
all_prices = store.get_merged_prices_for_instrument("SHFE_CU")
print(len(all_prices), "contracts;", sorted(all_prices.keys())[:3])
```

(Or read a file directly: `pd.read_parquet("<parquet_store>/futures_contract_prices/SHFE_CU#20260900.parquet")`.)

Expectations: FINAL equals the vendor `close`; zero-volume rows are absent
(cleaning drops them, so a series starts when the contract actually trades);
fibreboard vendor contracts crossing 2019-12 appear under both `DCE_FB_OLD`
and `DCE_FB`, sliced at the era boundary.

## 4. Contract state (mongo)

```python
from sysdata.data_blob import dataBlob
from sysproduction.data.contracts import dataContracts
contracts = dataContracts(dataBlob())
chain = contracts.get_all_contract_objects_for_instrument_code("SHFE_CU")
one = contracts.get_contract_from_db(chain[-1])
print(one.date_str, one.expiry_date, one.currently_sampling)
```

`expiry_date` is the exact Tushare delist date (not an approximation);
`currently_sampling` is on exactly for currently-listed contracts. Shell
alternative: `docker exec pysystemtrade-mongo mongosh production --quiet --eval
'db.futures_contracts.countDocuments({})'` (expect ≈ 10,914).

## 5. CNHUSD (parquet)

```python
from sysdata.data_blob import dataBlob
from sysproduction.data.currency_data import dataCurrency
fx = dataCurrency(dataBlob()).get_fx_prices("CNHUSD")
print(fx.tail())   # inverted USDCNH.FXCM bid/ask midpoint, from 2012-02-18
```

## 6. Roll parameters (CSV — manually maintained)

`data/futures/csvconfig/rollconfig.csv` is the policy source. Inspect the
native object exactly as the calendar and production-roll code will use it:

```python
from sysdata.csv.csv_roll_parameters import csvRollParametersData
params = csvRollParametersData().get_roll_parameters("SHFE_RB")
print(params)
```

The meanings of `HoldRollCycle`, `PricedRollCycle`, `RollOffsetDays`,
`CarryOffset`, and `ExpiryOffset` are documented in
[data.md](/docs/data.md). Research can inform a manual edit, but there is no
automatic proposal or config-write stage.

## 7. Candidate and reviewed roll calendars (CSV)

`data/futures/roll_calendars_csv/<INSTRUMENT>.csv`: one row per roll
(`DATE_TIME, current_contract, next_contract, carry_contract`).

Build a candidate in a temporary directory, never directly over the reviewed
calendar:

```python
from pathlib import Path
from sysinit.futures.rollcalendars_from_db_prices_to_csv import (
    build_and_write_roll_calendar,
)

Path("/tmp/pysystemtrade-roll-calendars").mkdir(parents=True, exist_ok=True)
candidate = build_and_write_roll_calendar(
    "SHFE_RB",
    output_datapath="/tmp/pysystemtrade-roll-calendars",
    check_before_writing=False,
)
print(candidate)
```

Inspect or edit the temporary CSV, then validate it against the stored raw
contract prices:

```python
from sysinit.futures.rollcalendars_from_db_prices_to_csv import (
    check_saved_roll_calendar,
)

checked = check_saved_roll_calendar(
    "SHFE_RB", input_datapath="/tmp/pysystemtrade-roll-calendars"
)
print(checked)
```

Sanity: contiguous chain (each row's `current` equals the previous row's
`next`); carry earlier(−1)/later(+1) than current per `rollconfig.csv`.
Both current and incoming contracts need real prices at every roll timestamp.
After successful review, deliberately copy that one file into
`data/futures/roll_calendars_csv/` and run:

```bash
uv run python -m sysinit.futures.rebuild_tushare_multiple_adjusted \
  --instrument SHFE_RB
```

The saved liquid-era bounds are also policy: `SHFE_RU` starts from raw
contract `19990100`, `SHFE_FU` from `20190100`, and `CZCE_SF`/`CZCE_SM` from
`20170100`. Preserve those bounds when replacing those calendars.

## 8. Multiple prices (parquet)

```python
from sysdata.data_blob import dataBlob
from sysproduction.data.prices import diagPrices
multiple = diagPrices(dataBlob()).get_multiple_prices("SHFE_RB")
print(multiple.tail())                       # PRICE/CARRY/FORWARD + *_CONTRACT columns
rolls = (multiple["PRICE_CONTRACT"] != multiple["PRICE_CONTRACT"].shift()).sum()
print("rolls in series:", rolls - 1)
print("carry coverage:", f"{multiple['CARRY'].notna().mean():.0%}")
```

For carry −1 instruments `CARRY_CONTRACT < PRICE_CONTRACT` (earlier
delivery); expect gaps in `CARRY` late in each hold when the previous
contract has expired — that is the accepted −1 trade-off, not a bug.

## 9. Adjusted prices (parquet)

```python
from sysdata.data_blob import dataBlob
from sysproduction.data.prices import diagPrices
adjusted = diagPrices(dataBlob()).get_adjusted_prices("SHFE_RB")
print(adjusted.index[0], "->", adjusted.index[-1], len(adjusted), "rows")
```

Panama-stitched; long histories can go negative — expected, not a defect.
Truncated-on-purpose series: renamed predecessors (CZCE_ER/ME/RO/WS/WT/TC)
end at their rename; dead markets (CZCE_JR/LR/PM/RI/RS/WH/ZC, DCE_BB,
SHFE_WR) end when liquidity died. Every active instrument should reach the
last trading day; the sweep below verifies that.

```python
# truncation sweep: adjusted series ending long before its contract data
from sysdata.data_blob import dataBlob
from sysproduction.data.prices import diagPrices
prices = diagPrices(dataBlob())
for code in sorted(prices.db_futures_adjusted_prices_data.get_list_of_instruments()):
    adjusted = prices.db_futures_adjusted_prices_data.get_adjusted_prices(code)
    contracts = prices.db_futures_contract_price_data.get_merged_prices_for_instrument(code)
    last = max(p.index.max() for p in contracts.values() if len(p))
    if (last - adjusted.index[-1]).days > 30:
        print(code, "ends", adjusted.index[-1].date(), "vs contract data", last.date())
```

## 10. Production continuation and live rolls

After collecting new concrete-contract prices, the native daily update
extends multiple and adjusted prices:

```bash
uv run python -m sysproduction.update_tushare_futures
uv run python -m sysproduction.run_daily_update_multiple_adjusted_prices
```

The updater continues from the current/forward/carry identities in the final
multiple-price row. It does not replay or extend the historical calendar CSV.
Inspect that live state directly:

```python
from sysdata.data_blob import dataBlob
from sysproduction.data.prices import diagPrices
print(diagPrices(dataBlob()).get_multiple_prices("SHFE_RB").tail(1).T)
```

When a contract actually rolls, use the native interactive workflow:

```bash
uv run python -m sysproduction.interactive_update_roll_status
```

It uses current multiple-price identities, actual expiry/contract state, roll
parameters, and positions. It updates multiple and adjusted prices, not the
calendar CSV.

## 11. Simulation view (what a backtest sees)

```python
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
data = dbFuturesSimData()
print(len(data.get_instrument_list()), "instruments")
print(data.get_backadjusted_futures_price("SHFE_RB").tail())
print(data.get_multiple_prices("SHFE_RB").tail())
print(data.get_fx_for_instrument("SHFE_RB", "USD").tail())   # via CNHUSD
print(data.get_spread_cost("SHFE_RB"))
```

## 12. Pipeline health in one shot

```bash
uv run python -m sysinit.futures.seed_price_data_from_tushare --dry-run   # catalogue + config validation
uv run pytest sysdata/tests/test_tushare_futures_config.py -q             # config invariants
PYSYSTEMTRADE_RUN_MONGO_TESTS=1 uv run pytest -q                          # full suite incl. storage workflows
```

A seed re-run is also a full store audit: it validates every stored
checkpoint (columns, 23:00 stamps, monotonicity, era window) and reports
`skipped_complete` for healthy contracts without re-downloading anything.
