"""Generate the canonical Chinese-futures notebooks.

Usage::

    python make_notebooks.py                 # numbered series plus the lab
    python make_notebooks.py 1 4 7            # selected numbered notebooks
    python make_notebooks.py lab tutorial     # named companions

Generation writes source-only notebooks. The complete tutorial is excluded
from the default selection so its saved execution is not cleared accidentally.
All notebook cell sources live in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

HERE = Path(__file__).resolve().parent

NOTEBOOK_METADATA = {
    "kernelspec": {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    },
    "language_info": {"name": "python"},
}

SETUP_CELL = """\
%matplotlib inline
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import research as R

R.set_notebook_style()"""


def md(text: str):
    return new_markdown_cell(text.strip())


def code(text: str):
    return new_code_cell(text.strip())


def _universe_cells() -> list:
    cells = [
        md(
            """
# 01 — The Chinese futures universe and its metadata

This is the first notebook in a series about researching **Chinese futures**
with [pysystemtrade](https://github.com/robcarver17/pysystemtrade), using
daily data for every contract ever listed on the six mainland exchanges
(SHFE, DCE, CZCE, CFFEX, INE, GFEX), sourced from Tushare.

**What you will learn here**: how the repo models futures data, why all 95
reviewed histories belong in the research universe, how trailing volume makes
availability point-in-time, and where every piece of metadata lives.

## The data model in one picture

pysystemtrade never trades a "continuous future" — it stores *individual
contracts* and stitches them itself:

```
per-contract prices  ──roll calendar──▶  multiple prices  ──Panama stitch──▶  adjusted prices
(parquet, OHLC+FINAL+VOLUME)             (PRICE/CARRY/FORWARD                (single back-adjusted
 one file per contract                    + their contract ids)               series per instrument)
```

Storage split (all locations configured in `private/private_config.yaml`):

| What | Where |
|---|---|
| per-contract, multiple, adjusted prices; CNHUSD | parquet |
| contract expiries & sampling state | MongoDB |
| instrument metadata, roll parameters, spread costs, roll calendars | CSV in `data/futures/` |

Deeper reading: `docs/tushare_chinese_futures.md` (design),
`docs/tushare_data_inspection.md` (inspecting each stage),
`docs/backtesting.md` (the full simulation manual).

## Loading the simulation data

`dbFuturesSimData` is the database-backed simulation data object. The helper
below cross-checks its adjusted-price instruments against the stitchable
Tushare manifest. Present-day labels such as "dead" or "predecessor" do not
remove an instrument from its own earlier history.
"""
        ),
        code(SETUP_CELL),
        code(
            """
from sysdata.sim.db_futures_sim_data import dbFuturesSimData

data = dbFuturesSimData()
instruments = R.chinese_universe(data)
assert len(instruments) == 95
print(f"{len(instruments)} reviewed instruments with stitched histories")
print(instruments[:10], "...")"""
        ),
        md(
            """
## Instrument metadata

Static metadata (point size, currency, asset class...) lives in
`data/futures/csvconfig/instrumentconfig.csv`; roll behaviour in
`rollconfig.csv`; half-spreads in `spreadcosts.csv` (imported into Mongo for
sim use). Naming is `EXCHANGE_CODE` — `SHFE_RB` is Shanghai rebar,
`CFFEX_IF` the CSI300 index future.

All instruments are quoted in CNY; the config says **CNH** so that the single
`CNHUSD` FX series can convert P&L for USD-based accounts. In this series we
run with `base_currency: "CNH"`, so FX is exactly 1 and pre-2012 history
(before the CNHUSD series starts) needs no FX at all.
"""
        ),
        code(
            """
meta = data.get_all_instrument_data_as_df().loc[instruments].copy()
meta["Exchange"] = [code.split("_")[0] for code in meta.index]
meta["SpreadCost"] = [data.get_spread_cost(code) for code in meta.index]

from sysdata.csv.csv_roll_parameters import csvRollParametersData

rolls = csvRollParametersData().get_roll_parameters_all_instruments()
meta = meta.join(
    rolls[["HoldRollCycle", "RollOffsetDays", "CarryOffset", "PricedRollCycle"]]
)
meta[["Description", "Pointsize", "Currency", "AssetClass", "Exchange",
      "SpreadCost", "HoldRollCycle", "RollOffsetDays", "CarryOffset"]].head(12)"""
        ),
        md(
            """
One metadata spot check is worth keeping visible. Plywood (`DCE_BB`) has a
perfectly finite configured half-spread, but that static cost object does not
make its later zombie market liquid. Price, cost, and point-in-time liquidity
are separate facts; the volume test below is what controls eligibility.
"""
        ),
        code(
            """
bb_cost = data.get_raw_cost_data("DCE_BB")
print("DCE_BB static cost metadata:", bb_cost)"""
        ),
        md(
            """
## The vendor manifest: renamed products and specification eras

Chinese exchanges have renamed or re-specified several products. Each era is
its **own instrument**, linked by a `Predecessor` column in the Tushare
manifest — so a backtest never accidentally splices two different contract
specs together. The famous case is DCE fibreboard, which changed its trading
unit in December 2019: one vendor contract crossing that date is split
between `DCE_FB_OLD` and `DCE_FB`.
"""
        ),
        code(
            """
manifest = pd.read_csv(
    R.REPO_ROOT / "sysdata/tushare/config/futures_instruments.csv",
    keep_default_na=False,
)
renames = manifest[manifest["Predecessor"] != ""][
    ["Instrument", "Predecessor", "ValidFrom"]
]
print("Product renames / re-specifications (successor <- predecessor):")
renames"""
        ),
        code(
            """
manifest[manifest["FutCode"] == "FB"]  # the fibreboard era split"""
        ),
        md(
            """
## What stitched histories exist?

The first and last adjusted-price rows describe the stored history; they are
not, by themselves, a liquidity test. A non-zero print can exist in a market
too thin to trade realistically. We keep present-day predecessor/dead labels
only for reporting and never feed them into historical eligibility.
"""
        ),
        code(
            """
spans = {}
for code in instruments:
    prices = data.daily_prices(code)
    spans[code] = dict(first=prices.index[0], last=prices.index[-1],
                       days=len(prices))
spans = pd.DataFrame(spans).T
spans["first"] = pd.to_datetime(spans["first"])
spans["last"] = pd.to_datetime(spans["last"])
spans["years"] = ((spans["last"] - spans["first"]).dt.days / 365.25).round(1)
spans["present_day_label"] = "open history"
spans.loc[R.predecessor_instruments(), "present_day_label"] = "predecessor era"
spans.loc[R.KNOWN_DEAD_MARKETS, "present_day_label"] = "known dead market"

universe = meta.join(spans)
print(universe["present_day_label"].value_counts().to_string())
history_display = universe[[
    "AssetClass", "Exchange", "Pointsize", "SpreadCost",
    "first", "last", "years", "present_day_label",
]].sort_values("first")
history_display.head(15)"""
        ),
        md(
            """
The terminal group has two flavours: a reviewed product rename/specification
boundary, or a market now known to have died. Calling either one "terminal"
uses information from the completed history. A backtest must instead react to
volume observed up to each date, with any final forced close disclosed as an
assumption.
"""
        ),
        code(
            """
terminal_history = universe.loc[R.terminal_instruments(), [
    "AssetClass", "first", "last", "years", "present_day_label",
]].sort_values("last")
terminal_history"""
        ),
        md(
            """
## Point-in-time liquidity: when could the backtest actually include one?

For each observed held-price row we read the raw volume of the contract that
supplied `PRICE`. A missing raw volume on such a row becomes zero (unknown is
unsafe); an exchange-closed date remains missing. The eligibility rule then
uses only the last 20 observed sessions:

- enter when mean held-contract volume reaches 130 lots;
- remain eligible until it falls below 70 lots;
- never backfill eligibility before the first qualifying date.

The gap between 130 and 70 is hysteresis: it prevents daily entry/exit churn
around one threshold. These are explicit research assumptions, not universal
claims that 130 lots makes every contract equally cheap to trade. A decision
uses volume known through that close; the native account machinery applies it
with its normal one-business-row fill delay.
"""
        ),
        code(
            """
volumes = R.held_contract_volumes(data, instruments)
average_volume = R.trailing_liquidity(volumes)
eligibility = R.liquidity_eligibility(volumes, force_terminal_close=True)
events = R.liquidity_event_table(eligibility, volumes)

ever_eligible = set(eligibility.columns[eligibility.any(axis=0)])
never_eligible = set(instruments) - ever_eligible
forced_exit_instruments = set(events.loc[
    events["event"] == "forced terminal exit", "instrument"])
expected_never = {"CZCE_LR", "CZCE_PM"}
expected_forced = {
    "CZCE_ER", "CZCE_JR", "CZCE_ME", "CZCE_RO",
    "CZCE_RS", "CZCE_TC", "CZCE_WS", "CZCE_WT",
}
assert len(ever_eligible) == 93
assert int(eligibility.iloc[-1].sum()) == 78
assert never_eligible == expected_never
assert forced_exit_instruments == expected_forced

print(f"volume panel: {volumes.index[0].date()} to {volumes.index[-1].date()}, "
      f"{volumes.shape[1]} instruments")
print(f"ever eligible: {len(ever_eligible)}; never eligible: "
      f"{sorted(never_eligible)}")
print(f"eligible on final business date: {int(eligibility.iloc[-1].sum())}")
print(f"forced terminal closes: {sorted(forced_exit_instruments)}")
events.tail(10)"""
        ),
        code(
            """
eligible_by_exchange = eligibility.T.groupby(meta["Exchange"]).sum().T
fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
eligibility.sum(axis=1).plot(
    ax=axes[0], title="Point-in-time eligible Chinese futures")
axes[0].set_ylabel("eligible instruments")
eligible_by_exchange.plot.area(
    ax=axes[1], alpha=0.75, linewidth=0,
    title="Eligible instruments by exchange")
axes[1].set_ylabel("eligible instruments")
axes[1].set_xlabel("decision date")
plt.tight_layout()"""
        ),
        md(
            """
## Three liquidity histories, seen without hindsight

Rebar is a long-lived liquid market, plywood later dies, and GFEX lithium is a
recent listing. Plotting their trailing averages separately preserves each
market's scale. The two horizontal lines are the actual entry/exit decisions;
no future observation is used to move an earlier decision.
"""
        ),
        code(
            """
case_studies = ["SHFE_RB", "DCE_BB", "GFEX_LC"]
fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
for axis, code_ in zip(axes, case_studies):
    average_volume[code_].plot(ax=axis, color="black")
    axis.axhline(R.LIQUIDITY_ENTRY, color="tab:green", linestyle="--",
                 label="entry threshold")
    axis.axhline(R.LIQUIDITY_EXIT, color="tab:red", linestyle=":",
                 label="exit threshold")
    axis.set_yscale("symlog", linthresh=1)
    axis.set_ylim(bottom=0)
    axis.set_title(code_)
    axis.set_ylabel("20-session mean lots (symlog)")
axes[0].legend()
axes[-1].set_xlabel("decision date")
plt.tight_layout()"""
        ),
        md(
            """
## Terminal histories and the last executable exit

Normal liquidity exits are causal. A market can also stop printing while it
is still above the threshold; there is then no future quote on which a delayed
backtest can discover the disappearance. For the 16 reviewed predecessor/dead
histories, the helper explicitly sets the target to zero on the system
business row immediately before the final observed quote. That row can itself
be an exchange holiday, because native
`delayfill=True` shifts the target by one system row. The resulting fill uses
the real final quote and retains the complete reopening move.

That row is labelled `forced terminal exit`: it is a conservative liquidation
assumption, not information the strategy possessed at the time.
"""
        ),
        code(
            """
terminal_events = events[
    events["instrument"].isin(R.terminal_instruments())
].sort_values(["instrument", "date"])
last_terminal_event = terminal_events.groupby("instrument").tail(1).set_index(
    "instrument")
terminal_report = terminal_history.join(last_terminal_event[[
    "date", "event", "mean_volume_20", "terminal_assumption",
]])
terminal_report.sort_values("last")"""
        ),
        md(
            """
## Held price versus adjusted price: a DCE_JD spot check

`multiple.PRICE` is an actual close from the held contract. The adjusted
series adds historical roll gaps so that **price differences** remain usable
through rolls; its absolute level is synthetic and percentage changes of that
level are invalid. On every non-roll row, held and adjusted daily differences
must be identical.
"""
        ),
        code(
            """
jd_multiple = pd.DataFrame(data.get_multiple_prices("DCE_JD"))
jd_adjusted = data.get_backadjusted_futures_price("DCE_JD")
jd_prices = pd.concat(
    {
        "held contract close": jd_multiple["PRICE"],
        "additive-Panama level": jd_adjusted,
    },
    axis=1,
    join="inner",
).dropna()

same_contract = jd_multiple["PRICE_CONTRACT"].eq(
    jd_multiple["PRICE_CONTRACT"].shift())
held_change = jd_multiple["PRICE"].diff()
adjusted_change = jd_adjusted.diff()
non_roll = same_contract & held_change.notna() & adjusted_change.notna()
max_non_roll_error = (held_change[non_roll] - adjusted_change[non_roll]).abs().max()
assert np.isclose(max_non_roll_error, 0.0)
print(f"maximum non-roll difference mismatch: {max_non_roll_error:.6f}")
display(jd_multiple.tail(5))

plot_start = "2022-01-01"
fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
jd_prices["held contract close"].loc[plot_start:].plot(
    ax=axes[0], title="DCE_JD held-contract close")
axes[0].set_ylabel("actual price")
jd_prices["additive-Panama level"].loc[plot_start:].plot(
    ax=axes[1], title="DCE_JD additive-Panama level (absolute level arbitrary)")
axes[1].set_ylabel("adjusted price units")
axes[1].set_xlabel("date")
plt.tight_layout()"""
        ),
        md(
            """
## The research universe used in the rest of the series

All 95 histories remain present. A successor does not erase its predecessor's
earlier opportunity, and a market known dead today is not removed from years
when its trailing volume passed the rule. Portfolio weights will be zero while
`eligibility` is false and are computed across the eligible set on each date.

This prevents literal pre-listing trades and avoids survivorship deletion.
The thresholds and terminal-close convention remain modelling assumptions and
will be carried visibly through later notebooks.

**Next**: the data-pipeline section below shows how those histories are
produced and updated.
"""
        ),
        code(
            """
print("all histories retained:", len(instruments))
print("present-day predecessor labels:", R.predecessor_instruments())
print("present-day dead-market labels:", R.KNOWN_DEAD_MARKETS)"""
        ),
    ]
    return cells


def _data_pipeline_cells() -> list:
    cells = [
        md(
            """
# 02 — Where the data comes from, and how to update it

The Tushare integration follows the repo's philosophy: Tushare is a *data
vendor* (like Barchart in the upstream docs), not a broker. It owns the
vendor-to-contract-price boundary; calendar, multiple-price, adjusted-price,
and live-roll logic stays in stock pysystemtrade.

| Task | Command |
|---|---|
| one-off full-history bootstrap (resumable) | `python -m sysinit.futures.seed_price_data_from_tushare` |
| **daily update** (contracts, prices, CNHUSD) | `python -m sysproduction.update_tushare_futures` |
| daily multiple + adjusted append | `python -m sysproduction.run_daily_update_multiple_adjusted_prices` |
| rebuild from accepted calendars | `python -m sysinit.futures.rebuild_tushare_multiple_adjusted --all` |

The daily update can also run under the production scheduler
(`run_daily_tushare_price_updates`, registered in
`syscontrol/control_config.yaml` and the crontab).

The final command is deliberately only a thin China-universe adapter around
the native single-instrument builders. It does not choose roll policy or
create calendars. Those are careful, manual initialization tasks taught in
notebook 02.

Full design: `docs/tushare_chinese_futures.md`. This notebook walks one
contract through every stage so you can *see* the pipeline.
"""
        ),
        code(SETUP_CELL),
        md(
            """
## Stage 0 — the vendor's raw response (needs Tushare credentials)

Everything starts with two Tushare endpoints: `fut_basic` (the contract
catalogue per exchange, including exact delisting dates) and `fut_daily`
(daily bars per contract). The client below rate-limits (180 req/min) and
retries transient failures. Credentials may come from `TUSHARE_TOKEN` or the
private-config `tushare_token` key; without either this cell just skips.
"""
        ),
        code(
            """
raw_daily = None
from sysdata.tushare.client import TushareClient
from sysdata.tushare.errors import TushareConfigError

try:
    client = TushareClient()
except TushareConfigError:
    client = None
    print("Tushare credentials or SDK unavailable - skipping the live vendor call")
else:
    raw_daily = client.fut_daily(ts_code="CU2609.SHF",
                                 start_date="20260601", end_date="20260610")
    display(raw_daily[["trade_date", "close", "settle", "vol", "oi"]])"""
        ),
        md(
            """
Note the vendor gives both `close` (last trade) and `settle` (the exchange's
daily mark). **We store `close` as FINAL** — a backtest can realistically
fill near the close; nobody fills at the settlement average. A missing close
stays missing rather than being papered over with settle.

## Stage 1 — per-contract prices in parquet

The seed/update writes each contract as `OPEN/HIGH/LOW/FINAL/VOLUME` rows
stamped at the repo's notional close (23:00, naive), one parquet file per
contract per frequency. The comparison below does not rely on row order: it
turns the vendor's date strings into repository timestamps, joins on those
timestamps, and verifies that stored `FINAL` is exactly the traded `close`.
"""
        ),
        code(
            """
from sysdata.data_blob import dataBlob
from sysproduction.data.prices import diagPrices
from sysobjects.contracts import futuresContract
from syscore.dateutils import DAILY_PRICE_FREQ

blob = dataBlob(log_name="notebook02")
price_store = diagPrices(blob).db_futures_contract_price_data
contract = futuresContract("SHFE_CU", "20260900")
stored = price_store.get_prices_at_frequency_for_contract_object(
    contract, frequency=DAILY_PRICE_FREQ
)
display(stored.tail())

if raw_daily is not None and not raw_daily.empty:
    vendor_close = raw_daily[["trade_date", "close"]].copy()
    vendor_close.index = (
        pd.to_datetime(vendor_close.pop("trade_date")) + pd.Timedelta(hours=23)
    )
    vendor_close = vendor_close.rename(columns={"close": "Tushare close"})
    close_check = vendor_close.join(
        stored[["FINAL"]].rename(columns={"FINAL": "stored FINAL"}),
        how="inner",
    ).sort_index()
    assert len(close_check) == len(vendor_close)
    assert np.allclose(
        close_check["Tushare close"], close_check["stored FINAL"],
        rtol=0.0, atol=0.0,
    )
    print(f"all {len(close_check)} overlapping closes match exactly")
    display(close_check)"""
        ),
        md(
            """
## Stage 2 — contract state in MongoDB

Mongo holds the small mutable facts per contract: its **exact expiry** (the
vendor's delisting date, not an approximation) and whether it is currently
listed ("sampling").
"""
        ),
        code(
            """
from sysproduction.data.contracts import dataContracts

contracts = dataContracts(blob)
chain = contracts.get_all_contract_objects_for_instrument_code("SHFE_CU")
db_contract = contracts.get_contract_from_db(contract)
print(f"SHFE_CU has {len(chain)} contracts in the database")
print(f"{contract.key}: expiry {db_contract.expiry_date}, "
      f"sampling={db_contract.currently_sampling}")"""
        ),
        md(
            """
## Stage 3 — initialized multiple and adjusted series

Roll calendars turn contract prices into `multiple prices` (price + carry +
forward contracts side by side) and then into one back-adjusted series. The
calendar is an initialization/recovery artifact: after this build, production
reads current contract identities from the last multiple-price row.
"""
        ),
        code(
            """
prices_stage = diagPrices(blob)
multiple = prices_stage.get_multiple_prices("SHFE_CU")
adjusted = prices_stage.get_adjusted_prices("SHFE_CU")
display(multiple.tail(3))

fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
multiple["PRICE"].plot(ax=axes[0], title="SHFE_CU held-contract close")
axes[0].set_ylabel("price")
adjusted.plot(ax=axes[1], title="additive-Panama level (absolute level is arbitrary)")
axes[1].set_ylabel("adjusted price units")
axes[1].set_xlabel(f"date ({adjusted.index[0]:%Y-%m-%d} onward)")
plt.tight_layout()"""
        ),
        md(
            """
## The daily update

```bash
python -m sysproduction.update_tushare_futures
```

does, in order: refresh the catalogue (6 requests) → upsert every contract's
expiry/sampling state into Mongo → for each *currently listed* contract,
re-fetch the last 7 days and append anything new → update CNHUSD. A typical
run summary looks like:

```
vendor=10919 internal=10929 written=872 ... rows=0 no_data=0 failures=0
```

(`rows=0` on a weekend: nothing new to append — the run is idempotent.)

After raw contracts update, the native daily multiple/adjusted process appends
the current `PRICE`, `FORWARD`, and `CARRY` contracts. It does not read the
calendar CSV. When a real contract roll is required,
`python -m sysproduction.interactive_update_roll_status` changes the
multiple-price identities and restitches adjusted prices; that is a separate
live-production decision.

**Designed to fail loudly** (see `docs/tushare_chinese_futures.md` for the
full failure model):

- If Tushare *changes already-stored history* inside the overlap window, the
  update refuses to write for that contract and names the changed dates.
  You accept new history deliberately:
  `python -m sysinit.futures.seed_price_data_from_tushare --instrument SHFE_CU --contract 202609 --no-resume`
- If Tushare *lists a brand-new product family*, known instruments still
  update but the run exits non-zero until you add a manifest row.
- Price spikes beyond `max_price_spike` block that contract's write.

The cell below runs the catalogue validation live (6 API calls) if the
client was configured — the same preflight the seed performs.
"""
        ),
        code(
            """
import subprocess, sys

if client is not None:
    result = subprocess.run(
        [sys.executable, "-m", "sysinit.futures.seed_price_data_from_tushare",
         "--dry-run"],
        capture_output=True, text=True, cwd=R.REPO_ROOT,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            "Tushare dry-run failed:\\n" + diagnostic[-2_000:]
        )
    visible = [
        line
        for line in result.stdout.splitlines()
        if " DEBUG " not in line and " INFO " not in line
        and not line.startswith("Configuring sim logging")
    ]
    print("\\n".join(visible[-12:]))
else:
    print("Tushare client unavailable - skipping the live catalogue check")"""
        ),
        md(
            """
**Next**: notebook 02 — roll calendars, including the fully manual workflow.
"""
        ),
    ]
    return cells


def _rolls_cells() -> list:
    cells = [
        md(
            """
# 03 — Rolls: cycles, calendars, and the manual workflow

Stitching is where a futures backtest is won or lost, and Chinese futures
roll differently from Western ones. This notebook explains the roll
configuration, then walks the **manual calendar workflow** — hand-setting
roll dates and rebuilding the stitched series — which is how this dataset
will be maintained going forward.

## Roll parameters, Chinese-style

One row per instrument in `data/futures/csvconfig/rollconfig.csv`:

- **HoldRollCycle** — the delivery months you actually hold. Chinese
  commodity liquidity concentrates in a few months (classically Jan/May/Sep
  = `FKU`), unlike Western markets where most listed months trade.
- **PricedRollCycle** — the liquid months a carry contract may come from.
- **RollOffsetDays** — how many days *before expiry* you roll. Chinese
  liquidity hands over unusually early (retail must exit before the delivery
  month), typically 30-60 days.
- **ExpiryOffset** — approximate expiry day within the month (most Chinese
  contracts: the 15th → offset 14; INE crude/fuel: end of the *prior* month
  → offset -1).
- **CarryOffset** — where carry is measured: `-1` = against the *previous*
  liquid contract (preferred: it mirrors the roll-down you actually earn),
  `+1` = against the next one. `-1` needs the previous contract to keep
  trading through enough of your holding window. The current manually
  reviewed file uses `-1` on 75 of 95 instruments; front-held products
  (CFFEX index futures — the previous month is already expired while you
  hold) and the bond quartet get `+1`.
"""
        ),
        code(SETUP_CELL),
        code(
            """
rollconfig = pd.read_csv(R.REPO_ROOT / "data/futures/csvconfig/rollconfig.csv",
                         index_col="Instrument")
rollconfig.loc[["SHFE_RB", "DCE_JD", "CFFEX_IF", "CFFEX_T", "INE_SC"]]"""
        ),
        md(
            """
Reading `SHFE_RB` (rebar): hold only Jan/May/Oct (`FKV`), roll ~55 days
before expiry, expiry mid-month, carry from the previous liquid contract.
`CFFEX_IF` holds every month (front-held, rolls at expiry, carry `+1`).

## The roll calendar

The calendar is the *realised historical* roll schedule: one CSV row per roll,
saying when you switched, into what, and what the carry contract was. It is a
hand-editable initialization/recovery artifact — the repo's own docs encourage
crafting it manually. Once multiple prices exist, the live system does not
consult this CSV to decide today's contract.
"""
        ),
        code(
            """
calendar_path = R.REPO_ROOT / "data/futures/roll_calendars_csv/DCE_JD.csv"
contract_columns = {"current_contract": str, "next_contract": str,
                    "carry_contract": str}
calendar = pd.read_csv(calendar_path, index_col="DATE_TIME", parse_dates=True,
                       dtype=contract_columns)
print(f"DCE_JD (eggs): {len(calendar)} rolls "
      f"({calendar.index[0]:%Y-%m-%d} to {calendar.index[-1]:%Y-%m-%d})")
calendar.tail(6)"""
        ),
        md(
            """
Each row's `DATE_TIME` is the **inclusive last timestamp** at which the
stitched series holds `current_contract`. The next row's contract begins at
the first price observation **strictly after** that timestamp. Internally the
builder slices one interval from `previous DATE_TIME + 1 second` through the
current `DATE_TIME`, so there is neither an overlap nor an ambiguous boundary.
`carry_contract` pairs with `current_contract` over that interval.

Two structural rules: dates strictly increasing, and each row's `current`
equals the previous row's `next` (an unbroken chain). Do not read the calendar
timestamp as "the first close in the new contract"; it is the last close in
the old one.

## The native manual workflow, end to end

Upstream treats calendar creation as one-instrument craftsmanship, not a
batch inference job. First generate a candidate from your manually chosen
`rollconfig.csv` row and raw contract prices into a temporary directory. Then
inspect it, edit it if necessary, validate it, and only then replace the saved
calendar deliberately.

Below we generate that candidate without touching live data and compare it
with the accepted calendar. For the visual experiment we then copy the
accepted calendar and move one explicit historical roll from 17 June to
3 June 2026. Hard-coding the contracts and dates makes the experiment stable
when newer calendar rows are appended.

This is a **hindsight sensitivity experiment**, not a causal roll rule. Seeing
both contracts in the completed historical data proves that a stitch can be
calculated; it does not prove that an operator would have chosen the earlier
date from liquidity information available at that time.
"""
        ),
        code(
            """
import shutil, tempfile
from pathlib import Path
from IPython.utils.io import capture_output
from sysinit.futures.rollcalendars_from_db_prices_to_csv import (
    build_and_write_roll_calendar,
)

scratch_root = Path(tempfile.mkdtemp(prefix="manual_roll_demo_"))
candidate_dir = scratch_root / "candidate"
candidate_dir.mkdir()

try:
    with capture_output() as candidate_log:
        candidate_calendar = build_and_write_roll_calendar(
            "DCE_JD",
            output_datapath=str(candidate_dir),
            write=True,
            check_before_writing=False,
        )
except Exception:
    print(candidate_log.stdout)
    raise

accepted_view = calendar.reset_index().rename(
    columns={"DATE_TIME": "accepted_date"})
candidate_view = candidate_calendar.reset_index().rename(
    columns={"current_roll_date": "candidate_date"})
contract_names = list(contract_columns)
accepted_view[contract_names] = accepted_view[contract_names].astype(str)
candidate_view[contract_names] = candidate_view[contract_names].astype(str)
calendar_comparison = accepted_view.merge(
    candidate_view,
    on=contract_names,
    how="outer",
    validate="one_to_one",
)
calendar_comparison["difference_days"] = (
    calendar_comparison["candidate_date"]
    - calendar_comparison["accepted_date"]
).dt.days
print(f"native candidate: {len(candidate_calendar)} rolls; "
      f"accepted: {len(calendar)}; "
      f"dates changed: {(calendar_comparison['difference_days'] != 0).sum()}")
display(calendar_comparison.tail(5))

scratch = scratch_root / "reviewed_edit"
scratch.mkdir()
shutil.copy(calendar_path, scratch / "DCE_JD.csv")

edited = pd.read_csv(scratch / "DCE_JD.csv", index_col="DATE_TIME",
                     parse_dates=True, dtype=contract_columns)
target_current = "20260700"
target_next = "20260800"
old_date = pd.Timestamp("2026-06-17 23:00:00")
new_date = pd.Timestamp("2026-06-03 23:00:00")
target = (
    (edited["current_contract"] == target_current)
    & (edited["next_contract"] == target_next)
)
assert target.sum() == 1
assert edited.index[target][0] == old_date
edited = edited.rename(index={old_date: new_date}).sort_index()
edited.to_csv(scratch / "DCE_JD.csv")
print(f"moved roll {target_current} -> {target_next}: {old_date:%Y-%m-%d} "
      f"=> {new_date:%Y-%m-%d}")"""
        ),
        md(
            """
### Validate the hand-edited calendar

`check_saved_roll_calendar` re-runs the structural checks (strictly increasing
dates and an unbroken current/next contract chain) and — the important one —
verifies **both contracts actually have prices on each roll date**. A hand-set
date where the incoming contract wasn't trading yet would corrupt the stitch;
this catches it.
"""
        ),
        code(
            """
from sysinit.futures.rollcalendars_from_db_prices_to_csv import (
    check_saved_roll_calendar,
)

try:
    with capture_output() as validation_log:
        checked_calendar = check_saved_roll_calendar(
            "DCE_JD", input_datapath=str(scratch))
except Exception:
    # On failure the native checker names the bad row and contract. Do not hide it.
    print(validation_log.stdout)
    raise

# This line is reached only after both checks have returned successfully.
assert checked_calendar is not None
print("checker returned: monotonicity and price-validity both passed")"""
        ),
        md(
            """
### Rebuild multiple prices and the adjusted series from it

`process_multiple_prices_single_instrument` combines contract prices with
the calendar. Two things to know:

- `adjust_calendar_to_prices=False` — respect the hand-set dates exactly
  (the default `True` re-snaps dates to price overlaps, which is right for
  *generated* calendars but would undo manual edits).
- `ADD_TO_DB=False` here, so this demo writes nothing; set it `True` when
  maintaining for real.
- Held-price dates anchor the multiple-price table, so a date without a real
  held `PRICE` is omitted before `FORWARD` and `CARRY` are aligned. The
  adjusted-price stitch is deliberately strict (`forward_fill=False`) and the
  batch adjusted-price builder rejects any residual missing held price rather
  than carrying an old contract's close across a new contract label.

The thin China-universe adapter follows the same safe rule:
`rebuild_tushare_multiple_adjusted --instrument DCE_JD` consumes the reviewed
calendar unchanged. It never creates or re-snaps calendars. The direct
function below is useful here because this demonstration reads a scratch
directory and must not write to the database. For both reconstructions we
assert the two Panama identities directly: within a held contract
`d(adjusted) = d(PRICE)`; on the first row of a new held contract,
`d(adjusted) = PRICE[t] - FORWARD[t-1]`.
"""
        ),
        code(
            """
from sysinit.futures.multipleprices_from_db_prices_and_csv_calendars_to_db import (
    process_multiple_prices_single_instrument,
)
from sysobjects.adjusted_prices import futuresAdjustedPrices

try:
    with capture_output() as rebuild_log:
        accepted_multiple = process_multiple_prices_single_instrument(
            "DCE_JD",
            roll_calendar=calendar,
            adjust_calendar_to_prices=False,
            ADD_TO_DB=False,
            ADD_TO_CSV=False,
        )
        edited_multiple = process_multiple_prices_single_instrument(
            "DCE_JD",
            csv_roll_data_path=str(scratch),
            adjust_calendar_to_prices=False,
            ADD_TO_DB=False,
            ADD_TO_CSV=False,
        )
except Exception:
    print(rebuild_log.stdout)
    raise

accepted_adjusted = futuresAdjustedPrices.stitch_multiple_prices(
    accepted_multiple, forward_fill=False
)
edited_adjusted = futuresAdjustedPrices.stitch_multiple_prices(
    edited_multiple, forward_fill=False
)
assert len(accepted_multiple) == len(edited_multiple)
assert not pd.Series(accepted_adjusted).isna().any()
assert not pd.Series(edited_adjusted).isna().any()


def panama_identity_row(label, multiple, adjusted):
    multiple = pd.DataFrame(multiple)
    aligned = pd.concat({
        "adjusted": pd.Series(adjusted),
        "price": multiple["PRICE"],
        "forward": multiple["FORWARD"],
        "contract": multiple["PRICE_CONTRACT"],
    }, axis=1, join="inner").dropna(subset=["adjusted", "price", "contract"])

    same_contract = aligned["contract"].eq(aligned["contract"].shift())
    actual_change = aligned["adjusted"].diff()
    expected_change = aligned["price"].diff().where(
        same_contract,
        aligned["price"] - aligned["forward"].shift(),
    )
    checked = actual_change.notna() & expected_change.notna()
    error = (actual_change[checked] - expected_change[checked]).abs()
    assert len(error) > 0
    assert error.max() < 1e-8
    return dict(
        reconstruction=label,
        rows=len(aligned),
        within_contract_checks=int((checked & same_contract).sum()),
        roll_boundary_checks=int((checked & ~same_contract).sum()),
        maximum_identity_error=error.max(),
    )


identity_table = pd.DataFrame([
    panama_identity_row("accepted calendar", accepted_multiple,
                        accepted_adjusted),
    panama_identity_row("hand-edited calendar", edited_multiple,
                        edited_adjusted),
]).set_index("reconstruction")
display(identity_table)
print(f"accepted and edited rebuilds: {len(edited_multiple)} rows each")
display(edited_multiple.loc[
    new_date - pd.Timedelta(days=3):old_date + pd.Timedelta(days=3),
    ["PRICE", "FORWARD", "PRICE_CONTRACT", "FORWARD_CONTRACT"],
])"""
        ),
        code(
            """
window = slice(new_date - pd.Timedelta(days=60), old_date + pd.Timedelta(days=60))
frame = pd.concat(
    {
        "accepted calendar": accepted_adjusted,
        "hand-edited calendar": edited_adjusted,
    },
    axis=1,
    join="inner",
).dropna()
difference = frame["hand-edited calendar"] - frame["accepted calendar"]

fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
frame.loc[window].plot(
    ax=axes[0], title="DCE_JD adjusted prices around the moved roll")
axes[0].axvline(old_date, color="grey", linestyle=":", label="old roll")
axes[0].axvline(new_date, color="red", linestyle=":", label="new roll")
axes[0].set_ylabel("additive-Panama price units")
axes[0].legend()

difference.loc[window].plot(
    ax=axes[1], color="tab:purple",
    title="hand-edited minus accepted adjusted level")
axes[1].axhline(0.0, color="black", linewidth=1)
axes[1].axvline(old_date, color="grey", linestyle=":")
axes[1].axvline(new_date, color="red", linestyle=":")
axes[1].set_ylabel("price-unit difference")
axes[1].set_xlabel("date")
plt.tight_layout()

before = difference[difference.index < new_date]
transition = difference[
    (difference.index >= new_date) & (difference.index <= old_date)
]
after = difference[difference.index > old_date]
assert np.isclose(before.min(), before.max())
assert np.allclose(after, 0.0)
print(f"before {new_date:%Y-%m-%d}: constant {before.iloc[0]:.2f}")
print(f"between roll dates: {transition.min():.2f} to {transition.max():.2f}")
print(f"after {old_date:%Y-%m-%d}: max |difference| {after.abs().max():.2f}")

shutil.rmtree(scratch_root)"""
        ),
        md(
            """
The two series differ by a constant before the moved roll — Panama
back-adjustment propagates each roll's price gap into all earlier history,
so changing one roll date re-levels the past, never the future.

### Doing it for real

1. Edit the instrument's row in `data/futures/csvconfig/rollconfig.csv`
   to make the policy decision yourself.
2. Call `build_and_write_roll_calendar` for that one instrument with an
   `output_datapath` in a temporary directory.
3. Inspect and, where justified, hand-edit the candidate CSV.
4. Validate the temporary file with
   `check_saved_roll_calendar("<CODE>", input_datapath="<TEMP_DIR>")`.
5. Deliberately copy the accepted file into
   `data/futures/roll_calendars_csv/`.
6. Rebuild and write while preserving those accepted dates:

```bash
python -m sysinit.futures.rebuild_tushare_multiple_adjusted --instrument <CODE>
```

The direct API equivalent is:

```python
from sysinit.futures.multipleprices_from_db_prices_and_csv_calendars_to_db import (
    process_multiple_prices_single_instrument,
)
from sysinit.futures.adjustedprices_from_db_multiple_to_db import (
    process_adjusted_prices_single_instrument,
)

multiple = process_multiple_prices_single_instrument(
    "<CODE>", adjust_calendar_to_prices=False, ADD_TO_DB=True)
process_adjusted_prices_single_instrument(
    "<CODE>", multiple_prices=multiple, ADD_TO_DB=True)
```

For a brand-new instrument you also need rows in `instrumentconfig.csv` and
`spreadcosts.csv` first — see `docs/tushare_chinese_futures.md`.

### Historical starts and live rolls

The accepted calendars are retained data, not something the batch rebuild
regenerates. Four intentionally begin at their usable liquid era because
earlier contracts cannot form a sound chain: `SHFE_RU` at `19990100`,
`SHFE_FU` at `20190100`, and `CZCE_SF`/`CZCE_SM` at `20170100`.

Daily appending uses the final multiple-price identities. A live contract
change is made through `interactive_update_roll_status`, which consults the
manual roll parameters and actual Mongo expiry, writes a new multiple-price
contract row, and restitches adjusted prices. It does **not** update or read
the historical calendar CSV. Its normal construction is strict; only after a
failure can the interactive command offer an explicit, less-accurate
forward-fill recovery. That operator-approved escape hatch is not the
historical rebuild policy.

**Next**: notebook 03 — your first backtest, stage by stage.
"""
        ),
    ]
    return cells


def _backtest_anatomy_cells() -> list:
    cells = [
        md(
            """
# 04 — Anatomy of a backtest

pysystemtrade backtests are a chain of *stages*, each cacheable and
inspectable. This notebook builds a small four-instrument system and pulls
every stage apart so you know exactly where any number comes from.

```
rawdata -> rules -> forecastScaleCap -> combForecast -> positionSize -> portfolio -> accounts
```

We start from the book's "chapter 15" configuration (6 EWMAC speeds +
carry, fixed forecast weights) and override just the universe and capital.
"""
        ),
        code(SETUP_CELL),
        code(
            """
from sysdata.config.configdata import Config
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from systems.provided.futures_chapter15.basesystem import futures_system

INSTRUMENTS = ["SHFE_RB", "CFFEX_IF", "DCE_M", "CZCE_TA"]
PLOT_START = "2015-01-01"

config = Config("systems.provided.futures_chapter15.futuresconfig.yaml")
config.instruments = INSTRUMENTS
config.instrument_weights = {code: 1 / len(INSTRUMENTS) for code in INSTRUMENTS}
config.notional_trading_capital = 10_000_000
config.base_currency = "CNH"          # everything in renminbi; FX == 1

system = futures_system(data=dbFuturesSimData(), config=config)
print(system)"""
        ),
        md(
            """
## Stage 1: `rawdata` — prices and volatility

Everything downstream is *risk-scaled*, so the first thing the system
computes is a robust daily return volatility (EWMA with a floor). The adjusted
price is an additive-Panama level: price differences are meaningful, but its
absolute vertical level is arbitrary.
"""
        ),
        code(
            """
code_ = "SHFE_RB"
prices = system.rawdata.get_daily_prices(code_)
vol = system.rawdata.daily_returns_volatility(code_)
plot_end = prices.index[-1]
plot_label = f"{PLOT_START} to {plot_end:%Y-%m-%d}"

fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
prices.loc[PLOT_START:].plot(
    ax=axes[0], title=f"{code_} additive-Panama level, {plot_label}")
axes[0].set_ylabel("adjusted price units")
vol.loc[PLOT_START:].plot(
    ax=axes[1], title="daily return volatility")
axes[1].set_ylabel("price units per day")
axes[1].set_xlabel("date")
plt.tight_layout()"""
        ),
        md(
            """
## Stage 2: `rules` — raw forecasts

A trading rule is just a function of data → signal. EWMAC divides a moving
average crossover by volatility, so forecasts are comparable across
instruments; carry annualises the price gap between the carry contract and
the priced contract.
"""
        ),
        code(
            """
ewmac_raw = system.rules.get_raw_forecast(code_, "ewmac64_256")
carry_raw = system.rules.get_raw_forecast(code_, "carry")
raw_forecasts = pd.concat(
    {"ewmac64_256": ewmac_raw, "carry": carry_raw}, axis=1
).loc[PLOT_START:]
fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
raw_forecasts["ewmac64_256"].plot(
    ax=axes[0], title=f"raw EWMAC forecast, {code_}, {plot_label}")
axes[0].set_ylabel("raw EWMAC units")
raw_forecasts["carry"].plot(
    ax=axes[1], title=f"raw carry forecast, {code_}")
axes[1].set_ylabel("raw carry units")
axes[1].set_xlabel("date")
plt.tight_layout()"""
        ),
        md(
            """
## Stage 3: `forecastScaleCap` — scale to ±10, cap at ±20

Raw forecasts have arbitrary units. A *forecast scalar* is an externally
calibrated number intended to put a rule near an average absolute forecast of
10 over a broad reference population; it is not fitted to this instrument or
guaranteed to hit exactly 10 in this sample. Forecasts are then capped at ±20.
"""
        ),
        code(
            """
scaled = system.forecastScaleCap.get_capped_forecast(code_, "carry")
print(f"carry forecast scalar (fixed in config): "
      f"{float(system.forecastScaleCap.get_forecast_scalar(code_, 'carry').iloc[-1]):.1f}")
print(f"observed average |scaled forecast| for {code_}: "
      f"{scaled.dropna().abs().mean():.2f}  (external calibration target: 10)")
ax = scaled.loc[PLOT_START:].plot(
    title=f"scaled and capped carry forecast, {code_}, {plot_label}")
ax.set_ylabel("forecast units")
ax.set_xlabel("date")"""
        ),
        md(
            """
## Stage 4: `combForecast` — one forecast per instrument

Rules are combined with *forecast weights*, then multiplied by an externally
calibrated *forecast diversification multiplier* (FDM). Its population target
is again an average magnitude near 10, not an exact in-sample identity.
Chapter 15 uses fixed weights — note the fast EWMACs get zero weight (they
trade too much for their edge; costs).
"""
        ),
        code(
            """
print("forecast weights (fixed):", system.config.forecast_weights)
print("externally calibrated FDM:", system.config.forecast_div_multiplier)
combined = system.combForecast.get_combined_forecast(code_)
print(f"observed average |combined forecast| for {code_}: "
      f"{combined.dropna().abs().mean():.2f}")
ax = combined.loc[PLOT_START:].plot(
    title=f"combined forecast, {code_}, {plot_label}")
ax.set_ylabel("forecast units")
ax.set_xlabel("date")"""
        ),
        md(
            """
## Stage 5: `positionSize` — from forecast to contracts

The volatility-targeting engine: capital × risk target defines a daily cash
volatility budget; an instrument's *block value* (point size × price × vol)
says how much risk one contract carries; the ratio is the position for an
average-strength (=10) forecast, scaled by the actual forecast.
"""
        ),
        code(
            """
subsystem_position = system.positionSize.get_subsystem_position(code_)
print(f"capital: {system.positionSize.get_notional_trading_capital():,.0f} CNH, "
      f"risk target {system.positionSize.get_percentage_vol_target():.0f}%/yr")
ax = subsystem_position.loc[PLOT_START:].plot(
    title=f"subsystem position, {code_}, {plot_label}")
ax.set_ylabel("contracts")
ax.set_xlabel("date")"""
        ),
        md(
            """
## Stages 6-7: `portfolio` and `accounts`

Instrument weights (fixed 25% each here) and the instrument diversification
multiplier scale subsystem positions into portfolio positions; `accounts`
computes P&L net of costs (Chinese spread costs come from the imported
half-spreads; commissions are ~zero and modelled as zero). The continuous
target is not what the backtest necessarily holds: buffering avoids small
trades and the account stage rounds the result to whole contracts.
"""
        ),
        code(
            """
target_position = system.portfolio.get_notional_position(code_)
buffered_position = system.accounts.get_buffered_position(code_)
instrument_curve = system.accounts.pandl_for_instrument(code_)
held_position = instrument_curve.pandl_calculator_with_costs.positions
position_comparison = pd.concat(
    {
        "continuous target": target_position,
        "buffered desired (pre-fill)": buffered_position,
        "held after delayed fill": held_position,
    },
    axis=1,
).loc[PLOT_START:]
ax = position_comparison.plot(
    title=f"portfolio target versus buffered position, {code_}, {plot_label}")
ax.set_ylabel("contracts")
ax.set_xlabel("date")"""
        ),
        code(
            """
portfolio_pandl = system.accounts.portfolio()
plotted_portfolio = R.rewrap(portfolio_pandl.percent.as_ts.loc[PLOT_START:])
portfolio_curve = R.cumulative_from_zero(plotted_portfolio.percent.as_ts)
ax = portfolio_curve.plot(
    title=f"four-instrument portfolio cumulative return, {plot_label}")
ax.set_ylabel("percent of capital")
ax.set_xlabel("date")
stats = dict(plotted_portfolio.stats()[0])
{key: stats[key] for key in ["ann_mean", "ann_std", "sharpe", "sortino",
                             "avg_drawdown", "skew", "t_stat", "p_value"]}"""
        ),
        md(
            """
## The account-curve toolkit

`accounts` methods return `accountCurve` / `accountCurveGroup` objects —
pandas Series with extra powers, chainable:

- `.gross` / `.net` / `.costs` — cost decomposition
- `.percent` / `.value_terms` — % of capital vs currency
- `.daily` / `.weekly` / `.monthly` / `.annual` — resampling
- `.stats()`, `.sharpe()`, `.t_stat()`, `.p_value()`, `curve()` ...
"""
        ),
        code(
            """
one_instrument = system.accounts.pandl_for_instrument("SHFE_RB")
daily_decomposition = pd.DataFrame({
    "gross": one_instrument.percent.gross.as_ts,
    "net": one_instrument.percent.net.as_ts,
    "cumulative costs": one_instrument.percent.costs.as_ts,
}).loc[PLOT_START:]
cost_decomposition = R.cumulative_from_zero(daily_decomposition)
fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
cost_decomposition[["gross", "net"]].plot(
    ax=axes[0], title=f"SHFE_RB gross and net cumulative P&L, {plot_label}")
axes[0].set_ylabel("percent of capital")
cost_decomposition["cumulative costs"].plot(
    ax=axes[1], color="tab:red", title="cumulative trading costs")
axes[1].set_ylabel("percent of capital")
axes[1].set_xlabel("date")
plt.tight_layout()"""
        ),
        md(
            """
## Two gotchas worth learning early

1. `portfolio()` returns an `accountCurveGroup`; its `[...]` indexes by
   **instrument name** (`group["SHFE_RB"]`), never by date.
2. An individual `accountCurve` subclasses `pd.Series`, and pandas date
   slicing returns a **plain Series** — the stats methods silently vanish.
   The supported way back is `account_curve_from_returns` (wrapped as
   `R.rewrap`). You'll use this constantly for subperiod analysis.
"""
        ),
        code(
            """
rebar_curve = portfolio_pandl["SHFE_RB"]      # group indexing: by instrument
print(f"group['SHFE_RB'] is a {type(rebar_curve).__name__}, "
      f"sharpe {rebar_curve.sharpe():.3f}")

sliced = rebar_curve["2020-01-01":]           # date slicing degrades it
print("date-sliced type:", type(sliced).__name__)
try:
    sliced.sharpe()
except AttributeError as error:
    print("AttributeError:", error)

recent = R.rewrap(rebar_curve.percent.as_ts["2020-01-01":])
print(f"SHFE_RB 2020+ sharpe, properly rewrapped: {recent.sharpe():.2f}")"""
        ),
        md(
            """
**Next**: the full-universe section below scales up the same native stages.
"""
        ),
    ]
    return cells


FULL_SYSTEM_CELL = """\
from sysdata.config.configdata import Config
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from systems.accounts.accounts_stage import Account
from systems.basesystem import System
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecasting import Rules
from systems.positionsizing import PositionSizing
from systems.rawdata import RawData

data = dbFuturesSimData()
# Keep every stored Chinese history.  A lagged, point-in-time liquidity mask
# decides when each market can receive capital; today's survivor list is never
# projected backwards.
universe = R.chinese_universe(data)
held_volume = R.held_contract_volumes(data, universe)
liquidity = R.liquidity_eligibility(held_volume, force_terminal_close=True)
liquidity_events = R.liquidity_event_table(liquidity, held_volume)

config = Config("systems.provided.futures_chapter15.futuresconfig.yaml")
config.instruments = universe
config.instrument_weights = {code: 1 / len(universe) for code in universe}
config.instrument_div_multiplier = 2.5   # IDM at its cap; estimation is in notebook 04
config.notional_trading_capital = 100_000_000
config.base_currency = "CNH"
# Research choice for ended histories: charge configured cash/spread costs as
# observed, without the default ex-post final-volatility cost normalisation.
config.vol_normalise_currency_costs = False

system = System(
    [
        Account(),
        R.PointInTimePortfolios(liquidity),
        PositionSizing(),
        RawData(),
        ForecastCombine(),
        ForecastScaleCap(),
        Rules(),
    ],
    data,
    config,
)
print(f"{len(universe)} instruments, rules: {list(config.trading_rules.keys())}")"""


def _full_universe_cells() -> list:
    cells = [
        md(
            """
# 05 — EWMAC + carry over the historical Chinese universe

Time to run trend and carry across every stored history. Two things matter
before the result: market membership must use only information available at
the time, and a many-instrument account curve must be decomposed as actual
capital contributions.

## Config exclusions are not a historical universe

Two mechanisms, for two intents:

1. **Per-study exclusions** — pass a list to `config.instruments`. This is
   appropriate for a declared experiment, but a list chosen today creates
   survivor bias if projected over the whole backtest.
2. **Standing config exclusions** — `config.exclude_instrument_lists`:
   - `ignore_instruments`: the system *cannot see them at all* (removed
     inside `get_instrument_list()`);
   - `bad_markets` / `trading_restrictions`: flags used by production and
     dynamic optimisation. In a static-weight backtest they do **not** by
     themselves turn an existing weight into zero. Add the instrument to
     `allocate_zero_instrument_weights_to_these_instruments`, or set its
     explicit weight to zero.

The cell below demonstrates the standing mechanism without running any P&L
(instrument lists are cheap; nothing else is computed).
"""
        ),
        code(SETUP_CELL),
        code(
            """
from sysdata.config.configdata import Config
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from systems.provided.futures_chapter15.basesystem import futures_system

demo_universe = R.chinese_universe(dbFuturesSimData())
demo_config = Config("systems.provided.futures_chapter15.futuresconfig.yaml")
demo_config.instruments = demo_universe
# TRAP: the chapter-15 yaml carries instrument_weights for its own six
# instruments, and instrument_weights BEATS instruments when the system
# picks its universe. Override it or the requested universe silently becomes 6.
demo_config.instrument_weights = {code: 1 / len(demo_universe)
                                  for code in demo_universe}
demo_config.exclude_instrument_lists = dict(
    ignore_instruments=["CZCE_JR", "CZCE_LR"],   # pretend we never heard of them
    trading_restrictions=[],
    bad_markets=["SHFE_WR"],                     # flag it; no automatic zero here
)
demo_config.allocate_zero_instrument_weights_to_these_instruments = ["SHFE_WR"]
demo_system = futures_system(data=dbFuturesSimData(), config=demo_config)
print(f"requested: {len(demo_universe)}")
print(f"visible to the system (ignore list removed): "
      f"{len(demo_system.get_instrument_list())}")
print(f"flagged bad (still simulated): "
      f"{demo_system.get_list_of_bad_markets()}")"""
        ),
        md(
            """
## A lagged liquidity rule, not a 2026 survivor list

`R.chinese_universe(data)` returns all 95 stitched histories, including old
names and markets whose liquidity later died. We reconstruct the held
contract's reported volume and require seasoning plus a rolling volume
threshold using observations through each close. The result is a Boolean
eligibility panel: an old market may receive capital while it was genuinely
liquid and then leave; a new market joins only after it has enough observable
history. Native `delayfill=True` shifts a target by one system business row,
so today's close and volume can never change the return just observed. On an
exchange holiday that business row has no fresh quote; the upstream account
then approximates execution with a stale or missing price before the next
observed session. We disclose that limitation rather than adding another
account engine.

The declared research rule is intentionally plain: a 20-observed-session
mean enters at 130 contracts and exits below 70, providing hysteresis; known
terminal histories are targeted flat before their final stored close so the
native account can book the closing trade and cost. These are capacity-proxy
assumptions, not optimised parameters.

This is still a research proxy. Daily volume is not order-book depth, open
interest, exchange limits, or proof that a 100m-CNH account could fill. The
event table makes every entry and exit auditable rather than hiding them in a
hard-coded "dead market" list.

## The native system: six EWMAC speeds + carry

Chapter-15 configuration again: fixed forecast weights (the two fastest
EWMACs get zero — too expensive), carry at 50%. Equal instrument weights
for now; estimation is notebook 04's subject. `base_currency: CNH`, 100m
CNH notional. The native system still constructs forecasts, volatility,
positions and modelled costs. The small research helper only gates and
renormalises native portfolio weights through time before positions are sized.

One deliberate non-default is `vol_normalise_currency_costs=False`. The
standard option rescales all historical cash costs using each instrument's
final 180-day price volatility; that is future-informed and becomes zero or
undefined for some terminal zombie histories. Here configured commissions and
spreads are charged directly at native fills, without that ex-post rescaling;
notebooks 04–06 keep the same research choice for comparable PIT portfolios.
"""
        ),
        code(FULL_SYSTEM_CELL),
        code(
            """
active_count = liquidity.sum(axis=1)
average_volume = R.trailing_liquidity(held_volume)
latest_average_volume = average_volume.reindex(liquidity.index).ffill().iloc[-1]
latest_active_volume = latest_average_volume[liquidity.iloc[-1]]
fig, axes = plt.subplots(1, 2, figsize=(13, 4))
active_count.plot(ax=axes[0], title="point-in-time eligible markets")
axes[0].set_ylabel("market count")
np.log1p(latest_active_volume).plot(
    ax=axes[1], kind="hist", bins=30,
    title="latest active markets: held-contract volume")
axes[1].set_xlabel("log(1 + 20-session mean contracts); zeros stay at zero")
plt.tight_layout()

print(f"eligibility dates: {liquidity.index[0].date()} to "
      f"{liquidity.index[-1].date()}; latest active: {int(active_count.iloc[-1])}")
display(liquidity_events.tail(12))"""
        ),
        code(
            """
portfolio_pandl = system.accounts.portfolio()
curve = portfolio_pandl.percent
R.cumulative_from_zero(curve.as_ts).plot(
    title=f"EWMAC+carry, {len(system.get_instrument_list())} "
          "stored Chinese histories; point-in-time eligible")
R.stats_table({"point-in-time equal weight, net": portfolio_pandl})"""
        ),
        md(
            """
## Where does it come from? Asset-class decomposition

The safest additive decomposition starts from cash P&L. Divide every
instrument's `value_terms` by the **same whole-portfolio** 100m-CNH notional,
then sum the resulting percentage-point contributions. This avoids relying on
the account curve's percentage-view conventions and gives us a direct daily
reconciliation to the portfolio.

Zeros are real observations: a closed weekday has zero P&L. We use
`sum(min_count=1)` so an all-missing row remains missing without converting
genuine zero-return days to `NaN`.
"""
        ),
        code(
            """
notional = system.accounts.get_notional_capital()
instrument_contributions = (
    portfolio_pandl.value_terms.to_frame() / notional * 100
)
portfolio_return = portfolio_pandl.value_terms.as_ts / notional * 100
reconciled = instrument_contributions.sum(axis=1, min_count=1)
comparison = pd.concat([portfolio_return.rename("portfolio"),
                        reconciled.rename("sum of instruments")], axis=1).dropna()
assert (comparison["portfolio"] - comparison["sum of instruments"]).abs().max() < 1e-8

asset_class = dict(data.get_instrument_asset_classes())
by_class = instrument_contributions.T.groupby(
    pd.Series(asset_class)
).sum(min_count=1).T

R.cumulative_from_zero(by_class).plot(
    title="cumulative contribution by asset class (% of capital)")
R.stats_table({name: R.rewrap(series.dropna())
               for name, series in by_class.items()})
print(f"maximum daily reconciliation error: "
      f"{(comparison['portfolio'] - comparison['sum of instruments']).abs().max():.3g}")"""
        ),
        md(
            """
## Per-instrument contribution Sharpe: describe the cross-section

With 95 histories you should think in distributions, not favourites. Markets
within one portfolio share rules, macro shocks and often contracts in the
same commodity complex, so a one-sample t-test that pretends 95 independent
draws is not valid inference. We report the median, interquartile range and
asset-class medians. These describe this backtest; they do not estimate a
population p-value.
"""
        ),
        code(
            """
import warnings

# Each series is its actual contribution to whole-portfolio capital. A market
# that was never eligible has zero variance and therefore undefined Sharpe.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="invalid value encountered in scalar divide",
        category=RuntimeWarning,
    )
    sharpes = pd.Series({
        code: R.rewrap(series.dropna()).sharpe()
        for code, series in instrument_contributions.items()
    })

ordered_sharpes = sharpes.sort_values()
ax = ordered_sharpes.plot.barh(
    figsize=(9, 18),
    color=np.where(ordered_sharpes < 0, "firebrick", "steelblue"),
)
ax.set_title("net Sharpe of each instrument's actual portfolio contribution")
ax.set_xlabel("Sharpe ratio")
ax.set_ylabel("instrument")

clean = sharpes.dropna()
summary = pd.Series({
    "defined instruments": len(clean),
    "median": clean.median(),
    "25th percentile": clean.quantile(0.25),
    "75th percentile": clean.quantile(0.75),
    "negative": int((clean < 0).sum()),
})
display(summary.to_frame("contribution Sharpe"))

class_summary = pd.DataFrame({"sharpe": clean}).assign(
    asset_class=pd.Series(asset_class)
).dropna().groupby("asset_class")["sharpe"].agg(
    count="count", median="median",
    q25=lambda values: values.quantile(0.25),
    q75=lambda values: values.quantile(0.75),
)
class_summary"""
        ),
        md(
            """
## Costs sanity check

The standardised "Sharpe-ratio cost per trade" makes instruments comparable;
Rob's rule of thumb is to investigate markets above roughly 0.01. This is a
latest-data diagnostic, not a historical liquidity screen. The portfolio
curve above uses each contract's configured cash/spread cost on its actual
trades.
"""
        ),
        code(
            """
sr_costs = pd.Series({
    code: system.accounts.get_SR_cost_per_trade_for_instrument(code)
    for code in system.get_instrument_list()
}).sort_values()
latest_active = liquidity.iloc[-1]
active_costs = sr_costs[latest_active.reindex(sr_costs.index).fillna(False)]
cheapest = active_costs.head(5)
most_expensive = active_costs.tail(5).sort_values(ascending=False)
ranked_costs = pd.DataFrame({
    "cheapest instrument": cheapest.index,
    "cheapest SR cost": cheapest.to_numpy(),
    "most expensive instrument": most_expensive.index,
    "most expensive SR cost": most_expensive.to_numpy(),
}, index=pd.RangeIndex(1, 6, name="rank"))
display(ranked_costs)
print(f"finite SR costs: {np.isfinite(sr_costs).sum()} of {len(sr_costs)} stored; "
      f"latest-active above 0.01: {(active_costs > 0.01).sum()} "
      f"of {len(active_costs)}")"""
        ),
        md(
            """
Read the figures produced by this run, not a result sentence copied from an
older data snapshot. Breadth and cost diagnostics can support the design;
they do not turn the best individual backtests into a selection rule. The
liquidity thresholds are also assumptions to stress, not fitted truth.

**Next**: notebook 04 separates weighting from pooling; notebook 05 then asks
whether carry or trend did the work.
"""
        ),
    ]
    return cells


def _weights_cells() -> list:
    cells = [
        md(
            """
# 08 — Weights and fitting: estimation, and pooling across instruments

So far every weight was fixed. This notebook turns the estimation machinery
on — for real, over the historically eligible universe — and answers the two questions that
matter most in practice:

1. **Which optimiser?** The registry contains exactly four:
   `equal_weights`, `shrinkage`, `handcraft` (the default: hierarchical
   clustering into pairs + a Sharpe-based tilt), and `one_period`.
   (Older docs mention bootstrapping; it is no longer in the code.)
2. **Fit across or within instruments?** Five independent pooling switches
   control whether estimation shares information across instruments:

| config key | pools what |
|---|---|
| `forecast_scalar_estimate.pool_instruments` | scalar from cross-sectional absolute forecasts |
| `forecast_correlation_estimate.pool_instruments` | rule correlations |
| `forecast_weight_estimate.pool_gross_returns` | rule gross returns |
| `forecast_cost_estimates.use_pooled_costs` | full rule SR-cost summaries |
| `forecast_cost_estimates.use_pooled_turnover` | rule turnovers |

Pooling matters enormously here: half the Chinese universe has under ten
years of history — fitting rule weights on three years of one instrument's
returns is mostly fitting noise.

The actual defaults are deliberately mixed: scalars, correlations, gross
rule returns and turnover are pooled; **full costs are not**. This preserves
each contract's cost per trade while borrowing the rule's more stable
turnover estimate. "Pool everything" is neither the default nor good advice.

One wiring rule to burn in: **`config.instrument_weights` beats
`config.instruments`** when the system decides its universe. For estimated
runs you must *not* set `instrument_weights`, and must set `instruments`.
"""
        ),
        code(SETUP_CELL),
        code(
            """
R.limit_blas_threads()   # ~90x90 matrix ops: OpenBLAS threading is pure overhead

from sysdata.config.configdata import Config
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from systems.accounts.accounts_stage import Account
from systems.basesystem import System
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecasting import Rules
from systems.positionsizing import PositionSizing
from systems.rawdata import RawData

data = dbFuturesSimData()
universe = R.chinese_universe(data)
held_volume = R.held_contract_volumes(data, universe)
liquidity = R.liquidity_eligibility(held_volume, force_terminal_close=True)
ever_eligible_universe = list(liquidity.columns[liquidity.any(axis=0)])
never_eligible = sorted(set(universe) - set(ever_eligible_universe))
assert len(universe) == 95
assert len(ever_eligible_universe) == 93
assert never_eligible == ["CZCE_LR", "CZCE_PM"]

EWMAC = "systems.provided.rules.ewmac.ewmac"
EWMAC_DATA = ["rawdata.get_daily_prices", "rawdata.daily_returns_volatility"]
CARRY = "systems.provided.rules.carry.carry"
trading_rules = {
    "ewmac16_64": dict(function=EWMAC, data=EWMAC_DATA,
                       other_args=dict(Lfast=16, Lslow=64)),
    "ewmac32_128": dict(function=EWMAC, data=EWMAC_DATA,
                        other_args=dict(Lfast=32, Lslow=128)),
    "ewmac64_256": dict(function=EWMAC, data=EWMAC_DATA,
                        other_args=dict(Lfast=64, Lslow=256)),
    "carry10": dict(function=CARRY, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=10)),
    "carry60": dict(function=CARRY, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=60)),
    "carry125": dict(function=CARRY, data=["rawdata.raw_carry"],
                     other_args=dict(smooth_days=125)),
}
fixed_scalars = dict(ewmac16_64=3.75, ewmac32_128=2.65, ewmac64_256=1.87,
                     carry10=27.82, carry60=28.40, carry125=29.37)
print(f"{len(universe)} stored histories; {len(ever_eligible_universe)} ever "
      f"eligible for fitting; never eligible: {never_eligible}; "
      f"{len(trading_rules)} rules; "
      f"latest eligible: {int(liquidity.iloc[-1].sum())}")"""
        ),
        md(
            """
## The experimental grid

One helper builds a fresh system per variant (estimation results are cached
*inside* a system, so reusing one across config changes is a classic bug),
extracts what we need, and frees the memory. Estimated variants switch on
the whole stack: forecast scalars, forecast weights + FDM, instrument
weights + IDM. `PointInTimePortfolios` replaces only the native portfolio
stage: it masks and renormalises either fixed or fitted weights using the
declared dated eligibility panel; every upstream forecast and fit remains
native. The only non-causal boundary is stated explicitly: if one of the
known ended histories is still eligible, we place a terminal zero target
early enough for the final stored quote to close it. That ex-post close is a
data-end convention, not a live delisting forecast.

All 95 histories remain in the eligibility audit. The native optimiser cannot
fit `CZCE_LR` or `CZCE_PM`: neither ever passes the rule and neither supplies a
usable cheap-rule account. We therefore omit only those two all-zero columns
from every optimiser variant. This is an ex-post computational omission, but
it cannot change portfolio membership or P&L because their dated weights are
zero on every observation.
"""
        ),
        code(
            """
import copy, gc
import yaml
from IPython.utils.io import capture_output

with open(R.REPO_ROOT / "sysdata/config/defaults.yaml") as handle:
    DEFAULTS = yaml.safe_load(handle)

def estimate_block(name, **overrides):
    block = copy.deepcopy(DEFAULTS[name])
    block.update(overrides)
    return block

def make_system(**config_overrides):
    settings = dict(
        trading_rules=trading_rules,
        instruments=ever_eligible_universe,
        notional_trading_capital=100_000_000,
        base_currency="CNH",
        vol_normalise_currency_costs=False,
    )
    settings.update(config_overrides)
    return System(
        [
            Account(),
            R.PointInTimePortfolios(liquidity),
            PositionSizing(),
            RawData(),
            ForecastCombine(),
            ForecastScaleCap(),
            Rules(),
        ],
        dbFuturesSimData(),
        Config(settings),
    )

def run_variant(label, **config_overrides):
    system = make_system(**config_overrides)

    # The optimisers print progress bars for every fit. Keep the saved output
    # to one result line rather than thousands of terminal redraws.
    with capture_output():
        portfolio = system.accounts.portfolio()
        extracted = dict(
            label=label,
            returns=portfolio.percent.as_ts,
            stats=R.stats_row(portfolio, label),
            forecast_weights_rb=system.combForecast.get_forecast_weights("SHFE_RB"),
            forecast_weights_lc=system.combForecast.get_forecast_weights("GFEX_LC"),
            idm=system.portfolio.get_instrument_diversification_multiplier(),
            fdm_rb=system.combForecast.get_forecast_diversification_multiplier(
                "SHFE_RB"),
            instrument_weights=system.portfolio.get_instrument_weights(),
            scalar_carry60_rb=system.forecastScaleCap.get_forecast_scalar(
                "SHFE_RB", "carry60"),
        )
    del system
    gc.collect()
    print(f"[{label}] done; performance is compared later on common dates")
    return extracted

ESTIMATE_ALL = dict(
    use_forecast_scale_estimates=True,
    use_forecast_weight_estimates=True,
    use_forecast_div_mult_estimates=True,
    use_instrument_weight_estimates=True,
    use_instrument_div_mult_estimates=True,
)
pooled_scalar_without_backfill = estimate_block(
    "forecast_scalar_estimate", pool_instruments=True, backfill=False)
"""
        ),
        md(
            """
### Variant A — everything fixed (the baseline)

Fixed scalars, equal forecast weights within style buckets, equal
instrument weights, IDM pinned at its cap.
"""
        ),
        code(
            """
fixed_forecast_weights = {rule: 0.5 / 3 for rule in
                          ["ewmac16_64", "ewmac32_128", "ewmac64_256"]}
fixed_forecast_weights.update({rule: 0.5 / 3 for rule in
                               ["carry10", "carry60", "carry125"]})
variant_a = run_variant(
    "A fixed",
    forecast_scalars=fixed_scalars,
    forecast_weights=fixed_forecast_weights,
    forecast_div_multiplier=1.5,
    instrument_weights={code: 1 / len(ever_eligible_universe)
                        for code in ever_eligible_universe},
    instrument_div_multiplier=2.5,
)"""
        ),
        md(
            """
### Variant B — handcraft with the repo's mixed pooling defaults

Everything estimated. Scalars, forecast correlations, gross rule returns and
turnover pool across compatible instruments; full cost summaries remain
instrument-specific. Expanding windows, weekly fits. This is the slow honest
machinery—expect a long runtime on 93 historically eligible histories. We make one explicit
causality correction to the defaults: scalar `backfill=False`, so the first
future scalar estimate is not copied into the early sample.
"""
        ),
        code(
            """
variant_b = run_variant(
    "B handcraft pooled",
    **ESTIMATE_ALL,
    forecast_scalar_estimate=pooled_scalar_without_backfill,
)"""
        ),
        md(
            """
### Variant C — shrinkage instead of handcraft

Same estimation stack, but weights come from a shrunk mean-variance
optimisation (correlations and Sharpe ratios shrunk toward priors).
Note a defaults-file trap: `instrument_weight_estimate.shrinkage_mean`
is dead config — the live parameter is `shrinkage_SR`.
"""
        ),
        code(
            """
variant_c = run_variant(
    "C shrinkage pooled",
    **ESTIMATE_ALL,
    forecast_scalar_estimate=pooled_scalar_without_backfill,
    forecast_weight_estimate=estimate_block("forecast_weight_estimate",
                                            method="shrinkage"),
    instrument_weight_estimate=estimate_block("instrument_weight_estimate",
                                              method="shrinkage"),
)"""
        ),
        md(
            """
### Diagnostic D — what breaks when each instrument is fitted alone

This uses B's optimiser but fits scalars, rule correlations and rule gross
returns only on each instrument's own history. It deliberately does **not**
claim to be a portfolio variant: on the current universe a fully unpooled
portfolio is undefined.

The diagnostic asks for weights on veteran SHFE rebar, newer GFEX lithium,
and short-history GFEX palladium. Cost per trade remains market-specific while
turnover remains pooled, matching the repository defaults. The palladium call
may still lack enough annual boundaries to construct a first unpooled fit;
that outcome is caught and printed rather than assumed or allowed to crash
the notebook.
"""
        ),
        code(
            """
unpooled_settings = dict(
    **ESTIMATE_ALL,
    forecast_scalar_estimate=estimate_block("forecast_scalar_estimate",
                                            pool_instruments=False,
                                            backfill=False),
    forecast_correlation_estimate=estimate_block(
        "forecast_correlation_estimate", pool_instruments=False),
    forecast_weight_estimate=estimate_block("forecast_weight_estimate",
                                            pool_gross_returns=False),
)
system_d = make_system(**unpooled_settings)
with capture_output():
    weights_d_rb = system_d.combForecast.get_forecast_weights("SHFE_RB")
    weights_d_lc = system_d.combForecast.get_forecast_weights("GFEX_LC")
    scalar_d_rb = system_d.forecastScaleCap.get_forecast_scalar(
        "SHFE_RB", "carry60")
    try:
        system_d.combForecast.get_forecast_weights("GFEX_PD")
    except (IndexError, ValueError) as error:
        newborn_failure = f"{type(error).__name__}: {error}"
    else:
        newborn_failure = None
del system_d
gc.collect()
variant_d = dict(
    forecast_weights_rb=weights_d_rb,
    forecast_weights_lc=weights_d_lc,
    scalar_carry60_rb=scalar_d_rb,
)
print("GFEX_PD unpooled fit:", newborn_failure or "now has enough history")"""
        ),
        md(
            """
### Variant E — handcraft pooled, rolling **weight fits**

Only the forecast-weight and instrument-weight optimisers use the 20-year
rolling window here. Scalars and correlation estimates retain their default
expanding histories. Most Chinese histories are shorter than 20 years, and
even the veterans may have less than 20 usable years after all stages align.
This isolates whether the default rolling **weight** fit differs here; it is
not a completely rolling estimation stack.
"""
        ),
        code(
            """
variant_e = run_variant(
    "E rolling weight fits",
    **ESTIMATE_ALL,
    forecast_scalar_estimate=pooled_scalar_without_backfill,
    forecast_weight_estimate=estimate_block("forecast_weight_estimate",
                                            date_method="rolling"),
    instrument_weight_estimate=estimate_block("instrument_weight_estimate",
                                              date_method="rolling"),
)"""
        ),
        md(
            """
## Results

Diagnostic D is absent from the performance table because its portfolio is
not defined for the full universe; comparing a silently reduced universe
would be misleading.
"""
        ),
        code(
            """
variants = [variant_a, variant_b, variant_c, variant_e]
aligned_variant_returns = pd.concat(
    {variant["label"]: variant["returns"] for variant in variants}, axis=1
).replace([np.inf, -np.inf], np.nan).dropna()
variant_stats = pd.DataFrame([
    R.stats_row(R.rewrap(aligned_variant_returns[variant["label"]]),
                variant["label"])
    for variant in variants
]).set_index("name")
print(f"common evaluation sample: {aligned_variant_returns.index[0].date()} "
      f"to {aligned_variant_returns.index[-1].date()}, "
      f"{len(aligned_variant_returns)} business-day observations")
variant_stats"""
        ),
        md(
            """
The table is deliberately generated from one inner-aligned return frame.
Read differences as in-sample model comparisons, not proof that the top row
will win. In particular, B versus E only tests whether the default 20-year
rolling window has started discarding observations from the two weight fits;
exact equality is an empirical output, not an assumption.

The first panel below keeps each native account's realised risk. The second
rescales those **same dated PIT-gated returns** ex post to variant A's realised
volatility, so different risk levels do not masquerade as better weighting.
That normalisation is a comparison diagnostic, not another backtest.
"""
        ),
        code(
            """
native_annual_vol = aligned_variant_returns.std() * 16
reference_vol = native_annual_vol["A fixed"]
volatility_normalised_returns = aligned_variant_returns.mul(
    reference_vol / native_annual_vol)

fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
R.cumulative_from_zero(aligned_variant_returns).plot(
    ax=axes[0], title="native cumulative % return (common dates)")
axes[0].set_ylabel("percent of initial capital")
R.cumulative_from_zero(volatility_normalised_returns).plot(
    ax=axes[1],
    title=f"cumulative return at A-fixed realised volatility "
          f"({reference_vol:.1f}% annualised)")
axes[1].set_ylabel("volatility-normalised percent")
plt.tight_layout()"""
        ),
        md(
            """
## What pooling does to forecast weights

Compare the estimated rule weights for a veteran (SHFE_RB, data since 2009)
and a newer market (GFEX_LC, continuous history since 2024) under pooled (B)
and unpooled diagnostic D fitting. Pooling gives the newer market the whole
universe's experience; the four panels show whether its shorter own sample
produces a materially different or less stable path. The GFEX_PD message above
records whether the latest data now contain enough annual boundaries rather
than freezing an old conclusion in prose.
"""
        ),
        code(
            """
rb_weight_paths = pd.concat({
    "pooled (B)": variant_b["forecast_weights_rb"],
    "unpooled (D)": variant_d["forecast_weights_rb"],
}, axis=1).dropna()
lc_weight_paths = pd.concat({
    "pooled (B)": variant_b["forecast_weights_lc"],
    "unpooled (D)": variant_d["forecast_weights_lc"],
}, axis=1).dropna()

fig, axes = plt.subplots(2, 2, figsize=(13, 7), sharex="row", sharey=True)
rb_weight_paths["pooled (B)"].plot(
    ax=axes[0, 0], legend=True, title="SHFE_RB - pooled (B)")
rb_weight_paths["unpooled (D)"].plot(
    ax=axes[0, 1], legend=False, title="SHFE_RB - unpooled (D)")
lc_weight_paths["pooled (B)"].plot(
    ax=axes[1, 0], legend=False, title="GFEX_LC - pooled (B)")
lc_weight_paths["unpooled (D)"].plot(
    ax=axes[1, 1], legend=False, title="GFEX_LC - unpooled (D)")
for ax in axes.flat:
    ax.set_ylabel("forecast weight")
    ax.set_xlabel("")
plt.tight_layout()
print(f"SHFE_RB common weight sample: {rb_weight_paths.index[0].date()} to "
      f"{rb_weight_paths.index[-1].date()}")
print(f"GFEX_LC common weight sample: {lc_weight_paths.index[0].date()} to "
      f"{lc_weight_paths.index[-1].date()}")"""
        ),
        code(
            """
scalar_compare = pd.DataFrame({
    "fixed (production fit)": pd.Series(28.40, index=variant_b["scalar_carry60_rb"].index),
    "estimated, pooled (B)": variant_b["scalar_carry60_rb"],
    "estimated, unpooled (D)": variant_d["scalar_carry60_rb"],
}).dropna()
scalar_compare.plot(title="carry60 forecast scalar for SHFE_RB");"""
        ),
        md(
            """
## Instrument weights and the diversification multipliers

Handcraft clusters the correlation matrix into nested pairs and splits
capital down the tree, then tilts by (noisy) Sharpe unless `equalise_SR`
(on by default for instrument weights). The IDM says how much the combined
portfolio can be levered because instruments diversify; it is capped at 2.5.
FDM is a different object: it diversifies trading rules within one market.
We plot them separately so two different correlation problems are not read as
one interchangeable multiplier.
The plotted weights are the native fitted weights after the dated liquidity
mask and same-date renormalisation, not the optimiser's ungated raw matrix.
"""
        ),
        code(
            """
final_weights = variant_b["instrument_weights"].iloc[-1].sort_values(ascending=False)
largest_weights = final_weights.head(20).sort_values()
ax = largest_weights.plot.barh(figsize=(9, 7))
ax.set_title("variant B: 20 largest latest PIT-eligible estimated weights")
ax.set_xlabel("instrument weight")
ax.set_ylabel("instrument")
print(f"effective number of instruments (1/sum w^2): "
      f"{1 / (final_weights ** 2).sum():.1f}; "
      f"positive weights: {(final_weights > 0).sum()}")"""
        ),
        code(
            """
fig, ax = plt.subplots(figsize=(11, 4))
pd.DataFrame({
    "IDM (B, estimated)": variant_b["idm"],
    "IDM (E, rolling weight fits)": variant_e["idm"],
}).plot(ax=ax, title="instrument diversification multiplier (IDM)")
ax.set_ylabel("IDM")
plt.tight_layout()

fig, ax = plt.subplots(figsize=(11, 4))
variant_b["fdm_rb"].plot(
    ax=ax, color="tab:purple",
    title="SHFE_RB forecast diversification multiplier (FDM), variant B")
ax.set_ylabel("FDM")
plt.tight_layout()"""
        ),
        md(
            """
## Recommendations for this universe

Judge from the tables above, but the structural argument goes:

- **Pool noisy rule evidence, not contract economics**: pooled scalars,
  correlations, gross rule returns and turnover are sensible defaults for
  young histories. Keep full cost summaries market-specific so the estimate
  is effectively own cost per trade × pooled rule turnover.
- **Do not declare an optimiser winner here**: handcraft and shrinkage were
  compared in sample. Treat either as a challenger and judge stability across
  windows; keep `equalise_SR=True` for instrument weights because notebook 05
  explains how little noisy cross-market Sharpe ranks mean.
- **Expanding versus 20-year rolling weight fits is a measured result**.
  Variant E leaves scalar and correlation estimation expanding. If its paths
  coincide with B, use expanding for simplicity; a shorter or fully rolling
  stack is a new, pre-declared adaptation experiment.
- Compare the scalar paths with the fixed external line before deciding.
  Fixing genuinely pre-existing scalars while estimating only weights and
  multipliers is a legitimate simplification that removes early-sample scalar
  noise; choosing the fixed value after seeing this graph is not.

A production-ish config can therefore start with variant B's mixed pooling
defaults and fixed external scalars, while keeping shrinkage C as a serious
challenger. Liquidity membership should remain point-in-time; a present-day
`bad_markets` list must not be projected backward over valid history.

**Next**: the pooling section below separates complete, none, and
partial/group pooling, then carries one production-ish trend+carry design
through the latest exact three-year window. The upstream manual
`docs/backtesting.md` covers the system machinery in still more depth.
"""
        ),
    ]
    return cells


def _pooling_cells() -> list:
    cells = [
        md(
            """
# 09 — Pooling without hand-waving: research, backtest, production

The preceding section exposed five pooling switches. That is the API, but it is not yet
a mental model. **Pooling means borrowing observations for an estimate. It
does not mean pooling capital, prices, trades, or realised P&L.**

This notebook slows down and answers the practical questions:

1. What exactly is combined by each switch?
2. What are complete pooling, no pooling, and partial/group pooling?
3. Which choices are honest in a backtest, and how do they move into
   production?
4. What did a deliberately plain 50% trend / 50% carry portfolio make over
   the latest three years in this data, net of modelled trading costs, when
   market membership is decided only from information available at the time?

The small diagnostics use four named markets and explicit code. There is no
clever loop-generating framework here: the point is to see the objects.
The final section then runs every stored Chinese history once and applies one
lagged liquidity/seasoning rule through time. A 2026 survivor list is never
projected backwards into the historical portfolio.
"""
        ),
        code(SETUP_CELL),
        code(
            """
import copy
import logging
import yaml
from IPython.utils.io import capture_output

from sysdata.config.configdata import Config
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from systems.provided.futures_chapter15.basesystem import futures_system

R.limit_blas_threads()
data = dbFuturesSimData()
universe = R.chinese_universe(data)

EWMAC = "systems.provided.rules.ewmac.ewmac"
EWMAC_DATA = ["rawdata.get_daily_prices", "rawdata.daily_returns_volatility"]
CARRY = "systems.provided.rules.carry.carry"

trading_rules = {
    "ewmac16_64": dict(function=EWMAC, data=EWMAC_DATA,
                       other_args=dict(Lfast=16, Lslow=64)),
    "ewmac32_128": dict(function=EWMAC, data=EWMAC_DATA,
                        other_args=dict(Lfast=32, Lslow=128)),
    "ewmac64_256": dict(function=EWMAC, data=EWMAC_DATA,
                        other_args=dict(Lfast=64, Lslow=256)),
    "carry10": dict(function=CARRY, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=10)),
    "carry60": dict(function=CARRY, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=60)),
    "carry125": dict(function=CARRY, data=["rawdata.raw_carry"],
                     other_args=dict(smooth_days=125)),
}

# Treated here as external long-run production fits. This notebook does not
# re-estimate them on the evaluation window; provenance caveat at the end.
fixed_scalars = dict(
    ewmac16_64=3.75, ewmac32_128=2.65, ewmac64_256=1.87,
    carry10=27.82, carry60=28.40, carry125=29.37,
)

# A deliberately simple prior, not an optimiser output: half the forecast
# budget to each style, equal within each style.
fixed_forecast_weights = {
    "ewmac16_64": 1 / 6, "ewmac32_128": 1 / 6, "ewmac64_256": 1 / 6,
    "carry10": 1 / 6, "carry60": 1 / 6, "carry125": 1 / 6,
}

print(f"{len(universe)} stored Chinese histories; data through "
      f"{data.daily_prices('SHFE_RB').index[-1].date()}")
"""
        ),
        md(
            """
## One word, three statistical operations

Those five switches implement three genuinely different kinds
of pooling:

| operation | switches | what the code does | resulting estimate |
|---|---|---|---|
| **cross-sectional reduction** | `forecast_scalar_estimate.pool_instruments` | each date, take the median absolute raw forecast across markets; then average through time | one scalar history per rule, shared by every market |
| **panel stacking** | `forecast_correlation_estimate.pool_instruments`; `forecast_weight_estimate.pool_gross_returns` | resample each market, align rule columns, add microseconds to duplicate dates, and stack market-rows vertically | more observations for rule correlations or rule P&L means/covariances |
| **summary averaging** | `forecast_cost_estimates.use_pooled_costs`; `use_pooled_turnover` | average annual SR-cost or turnover summaries across compatible markets | a steadier cost penalty used when fitting forecast weights |

There is deliberately **no master pooling switch**. A scalar can be pooled
while costs remain market-specific. Correlations can be pooled while rule
weights are fixed. That independence is useful, but it makes vague advice
like "turn pooling on" dangerous.

Two more distinctions matter:

- Pooling is only over markets with the relevant rule (and, for forecast
  weights/correlations, a compatible set of cheap rules). It is not always
  literally the entire universe.
- Instrument-weight estimation is cross-sectional by construction: its
  columns *are instruments*. It has no `pool_instruments` flag and should not
  be confused with pooling forecast evidence across instruments.
"""
        ),
        md(
            """
## A four-market research desk

Use three seasoned contracts from different exchanges and one young GFEX
contract. This is intentionally hand-picked for diagnosis, not claimed as a
portfolio. Rebar, soymeal and PTA tell us whether a result is shared by old
markets; lithium shows what happens when an individual history is short.
"""
        ),
        code(
            """
FOCUS = ["SHFE_RB", "DCE_M", "CZCE_TA", "GFEX_LC"]

focus_history = pd.DataFrame({
    "SHFE_RB": [data.daily_prices("SHFE_RB").index[0],
                data.daily_prices("SHFE_RB").index[-1],
                len(data.daily_prices("SHFE_RB"))],
    "DCE_M": [data.daily_prices("DCE_M").index[0],
              data.daily_prices("DCE_M").index[-1],
              len(data.daily_prices("DCE_M"))],
    "CZCE_TA": [data.daily_prices("CZCE_TA").index[0],
                data.daily_prices("CZCE_TA").index[-1],
                len(data.daily_prices("CZCE_TA"))],
    "GFEX_LC": [data.daily_prices("GFEX_LC").index[0],
                data.daily_prices("GFEX_LC").index[-1],
                len(data.daily_prices("GFEX_LC"))],
}, index=["first", "last", "daily rows"]).T
focus_history
"""
        ),
        code(
            """
focus_config = Config(dict(
    trading_rules=trading_rules,
    forecast_scalars=fixed_scalars,
    forecast_weights=fixed_forecast_weights,
    forecast_div_multiplier=1.5,
    instruments=FOCUS,
    instrument_weights={"SHFE_RB": 0.25, "DCE_M": 0.25,
                        "CZCE_TA": 0.25, "GFEX_LC": 0.25},
    instrument_div_multiplier=2.0,
    notional_trading_capital=100_000_000,
    base_currency="CNH",
))
focus_system = futures_system(data=dbFuturesSimData(), config=focus_config)
"""
        ),
        md(
            """
## 1. Forecast-scalar pooling: robust complete pooling

A raw rule has arbitrary amplitude. The scalar makes its average absolute
forecast about 10. With complete pooling the implementation first takes the
cross-sectional median absolute forecast on each date, then a rolling time
average. The median stops one wild market dominating and taking the
cross-section first avoids jumps merely because another market was listed.

Below we reproduce the actual scalar function four ways. "Veteran pool" is
**group pooling**: lithium borrows the three old markets' scale rather than
either standing alone or entering the full pool. It is a coarse practical
substitute for true partial pooling, which would blend own, group and global
estimates continuously. This grouping is a research choice; there is no
asset-class/group switch for scalars in the standard config.
"""
        ),
        code(
            """
from sysquant.estimators.forecast_scalar import forecast_scalar

rb_raw = focus_system.forecastScaleCap.get_raw_forecast(
    "SHFE_RB", "carry60").squeeze()
m_raw = focus_system.forecastScaleCap.get_raw_forecast(
    "DCE_M", "carry60").squeeze()
ta_raw = focus_system.forecastScaleCap.get_raw_forecast(
    "CZCE_TA", "carry60").squeeze()
lc_raw = focus_system.forecastScaleCap.get_raw_forecast(
    "GFEX_LC", "carry60").squeeze()

raw_carry_panel = pd.concat(
    [rb_raw.rename("SHFE_RB"), m_raw.rename("DCE_M"),
     ta_raw.rename("CZCE_TA"), lc_raw.rename("GFEX_LC")], axis=1)

scalar_complete = forecast_scalar(raw_carry_panel, min_periods=500,
                                  backfill=False).rename("complete: four markets")
scalar_veterans = forecast_scalar(raw_carry_panel[
    ["SHFE_RB", "DCE_M", "CZCE_TA"]], min_periods=500,
    backfill=False).rename("group: veterans")
scalar_rb = forecast_scalar(raw_carry_panel[["SHFE_RB"]], min_periods=500,
                            backfill=False).rename("none: rebar alone")
scalar_lc = forecast_scalar(raw_carry_panel[["GFEX_LC"]], min_periods=500,
                            backfill=False).rename("none: lithium alone")

scalar_demo = pd.concat(
    [scalar_complete, scalar_veterans, scalar_rb, scalar_lc], axis=1)
print("latest scalar estimate:")
display(scalar_demo.apply(lambda series: series.dropna().iloc[-1]).to_frame("scalar"))
scalar_demo.loc["2023":].plot(title="carry60 scalar: what each market is allowed to learn");
"""
        ),
        md(
            """
Read this as a bias–variance choice, not a contest with a universal winner:

- **No pooling** preserves a genuinely different market but gives lithium
  only its short and regime-specific sample.
- **Complete pooling** is stable and gives a new listing a usable estimate on
  day one, but assumes rule amplitude is exchangeable across markets.
- **Group pooling** is often a useful answer when asset classes differ. True
  **partial pooling** blends own and shared evidence according to uncertainty.
  In this codebase either normally means separate systems, separate pre-fit
  constants, or a custom estimator—not another Boolean.

The default scalar estimator also has `backfill=True`: it fills the early
period with the first estimate, which uses later data. That small, explicit
look-ahead is convenient for long backtests but cannot be recreated live.
Fixed long-run scalars, used in the final run, are the cleaner production
choice when those constants were chosen outside the test window.
"""
        ),
        md(
            """
## 2. Correlation pooling: stack markets, keep rules as columns

For forecast correlations, time remains horizontal and rules remain columns;
markets supply additional rows. The real estimator does this through time
with expanding or rolling fits and adjusts exponential lookbacks for the
number of stacked markets. This cell exposes the same stacking mechanism on
our four markets and reports three economically meaningful pairs.

Pooling destroys cross-market ordering at equal timestamps (the code offsets
rows by microseconds), which is fine for contemporaneous rule correlation but
would be wrong for estimating lead/lag or autocorrelation.
"""
        ),
        code(
            """
from syscore.pandas.list_of_df import (
    listOfDataFrames, stacked_df_with_added_time_from_list,
)

rb_weekly = focus_system.combForecast.get_all_forecasts("SHFE_RB").resample("W").last()
m_weekly = focus_system.combForecast.get_all_forecasts("DCE_M").resample("W").last()
ta_weekly = focus_system.combForecast.get_all_forecasts("CZCE_TA").resample("W").last()
lc_weekly = focus_system.combForecast.get_all_forecasts("GFEX_LC").resample("W").last()

pooled_weekly = stacked_df_with_added_time_from_list(listOfDataFrames([
    rb_weekly, m_weekly, ta_weekly, lc_weekly,
]))

rb_corr = rb_weekly.corr(min_periods=20)
lc_corr = lc_weekly.corr(min_periods=20)
pooled_corr = pooled_weekly.corr(min_periods=20)

correlation_demo = pd.DataFrame({
    "rebar only": [
        rb_corr.loc["carry60", "carry125"],
        rb_corr.loc["ewmac32_128", "ewmac64_256"],
        rb_corr.loc["carry60", "ewmac32_128"],
    ],
    "lithium only": [
        lc_corr.loc["carry60", "carry125"],
        lc_corr.loc["ewmac32_128", "ewmac64_256"],
        lc_corr.loc["carry60", "ewmac32_128"],
    ],
    "four-market pool": [
        pooled_corr.loc["carry60", "carry125"],
        pooled_corr.loc["ewmac32_128", "ewmac64_256"],
        pooled_corr.loc["carry60", "ewmac32_128"],
    ],
}, index=["carry60 vs carry125", "trend32 vs trend64",
          "carry60 vs trend32"])

print("weekly rows before dropping missing values:")
display(pd.Series({"rebar": len(rb_weekly), "lithium": len(lc_weekly),
                   "stacked four-market panel": len(pooled_weekly)},
                  name="rows").to_frame())
correlation_demo
"""
        ),
        md(
            """
## 3. Return pooling is not forecast pooling

`forecast_weight_estimate.pool_gross_returns` stacks **hypothetical weekly
P&L from each rule**, not the forecast values above. The optimiser then learns
rule means, volatilities and correlations from that P&L panel. This answers
"which rules have paid?"; scalar and forecast-correlation pooling answer
different questions.

Subtleties worth remembering:

- With `pool_gross_returns=True`, gross evidence is shared. If costs remain
  market-specific, fitted weights can still differ by market because their
  net evidence differs.
- `equalise_SR`, shrinkage and pooling are separate controls. Shrinkage pulls
  noisy rule estimates toward priors **after** the sample is assembled; it is
  not hierarchical partial pooling across instruments.
- An expanding fit uses only history available before each use period. An
  `in_sample` fit sees the future and belongs only in a diagnostic.
- A newborn market cannot create annual unpooled fitting periods. Complete
  pooling is what lets it inherit a usable rule mix.
"""
        ),
        md(
            """
## 4. Cost pooling and turnover pooling

Costs have two pieces: how much a rule trades, and what one unit of turnover
costs in a particular contract. Pooling turnover says "this rule usually
trades about this much" while preserving rebar's or lithium's own price,
volatility, point value and spread cost. Fully pooled cost says the whole
annual SR-cost number is exchangeable too.

The middle choice—**own contract cost × pooled rule turnover**—is usually
the sensible production default. Turnover is a property of the rule and is
noisy in a short series; cost per trade really is market-specific.
"""
        ),
        code(
            """
def focus_system_with_cost_pooling(use_pooled_costs, use_pooled_turnover):
    return futures_system(data=dbFuturesSimData(), config=Config(dict(
        trading_rules=trading_rules,
        forecast_scalars=fixed_scalars,
        forecast_weights=fixed_forecast_weights,
        instruments=FOCUS,
        instrument_weights={"SHFE_RB": 0.25, "DCE_M": 0.25,
                            "CZCE_TA": 0.25, "GFEX_LC": 0.25},
        notional_trading_capital=100_000_000,
        base_currency="CNH",
        forecast_cost_estimates=dict(
            use_pooled_costs=use_pooled_costs,
            use_pooled_turnover=use_pooled_turnover,
        ),
    )))

cost_own = focus_system_with_cost_pooling(False, False)
turnover_pool = focus_system_with_cost_pooling(False, True)
cost_pool = focus_system_with_cost_pooling(True, False)

cost_pooling_demo = pd.DataFrame({
    "SHFE_RB carry60": [
        cost_own.accounts.forecast_turnover("SHFE_RB", "carry60"),
        turnover_pool.accounts.forecast_turnover("SHFE_RB", "carry60"),
        cost_own.accounts.get_SR_transaction_cost_for_instrument_forecast(
            "SHFE_RB", "carry60"),
        turnover_pool.accounts.get_SR_transaction_cost_for_instrument_forecast(
            "SHFE_RB", "carry60"),
        cost_pool.accounts.get_SR_transaction_cost_for_instrument_forecast(
            "SHFE_RB", "carry60"),
    ],
    "GFEX_LC carry60": [
        cost_own.accounts.forecast_turnover("GFEX_LC", "carry60"),
        turnover_pool.accounts.forecast_turnover("GFEX_LC", "carry60"),
        cost_own.accounts.get_SR_transaction_cost_for_instrument_forecast(
            "GFEX_LC", "carry60"),
        turnover_pool.accounts.get_SR_transaction_cost_for_instrument_forecast(
            "GFEX_LC", "carry60"),
        cost_pool.accounts.get_SR_transaction_cost_for_instrument_forecast(
            "GFEX_LC", "carry60"),
    ],
}, index=["own turnover", "four-market pooled turnover",
          "own transaction SR cost", "own cost x pooled turnover",
          "fully pooled transaction SR cost"])
cost_pooling_demo
"""
        ),
        md(
            """
These cost switches govern the **synthetic SR-cost penalty used to choose
forecast weights and reject expensive rules**. They do not replace final
portfolio accounting: the system's realised net P&L still charges each
instrument's actual configured cash/spread costs to its own trades. If
forecast weights are fixed, as in the final run, changing these estimation
switches does not change those weights or the final cash-cost calculation.
"""
        ),
        md(
            """
## Complete, none, or partial? A decision rule

| situation | scalar | rule correlation | rule gross P&L | turnover | cost per trade |
|---|---|---|---|---|---|
| young, broad Chinese universe | complete pool | complete pool | complete pool if estimating weights | pool | own market |
| mature and demonstrably heterogeneous asset classes | group/partial pool | group/partial pool | group/partial pool | usually pool by rule | own market |
| one market with decades of data and a structural reason to differ | perhaps own | perhaps own | own | pooled as a prior | own market |
| production simplicity / weak evidence for fitted means | fixed external scalar | pool for FDM | **fixed rule weights** | irrelevant to fixed weights | actual cash cost |

Partial pooling is not available as a universal config value. Practical
implementations are:

1. fit separate groups (for example financials, commodities) and store the
   resulting constants;
2. blend an own estimate with the pooled estimate according to effective
   sample size; or
3. write a hierarchical estimator.

Do not silently call an asset-class average "more realistic" after seeing
which grouping wins. The grouping and blend rule must be chosen before the
evaluation window.
"""
        ),
        md(
            """
## Backtest-to-production protocol

The safest way to think about a fit is **fit through date _t_, freeze it,
trade after _t_**.

In a backtest:

- use `date_method="expanding"` or a pre-declared rolling window;
- build a fresh system for every configuration (cached estimates otherwise
  leak between experiments);
- never use `in_sample` for a performance claim;
- keep universe/liquidity eligibility point-in-time; and
- record whether early scalar backfill is present.

In production:

- refit offline on a schedule (annual is plenty for these slow estimates),
  version the fitted artefact and deploy it for the next period;
- let a new market borrow pooled scalars/correlations/turnover, but require
  enough own price/volatility history and liquidity before allocating risk;
- monitor realised turnover, forecast amplitude and correlations against the
  fitted panel; and
- retain caps, smoothing and a fallback set of fixed weights. "Re-estimate
  every day" is not the same thing as "adapt intelligently".
"""
        ),
        md(
            """
## The actual money question: a production-ish 50/50 model

Now make one choice and live with it. This specification is intentionally
boring:

- three medium/slow EWMAC rules and three carry smoothings;
- 50% of forecast weight to trend, 50% to carry, equal within style;
- fixed external forecast scalars (no early backfill and no refit here);
- pooled, expanding forecast correlations for the FDM;
- expanding handcraft instrument weights with `equalise_SR=True`, so noisy
  per-market mean returns do not decide the allocation;
- estimated IDM, 10% forecast buffering, delayed fills, whole contracts and
  each market's configured native cash/spread fill costs.

The full history is run first and only then sliced. Starting the engine three
years ago would give it no mature volatility, correlation, weight or buffer
state and would answer a different question.

Membership is also historical. A market enters only after its held contract
has averaged at least 130 contracts over 20 observed sessions, remains until
that trailing mean falls below 70, and uses the normal one-business-row delay.
Around exchange holidays this is an upstream execution approximation, not an
exchange-calendar-accurate next-session fill. Known
terminal histories are targeted flat before their final stored close so the
exit and its native transaction cost are present in P&L. That terminal close
is an explicitly ex-post data-boundary assumption, not a causal delisting
signal. The volume thresholds themselves are deliberately modest tutorial
choices, not a capacity claim.

For this point-in-time study, `vol_normalise_currency_costs=False` is also
pre-declared. The optional upstream rescaling divides all historical costs by
the instrument's **final** 180-day volatility. That is future-informed and can
be zero for a terminal history. We retain native cash/spread fill costs and
whole-contract rounding; we only remove that ex-post cost deflator. This is a
research choice, not the upstream default.
"""
        ),
        code(
            """
from systems.accounts.accounts_stage import Account
from systems.basesystem import System
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecasting import Rules
from systems.positionsizing import PositionSizing
from systems.rawdata import RawData


print("reading held-contract volume for the historical universe ...")
with capture_output():
    held_volumes = R.held_contract_volumes(data, universe)
liquidity = R.liquidity_eligibility(held_volumes, force_terminal_close=True)
liquidity_events = R.liquidity_event_table(liquidity, held_volumes)


def point_in_time_futures_system(data, config, eligibility,
                                 fixed_weights=None):
    '''The normal futures system with only its portfolio stage replaced.'''
    return System(
        [
            Account(),
            R.PointInTimePortfolios(
                eligibility=eligibility, fixed_weights=fixed_weights),
            PositionSizing(), RawData(), ForecastCombine(),
            ForecastScaleCap(), Rules(),
        ],
        data,
        config,
    )


eligibility_summary = pd.DataFrame({
    "first eligible": {
        code: liquidity.index[liquidity[code]][0].date()
        if liquidity[code].any() else pd.NaT
        for code in universe
    },
    "last eligible": {
        code: liquidity.index[liquidity[code]][-1].date()
        if liquidity[code].any() else pd.NaT
        for code in universe
    },
    "eligible business days": liquidity.sum(),
})
print(f"{int((liquidity.sum() > 0).sum())} histories ever pass the rule; "
      f"{int(liquidity.iloc[-1].sum())} pass on {liquidity.index[-1].date()}")
display(eligibility_summary.sort_values("last eligible").head(12))
display(liquidity_events.tail(12))


with open(R.REPO_ROOT / "sysdata/config/defaults.yaml") as handle:
    DEFAULTS = yaml.safe_load(handle)

pooled_forecast_corr = copy.deepcopy(DEFAULTS["forecast_correlation_estimate"])
pooled_forecast_corr.update(pool_instruments=True, date_method="expanding")

instrument_weight_fit = copy.deepcopy(DEFAULTS["instrument_weight_estimate"])
instrument_weight_fit.update(method="handcraft", equalise_SR=True,
                             date_method="expanding")

instrument_corr_fit = copy.deepcopy(DEFAULTS["instrument_correlation_estimate"])
instrument_corr_fit.update(date_method="expanding")

productionish_config = Config(dict(
    trading_rules=trading_rules,
    forecast_scalars=fixed_scalars,
    forecast_weights=fixed_forecast_weights,
    use_forecast_scale_estimates=False,
    use_forecast_weight_estimates=False,
    use_forecast_div_mult_estimates=True,
    forecast_correlation_estimate=pooled_forecast_corr,
    use_instrument_weight_estimates=True,
    instrument_weight_estimate=instrument_weight_fit,
    use_instrument_div_mult_estimates=True,
    instrument_correlation_estimate=instrument_corr_fit,
    instruments=universe,              # do not also set instrument_weights
    notional_trading_capital=100_000_000,
    percentage_vol_target=16.0,
    base_currency="CNH",
    vol_normalise_currency_costs=False,
    capital_multiplier=dict(func="syscore.capital.fixed_capital"),
))

productionish_system = point_in_time_futures_system(
    data=dbFuturesSimData(), config=productionish_config,
    eligibility=liquidity)

# Estimation progress bars are valuable interactively but make a terrible
# saved notebook. The result cells below are the research record.
with capture_output():
    portfolio = productionish_system.accounts.portfolio()
    production_weights = productionish_system.portfolio.get_instrument_weights()

allowed_on_weight_dates = liquidity.reindex(production_weights.index).ffill()
allowed_on_weight_dates = allowed_on_weight_dates.reindex(
    columns=production_weights.columns, fill_value=False).fillna(False)
assert (production_weights.where(~allowed_on_weight_dates, 0.0).abs().max().max()
        < 1e-12)

print("full-history point-in-time run complete")
"""
        ),
        code(
            """
gross = portfolio.percent.gross.as_ts.rename("gross")
net = portfolio.percent.as_ts.rename("modelled net")
costs = portfolio.percent.costs.as_ts.rename("costs")

# One accounting frame is the source of every number and graph below.  Do not
# let pandas silently compare gross, net and costs on different dates.
accounting = pd.concat([gross, net, costs], axis=1)
accounting = accounting.replace([np.inf, -np.inf], np.nan).dropna()
reconciliation_error = (
    accounting["modelled net"]
    - accounting["gross"]
    - accounting["costs"]
).abs()
assert reconciliation_error.max() < 1e-8

data_end = accounting.index.max()
three_year_cutoff = data_end - pd.DateOffset(years=3)

# Strictly after the cutoff gives 2023-07-28 through 2026-07-27 here.
recent = accounting.loc[accounting.index > three_year_cutoff].copy()
recent["2x cost stress"] = recent["gross"] + 2 * recent["costs"]

def fixed_capital_summary(series):
    series = series.dropna()
    curve = R.rewrap(series)
    return dict(
        observations=len(series),
        first=series.index[0].date(),
        last=series.index[-1].date(),
        total_return=series.sum(),
        ann_mean=curve.percent.ann_mean(),
        ann_vol=curve.percent.ann_std(),
        sharpe=curve.percent.sharpe(),
        worst_drawdown=curve.percent.worst_drawdown(),
    )

recent_summary = pd.DataFrame({
    "gross": fixed_capital_summary(recent["gross"]),
    "modelled net": fixed_capital_summary(recent["modelled net"]),
    "2x cost stress": fixed_capital_summary(recent["2x cost stress"]),
}).T
print(f"maximum |net - gross - costs|: {reconciliation_error.max():.3g}")
recent_summary
"""
        ),
        md(
            """
`total_return` above is the direct pysystemtrade answer: percentage P&L on
**fixed 100m CNH notional capital**. Fixed capital is useful because risk does
not snowball during a research comparison. Multiplying by 1m converts one
percentage point into CNH for this configuration.

A client who continuously reinvested would experience compounded wealth
instead. Mechanically compounding the same daily percentage stream is a
useful illustration, but it is not a second independent backtest: exact
variable-capital positions would differ slightly because contracts are
integer-sized and buffers create path dependence.
"""
        ),
        code(
            """
net_fixed_total = recent["modelled net"].sum()
net_compounded_total = (
    (1 + recent["modelled net"] / 100).prod() - 1
) * 100
net_cnh = net_fixed_total / 100 * 100_000_000

pd.Series({
    "fixed-capital net return (%)": net_fixed_total,
    "fixed-capital net P&L (CNH)": net_cnh,
    "mechanically reinvested wealth return (%)": net_compounded_total,
}, name="latest exact three-year window").to_frame()
"""
        ),
        code(
            """
R.cumulative_from_zero(
    recent[["gross", "modelled net", "2x cost stress"]]
).plot(
    title=f"latest three years: cumulative % of fixed capital "
          f"({recent.index[0].date()} to {recent.index[-1].date()})")

calendar_returns = recent[["gross", "modelled net", "2x cost stress"]].groupby(
    recent.index.year).sum()
calendar_returns.index.name = "calendar year (2023 and 2026 are partial)"
calendar_returns
"""
        ),
        md(
            """
## How much confidence should we put in three years?

The next cell writes the headline from the same aligned frame as the table and
chart. That prevents an old sentence surviving after the data are refreshed.
"""
        ),
        code(
            """
net_row = recent_summary.loc["modelled net"]
gross_row = recent_summary.loc["gross"]
stress_row = recent_summary.loc["2x cost stress"]
largest_year = calendar_returns["modelled net"].abs().idxmax()
largest_year_return = calendar_returns.loc[largest_year, "modelled net"]
largest_year_share = (
    100 * largest_year_return / net_fixed_total
    if net_fixed_total != 0 else np.nan
)

print(
    f"Exact window: {net_row['first']} through {net_row['last']} "
    f"({int(net_row['observations'])} aligned observations)."
)
print(
    f"Fixed-capital net return {net_row['total_return']:+.2f}%; "
    f"annual mean {net_row['ann_mean']:.2f}%, annual volatility "
    f"{net_row['ann_vol']:.2f}%, Sharpe {net_row['sharpe']:.3f}, "
    f"worst arithmetic drawdown {net_row['worst_drawdown']:.2f}% "
    "of initial capital."
)
print(
    f"Gross {gross_row['total_return']:+.2f}%; two-times-cost stress "
    f"{stress_row['total_return']:+.2f}%; mechanical reinvestment "
    f"{net_compounded_total:+.2f}%."
)
print(
    f"Largest absolute calendar contribution: {largest_year}, "
    f"{largest_year_return:+.2f} percentage points "
    f"({largest_year_share:.1f}% of the window total; boundary years may be partial)."
)
"""
        ),
        md(
            """
Mechanical reinvestment is not the native fixed-risk backtest result, and a
large contribution from one calendar year is evidence against reading the
three-year total as three uniform years of edge.

This is a useful implementation result, not an expected-return forecast.
Three years contain only one inflation/commodity/policy path, and the
configuration was not run as a sealed prospective experiment. In particular:

- fixed rule budgets prevent us from choosing carry merely because it won
  recently, and every fitted correlation/weight used an expanding window;
- nevertheless the long-run scalar constants were developed elsewhere, not
  registered in a pre-study protocol;
- membership comes from the declared lagged point-in-time liquidity and
  seasoning rule. Its thresholds are research assumptions, not proof that
  every indicated order could have been filled; and
- capacity, exchange limit moves, margin calls, tax and operational failures
  are outside this daily simulation.

The most honest headline is therefore the modelled fixed-capital result with
its volatility and drawdown beside it, plus the 2x-cost stress—not the
compounded number alone.

## Production recommendation

For this young Chinese universe, pool rule amplitude, rule correlations and
rule turnover unless a pre-declared group test demonstrates stable
heterogeneity. Keep cost per trade market-specific. Avoid fitting forecast
means unless the result is stable across windows and optimiser families;
fixed 50/50 style budgets are a very respectable production prior. Refit the
slow covariance/weight objects offline, version them, and make the next
year—not the fitting history—the scorecard.

**Next**: notebook 05 compares carry and trend under honest exclusions;
notebook 06 and the compact lab compare alternative trend-rule families.
"""
        ),
    ]
    return cells


def _backtesting_tutorial_cells() -> list:
    cells = [
        md(
            """
# Backtesting with Chinese futures: the complete pysystemtrade tutorial

This is a practical companion to `docs/backtesting.md`. It follows the
manual from the quick "How do I?" recipes through data, configuration,
systems, stages, caching, accounting, optimisation, and the reference
material. Every market example is a Tushare-sourced Chinese future and every
runnable price example uses `dbFuturesSimData`; the stale price CSVs shipped
with the upstream project are never loaded.

The notebook is intentionally long. It is written to be read in order once,
then searched later. Four markets keep the examples quick and legible:

| Instrument | Market | Asset class |
|---|---|---|
| `SHFE_RB` | Shanghai rebar | Metals |
| `DCE_M` | Dalian soybean meal | Agriculture |
| `CFFEX_IF` | CSI 300 index future | Equity |
| `CFFEX_T` | 10-year Chinese government bond future | Bonds |

You need the Chinese MongoDB/Parquet stores described in
`docs/tushare_chinese_futures.md`. No Tushare token is needed: this notebook
only reads the already-seeded stores. Some sections deliberately create
temporary YAML, CSV, and pickle files; they live in an operating-system temp
directory and disappear immediately afterwards.

## A small warning about the manual

`docs/backtesting.md` has grown over many versions of pysystemtrade. Where a
name or default in the prose differs from the current code, this notebook uses
the current code and points out the difference. Copy the working cells here,
not an old traceback or typo from the manual.
"""
        ),
        code(
            """
%matplotlib inline
import inspect
import logging
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from IPython.display import display

# Let pysystemtrade install its simulation logger, then keep the tutorial quiet.
with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
    from syslogging.logger import get_logger
    get_logger("chinese_backtesting_tutorial")
logging.getLogger().setLevel(logging.WARNING)

plt.rcParams.update({
    "figure.figsize": (10, 5),
    "axes.grid": True,
    "grid.alpha": 0.3,
    "legend.frameon": False,
})
pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 150)
pd.set_option("display.float_format", lambda value: f"{value:,.4f}")

TUTORIAL_DIR = Path.cwd()
if not (TUTORIAL_DIR / "config.yaml").exists():
    TUTORIAL_DIR = (
        Path.cwd() / "examples/chinese_futures/backtesting_tutorial"
    ).resolve()
assert (TUTORIAL_DIR / "config.yaml").exists(), "Run from the repo or notebook directory"
if str(TUTORIAL_DIR) not in sys.path:
    sys.path.insert(0, str(TUTORIAL_DIR))

CONFIG_PATH = TUTORIAL_DIR / "config.yaml"
INSTRUMENTS = ["SHFE_RB", "DCE_M", "CFFEX_IF", "CFFEX_T"]
FOCUS = "SHFE_RB"
PLOT_START = "2018-01-01"
"""
        ),
        code(
            """
from sysdata.sim.db_futures_sim_data import dbFuturesSimData

data = dbFuturesSimData()
available = set(data.get_instrument_list())
missing = sorted(set(INSTRUMENTS) - available)
assert not missing, f"Seed/rebuild the missing Chinese histories first: {missing}"

metadata = data.get_all_instrument_data_as_df().loc[INSTRUMENTS]
coverage = pd.DataFrame({
    "first": {code_: data.daily_prices(code_).index.min() for code_ in INSTRUMENTS},
    "last": {code_: data.daily_prices(code_).index.max() for code_ in INSTRUMENTS},
    "rows": {code_: len(data.daily_prices(code_)) for code_ in INSTRUMENTS},
})
display(metadata[["Description", "Pointsize", "Currency", "AssetClass"]].join(coverage))
print(f"Chinese simulation universe: {len(available)} stitched instruments")"""
        ),
        md(
            """
# Part 1 — How do I?

The manual starts with recipes. We will do the same, but keep one system
alive so later sections can pull it apart.

## Experiment with one trading rule and one instrument

A rule is just a function from data to an uncapped signal. For a quick idea
you do not need a full `System`. This standalone EWMAC recalculates its own
volatility, which is convenient for exploration; the full system reuses the
raw-data stage instead.
"""
        ),
        code(
            """
from systems.provided.rules.ewmac import ewmac_forecast_with_defaults

rebar_price = data.daily_prices(FOCUS)
standalone_ewmac = ewmac_forecast_with_defaults(
    rebar_price, Lfast=16, Lslow=64
)
ax = standalone_ewmac.loc[PLOT_START:].plot(
    title="SHFE_RB raw 16/64 EWMAC — unscaled and uncapped"
)
ax.set_ylabel("raw forecast")
ax.set_xlabel("date")
standalone_ewmac.dropna().tail()"""
        ),
        code(
            """
ewmac_forecast_with_defaults(
    rebar_price, Lfast=32, Lslow=128
).plot()"""
        ),
        md(
            """
## Create a standard futures backtest

The tutorial folder contains a real, deliberately boring pre-baked system:
`system.py` chooses the seven standard stages, `config.yaml` supplies three
rules and four Chinese instruments, and `dbFuturesSimData` supplies the
Chinese stores. This is the same pattern as the chapter 15 system, without
its default CSV data.
"""
        ),
        code(
            """
from system import futures_system

fixed_system = futures_system()
print(fixed_system)
print("instruments:", fixed_system.get_instrument_list())
fixed_system.portfolio.get_notional_position(FOCUS).dropna().tail()"""
        ),
        md(
            """
## Create a backtest which estimates parameters

Estimation is selected by configuration, not by a different class hierarchy.
For a clean comparison we keep the same three rules and four markets, remove
the fixed instrument weights, and estimate scalars, forecast weights, FDM,
instrument weights, and IDM. Weekly expanding-window handcrafting is the
current default and is fast for this small example.
"""
        ),
        code(
            """
from sysdata.config.configdata import Config

estimated_config = Config([
    str(CONFIG_PATH),
    {
        "instruments": INSTRUMENTS,
        "rule_variations": ["ewmac16_64", "ewmac32_128", "carry"],
        "use_forecast_scale_estimates": True,
        "use_forecast_weight_estimates": True,
        "use_forecast_div_mult_estimates": True,
        "use_instrument_weight_estimates": True,
        "use_instrument_div_mult_estimates": True,
        "forecast_weight_estimate": {"method": "handcraft", "date_method": "expanding"},
        "instrument_weight_estimate": {"method": "handcraft", "date_method": "expanding"},
    },
])
del estimated_config.instrument_weights

estimated_system = futures_system(
    data=dbFuturesSimData(), config=estimated_config
)
with redirect_stdout(StringIO()):
    estimated_forecast_weights = estimated_system.combForecast.get_forecast_weights(FOCUS)
    estimated_instrument_weights = estimated_system.portfolio.get_instrument_weights()

print("latest SHFE_RB forecast weights")
display(estimated_forecast_weights.dropna(how="all").tail(1))
print("latest portfolio instrument weights")
display(estimated_instrument_weights.dropna(how="all").tail(1))"""
        ),
        md(
            """
## See intermediate results

Every stage is inspectable. The sequence below is the backbone of the whole
notebook: adjusted prices → volatility and carry → raw forecasts → scaled and
capped forecasts → combined forecast → subsystem position → portfolio
position → buffered position and P&L.
"""
        ),
        code(
            """
intermediate = {
    "daily price": fixed_system.rawdata.get_daily_prices(FOCUS),
    "daily price-unit vol": fixed_system.rawdata.daily_returns_volatility(FOCUS),
    "raw carry": fixed_system.rawdata.raw_carry(FOCUS),
    "raw EWMAC": fixed_system.rules.get_raw_forecast(FOCUS, "ewmac16_64"),
    "capped EWMAC": fixed_system.forecastScaleCap.get_capped_forecast(
        FOCUS, "ewmac16_64"
    ),
    "combined forecast": fixed_system.combForecast.get_combined_forecast(FOCUS),
    "subsystem position": fixed_system.positionSize.get_subsystem_position(FOCUS),
    "portfolio position": fixed_system.portfolio.get_notional_position(FOCUS),
    "buffered position": fixed_system.accounts.get_buffered_position(FOCUS),
}
display(pd.concat(intermediate, axis=1).dropna(how="all").tail())
print("Try any_stage.methods() to discover the current public surface.")"""
        ),
        md(
            """
## See how profitable a backtest was

Account objects default to net daily P&L. `.percent` changes units, `.curve()`
accumulates returns, and `.stats()` describes the available statistics.
Never judge a system from one Sharpe ratio; this is API orientation, not an
investment conclusion.
"""
        ),
        code(
            """
portfolio = fixed_system.accounts.portfolio()
stats = dict(portfolio.percent.stats()[0])
display(pd.Series({key: stats[key] for key in [
    "ann_mean", "ann_std", "sharpe", "sortino", "avg_drawdown",
    "skew", "t_stat", "p_value",
]}).to_frame("portfolio statistic"))

ax = portfolio.percent.curve().loc[PLOT_START:].plot(
    title="Four-market Chinese futures system — cumulative net P&L"
)
ax.set_ylabel("percent of starting capital")
ax.set_xlabel("date")"""
        ),
        md(
            """
## Change backtest parameters

There are five routes in the manual:

1. edit or create a system YAML file;
2. change a `Config` object and build a new system;
3. mutate the config inside a live system and manage its cache carefully;
4. put global overrides in `private/private_config.yaml`;
5. edit project defaults.

The first two are normal. The third is demonstrated in the cache section.
The fourth is appropriate for genuinely global private settings, but this
notebook never prints or edits your private config. The fifth is deliberately
not performed: changing repository defaults would alter every system and many
tests.
"""
        ),
        code(
            """
changed_config = Config([
    str(CONFIG_PATH),
    {
        "percentage_vol_target": 12.0,
        "instrument_div_multiplier": 1.25,
    },
])
# Change one nested member. Replacing the whole dictionary after defaults are
# filled would silently discard every sibling setting.
changed_system = futures_system(
    data=dbFuturesSimData(), config=changed_config
)
changed_system.config.volatility_calculation["days"] = 20

pd.Series({
    "original vol target": fixed_system.config.percentage_vol_target,
    "changed vol target": changed_system.config.percentage_vol_target,
    "changed IDM": changed_system.config.instrument_div_multiplier,
    "changed fast-vol span": changed_system.config.volatility_calculation["days"],
})"""
        ),
        md(
            """
## Choose instruments and dates

For fixed weights, the keys of `instrument_weights` select the markets. For
estimated weights, remove that dictionary and set `instruments` explicitly.
If neither exists, the system considers every adjusted-price instrument in
the data source. `start_date` limits what a system sees; use ISO `YYYY-MM-DD`
in YAML.
"""
        ),
        code(
            """
two_market_config = Config([
    str(CONFIG_PATH),
    {
        "instrument_weights": {"SHFE_RB": 0.5, "CFFEX_T": 0.5},
        "instrument_div_multiplier": 1.2,
        "start_date": "2020-01-01",
    },
])
two_market_system = futures_system(
    data=dbFuturesSimData(), config=two_market_config
)

estimated_selection = Config(str(CONFIG_PATH))
del estimated_selection.instrument_weights
estimated_selection.instruments = ["SHFE_RB", "CFFEX_T"]
estimated_selection.use_instrument_weight_estimates = True

all_market_config = Config(str(CONFIG_PATH))
del all_market_config.instrument_weights
all_market_system = futures_system(
    data=dbFuturesSimData(), config=all_market_config
)

print("fixed selection:", two_market_system.get_instrument_list())
print("estimated selection:", estimated_selection.instruments)
print("first visible rebar row:",
      two_market_system.rawdata.get_daily_prices("SHFE_RB").first_valid_index().date())
print("all adjusted-price markets after global exclusions:",
      len(all_market_system.get_instrument_list()))"""
        ),
        md(
            """
## Exclude instruments or force zero weights

Ignored and duplicate instruments disappear from `get_instrument_list()`.
Bad markets and trading restrictions remain available for diagnostics but
should receive zero allocation during weight construction. Here the labels
are illustrative and live only in this temporary config; they do not change
the repository's Chinese-universe policy.
"""
        ),
        code(
            """
exclusion_config = Config([
    str(CONFIG_PATH),
    {
        "exclude_instrument_lists": {
            "ignore_instruments": ["CZCE_PM"],
            "bad_markets": ["DCE_BB"],
            "trading_restrictions": ["CZCE_LR"],
        },
        "allocate_zero_instrument_weights_to_these_instruments": [
            "DCE_BB", "CZCE_LR"
        ],
    },
])
del exclusion_config.instrument_weights
exclusion_system = futures_system(
    data=dbFuturesSimData(), config=exclusion_config
)

print("CZCE_PM visible after ignore?", "CZCE_PM" in exclusion_system.get_instrument_list())
print("bad markets:", exclusion_system.get_list_of_bad_markets())
print("trading restrictions:",
      exclusion_system.get_list_of_markets_with_trading_restrictions())"""
        ),
        md(
            """
## Create a trading rule

A trading rule function accepts one or more pandas objects as positional data
arguments, optional control parameters as keywords, and returns a one-column
Series or DataFrame. An inline function is ideal while experimenting. A rule
stored in YAML must point to an importable module function, which is why the
tutorial YAML uses the repository's EWMAC and carry functions.
"""
        ),
        code(
            """
from systems.trading_rules import TradingRule

def price_momentum(price, lookback=20):
    return price.diff(lookback) / price.diff().ewm(span=35, min_periods=10).std()

momentum_rule = TradingRule(
    price_momentum,
    data="rawdata.get_daily_prices",
    other_args={"lookback": 20},
)
custom_config = Config([
    str(CONFIG_PATH),
    {
        "forecast_scalars": {"momentum20": 1.0},
        "forecast_weights": {"momentum20": 1.0},
        "forecast_div_multiplier": 1.0,
    },
])
custom_system = futures_system(
    data=dbFuturesSimData(),
    config=custom_config,
    trading_rules={"momentum20": momentum_rule},
)
display(custom_system.rules.get_raw_forecast(FOCUS, "momentum20").dropna().tail())
print(custom_system.rules.trading_rules())"""
        ),
        md(
            """
## Use different data and save work

This tutorial's answer to "different data" is the native database-backed
simulation object. The Tushare pipeline has already transformed concrete
Chinese contracts into multiple and adjusted prices, so the backtest needs no
provider-specific code.

Configurations and expensive system caches can both be saved. We use
temporary paths here; the detailed config and cache examples appear below.
"""
        ),
        code(
            """
with tempfile.TemporaryDirectory() as tmp:
    saved_config = Path(tmp) / "changed_config.yaml"
    changed_config.save(saved_config)
    round_trip = Config(str(saved_config))
    print("saved config exists:", saved_config.exists())
    print("round-tripped vol target:", round_trip.percentage_vol_target)"""
        ),
        md(
            """
# Part 2 — Guide

The guide explains the four objects that matter: data, configuration, the
parent system, and its stages.

## Data

### Generic data-object methods

`dbFuturesSimData` implements the same simulation API as other futures data
sources. It exposes adjusted prices for rules, current-contract prices for
percentage risk and P&L, multiple prices for carry, static metadata and costs,
and FX conversion.
"""
        ),
        code(
            """
generic_data_examples = {
    "raw/back-adjusted price": data.get_raw_price(FOCUS).dropna().iloc[-1],
    "instrument count": len(data.get_instrument_list()),
    "point value": data.get_value_of_block_price_move(FOCUS),
    "currency": data.get_instrument_currency(FOCUS),
    "spread cost": data.get_spread_cost(FOCUS),
    "rolls per year": data.get_rolls_per_year(FOCUS),
}
display(pd.Series(generic_data_examples))

multiple = data.get_multiple_prices(FOCUS)
display(multiple.tail(3))
print("CNH account FX latest:", data.get_fx_for_instrument(FOCUS, "CNH").iloc[-1])
print("USD account FX latest:", data.get_fx_for_instrument(FOCUS, "USD").dropna().iloc[-1])"""
        ),
        md(
            """
### Database-backed futures data and the storage hierarchy

The simulation-facing object hides storage details:

```
System
  └── dbFuturesSimData
        └── dataBlob
              ├── adjusted, multiple, FX time series → Parquet
              ├── spread costs                    → MongoDB
              └── instrument and roll policy      → reviewed repository CSV config
```

Those CSV configuration files are policy and metadata, not the stale shipped
price CSV dataset. Roll calendars are initialization/recovery artifacts; the
simulation reads the already-built multiple and adjusted series.
"""
        ),
        code(
            """
store_types = {
    name.removeprefix("db_"): type(getattr(data.data, name)).__name__
    for name in dir(data.data)
    if name.startswith("db_")
}
display(pd.Series(store_types, name="concrete storage class"))"""
        ),
        md(
            """
### CSV, Arctic, and custom data objects

The manual also documents `csvFuturesSimData`, custom CSV directories, and
the retired Arctic backend. They share the same simulation interface, but we
do not instantiate them here: doing so would violate this notebook's promise
to use only the Chinese database-backed prices. Parquet is the current time-
series backend; changing to Arctic requires editing the storage-class mapping
and installing an optional dependency, not changing trading rules.

For a genuinely new asset type or source, inherit from `simData` or the
asset-specific futures class and implement the required public contract:
prices, instrument list, point value, currency, FX source, costs, and rolls
per year. Data methods must remain uncached; the parent `System` owns caching.
The Tushare integration already satisfies this contract, so creating another
data framework would be duplication.
"""
        ),
        md(
            """
## Configuration

### Construct configs from a dictionary, YAML, a system, or a list

Top-level dictionary keys become attributes. A list merges sources from left
to right, so later items override earlier ones. Defaults and private values
are filled only when `fill_with_defaults()` is called or the config joins a
`System`.
"""
        ),
        code(
            """
dict_config = Config({
    "instruments": ["SHFE_RB", "DCE_M"],
    "percentage_vol_target": 10.0,
    "example_nested": {"note": "ordinary nested dictionaries are fine"},
})
yaml_config = Config(str(CONFIG_PATH))
system_config = fixed_system.config
merged_config = Config([
    str(CONFIG_PATH),
    {"percentage_vol_target": 11.0, "base_currency": "CNH"},
])

pd.DataFrame({
    "source": ["dictionary", "YAML", "existing system", "merged list"],
    "vol target": [
        dict_config.percentage_vol_target,
        yaml_config.percentage_vol_target,
        system_config.percentage_vol_target,
        merged_config.percentage_vol_target,
    ],
})"""
        ),
        md(
            """
### Convert CSV configuration tables to YAML

The three conversion utilities are for human-edited weights and mapping
parameters. This example uses Chinese names and temporary files. The returned
YAML can be reviewed and pasted into a system config.
"""
        ),
        code(
            """
from sysinit.configtools.csvweights_to_yaml import (
    forecast_mapping_csv_to_yaml,
    forecast_weights_by_instrument_csv_to_yaml,
    instr_weights_csv_to_yaml,
)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    pd.DataFrame({
        "Instrument": INSTRUMENTS,
        "instrumentWeight": [0.25, 0.25, 0.25, 0.25],
    }).to_csv(tmp / "instrument_weights.csv", index=False)
    instr_weights_csv_to_yaml(
        tmp / "instrument_weights.csv", tmp / "instrument_weights.yaml"
    )

    pd.DataFrame({
        "rule": ["ewmac16_64", "ewmac32_128", "carry"],
        "SHFE_RB": [0.3, 0.2, 0.5],
        "DCE_M": [0.3, 0.2, 0.5],
    }).to_csv(tmp / "forecast_weights.csv", index=False)
    forecast_weights_by_instrument_csv_to_yaml(
        tmp / "forecast_weights.csv", tmp / "forecast_weights.yaml"
    )

    pd.DataFrame({
        "instrument": ["SHFE_RB"],
        "a_param": [1.2],
        "b_param": [1.6],
        "threshold": [5.0],
    }).to_csv(tmp / "mapping.csv", index=False)
    forecast_mapping_csv_to_yaml(tmp / "mapping.csv", tmp / "mapping.yaml")

    converted = {
        path.stem: yaml.safe_load(path.read_text())
        for path in sorted(tmp.glob("*.yaml"))
    }
display(converted)"""
        ),
        md(
            """
### Project defaults and private configuration

Precedence is: explicit backtest config, then private config, then project
defaults. Nested missing keys are filled recursively when shapes match. To
show this without touching or revealing your real private file, we create a
temporary private override.
"""
        ),
        code(
            """
with tempfile.TemporaryDirectory() as tmp:
    private_path = Path(tmp) / "private_config.yaml"
    private_path.write_text("percentage_vol_target: 12.0\\n")

    private_only = Config({}, private_filename=str(private_path))
    private_only.fill_with_defaults()

    explicit_wins = Config(
        {"percentage_vol_target": 14.0}, private_filename=str(private_path)
    )
    explicit_wins.fill_with_defaults()

    nested = Config({"volatility_calculation": {"days": 20}})
    nested.fill_with_defaults()

    print("private beats default:", private_only.percentage_vol_target)
    print("explicit beats private:", explicit_wins.percentage_vol_target)
    print("nested days override:", nested.volatility_calculation["days"])
    print("nested function filled from defaults:",
          nested.volatility_calculation["func"])"""
        ),
        md(
            """
### View, modify, extend, and save configuration

Read top-level values as attributes and nested values as ordinary dict/list
members. Add or delete top-level attributes normally. Preserve sibling values
when changing nested dictionaries. New stages may read new config attributes;
the inline stage later uses `tutorial_zscore_days` as an example.

`Config` is intentionally lightweight, so modifying its class is almost
never useful. Add data to a config, not methods to `Config`.
"""
        ),
        code(
            """
editable = Config(str(CONFIG_PATH))
editable.instrument_div_multiplier = 1.4
editable.volatility_calculation = {"days": 20}
editable.fill_with_defaults()
editable.volatility_calculation["days"] = 25
editable.tutorial_zscore_days = 60

with tempfile.TemporaryDirectory() as tmp:
    output = Path(tmp) / "editable.yaml"
    editable.save(output)
    reloaded = Config(str(output))
    print("saved elements:", len(reloaded.elements))
    print("z-score window:", reloaded.tutorial_zscore_days)

safe_view = {
    key: getattr(editable, key)
    for key in [
        "instrument_div_multiplier", "percentage_vol_target",
        "base_currency", "tutorial_zscore_days",
    ]
}
display(pd.Series(safe_view))"""
        ),
        md(
            """
### Save final estimated parameters as fixed configuration

`systemDiag` extracts the last estimated values for later paper/live use. The
estimation flags must be switched off when those fixed values are adopted.
We again write only to a temporary file.
"""
        ),
        code(
            """
from systems.diagoutput import systemDiag

with tempfile.TemporaryDirectory() as tmp:
    estimates_yaml = Path(tmp) / "estimated_parameters.yaml"
    systemDiag(estimated_system).yaml_config_with_estimated_parameters(
        estimates_yaml,
        attr_names=[
            "forecast_scalars",
            "forecast_weights",
            "forecast_div_multiplier",
            "instrument_weights",
            "instrument_div_multiplier",
        ],
    )
    estimated_export = yaml.safe_load(estimates_yaml.read_text())

print("exported fixed keys:", sorted(estimated_export))
display(pd.Series({
    "instrument_div_multiplier": estimated_export["instrument_div_multiplier"],
    "number of instrument weights": len(estimated_export["instrument_weights"]),
}))"""
        ),
        md(
            """
## System

### A pre-baked system is a small factory, not a framework

The companion file is worth reading in full. It selects defaults, constructs
`Rules`, and hands ordinary stage instances to `System`. Callers may replace
data, config, or trading rules. There is no reason to subclass `System` for
this use case.
"""
        ),
        code(
            """
from system import futures_system as tutorial_futures_system

print(inspect.getsource(tutorial_futures_system))"""
        ),
        md(
            """
### Access child stages, data, config, and system methods

Stages become attributes of the parent. `system.get_instrument_list()` applies
the config's selection/exclusion rules; `system.data.get_instrument_list()`
reports the underlying adjusted-price store. The system and every stage also
have a logger.
"""
        ),
        code(
            """
stage_names = [
    "accounts", "portfolio", "positionSize", "rawdata",
    "combForecast", "forecastScaleCap", "rules",
]
stage_overview = pd.DataFrame({
    "stage": stage_names,
    "class": [type(getattr(fixed_system, name)).__name__ for name in stage_names],
    "public members": [len(getattr(fixed_system, name).methods()) for name in stage_names],
})
display(stage_overview)
print("system instruments:", fixed_system.get_instrument_list())
print("data-source instruments:", len(fixed_system.data.get_instrument_list()))
print("sample raw-data methods:", fixed_system.rawdata.methods()[:12])"""
        ),
        md(
            """
### System caching

Stage outputs and diagnostics cache automatically. A config mutation does not
invalidate existing results: build a new system, or deliberately delete the
affected cache entries. Turning caching off is useful for tiny experiments,
not normal backtests.
"""
        ),
        code(
            """
cache_system = futures_system()
first_combined = cache_system.combForecast.get_combined_forecast(FOCUS)
refs = cache_system.cache.get_cache_refs_for_instrument(FOCUS)
print("cached SHFE_RB items:", len(refs))
print("first few refs:")
for ref in refs[:8]:
    print(" ", ref)

cache_system.config.forecast_div_multiplier = 0.25
still_cached = cache_system.combForecast.get_combined_forecast(FOCUS)
print("mutation changed cached result?", not first_combined.equals(still_cached))

cache_system.cache.delete_items_for_instrument(FOCUS)
recalculated = cache_system.combForecast.get_combined_forecast(FOCUS)
print("after targeted deletion, result changed:",
      not first_combined.equals(recalculated))

cache_system.cache.clear()
cache_system.cache.set_caching_off()
cache_system.rules.get_raw_forecast(FOCUS, "ewmac16_64")
print("items with caching off:", len(cache_system.cache.get_items_with_data()))
cache_system.cache.set_caching_on()"""
        ),
        md(
            """
### Selective deletion, protected estimates, and development workflow

Cache references can be filtered by stage, item, instrument, or the special
across-system key. Estimated scalars, weights, correlations, and
diversification multipliers are protected because they are relatively slow.
`delete_all_items()` retains them; `delete_all_items(delete_protected=True)`
does not. This supports quick iterations, followed by one clean final run.
For a live system, instrument-specific deletion after new prices avoids
recomputing stable cross-market estimates; we describe that pattern but do
not mutate a live process here.
"""
        ),
        code(
            """
all_estimated_refs = estimated_system.cache.get_items_with_data()
protected_refs = estimated_system.cache._get_protected_items()
print("estimated cache items:", len(all_estimated_refs))
print("protected items:", len(protected_refs))
print("portfolio-stage items:",
      len(all_estimated_refs.filter_by_stage_name("portfolio")))
print("across-system items:",
      len(estimated_system.cache.get_cache_refs_across_system()))
for ref in protected_refs[:8]:
    print(" ", ref)"""
        ),
        md(
            """
### Pickle and unpickle cache data

Accounting curves and optimisation objects marked `not_pickable` are omitted,
but their reusable inputs are saved. Compression is controlled by
`backtest_compress`. Loading clears the destination cache by default.
"""
        ),
        code(
            """
pickle_results = []
with tempfile.TemporaryDirectory() as tmp:
    for compressed, suffix in [(False, ".pck"), (True, ".pckz")]:
        pickle_config = Config([
            str(CONFIG_PATH), {"backtest_compress": compressed}
        ])
        source = futures_system(
            data=dbFuturesSimData(), config=pickle_config
        )
        source.portfolio.get_notional_position(FOCUS)
        filename = Path(tmp) / f"system{suffix}"
        source.cache.pickle(str(filename))

        destination = futures_system(
            data=dbFuturesSimData(), config=pickle_config
        )
        destination.cache.unpickle(str(filename))
        pickle_results.append({
            "compressed": compressed,
            "bytes": filename.stat().st_size,
            "loaded cache items": len(destination.cache.get_items_with_data()),
        })

display(pd.DataFrame(pickle_results))"""
        ),
        md(
            """
### Caching in new code: a tiny custom stage

Cache once, as early as useful. Data and input methods are pipes and should
not cache. Diagnostics and outputs may cache; slow cross-instrument results
may be protected, and complex objects may be marked non-picklable. This short
stage also shows a custom config option. It lives in the notebook because a
one-off teaching helper does not deserve another module.
"""
        ),
        code(
            """
from systems.basesystem import System
from systems.rawdata import RawData
from systems.stage import SystemStage
from systems.system_cache import diagnostic, input

class PriceDiagnostics(SystemStage):
    @property
    def name(self):
        return "priceDiagnostics"

    @input
    def get_daily_prices(self, instrument_code):
        return self.parent.rawdata.get_daily_prices(instrument_code)

    @diagnostic()
    def rolling_zscore(self, instrument_code):
        days = self.parent.config.tutorial_zscore_days
        price = self.get_daily_prices(instrument_code)
        mean = price.rolling(days).mean()
        std = price.rolling(days).std()
        return (price - mean) / std

diagnostic_config = Config({"tutorial_zscore_days": 60})
diagnostic_system = System(
    [RawData(), PriceDiagnostics()],
    dbFuturesSimData(),
    diagnostic_config,
)
display(diagnostic_system.priceDiagnostics.rolling_zscore(FOCUS).dropna().tail())
print(diagnostic_system.cache.get_cacherefs_for_stage("priceDiagnostics"))"""
        ),
        md(
            """
## Stages

A stage has non-cached inputs, cached diagnostic calculations, and cached
outputs consumed downstream. The standard wiring is:

```
data → rawdata → rules → forecastScaleCap → combForecast
     → positionSize → portfolio → accounts
```

Replacing a stage means inheriting from the existing class and preserving its
name/output contract. A completely new stage needs a unique `name`, should
inherit `SystemStage`, use public upstream outputs, and document cache
behaviour. Large stages may use multiple inheritance. The custom diagnostic
above is the smallest working example.

### Raw data: adjusted levels, denominator prices, and volatility

Additive Panama adjusted levels are for differences, never percentage
returns. Futures percentage risk divides adjusted-price changes by the actual
held-contract price from multiple prices. The current project default is
`mixed_vol_calc`; the manual's robust EWMA-with-floor example remains a valid
override but is no longer the default.
"""
        ),
        code(
            """
adjusted = fixed_system.rawdata.get_daily_prices(FOCUS)
denominator = fixed_system.rawdata.daily_denominator_price(FOCUS)
price_returns = fixed_system.rawdata.daily_returns(FOCUS)
percentage_returns = fixed_system.rawdata.get_daily_percentage_returns(FOCUS)
price_vol = fixed_system.rawdata.daily_returns_volatility(FOCUS)
percentage_vol = fixed_system.rawdata.get_daily_percentage_volatility(FOCUS)

raw_table = pd.concat({
    "adjusted level": adjusted,
    "held-contract denominator": denominator,
    "price difference": price_returns,
    "percentage return": percentage_returns,
    "price-unit vol": price_vol,
    "percentage vol": percentage_vol,
}, axis=1)
display(raw_table.dropna(how="any").tail())
print("current volatility function:",
      fixed_system.config.volatility_calculation["func"])

fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
adjusted.loc[PLOT_START:].plot(ax=axes[0], title="Adjusted level")
price_vol.loc[PLOT_START:].plot(ax=axes[1], title="Daily price-unit volatility")
plt.tight_layout()"""
        ),
        md(
            """
New raw-data subclasses make sense when reusable preprocessing deserves
diagnostic visibility—for example fundamental factors or moving averages.
For a new asset class, decide explicitly what `daily_denominator_price`
means. The Chinese futures class already makes the correct held-contract
choice.

### Rules and the `TradingRule` container

A `TradingRule` stores a function, ordered data-method references, and keyword
arguments. Functions may be passed directly or by import string; definitions
may be a callable, tuple, dictionary, or an already-built object. Missing
`data` defaults to `data.daily_prices`.
"""
        ),
        code(
            """
from systems.provided.rules.ewmac import ewmac, ewmac_forecast_with_defaults

rule_forms = {
    "callable / default data": TradingRule(ewmac_forecast_with_defaults),
    "string": TradingRule(
        "systems.provided.rules.ewmac.ewmac_forecast_with_defaults"
    ),
    "separate args": TradingRule(
        ewmac,
        data=["rawdata.get_daily_prices", "rawdata.daily_returns_volatility"],
        other_args={"Lfast": 16, "Lslow": 64},
    ),
    "tuple": TradingRule((
        "systems.provided.rules.ewmac.ewmac",
        ["rawdata.get_daily_prices", "rawdata.daily_returns_volatility"],
        {"Lfast": 16, "Lslow": 64},
    )),
    "dictionary": TradingRule({
        "function": "systems.provided.rules.ewmac.ewmac",
        "data": ["rawdata.get_daily_prices", "rawdata.daily_returns_volatility"],
        "other_args": {"Lfast": 16, "Lslow": 64},
    }),
}
for label, rule in rule_forms.items():
    print(label, "→", rule)"""
        ),
        md(
            """
### Data arguments for rules

Leading underscores route keywords to data methods: `_span` goes to the first
data method, `__span` to the second, while ordinary names go to the rule
function. Data methods must accept only `instrument_code` plus keyword
arguments. This lets cached raw-data calculations be reused across rules.
"""
        ),
        code(
            """
from systems.forecasting import Rules
from systems.system_cache import diagnostic

class MovingAverageRawData(RawData):
    @diagnostic()
    def moving_average(self, instrument_code, span=8):
        return self.get_daily_prices(instrument_code).ewm(span=span).mean()

def moving_average_difference(fast, slow, vol, multiplier=1.0):
    return multiplier * (fast - slow) / vol.ffill()

data_argument_rule = TradingRule({
    "function": moving_average_difference,
    "data": [
        "rawdata.moving_average",
        "rawdata.moving_average",
        "rawdata.daily_returns_volatility",
    ],
    "other_args": {"_span": 16, "__span": 64, "multiplier": 1.0},
})
data_argument_system = System(
    [MovingAverageRawData(), Rules({"ma16_64": data_argument_rule})],
    dbFuturesSimData(),
    Config({}),
)
display(data_argument_system.rules.get_raw_forecast(FOCUS, "ma16_64").dropna().tail())"""
        ),
        md(
            """
### Lists of rules, variations, pre-baked systems, and on-the-fly changes

`Rules` accepts one rule, a list (automatic names), or a dictionary (chosen
names). With no argument it lazily parses `config.trading_rules` after joining
a system. Variation helpers copy a base rule while changing one or several
parameters. Passing a rules dictionary into `futures_system` is the clean
interactive route.
"""
        ),
        code(
            """
from systems.trading_rules import create_variations, create_variations_oneparameter

base_rule = TradingRule(
    "systems.provided.rules.ewmac.ewmac_forecast_with_defaults"
)
one_parameter = create_variations_oneparameter(
    base_rule, [4, 16, 32], "Lfast"
)
two_parameters = create_variations(
    base_rule,
    [
        {"Lfast": 4, "Lslow": 16},
        {"Lfast": 16, "Lslow": 64},
    ],
    key_argname="Lfast",
)

print("one-parameter variations:", sorted(one_parameter))
print("two-parameter variations:", sorted(two_parameters))
print("config-loaded rules:", sorted(fixed_system.rules.trading_rules()))

interactive_rule_system = System(
    [RawData(), Rules(two_parameters)], dbFuturesSimData(), Config({})
)
chosen_name = sorted(two_parameters)[0]
display(interactive_rule_system.rules.get_raw_forecast(FOCUS, chosen_name).dropna().tail(2))"""
        ),
        md(
            """
Directly editing `system.rules._trading_rules` is advanced and private. If you
do it, first force parsing, insert only real `TradingRule` objects, and update
forecast weights or rule variations too. There is usually no speed benefit
to deleting an unused rule: rules outside the active weight/variation set are
not calculated.
"""
        ),
        code(
            """
rule_edit_system = futures_system()
processed = rule_edit_system.rules.trading_rules()
before = sorted(processed)
rule_edit_system.rules._trading_rules["temporary_rule"] = TradingRule(
    "systems.provided.rules.ewmac.ewmac_forecast_with_defaults",
    other_args={"Lfast": 8, "Lslow": 32},
)
after_add = sorted(rule_edit_system.rules._trading_rules)
rule_edit_system.rules._trading_rules.pop("temporary_rule")
print("before:", before)
print("after temporary add:", after_add)
print("restored:", sorted(rule_edit_system.rules._trading_rules))"""
        ),
        md(
            """
### Forecast scale and cap

Fixed scalars can live inside each rule or in `forecast_scalars`; the rule-
local value wins. Defaults are scalar 1 and cap 20. Estimated scalars target
an average absolute forecast of 10 with an expanding out-of-sample estimate.
Pooling across instruments is recommended; individual estimates are useful
mainly to diagnose a poorly portable rule. `backfill=True` fills the earliest
period with the first available scale and is a small, explicit look-ahead.
"""
        ),
        code(
            """
fixed_scalar = fixed_system.forecastScaleCap.get_forecast_scalar(
    FOCUS, "ewmac16_64"
)
pooled_scalar = estimated_system.forecastScaleCap.get_forecast_scalar(
    FOCUS, "ewmac16_64"
)

individual_scalar_config = Config([
    str(CONFIG_PATH),
    {
        "use_forecast_scale_estimates": True,
        "forecast_scalar_estimate": {"pool_instruments": False},
    },
])
individual_scalar_system = futures_system(
    data=dbFuturesSimData(), config=individual_scalar_config
)
individual_scalar = individual_scalar_system.forecastScaleCap.get_forecast_scalar(
    FOCUS, "ewmac16_64"
)

scalar_comparison = pd.concat({
    "fixed": fixed_scalar,
    "pooled estimate": pooled_scalar,
    "individual estimate": individual_scalar,
}, axis=1)
display(scalar_comparison.dropna(how="all").tail())
print("forecast cap:", fixed_system.forecastScaleCap.get_forecast_cap())"""
        ),
        md(
            """
### Combine forecasts: fixed and estimated weights and multipliers

Fixed forecast weights may be common or nested by instrument; absent weights
default to equal weighting. The FDM may likewise be one scalar or a mapping
by instrument. Missing early forecasts cause available fixed weights to be
renormalised, but the fixed FDM is not retrospectively adjusted.

Estimated weights use rule P&L, costs, and the optimisation settings covered
in Part 3. Estimated FDM combines time-varying weights with forecast
correlations; pooled weights/correlations give matching FDMs across markets.
"""
        ),
        code(
            """
fixed_combine = pd.DataFrame({
    "configured weight": fixed_system.config.forecast_weights,
})
display(fixed_combine)

combine_summary = pd.Series({
    "fixed FDM": fixed_system.combForecast.get_forecast_diversification_multiplier(
        FOCUS
    ).dropna().iloc[-1],
    "estimated FDM": estimated_system.combForecast.get_forecast_diversification_multiplier(
        FOCUS
    ).dropna().iloc[-1],
    "fixed combined forecast": fixed_system.combForecast.get_combined_forecast(
        FOCUS
    ).dropna().iloc[-1],
    "estimated combined forecast": estimated_system.combForecast.get_combined_forecast(
        FOCUS
    ).dropna().iloc[-1],
})
display(combine_summary)"""
        ),
        md(
            """
### Remove expensive rules

`forecast_weight_estimate.ceiling_cost_SR` controls which rules enter pooled
optimisation. The current post-processing name is
`forecast_post_ceiling_cost_SR`; it can remove expensive rules after fitting
and also applies to fixed weights. A deliberately tight 0.015 SR ceiling
shows why the surviving set can differ by Chinese market.
"""
        ),
        code(
            """
cost_ceiling_config = Config([
    str(CONFIG_PATH),
    {"forecast_post_ceiling_cost_SR": 0.015},
])
cost_ceiling_system = futures_system(
    data=dbFuturesSimData(), config=cost_ceiling_config
)
cost_rows = []
for code_ in INSTRUMENTS:
    for rule_name in ["ewmac16_64", "ewmac32_128", "carry"]:
        cost_rows.append({
            "instrument": code_,
            "rule": rule_name,
            "SR cost": cost_ceiling_system.accounts.get_SR_cost_for_instrument_forecast(
                code_, rule_name
            ),
            "survives 0.015": rule_name in cost_ceiling_system.combForecast.cheap_trading_rules_post_processing(code_),
        })
display(pd.DataFrame(cost_rows).set_index(["instrument", "rule"]))"""
        ),
        md(
            """
### Forecast mapping

Mapping introduces a no-trade zone around zero, then steepens the remaining
forecast so average magnitude is preserved. If a mapping is absent for an
instrument, no mapping occurs. Here `threshold=5`, `a=1.2`, and `b=1.6`
satisfy `b = cap × a / (cap - threshold)` for a cap of 20.
"""
        ),
        code(
            """
mapping_config = Config([
    str(CONFIG_PATH),
    {
        "forecast_mapping": {
            "SHFE_RB": {"a_param": 1.2, "b_param": 1.6, "threshold": 5.0}
        }
    },
])
mapping_system = futures_system(
    data=dbFuturesSimData(), config=mapping_config
)
before_mapping = mapping_system.combForecast.get_raw_combined_forecast_before_mapping(
    FOCUS
)
after_mapping = mapping_system.combForecast.get_combined_forecast(FOCUS)
mapping_comparison = pd.concat({
    "before mapping": before_mapping,
    "after mapping": after_mapping,
}, axis=1).loc[PLOT_START:]
display(mapping_comparison.tail())
ax = mapping_comparison.plot(title="SHFE_RB forecast mapping")
ax.set_ylabel("forecast")"""
        ),
        md(
            """
### Position scaling

Position sizing converts a combined forecast into contracts. Capital and the
annual percentage target define a daily cash-volatility budget. Point value,
held-contract price, percentage volatility, and FX determine the cash risk of
one contract. A forecast of 10 receives the average subsystem position; the
actual forecast scales it linearly.
"""
        ),
        code(
            """
position_details = systemDiag(fixed_system).calculation_details(FOCUS)
display(position_details)

cash_target = fixed_system.positionSize.get_daily_cash_vol_target()
display(pd.Series(cash_target))
position_components = {
    "block value": fixed_system.positionSize.get_block_value(FOCUS),
    "instrument currency vol": fixed_system.positionSize.get_instrument_currency_vol(FOCUS),
    "account currency vol": fixed_system.positionSize.get_instrument_value_vol(FOCUS),
    "average subsystem position": fixed_system.positionSize.get_average_position_at_subsystem_level(FOCUS),
    "subsystem position": fixed_system.positionSize.get_subsystem_position(FOCUS),
}
display(pd.concat(position_components, axis=1).dropna(how="all").tail())"""
        ),
        md(
            """
### Portfolio construction: weights and IDM

Instrument weights combine subsystem positions; IDM restores risk lost to
diversification. Fixed weights default to equal and IDM to 1. Estimated
weights use subsystem P&L, while estimated IDM uses instrument correlations.
Different history start dates cause fixed weights to be renormalised among
available markets; a fixed IDM is not adjusted for the smaller early set.
"""
        ),
        code(
            """
portfolio_summary = pd.Series({
    "fixed SHFE_RB weight": fixed_system.portfolio.get_instrument_weights()[FOCUS].dropna().iloc[-1],
    "fixed IDM": fixed_system.portfolio.get_instrument_diversification_multiplier().dropna().iloc[-1],
    "estimated SHFE_RB weight": estimated_system.portfolio.get_instrument_weights()[FOCUS].dropna().iloc[-1],
    "estimated IDM": estimated_system.portfolio.get_instrument_diversification_multiplier().dropna().iloc[-1],
})
display(portfolio_summary)

positions = pd.concat({
    "subsystem": fixed_system.positionSize.get_subsystem_position(FOCUS),
    "without IDM": fixed_system.portfolio.get_notional_position_without_idm(FOCUS),
    "final notional": fixed_system.portfolio.get_notional_position(FOCUS),
}, axis=1)
display(positions.dropna(how="all").tail())"""
        ),
        md(
            """
### Buffering and position inertia

`position` buffers use a fraction of the current target. `forecast` buffers
use a fraction of the average position at forecast 10 and therefore do not
collapse near zero. `none` disables buffering. Outside the band, trading may
go to the nearest edge or all the way to target. Accounts may round to whole
contracts.
"""
        ),
        code(
            """
buffer_rows = []
buffer_systems = {}
for method, trade_to_edge in [
    ("position", False), ("forecast", True), ("none", True)
]:
    buffer_config = Config([
        str(CONFIG_PATH),
        {
            "buffer_method": method,
            "buffer_size": 0.10,
            "buffer_trade_to_edge": trade_to_edge,
        },
    ])
    candidate = futures_system(
        data=dbFuturesSimData(), config=buffer_config
    )
    buffer_systems[method] = candidate
    held = candidate.accounts.get_buffered_position(FOCUS, roundpositions=True)
    buffer_rows.append({
        "method": method,
        "trade to edge": trade_to_edge,
        "latest rounded position": held.dropna().iloc[-1],
        "annual turnover": candidate.accounts.instrument_turnover(FOCUS),
    })
display(pd.DataFrame(buffer_rows).set_index("method"))
display(fixed_system.portfolio.get_buffers_for_position(FOCUS).dropna().tail())"""
        ),
        md(
            """
### Accounting stage and its outputs

The accounting stage exposes portfolio, instrument, subsystem, rule, and
instrument-rule P&L. `delayfill=True` assumes the next business-row close;
portfolio and instrument curves round by default, while subsystem/rule
diagnostics generally do not. Buffering applies only to the final portfolio
position, so lower-level diagnostic costs can be overstated.
"""
        ),
        code(
            """
account_outputs = {
    "portfolio": fixed_system.accounts.portfolio(),
    "instrument contribution": fixed_system.accounts.pandl_for_instrument(FOCUS),
    "standalone subsystem": fixed_system.accounts.pandl_for_subsystem(FOCUS),
    "one instrument + rule": fixed_system.accounts.pandl_for_instrument_forecast(
        FOCUS, "carry"
    ),
    "weighted instrument + rule": fixed_system.accounts.pandl_for_instrument_forecast_weighted(
        FOCUS, "carry"
    ),
    "rules within instrument": fixed_system.accounts.pandl_for_instrument_rules(FOCUS),
    "rules within instrument, unweighted": fixed_system.accounts.pandl_for_instrument_rules_unweighted(FOCUS),
    "carry across instruments": fixed_system.accounts.pandl_for_trading_rule("carry"),
    "carry weighted to total capital": fixed_system.accounts.pandl_for_trading_rule_weighted("carry"),
    "carry unweighted": fixed_system.accounts.pandl_for_trading_rule_unweighted("carry"),
    "all subsystems": fixed_system.accounts.pandl_across_subsystems(),
    "all rules": fixed_system.accounts.pandl_for_all_trading_rules(),
    "all rules, unweighted": fixed_system.accounts.pandl_for_all_trading_rules_unweighted(),
}

output_types = []
for name, obj in account_outputs.items():
    output_types.append({
        "output": name,
        "class": type(obj).__name__,
        "weighted": getattr(obj, "weighted", None),
        "members": len(obj.asset_columns) if hasattr(obj, "asset_columns") else 1,
    })
display(pd.DataFrame(output_types).set_index("output"))"""
        ),
        md(
            """
### `accountCurve`

An `accountCurve` wraps gross P&L, costs, and net P&L. Chain curve type,
frequency, and units in any order: `.gross.weekly.percent` and
`.percent.weekly.gross` are equivalent. `.value_terms` reverses percentage
display. Statistics, cumulative curves, drawdowns, and rolling volatility are
methods on the wrapper.
"""
        ),
        code(
            """
instrument_curve = fixed_system.accounts.pandl_for_instrument(FOCUS)
display(instrument_curve.percent.to_ncg_frame().tail())
curve_stats = pd.Series({
    "daily net Sharpe": instrument_curve.percent.daily.sharpe(),
    "weekly gross standard deviation": instrument_curve.percent.gross.weekly.std(),
    "annual median costs": instrument_curve.percent.costs.annual.median(),
    "annualised daily volatility": instrument_curve.percent.daily.ann_std(),
    "worst drawdown": instrument_curve.percent.worst_drawdown(),
    "t statistic": instrument_curve.percent.t_stat(),
    "p value": instrument_curve.percent.p_value(),
})
display(curve_stats)

fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
instrument_curve.percent.gross.curve().loc[PLOT_START:].plot(
    ax=axes[0], label="gross"
)
instrument_curve.percent.net.curve().loc[PLOT_START:].plot(
    ax=axes[0], label="net", title="SHFE_RB gross and net cumulative P&L"
)
axes[0].legend()
instrument_curve.percent.drawdown().loc[PLOT_START:].plot(
    ax=axes[1], title="Net drawdown"
)
plt.tight_layout()"""
        ),
        md(
            """
### `accountCurveGroup`, nested groups, and weighting

A group behaves like the aggregate curve, while `.asset_columns` and
`group["name"]` expose members. `.to_frame()` displays every member and
`.get_stats()` calculates a statistic across them with equal or history-
length weighting. `pandl_for_all_trading_rules()` is nested: each rule member
is itself an instrument group.

Weighted members are contributions to total system capital; their standalone
statistics can be non-stationary as weights change. Unweighted members are
useful individually, but summing the group usually has no portfolio meaning.
`pandl_for_trading_rule` is deliberately intermediate: normalised to full
strategy risk but internally weighted across instruments.
"""
        ),
        code(
            """
portfolio_group = fixed_system.accounts.portfolio()
print("members:", portfolio_group.asset_columns)
display(portfolio_group.percent.to_frame().tail())
print("SHFE_RB contribution Sharpe:",
      portfolio_group[FOCUS].percent.sharpe())

sharpes = portfolio_group.get_stats("sharpe", curve_type="net", freq="daily")
display(pd.Series(sharpes, name="net Sharpe"))
print("time-weighted mean:", sharpes.mean(timeweighted=True))
print("equal-weighted mean:", sharpes.mean(timeweighted=False))
print("cross-sectional t-stat / p-value:",
      sharpes.tstat(timeweighted=True), sharpes.pvalue(timeweighted=True))

nested = fixed_system.accounts.pandl_for_all_trading_rules()
carry_group = nested["carry"]
print("nested members (rules):", nested.asset_columns)
print("carry members (instruments):", carry_group.asset_columns)
display(carry_group.percent.to_frame().tail(2))"""
        ),
        md(
            """
### Test account curves

`curve.t_test()` tests a zero mean. To compare two aligned curves use the
current `account_t_test` location below. Both are conventional t-tests whose
independence/normality assumptions are dubious for financial returns; treat
them as diagnostics, not proof.
"""
        ),
        code(
            """
from systems.accounts.curves.account_curve_analysis import account_t_test

rebar_test = fixed_system.accounts.pandl_for_subsystem("SHFE_RB").percent.t_test()
paired_test = account_t_test(
    fixed_system.accounts.pandl_for_subsystem("SHFE_RB").percent,
    fixed_system.accounts.pandl_for_subsystem("DCE_M").percent,
)
print("SHFE_RB zero-mean test:", rebar_test)
print("SHFE_RB versus DCE_M paired test:", paired_test)"""
        ),
        md(
            """
### Costs and turnover

Forecast-level diagnostics always use standardised Sharpe-ratio costs.
Position-level P&L can use actual trade costs (`use_SR_costs: false`) or the
faster SR drag approximation. Actual costs normally scale historic currency
costs by volatility; `vol_normalise_currency_costs: false` is useful for a
live-like comparison, but not preferred for long historical work.

The manual's no-argument `turnover_at_portfolio_level()` snippet is stale.
That method now takes one instrument; use `total_portfolio_level_turnover()`
to add the portfolio-level contributions across all instruments.

Configured Chinese costs are half-spreads in price points; per-block,
percentage, and per-trade commissions are also supported. Holding costs count
rolls. Per-trade charges require rounded positions.
"""
        ),
        code(
            """
sr_cost_config = Config([str(CONFIG_PATH), {"use_SR_costs": True}])
sr_cost_system = futures_system(
    data=dbFuturesSimData(), config=sr_cost_config
)

turnover_and_costs = pd.Series({
    "portfolio turnover": fixed_system.accounts.total_portfolio_level_turnover(),
    "SHFE_RB subsystem turnover": fixed_system.accounts.subsystem_turnover(FOCUS),
    "SHFE_RB instrument turnover": fixed_system.accounts.instrument_turnover(FOCUS),
    "SHFE_RB carry forecast turnover": fixed_system.accounts.forecast_turnover(FOCUS, "carry"),
    "rolls per year": fixed_system.accounts.get_rolls_per_year(FOCUS),
    "SR cost per trade": fixed_system.accounts.get_SR_cost_per_trade_for_instrument(FOCUS),
    "SR holding cost": fixed_system.accounts.get_SR_holding_cost_only(FOCUS),
    "SR trading cost at turnover 5": fixed_system.accounts.get_SR_trading_cost_only_given_turnover(FOCUS, 5.0),
    "SR total cost at turnover 5": fixed_system.accounts.get_SR_cost_given_turnover(FOCUS, 5.0),
    "SR cost for carry": fixed_system.accounts.get_SR_cost_for_instrument_forecast(FOCUS, "carry"),
})
display(turnover_and_costs)

cost_method_comparison = pd.Series({
    "actual-cost annual drag (%)": fixed_system.accounts.portfolio().percent.costs.ann_mean(),
    "SR-cost annual drag (%)": sr_cost_system.accounts.portfolio().percent.costs.ann_mean(),
})
display(cost_method_comparison)"""
        ),
        md(
            """
# Part 3 — Processes

## File names

Repository-relative dotted names, ordinary slash paths, absolute path strings,
and a separate directory/filename are supported. Dots inside a real directory
or filename are ambiguous in dotted notation, so use an absolute string or a
separate filename. The current combined filename resolver does not reliably
accept `pathlib.Path`, even though some adjacent APIs do. Windows absolute
syntax is documented but not executed on this macOS notebook host.
"""
        ),
        code(
            """
from syscore.fileutils import get_resolved_pathname, resolve_path_and_filename_for_package

path_examples = {
    "absolute path string": resolve_path_and_filename_for_package(str(CONFIG_PATH)),
    "repository dotted": resolve_path_and_filename_for_package(
        "examples.chinese_futures.backtesting_tutorial.config.yaml"
    ),
    "path plus filename": resolve_path_and_filename_for_package(
        "examples.chinese_futures.backtesting_tutorial", "config.yaml"
    ),
    "resolved directory": get_resolved_pathname(TUTORIAL_DIR),
}
portable_path_examples = {
    key: str(value).replace(str(TUTORIAL_DIR.parents[2]), "<repo>")
    for key, value in path_examples.items()
}
display(pd.Series(portable_path_examples))"""
        ),
        md(
            """
## Logging

Systems, stages, configs, and data objects have `.log`. Simulation logging is
configured through Python logging; `PYSYS_LOGGING_CONFIG` may point to another
YAML configuration. Use parameterised messages and structured attributes
instead of `print` in reusable or headless production code.
"""
        ),
        code(
            """
from syslogging.logger import get_logger

log = get_logger("backtesting_tutorial", {"stage": "demonstration"})
log.warning(
    "Structured Chinese-futures example for %s",
    FOCUS,
    instrument_code=FOCUS,
)
print("system logger:", type(fixed_system.log).__name__)
print("stage logger:", type(fixed_system.rawdata.log).__name__)"""
        ),
        md(
            """
## Optimisation

Forecast and instrument weight estimation share the same process: prepare
gross and net returns, choose a fit schedule, estimate moments, optimise, then
clean/smooth/post-process weights. Weekly data is normally enough and much
faster than daily.

Forecast weights may pool gross returns across instruments. Rules first face
`ceiling_cost_SR`; `forecast_post_ceiling_cost_SR` may remove expensive rules
after fitting. `forecast_cost_estimates` controls whether turnover or total
cost is pooled. For either forecast or instrument weights,
`equalise_gross`, `cost_multiplier`, and `apply_cost_weight` decide how costs
enter before or after optimisation.

### Fitting periods and moment estimates

- `expanding` is the normal out-of-sample choice;
- `rolling` uses only `rollyears` of history;
- `in_sample` leaks future information and is only a speed/debugging tool.

Correlations, means, and standard deviations each have configurable
estimators, exponential weighting, lookbacks, and minimum observations.
Exponential moments are especially useful for shrinkage or one-period fits.
"""
        ),
        code(
            """
optimisation_config = estimated_system.config
optimisation_settings = {
    "forecast weighting": optimisation_config.forecast_weight_estimate,
    "instrument weighting": optimisation_config.instrument_weight_estimate,
    "forecast costs": optimisation_config.forecast_cost_estimates,
}
for label, settings in optimisation_settings.items():
    print(f"\\n{label}")
    display(pd.Series(settings, dtype=object))"""
        ),
        md(
            """
### Optimisation methods

The current register contains four methods:

- `equal_weights`: one-over-N among available assets;
- `one_period`: classical Markowitz, stabilised by optional equal Sharpe and
  volatility assumptions;
- `shrinkage`: shrink Sharpe ratios and correlations toward common priors;
- `handcraft`: robust correlation clustering and the recommended default.

The manual retains a bootstrapping heading, but bootstrapping was removed
after a refactor and is not a registered method. We say that plainly instead
of showing code that cannot run.
"""
        ),
        code(
            """
def small_estimated_system(method, date_method="expanding", rollyears=20):
    config = Config([
        str(CONFIG_PATH),
        {
            "instruments": INSTRUMENTS,
            "rule_variations": ["ewmac16_64", "ewmac32_128", "carry"],
            "use_forecast_scale_estimates": True,
            "use_forecast_weight_estimates": True,
            "use_forecast_div_mult_estimates": True,
            "use_instrument_weight_estimates": True,
            "use_instrument_div_mult_estimates": True,
            "forecast_weight_estimate": {
                "method": method, "date_method": date_method, "rollyears": rollyears,
            },
            "instrument_weight_estimate": {
                "method": method, "date_method": date_method, "rollyears": rollyears,
            },
        },
    ])
    del config.instrument_weights
    return futures_system(data=dbFuturesSimData(), config=config)

method_rows = []
method_systems = {"handcraft": estimated_system}
for method in ["equal_weights", "one_period", "shrinkage", "handcraft"]:
    candidate = method_systems.get(method)
    if candidate is None:
        candidate = small_estimated_system(method)
        method_systems[method] = candidate
    with redirect_stdout(StringIO()):
        forecast_weights = candidate.combForecast.get_forecast_weights(FOCUS)
        instrument_weights = candidate.portfolio.get_instrument_weights()
    method_rows.append({
        "method": method,
        "carry forecast weight": forecast_weights["carry"].dropna().iloc[-1],
        "SHFE_RB instrument weight": instrument_weights[FOCUS].dropna().iloc[-1],
        "CFFEX_T instrument weight": instrument_weights["CFFEX_T"].dropna().iloc[-1],
    })
display(pd.DataFrame(method_rows).set_index("method"))"""
        ),
        md(
            """
### Expanding, rolling, and in-sample fits

The same optimiser can use different fitting schedules. This compact
comparison is descriptive; the in-sample result must never be reported as an
honest historical backtest.
"""
        ),
        code(
            """
fit_rows = []
for date_method in ["expanding", "rolling", "in_sample"]:
    candidate = (
        estimated_system
        if date_method == "expanding"
        else small_estimated_system("handcraft", date_method=date_method, rollyears=5)
    )
    with redirect_stdout(StringIO()):
        weights = candidate.portfolio.get_instrument_weights()
    latest = weights.dropna(how="all").iloc[-1]
    fit_rows.append({
        "fit schedule": date_method,
        **{code_: latest[code_] for code_ in INSTRUMENTS},
        "honest backtest?": date_method != "in_sample",
    })
display(pd.DataFrame(fit_rows).set_index("fit schedule"))"""
        ),
        md(
            """
### Post-processing

`cleaning=True` supplies sensible weights when a fit lacks enough data.
`apply_cost_weight=True` can penalise costs after an optimiser that used gross
returns; do not double-count by also applying a non-zero cost multiplier
without intending to. Weight EWMA spans smooth jumps that would otherwise
cause trading. The post-fit cost ceiling is applied at this stage.

## Estimate correlations and diversification multipliers

Forecast correlations can pool instruments; instrument correlations cannot.
Both are commonly estimated weekly with expanding exponentially weighted
windows, cleaned for missing early history, and floored at zero before a
diversification multiplier is calculated. FDM is instrument-specific, though
pooled inputs often make it identical. Smoothing prevents changes in the
multiplier itself from creating excessive trading, and `dm_max` caps the
result.
"""
        ),
        code(
            """
forecast_corrs = estimated_system.combForecast.get_forecast_correlation_matrices(FOCUS)
instrument_corrs = estimated_system.portfolio.get_instrument_correlation_matrix()

forecast_corr = forecast_corrs.most_recent_correlation_before_date()
instrument_corr = instrument_corrs.most_recent_correlation_before_date()
display(pd.DataFrame(
    forecast_corr.values,
    index=forecast_corrs.column_names,
    columns=forecast_corrs.column_names,
).rename_axis("latest forecast correlation"))
display(pd.DataFrame(
    instrument_corr.values,
    index=instrument_corrs.column_names,
    columns=instrument_corrs.column_names,
).rename_axis("latest instrument correlation"))

dm_summary = pd.Series({
    "latest SHFE_RB FDM": estimated_system.combForecast.get_forecast_diversification_multiplier(FOCUS).dropna().iloc[-1],
    "latest portfolio IDM": estimated_system.portfolio.get_instrument_diversification_multiplier().dropna().iloc[-1],
    "FDM cap": estimated_system.config.forecast_div_mult_estimate["dm_max"],
    "IDM cap": estimated_system.config.instrument_div_mult_estimate["dm_max"],
})
display(dm_summary)"""
        ),
        md(
            """
## Specify weights as a hierarchy

Hierarchies preserve high-level style or asset-class allocations when a rule
or market disappears. Leaves are actual rule/instrument names. Group weights
renormalise within branches; optional approximate diversification multipliers
can compensate for deeper groups. Forecast hierarchies may apply the post-
cost ceiling before resolving weights.
"""
        ),
        code(
            """
hierarchy_config = Config([
    str(CONFIG_PATH),
    {
        "forecast_weights": {
            "auto_weight_from_grouping": {
                "parameters": {
                    "use_approx_DM": False,
                    "apply_forecast_post_ceiling_cost_SR_before_weighting": True,
                },
                "groups": {
                    "trend": {
                        "weight": 0.5,
                        "ewmac16_64": 0.6,
                        "ewmac32_128": 0.4,
                    },
                    "carry_style": {"weight": 0.5, "carry": 1.0},
                },
            }
        },
        "instrument_weights": {
            "auto_weight_from_grouping": {
                "parameters": {"use_approx_DM": False},
                "groups": {
                    "growth_sensitive": {
                        "weight": 0.5,
                        "SHFE_RB": 0.5,
                        "CFFEX_IF": 0.5,
                    },
                    "defensive_and_agricultural": {
                        "weight": 0.5,
                        "CFFEX_T": 0.5,
                        "DCE_M": 0.5,
                    },
                },
            }
        },
    },
])
hierarchy_system = futures_system(
    data=dbFuturesSimData(), config=hierarchy_config
)
display(hierarchy_system.combForecast.get_forecast_weights(FOCUS).dropna().tail(1))
display(hierarchy_system.portfolio.get_instrument_weights().dropna(how="all").tail(1))"""
        ),
        md(
            """
## Capital correction: fixed and varying capital

Most methods use fixed notional capital, keeping risk stable and account
curves comparable. `full_compounding` reinvests gains and losses.
`half_compounding` is the manual's loss-only method: capital can fall below
its initial level but is capped at one after gains. Variable-capital methods
have explicit counterparts for positions, buffers, instrument P&L, and the
whole portfolio.
"""
        ),
        code(
            """
capital_rows = []
capital_systems = {}
for label, function in [
    ("fixed", "syscore.capital.fixed_capital"),
    ("full compounding", "syscore.capital.full_compounding"),
    ("loss-only / half", "syscore.capital.half_compounding"),
]:
    capital_config = Config([
        str(CONFIG_PATH), {"capital_multiplier": {"func": function}}
    ])
    candidate = futures_system(
        data=dbFuturesSimData(), config=capital_config
    )
    capital_systems[label] = candidate
    multiplier = candidate.accounts.capital_multiplier().dropna()
    variable_curve = candidate.accounts.portfolio_with_multiplier().percent
    capital_rows.append({
        "policy": label,
        "final multiplier": multiplier.iloc[-1],
        "minimum multiplier": multiplier.min(),
        "final cumulative P&L (%)": variable_curve.curve().dropna().iloc[-1],
    })
display(pd.DataFrame(capital_rows).set_index("policy"))

fixed_capital_system = capital_systems["fixed"]
variable_method_pairs = pd.DataFrame({
    "fixed capital": [
        "positionSize.get_daily_cash_vol_target",
        "portfolio.get_notional_position",
        "portfolio.get_buffers_for_position",
        "accounts.get_buffered_position",
        "accounts.pandl_for_instrument",
        "accounts.portfolio",
    ],
    "variable capital": [
        "accounts.get_actual_capital",
        "portfolio.get_actual_position",
        "portfolio.get_actual_buffers_for_position",
        "accounts.get_buffered_position_with_multiplier",
        "accounts.pandl_for_instrument_with_multiplier",
        "accounts.portfolio_with_multiplier",
    ],
})
display(variable_method_pairs)"""
        ),
        md(
            """
# Part 4 — Reference

## Current system, data, and stage method inventory

The manual's tables distinguish diagnostic, input, and output methods. The
most reliable live reference is still `.methods()` on the current object.
The cells above exercised the principal outputs and diagnostics; this table
shows the complete public surface in this checkout, including methods added
since the prose table was written.
"""
        ),
        code(
            """
method_objects = {
    "data": fixed_system.data,
    "rawdata": fixed_system.rawdata,
    "rules": fixed_system.rules,
    "forecastScaleCap": fixed_system.forecastScaleCap,
    "combForecast": fixed_system.combForecast,
    "positionSize": fixed_system.positionSize,
    "portfolio": fixed_system.portfolio,
    "accounts": fixed_system.accounts,
}
method_inventory = pd.DataFrame([
    {
        "object": name,
        "public member count": len(obj.methods()),
        "members": ", ".join(obj.methods()),
    }
    for name, obj in method_objects.items()
]).set_index("object")
display(method_inventory)"""
        ),
        md(
            """
## Configuration option inventory

The reference config spans raw-data volatility; rule definitions; fixed or
estimated scalar/cap; fixed or estimated forecast weights and FDM; mapping;
capital/risk; fixed or estimated instrument weights and IDM; buffering;
costs; and capital correction. Values below come from the fully populated
tutorial system, but only a safe allow-list is displayed—private values and
credentials are never inspected.
"""
        ),
        code(
            """
config_groups = {
    "raw data": ["volatility_calculation"],
    "rules": ["trading_rules"],
    "forecast scale/cap": [
        "use_forecast_scale_estimates", "forecast_scalar",
        "forecast_scalar_estimate", "forecast_cap", "average_absolute_forecast",
    ],
    "forecast combination": [
        "forecast_weights", "rule_variations", "use_forecast_weight_estimates",
        "forecast_weight_estimate", "forecast_weight_ewma_span",
        "forecast_post_ceiling_cost_SR", "forecast_div_multiplier",
        "use_forecast_div_mult_estimates", "forecast_correlation_estimate",
        "forecast_div_mult_estimate", "forecast_mapping",
    ],
    "position sizing": [
        "percentage_vol_target", "notional_trading_capital", "base_currency",
    ],
    "portfolio": [
        "instrument_weights", "instruments", "use_instrument_weight_estimates",
        "instrument_weight_estimate", "instrument_weight_ewma_span",
        "instrument_div_multiplier", "use_instrument_div_mult_estimates",
        "instrument_correlation_estimate", "instrument_div_mult_estimate",
    ],
    "buffering/accounting": [
        "buffer_method", "buffer_size", "buffer_trade_to_edge",
        "use_SR_costs", "vol_normalise_currency_costs",
        "forecast_cost_estimates", "capital_multiplier",
    ],
}

def compact_value(value):
    text = repr(value)
    return text if len(text) <= 120 else text[:117] + "..."

config_rows = []
for group, names in config_groups.items():
    for name in names:
        config_rows.append({
            "section": group,
            "option": name,
            "present": hasattr(fixed_system.config, name),
            "tutorial/current value": compact_value(
                getattr(fixed_system.config, name, "not set")
            ),
        })
display(pd.DataFrame(config_rows).set_index(["section", "option"]))"""
        ),
        md(
            """
## Important manual-to-current-code corrections

| Manual-era spelling or example | Current working form |
|---|---|
| `system.rawdata.methods.methods()` | `system.rawdata.methods()` |
| robust EWMA volatility shown as the default | current default is `mixed_vol_calc`; robust remains configurable |
| `forecast_cost_estimate` | `forecast_cost_estimates` |
| `post_ceiling_cost_SR` | `forecast_post_ceiling_cost_SR` |
| diversification cap `div_mult` | `dm_max` |
| cache deletion called on `system` | call `system.cache.delete_*` |
| `from syscore.accounting import account_t_test` | `systems.accounts.curves.account_curve_analysis` |
| bootstrapping listed as a method | not implemented or registered now |
| DB example builds `data` but then omits it | pass `data=dbFuturesSimData()` or use this tutorial factory |

Other principles remain intact: adjusted futures levels support differences,
not returns; rebuild a system after config changes unless you deliberately
manage caches; pool stable estimates where sensible; use expanding or rolling
fits; and judge net, costed, buffered portfolios rather than attractive raw
signals.

## Coverage checklist

This final table is intentionally explicit. Each meaningful heading in
`docs/backtesting.md` is represented above; repeated reference entries are
grouped only when they describe the same demonstrated interface.
"""
        ),
        code(
            """
COVERAGE_TOPICS = {
    "How do I?": [
        "single rule and instrument", "standard futures backtest",
        "estimated futures backtest", "intermediate results", "profitability",
        "config file changes", "config object and new system",
        "in-system config mutation", "private config", "project defaults",
        "fixed and estimated instrument selection", "recent start date",
        "all available instruments", "exclude instruments",
        "zero positive weights", "write a rule", "configure a rule",
        "different data or instruments", "save config and cache",
    ],
    "Data": [
        "generic data API", "csvFuturesSimData architecture",
        "dbFuturesSimData setup and use", "Arctic legacy backend",
        "custom data objects", "simData method contract",
    ],
    "Configuration": [
        "dictionary", "YAML file", "pre-baked system", "config list",
        "CSV-to-YAML utilities", "project defaults", "private precedence",
        "defaults for changed functions", "view parameters", "modify parameters",
        "use config in a system", "custom config options", "save config",
        "export estimated parameters", "why not modify Config",
    ],
    "System": [
        "pre-baked fixed system", "pre-baked estimated system",
        "children/data/config", "system methods", "cache labels and hits",
        "cache on/off", "pickle", "compressed pickle", "selective deletion",
        "protected cache workflow", "live cache pattern", "cache decorators",
        "new pre-baked system", "why not subclass System",
    ],
    "Stages": [
        "stage wiring", "write a stage", "standard stage list",
        "raw-data methods", "price and percentage volatility",
        "new raw-data subclass", "TradingRule forms", "data arguments",
        "Rules from config", "interactive rules", "rule variations",
        "new Rules instance", "rules passed to pre-baked system",
        "on-the-fly private rule edits", "fixed scalar and cap",
        "pooled scalar estimate", "individual scalar estimate",
        "fixed forecast weights/FDM", "estimated forecast weights/FDM",
        "remove expensive rules", "forecast mapping", "position sizing",
        "fixed instrument weights/IDM", "estimated instrument weights/IDM",
        "position and forecast buffers", "capital correction link",
        "account outputs", "accountCurve", "accountCurveGroup", "nested groups",
        "weighted versus unweighted", "account-curve tests", "costs and turnover",
    ],
    "Processes": [
        "file-name formats", "basic logging", "structured logging",
        "optimisation function/data", "expensive assets", "pooled gross returns",
        "net costs", "expanding/in-sample/rolling periods", "moment estimates",
        "equal weights", "one period", "bootstrapping unavailable", "shrinkage",
        "handcrafting", "post-processing", "correlations", "FDM and IDM",
        "hierarchical forecast weights", "hierarchical instrument weights",
        "fixed/full/loss-only capital",
    ],
    "Reference": [
        "system method table", "data method table", "raw-data method table",
        "rules method table", "scale/cap method table", "combine method table",
        "position method table", "portfolio method table", "accounts inputs",
        "accounts diagnostics", "accounts outputs", "volatility config",
        "trading-rules config", "fixed/estimated scalar config", "forecast cap",
        "fixed/estimated forecast weights", "rule variations", "fixed/estimated FDM",
        "mapping config", "capital scaling", "fixed/estimated instrument weights",
        "fixed/estimated IDM", "buffer config", "cost config", "capital config",
    ],
}
coverage_rows = [
    {"manual area": area, "topic": topic, "covered": True}
    for area, topics in COVERAGE_TOPICS.items()
    for topic in topics
]
coverage_checklist = pd.DataFrame(coverage_rows)
assert coverage_checklist["covered"].all()
display(coverage_checklist.groupby("manual area").agg(
    topics=("topic", "count"), covered=("covered", "all")
))
print(f"Coverage checklist: {len(coverage_checklist)} topics, all represented.")"""
        ),
        md(
            """
## Where to go next

- `docs/backtesting.md` remains the full prose reference and historical record.
- `docs/tushare_chinese_futures.md` explains how these Chinese stores are built.
- `docs/tushare_data_inspection.md` provides read-only checks for each data stage.
- The other notebooks in `examples/chinese_futures/` move from mechanics into
  point-in-time liquidity, full-universe research, pooling, and strategy
  comparisons.

The useful habit is simple: keep data, config, stage outputs, and accounting
visible. When a backtest surprises you, walk upstream one stage at a time.
"""
        ),
    ]
    return cells


def _carry_trend_cells() -> list:
    cells = [
        md(
            """
# 18 — Carry versus trend: allocation and honest exclusions

This notebook replaces the sprawling carry-parameter and instrument-selection
project with one linear, inspectable experiment.  It asks four questions:

1. Is Chinese-futures trend P&L positively skewed in these data?
2. Are carry and trend actually weakly correlated, including bad months?
3. Does a larger carry forecast budget improve Sharpe without hiding worse
   tail risk or drawdown?
4. If we fit only through 2023-07-27, is any lineage or whole style so
   consistently loss-making that exclusion is defensible?

All 95 reviewed Chinese histories are present.  A new market enters as soon
as price, volatility, every core forecast, and the causal liquidity rule are
ready.  Present-day survivor labels never remove it.  The core is deliberately
small: four native absolute-carry smoothings (10/30/60/125) and three native
medium/slow EWMAC rules.  Earlier research found no robust reason to replace
that carry grid, and the 28-rule extension did not beat the carry-diversified
core convincingly enough to justify making it the production default.

## The rules are registered before looking at 2023–2026

- Fit window: 2008-07-28 through 2023-07-27.
- Revealed audit: 2023-07-28 through 2026-07-27.  It is already consumed and
  is **not** a fresh holdout.
- Carry-budget candidates are 0/20/40/50/60/70/80/100%.  Pure styles are
  diagnostics.  A 20–80% candidate replaces the 40% carry prior only if its
  pre-2023 robust annual-block score is at least 0.10 better; near-ties keep
  the candidate closest to 40%.
- A lineage/style is a “proven loser” only with at least eight complete
  July-to-July blocks, a loss in at least 75% of them, and a Bonferroni-adjusted
  one-sided 95% upper confidence bound for mean annual return below zero.
- Selection is only at lineage and whole-style level.  Choosing the best
  EWMAC or carry horizon separately for 95 instruments would be multiple-test
  overfitting, so every surviving style keeps all its horizons.

The systems use native forecasts, sizing, buffering, delayed whole-contract
fills, and stored cash/spread costs.  Only the dated portfolio gate is the
same thin repository helper introduced earlier in this series.  There is no
new `system.py`, YAML hierarchy, artifact framework, or hidden research helper.
"""
        ),
        code(SETUP_CELL),
        code(
            """
import gc
import math
from IPython.utils.io import capture_output
from scipy import stats

from sysdata.config.configdata import Config
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from sysobjects.adjusted_prices import futuresAdjustedPrices
from sysobjects.multiple_prices import futuresMultiplePrices
from sysobjects.spot_fx_prices import fxPrices
from systems.accounts.accounts_stage import Account
from systems.basesystem import System
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecasting import Rules
from systems.positionsizing import PositionSizing
from systems.rawdata import RawData

R.limit_blas_threads()

FIT_START = pd.Timestamp("2008-07-28")
FIT_END = pd.Timestamp("2023-07-27")
AUDIT_START = pd.Timestamp("2023-07-28")
CUTOFF = pd.Timestamp("2026-07-27")
TRADING_DAYS = 256.0
CAPITAL = 100_000_000
TARGET_VOL = 16.0
"""
        ),
        md(
            """
## A visible database cutoff

The database may later contain more data.  This tiny subclass clips the three
time-series boundaries used by simulation and exposes only the reviewed
Tushare manifest.  It is intentionally here in the notebook rather than in a
project framework.
"""
        ),
        code(
            """
class CutoffChinaData(dbFuturesSimData):
    def __init__(self, cutoff):
        self.cutoff = (
            pd.Timestamp(cutoff).normalize()
            + pd.Timedelta(days=1)
            - pd.Timedelta(nanoseconds=1)
        )
        manifest = R.TushareInstrumentManifest.from_csv()
        expected = sorted(
            item.instrument_code for item in manifest.mappings if item.is_stitchable
        )
        assert len(expected) == len(set(expected)) == 95
        self._instruments = tuple(expected)
        super().__init__()

        stored = set(super().get_instrument_list())
        missing = sorted(set(expected) - stored)
        if missing:
            raise ValueError(f"Database is missing reviewed instruments: {missing}")

        metadata = self.get_all_instrument_data_as_df().reindex(expected)
        bad = metadata.index[
            (metadata["Currency"] != "CNH") | (metadata["Region"] != "ASIA")
        ].tolist()
        if bad:
            raise ValueError(f"Non-Chinese instruments exposed: {bad}")

        listed = []
        for instrument in expected:
            raw = self.db_futures_adjusted_prices_data.get_adjusted_prices(
                instrument
            ).dropna()
            if len(raw) and raw.index[0] <= self.cutoff:
                listed.append(instrument)
        self._instruments = tuple(listed)

    def get_instrument_list(self):
        return list(self._instruments)

    def _check(self, instrument):
        if instrument not in self._instruments:
            raise ValueError(f"{instrument} is outside this cutoff universe")

    def get_backadjusted_futures_price(self, instrument_code):
        self._check(instrument_code)
        prices = super().get_backadjusted_futures_price(instrument_code)
        return futuresAdjustedPrices(pd.Series(prices.loc[: self.cutoff]).copy())

    def get_multiple_prices_from_start_date(self, instrument_code, start_date):
        self._check(instrument_code)
        prices = super().get_multiple_prices_from_start_date(
            instrument_code, start_date=start_date
        )
        return futuresMultiplePrices(pd.DataFrame(prices.loc[: self.cutoff]).copy())

    def _get_fx_data_from_start_date(self, currency1, currency2, start_date):
        prices = super()._get_fx_data_from_start_date(
            currency1, currency2, start_date=start_date
        )
        return fxPrices(pd.Series(prices.loc[: self.cutoff]).copy())


data = CutoffChinaData(CUTOFF)
ALL = R.chinese_universe(data)
assert len(ALL) == 95
assert max(data.daily_prices(code).index.max() for code in ALL) <= data.cutoff

# A predecessor and successor are one economic experiment, never two votes.
PREDECESSOR_SUCCESSOR = (
    ("CZCE_ME", "CZCE_MA"), ("CZCE_RO", "CZCE_OI"),
    ("CZCE_WT", "CZCE_PM"), ("CZCE_ER", "CZCE_RI"),
    ("CZCE_WS", "CZCE_WH"), ("CZCE_TC", "CZCE_ZC"),
    ("DCE_FB_OLD", "DCE_FB"),
)
LINEAGE = {instrument: instrument for instrument in ALL}
for predecessor, successor in PREDECESSOR_SUCCESSOR:
    LINEAGE[predecessor] = successor
assert len(set(LINEAGE.values())) == 88

print(f"{len(ALL)} Chinese instruments, {len(set(LINEAGE.values()))} lineages")
print(f"latest readable price date: {CUTOFF.date()}")
"""
        ),
        md(
            """
## Native rules and common controls

Fixed forecast scalars are the repository's long-run production fits.  They
normalise forecast amplitude; they do not optimise expected return.  The FDM
is fixed at one for every carry budget so the weight experiment is not quietly
helped by a separately fitted diversification multiplier.  Realised volatility
and a common-16%-volatility drawdown are both reported later.

The adjusted price is used in differences, never percentage changes.
Volatility backfilling is off, early warm-up stays missing, instrument weights
are equal across the currently eligible set, IDM is 2.5, capital is fixed at
100m CNH, and costs are the stored native cash/spread costs without future-vol
normalisation.
"""
        ),
        code(
            """
EWMAC = "systems.provided.rules.ewmac.ewmac"
EWMAC_DATA = ["rawdata.get_daily_prices", "rawdata.daily_returns_volatility"]
CARRY = "systems.provided.rules.carry.carry"

TREND_RULES = ("ewmac16_64", "ewmac32_128", "ewmac64_256")
CARRY_RULES = ("carry10", "carry30", "carry60", "carry125")
ALL_RULES = TREND_RULES + CARRY_RULES

TRADING_RULES = {
    "ewmac16_64": dict(function=EWMAC, data=EWMAC_DATA,
                       other_args=dict(Lfast=16, Lslow=64)),
    "ewmac32_128": dict(function=EWMAC, data=EWMAC_DATA,
                        other_args=dict(Lfast=32, Lslow=128)),
    "ewmac64_256": dict(function=EWMAC, data=EWMAC_DATA,
                        other_args=dict(Lfast=64, Lslow=256)),
    "carry10": dict(function=CARRY, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=10)),
    "carry30": dict(function=CARRY, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=30)),
    "carry60": dict(function=CARRY, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=60)),
    "carry125": dict(function=CARRY, data=["rawdata.raw_carry"],
                     other_args=dict(smooth_days=125)),
}
FORECAST_SCALARS = {
    "ewmac16_64": 3.75, "ewmac32_128": 2.65, "ewmac64_256": 1.87,
    "carry10": 27.82, "carry30": 28.38,
    "carry60": 28.40, "carry125": 29.37,
}


def weights_for(carry_budget):
    return {
        **{name: (1.0 - carry_budget) / len(TREND_RULES)
           for name in TREND_RULES},
        **{name: carry_budget / len(CARRY_RULES) for name in CARRY_RULES},
    }


def native_system(carry_budget, eligibility, fixed_weights,
                  forecast_weights_by_instrument=None):
    forecast_weights = (
        weights_for(carry_budget)
        if forecast_weights_by_instrument is None
        else forecast_weights_by_instrument
    )
    config = Config(dict(
        trading_rules=TRADING_RULES,
        forecast_scalars=FORECAST_SCALARS,
        forecast_weights=forecast_weights,
        use_forecast_scale_estimates=False,
        use_forecast_weight_estimates=False,
        forecast_div_multiplier=1.0,
        use_forecast_div_mult_estimates=False,
        instruments=ALL,
        instrument_weights={name: 1 / len(ALL) for name in ALL},
        instrument_div_multiplier=2.5,
        use_instrument_weight_estimates=False,
        use_instrument_div_mult_estimates=False,
        notional_trading_capital=CAPITAL,
        percentage_vol_target=TARGET_VOL,
        base_currency="CNH",
        capital_multiplier=dict(func="syscore.capital.fixed_capital"),
        buffer_method="forecast",
        buffer_size=0.10,
        buffer_trade_to_edge=True,
        forecast_cap=20.0,
        use_SR_costs=False,
        forecast_post_ceiling_cost_SR=999.0,
        vol_normalise_currency_costs=False,
        multiply_roll_costs_by=0.5,
        volatility_calculation=dict(
            func="sysquant.estimators.vol.mixed_vol_calc",
            name_returns_attr_in_rawdata="daily_returns",
            multiplier_to_get_daily_vol=1.0,
            days=35,
            min_periods=10,
            slow_vol_years=20,
            proportion_of_slow_vol=0.35,
            vol_abs_min=0.0000000001,
            backfill=False,
        ),
    ))
    return System(
        [Account(), R.PointInTimePortfolios(eligibility, fixed_weights),
         PositionSizing(), RawData(), ForecastCombine(),
         ForecastScaleCap(), Rules()],
        data,
        config,
    )


def accounting_frame(curve):
    frame = pd.concat({
        "gross": curve.percent.gross.as_ts,
        "costs": curve.percent.costs.as_ts,
        "net": curve.percent.as_ts,
    }, axis=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    error = (frame["net"] - frame["gross"] - frame["costs"]).abs().max()
    assert error < 1e-8
    return frame


def portfolio_frame(system):
    return accounting_frame(
        system.accounts.portfolio(delayfill=True, roundpositions=True)
    )
"""
        ),
        md(
            """
## One membership panel for every comparison

Liquidity enters at a 20-observed-session average of 130 contracts and exits
below 70.  Forecast readiness is kept separate because pandas can otherwise
turn an all-missing forecast panel into a plausible zero.  Every allocation
below trades exactly the same instruments on exactly the same dates.
"""
        ),
        code(
            """
print("reading held-contract volume ...")
with capture_output():
    held_volume = R.held_contract_volumes(data, ALL)
liquidity = R.liquidity_eligibility(
    held_volume, force_terminal_close=True
)

print("checking all seven native forecasts for readiness ...")
probe = native_system(0.40, liquidity, R.equal_weight_panel(liquidity))
ready = {}
with capture_output():
    for instrument in ALL:
        forecasts = probe.combForecast.get_all_forecasts(instrument)
        subsystem = probe.portfolio.get_subsystem_position(instrument)
        aligned = pd.concat({
            "all forecasts": forecasts.notna().all(axis=1),
            "volatility and sizing": subsystem.notna(),
        }, axis=1).reindex(liquidity.index).ffill().fillna(False)
        ready[instrument] = aligned.all(axis=1)

ready = pd.DataFrame(ready, index=liquidity.index).astype(bool)
common_eligibility = liquidity & ready
common_weights = R.equal_weight_panel(common_eligibility)
first_investable = common_weights.index[
    common_weights.abs().sum(axis=1) > 0
][0]

assert not common_weights.where(~common_eligibility, 0.0).abs().to_numpy().any()
assert common_weights.loc[common_weights.sum(axis=1) > 0].sum(axis=1).sub(1).abs().max() < 1e-12

history = pd.DataFrame({
    "first price": {name: data.daily_prices(name).index[0] for name in ALL},
    "first eligible": {
        name: common_eligibility.index[common_eligibility[name]][0]
        if common_eligibility[name].any() else pd.NaT
        for name in ALL
    },
    "eligible days": common_eligibility.sum(),
})
print(f"{int((common_eligibility.sum() > 0).sum())} instruments ever trade; "
      f"{int(common_eligibility.iloc[-1].sum())} trade at the cutoff")
display(history.sort_values("first price").tail(12))

del probe
gc.collect()
"""
        ),
        md(
            """
## Carry-weight sweep

These are forecast budgets, not guaranteed realised risk shares.  Each system
is a complete native rerun, so forecast buffering, integer contracts, and costs
are allowed to respond to the different blend.  Pure carry and pure trend are
diagnostics; neither is eligible to become the production blend.  Full and
half-exposure CAGR mechanically compound the native fixed-capital daily P&L;
they are interpretable wealth illustrations, not separate variable-capital
contract reruns.
"""
        ),
        code(
            """
CARRY_WEIGHTS = (0.0, 0.20, 0.40, 0.50, 0.60, 0.70, 0.80, 1.0)
allocation_frames = {}
style_systems = {}

for carry_weight in CARRY_WEIGHTS:
    print(f"running {carry_weight:.0%} carry / {1-carry_weight:.0%} trend ...")
    system = native_system(carry_weight, common_eligibility, common_weights)
    with capture_output():
        frame = portfolio_frame(system).loc[first_investable:CUTOFF]
    allocation_frames[carry_weight] = frame
    if carry_weight in (0.0, 1.0):
        style_systems[carry_weight] = system
    else:
        del system
        gc.collect()

print("allocation sweep complete")
"""
        ),
        code(
            """
def sharpe(returns):
    clean = returns.dropna().astype(float)
    volatility = clean.std(ddof=1)
    if len(clean) < 2 or not np.isfinite(volatility) or volatility <= 0:
        return np.nan
    return clean.mean() / volatility * math.sqrt(TRADING_DAYS)


def compounded_stats(returns, exposure=1.0):
    clean = returns.dropna().astype(float) * exposure / 100.0
    if clean.empty:
        return np.nan, np.nan, np.nan
    wealth = (1.0 + clean).cumprod()
    anchored = pd.concat([
        pd.Series([1.0], index=[clean.index[0] - pd.Timedelta(nanoseconds=1)]),
        wealth,
    ])
    drawdown = anchored / anchored.cummax() - 1.0
    years = (clean.index[-1] - clean.index[0]).days / 365.25
    ending = float(wealth.iloc[-1])
    cagr = ending ** (1 / years) - 1 if years > 0 and ending > 0 else np.nan
    return 100 * (ending - 1), 100 * cagr, 100 * drawdown.min()


def performance(frame, start, end):
    sample = frame.loc[start:end].copy()
    net = sample["net"]
    annual_vol = net.std(ddof=1) * math.sqrt(TRADING_DAYS)
    cumulative = pd.concat([
        pd.Series([0.0], index=[net.index[0] - pd.Timedelta(nanoseconds=1)]),
        net.cumsum(),
    ])
    additive_drawdown = cumulative - cumulative.cummax()
    full_total, full_cagr, full_drawdown = compounded_stats(net, 1.0)
    half_total, half_cagr, half_drawdown = compounded_stats(net, 0.5)
    scaled = net * TARGET_VOL / annual_vol if annual_vol > 0 else net * np.nan
    _, _, common_vol_drawdown = compounded_stats(scaled, 1.0)
    return {
        "observations": len(sample),
        "net return %": net.sum(),
        "cost drag %": -sample["costs"].sum(),
        "net Sharpe": sharpe(net),
        "2x-cost Sharpe": sharpe(sample["gross"] + 2 * sample["costs"]),
        "ann vol %": annual_vol,
        "additive max DD %": additive_drawdown.min(),
        "full CAGR %": full_cagr,
        "full max DD %": full_drawdown,
        "half-exposure CAGR %": half_cagr,
        "half-exposure max DD %": half_drawdown,
        "16%-vol full max DD %": common_vol_drawdown,
    }


WINDOWS = {
    "post-2008": (FIT_START, CUTOFF),
    "fit through 2023-07-27": (FIT_START, FIT_END),
    "revealed last 3 years": (AUDIT_START, CUTOFF),
}
rows = []
for window, (start, end) in WINDOWS.items():
    for carry_weight, frame in allocation_frames.items():
        rows.append({
            "window": window,
            "carry weight": carry_weight,
            **performance(frame, start, end),
        })
allocation_summary = pd.DataFrame(rows).set_index(["window", "carry weight"])
display(allocation_summary[[
    "net Sharpe", "2x-cost Sharpe", "ann vol %", "additive max DD %",
    "full CAGR %", "full max DD %", "half-exposure CAGR %",
    "half-exposure max DD %", "16%-vol full max DD %",
]])
"""
        ),
        code(
            """
fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
for window in WINDOWS:
    table = allocation_summary.loc[window]
    axes[0].plot(100 * table.index, table["net Sharpe"], marker="o", label=window)
    axes[1].plot(100 * table.index, table["ann vol %"], marker="o", label=window)
    axes[2].plot(
        100 * table.index, table["16%-vol full max DD %"], marker="o", label=window
    )
axes[0].set_title("Net Sharpe")
axes[1].set_title("Resulting annual volatility")
axes[2].set_title("Full-compounding DD at common 16% vol")
for axis in axes:
    axis.set_xlabel("carry forecast budget (%)")
axes[0].legend()
plt.tight_layout()
plt.show()
"""
        ),
        md(
            """
## Choose the global carry budget using pre-2023 annual blocks

The score below is median annual-block Sharpe minus half its median absolute
deviation.  It rewards a repeatable result rather than one huge year.  The
40% carry prior stays unless another mixed candidate clears the predeclared
0.10 hurdle.  The latest three years have no vote.
"""
        ),
        code(
            """
def annual_block_sharpes(frame):
    values = {}
    for year in range(2008, 2023):
        start = pd.Timestamp(year, 7, 28)
        end = pd.Timestamp(year + 1, 7, 27)
        sample = frame.loc[start:end, "net"]
        values[f"{year}-{year+1}"] = sharpe(sample)
    return pd.Series(values)


block_sharpes = pd.DataFrame({
    weight: annual_block_sharpes(frame)
    for weight, frame in allocation_frames.items()
})
score_rows = []
for weight in CARRY_WEIGHTS:
    values = block_sharpes[weight].dropna()
    median = values.median()
    mad = (values - median).abs().median()
    score_rows.append({
        "carry weight": weight,
        "median block Sharpe": median,
        "MAD": mad,
        "robust score": median - 0.5 * mad,
        "positive blocks": (values > 0).mean(),
    })
weight_scores = pd.DataFrame(score_rows).set_index("carry weight")

PRIOR = 0.40
selectable = weight_scores.loc[[0.20, 0.40, 0.50, 0.60, 0.70, 0.80]]
best_score = selectable["robust score"].max()
if best_score >= selectable.loc[PRIOR, "robust score"] + 0.10:
    near_best = selectable[
        selectable["robust score"] >= best_score - 0.10
    ].index
    CHOSEN_CARRY_WEIGHT = min(near_best, key=lambda value: abs(value - PRIOR))
    selection_reason = "a challenger cleared the 0.10 pre-2023 hurdle"
else:
    CHOSEN_CARRY_WEIGHT = PRIOR
    selection_reason = "no challenger cleared the 0.10 pre-2023 hurdle"

display(weight_scores)
block_sharpes.plot(kind="box", figsize=(11, 4.5),
                   title="July-to-July net Sharpe by carry forecast budget")
plt.xlabel("carry forecast budget")
plt.show()
print(f"Frozen global budget: {CHOSEN_CARRY_WEIGHT:.0%} carry / "
      f"{1-CHOSEN_CARRY_WEIGHT:.0%} trend because {selection_reason}.")
"""
        ),
        md(
            """
## Is trend positively skewed, and is carry really diversifying?

Skew depends on sampling horizon.  Daily skew alone is a poor description of
a slow trend trade, so the table reports daily, weekly, and monthly net P&L.
It also reports expected losses in the worst 5% of observations.  Correlation
is shown over the same horizons.  For monthly tails we report how often both
styles are simultaneously in their own bottom quintile (4% under independence)
and what one style makes during the other's bad months.  Low average
correlation is not protection if the two sleeves fail together in bad regimes.
"""
        ),
        code(
            """
def aggregate(series, frequency):
    if frequency == "daily":
        return series
    rule = "W-FRI" if frequency == "weekly" else "M"
    return series.resample(rule).sum(min_count=1)


def expected_shortfall(series, probability=0.05):
    cutoff = series.quantile(probability)
    return series[series <= cutoff].mean()


diagnostic_rows = []
for window, (start, end) in WINDOWS.items():
    trend = allocation_frames[0.0].loc[start:end, "net"]
    carry = allocation_frames[1.0].loc[start:end, "net"]
    for frequency in ("daily", "weekly", "monthly"):
        pair = pd.concat({
            "carry": aggregate(carry, frequency),
            "trend": aggregate(trend, frequency),
        }, axis=1).fillna(0.0)
        periods_per_year = {
            "daily": TRADING_DAYS, "weekly": 52.0, "monthly": 12.0
        }[frequency]
        carry_period_vol = pair["carry"].std(ddof=1)
        trend_period_vol = pair["trend"].std(ddof=1)
        row = {
            "window": window,
            "frequency": frequency,
            "carry Sharpe": (
                pair["carry"].mean() / carry_period_vol
                * math.sqrt(periods_per_year)
            ),
            "trend Sharpe": (
                pair["trend"].mean() / trend_period_vol
                * math.sqrt(periods_per_year)
            ),
            "carry skew": pair["carry"].skew(),
            "trend skew": pair["trend"].skew(),
            "correlation": pair.corr().loc["carry", "trend"],
            "carry worst-5% mean": expected_shortfall(pair["carry"]),
            "trend worst-5% mean": expected_shortfall(pair["trend"]),
        }
        if frequency == "monthly":
            carry_bad = pair["carry"] <= pair["carry"].quantile(0.20)
            trend_bad = pair["trend"] <= pair["trend"].quantile(0.20)
            row["joint bottom-quintile frequency"] = (carry_bad & trend_bad).mean()
            row["carry mean when trend is bad"] = pair.loc[trend_bad, "carry"].mean()
            row["trend mean when carry is bad"] = pair.loc[carry_bad, "trend"].mean()
        diagnostic_rows.append(row)

style_diagnostics = pd.DataFrame(diagnostic_rows).set_index(
    ["window", "frequency"]
)
display(style_diagnostics)
"""
        ),
        code(
            """
style_pair = pd.concat({
    "carry": allocation_frames[1.0]["net"],
    "trend": allocation_frames[0.0]["net"],
}, axis=1).fillna(0.0).loc[FIT_START:CUTOFF]

fig, axes = plt.subplots(1, 2, figsize=(15, 5))
R.cumulative_from_zero(style_pair).plot(
    ax=axes[0], title="Standalone carry and trend: cumulative fixed-capital P&L"
)
style_pair["carry"].rolling(256, min_periods=128).corr(
    style_pair["trend"]
).plot(ax=axes[1], title="Rolling one-year daily P&L correlation")
axes[1].axhline(0.0, color="black", linewidth=0.8)
plt.tight_layout()
plt.show()

monthly_pair = style_pair.resample("M").sum(min_count=1)
monthly_pair.plot.scatter(
    x="trend", y="carry", title="Monthly trend versus carry net P&L"
)
plt.axhline(0.0, color="black", linewidth=0.7)
plt.axvline(0.0, color="black", linewidth=0.7)
plt.show()
"""
        ),
        md(
            """
## Which instruments made money from carry and trend recently?

The table is descriptive and includes all 95 names.  “Insufficient” means
fewer than 100 common-eligibility sessions in the three-year window, not a
loss.  A recent positive Sharpe is never fed back into the exclusion mask.
"""
        ),
        code(
            """
def instrument_frames(system):
    answer = {}
    with capture_output():
        for instrument in ALL:
            curve = system.accounts.pandl_for_instrument(
                instrument, delayfill=True, roundpositions=True
            )
            answer[instrument] = accounting_frame(curve)
    return answer


print("extracting native instrument P&L for standalone carry and trend ...")
trend_instruments = instrument_frames(style_systems[0.0])
carry_instruments = instrument_frames(style_systems[1.0])


def style_label(carry_sharpe, trend_sharpe, sessions):
    if sessions < 100 or not np.isfinite(carry_sharpe + trend_sharpe):
        return "insufficient"
    if carry_sharpe > 0 and trend_sharpe > 0:
        return "both"
    if carry_sharpe > 0:
        return "carry only"
    if trend_sharpe > 0:
        return "trend only"
    return "neither"


instrument_rows = []
for instrument in ALL:
    sessions = int(common_eligibility.loc[AUDIT_START:CUTOFF, instrument].sum())
    carry_recent = carry_instruments[instrument].loc[AUDIT_START:CUTOFF, "net"]
    trend_recent = trend_instruments[instrument].loc[AUDIT_START:CUTOFF, "net"]
    carry_sr = sharpe(carry_recent) if sessions >= 100 else np.nan
    trend_sr = sharpe(trend_recent) if sessions >= 100 else np.nan
    instrument_rows.append({
        "instrument": instrument,
        "lineage": LINEAGE[instrument],
        "recent eligible sessions": sessions,
        "recent carry return %": carry_recent.sum(),
        "recent carry Sharpe": carry_sr,
        "recent trend return %": trend_recent.sum(),
        "recent trend Sharpe": trend_sr,
        "recent result": style_label(carry_sr, trend_sr, sessions),
    })

instrument_style = pd.DataFrame(instrument_rows).set_index("instrument")
print(instrument_style["recent result"].value_counts().to_string())
display(instrument_style.sort_values(["recent result", "recent carry Sharpe"],
                                     ascending=[True, False]))
"""
        ),
        code(
            """
heatmap = instrument_style[["recent carry Sharpe", "recent trend Sharpe"]].copy()
heatmap = heatmap.sort_values("recent carry Sharpe")
fig, ax = plt.subplots(figsize=(8, 20))
image = ax.imshow(heatmap.to_numpy(), aspect="auto", cmap="RdYlGn",
                  vmin=-2, vmax=2)
ax.set_yticks(range(len(heatmap)))
ax.set_yticklabels(heatmap.index, fontsize=7)
ax.set_xticks([0, 1])
ax.set_xticklabels(["carry", "trend"])
ax.set_title("Revealed 2023–2026 net Sharpe by instrument and style")
fig.colorbar(image, ax=ax, label="net Sharpe", shrink=0.4)
plt.tight_layout()
plt.show()
"""
        ),
        md(
            """
## “Proven negative” is deliberately hard to satisfy

For each lineage and for carry, trend, and the chosen mixed core, we sum native
instrument P&L inside completed July-to-July blocks.  A block counts only with
at least 150 eligible sessions.  The confidence test is on annual net return,
which has the same sign as expected Sharpe when volatility is positive.

Bonferroni uses all 88 × 3 possible lineage/style tests, not merely the rows
that happened to look bad.  This is conservative by design.  If it finds no
proven losers, the correct output is “exclude none.”
"""
        ),
        code(
            """
print(f"running the chosen {CHOSEN_CARRY_WEIGHT:.0%}-carry core for attribution ...")
core_system = native_system(
    CHOSEN_CARRY_WEIGHT, common_eligibility, common_weights
)
core_frame = portfolio_frame(core_system).loc[first_investable:CUTOFF]
core_instruments = instrument_frames(core_system)


def lineage_series(instrument_data):
    result = {}
    for lineage in sorted(set(LINEAGE.values())):
        members = [name for name in ALL if LINEAGE[name] == lineage]
        result[lineage] = pd.concat(
            [instrument_data[name]["net"] for name in members], axis=1
        ).sum(axis=1, min_count=1).fillna(0.0)
    return result


def lineage_allowed(lineage):
    members = [name for name in ALL if LINEAGE[name] == lineage]
    return common_eligibility[members].any(axis=1)


model_lineages = {
    "carry": lineage_series(carry_instruments),
    "trend": lineage_series(trend_instruments),
    "core": lineage_series(core_instruments),
}

NUMBER_OF_TESTS = len(set(LINEAGE.values())) * len(model_lineages)
ALPHA_PER_TEST = 0.05 / NUMBER_OF_TESTS
proof_rows = []
for model, lineages in model_lineages.items():
    for lineage, returns in lineages.items():
        allowed = lineage_allowed(lineage)
        annual_returns = []
        daily_pieces = []
        for year in range(2008, 2023):
            start = pd.Timestamp(year, 7, 28)
            end = pd.Timestamp(year + 1, 7, 27)
            block_allowed = allowed.loc[start:end]
            if int(block_allowed.sum()) < 150:
                continue
            block = returns.loc[start:end]
            annual_returns.append(float(block.sum()))
            daily_pieces.append(block)

        n_blocks = len(annual_returns)
        mean_return = np.mean(annual_returns) if n_blocks else np.nan
        loss_fraction = (
            np.mean(np.asarray(annual_returns) < 0) if n_blocks else np.nan
        )
        if n_blocks >= 2:
            standard_error = np.std(annual_returns, ddof=1) / math.sqrt(n_blocks)
            critical = stats.t.ppf(1 - ALPHA_PER_TEST, n_blocks - 1)
            upper_bound = mean_return + critical * standard_error
        else:
            upper_bound = np.nan
        pooled = pd.concat(daily_pieces) if daily_pieces else pd.Series(dtype=float)
        proven = bool(
            n_blocks >= 8 and loss_fraction >= 0.75
            and np.isfinite(upper_bound) and upper_bound < 0
        )
        proof_rows.append({
            "model": model,
            "lineage": lineage,
            "complete blocks": n_blocks,
            "pooled net Sharpe": sharpe(pooled),
            "mean annual contribution %": mean_return,
            "losing block fraction": loss_fraction,
            "Bonferroni upper 95% bound": upper_bound,
            "proven negative": proven,
        })

proof = pd.DataFrame(proof_rows).set_index(["model", "lineage"]).sort_index()
assert (proof.loc[proof["proven negative"], "complete blocks"] >= 8).all()
assert (proof.loc[proof["proven negative"], "losing block fraction"] >= 0.75).all()
assert (proof.loc[proof["proven negative"], "Bonferroni upper 95% bound"] < 0).all()

proven_table = proof[proof["proven negative"]]
print(f"Bonferroni alpha per test: {ALPHA_PER_TEST:.6g}")
print(f"proven-negative lineage/style rows: {len(proven_table)}")
display(proven_table if len(proven_table) else
        proof.sort_values("Bonferroni upper 95% bound").head(15))
"""
        ),
        md(
            """
## Freeze the simple mask, then launch the revealed audit flat

A proven-negative core removes the lineage.  Otherwise a proven-negative
carry or trend sleeve removes that entire style for the lineage and reallocates
the forecast budget to the surviving style.  If both styles look proven
negative but the combined core does not, we keep the core: contradictory
selection evidence is not permission to delete the market.

Lineages with fewer than eight blocks—including new listings—must retain the
global rule mix.  The audit systems hold zero positions before 2023-07-28,
delay the first trade, and pay its native entry cost.
"""
        ),
        code(
            """
EXCLUDED_LINEAGES = set(
    proof.xs("core").index[proof.xs("core")["proven negative"]]
)
REMOVE_CARRY = set(
    proof.xs("carry").index[proof.xs("carry")["proven negative"]]
) - EXCLUDED_LINEAGES
REMOVE_TREND = set(
    proof.xs("trend").index[proof.xs("trend")["proven negative"]]
) - EXCLUDED_LINEAGES

contradictory = REMOVE_CARRY & REMOVE_TREND
REMOVE_CARRY -= contradictory
REMOVE_TREND -= contradictory

core_blocks = proof.xs("core")["complete blocks"]
short_history = set(core_blocks.index[core_blocks < 8])
assert not short_history & (EXCLUDED_LINEAGES | REMOVE_CARRY | REMOVE_TREND)

weights_by_instrument = {}
for instrument in ALL:
    lineage = LINEAGE[instrument]
    if lineage in REMOVE_CARRY:
        weights_by_instrument[instrument] = weights_for(0.0)
    elif lineage in REMOVE_TREND:
        weights_by_instrument[instrument] = weights_for(1.0)
    else:
        weights_by_instrument[instrument] = weights_for(CHOSEN_CARRY_WEIGHT)

print("excluded lineages:", sorted(EXCLUDED_LINEAGES) or "none")
print("carry removed:", sorted(REMOVE_CARRY) or "none")
print("trend removed:", sorted(REMOVE_TREND) or "none")
print(f"{len(short_history)} short-history lineages keep the universal mix")

audit_allowed = common_eligibility.copy()
audit_allowed.loc[audit_allowed.index < AUDIT_START] = False
selected_allowed = audit_allowed.copy()
for instrument in ALL:
    if LINEAGE[instrument] in EXCLUDED_LINEAGES:
        selected_allowed[instrument] = False

baseline_weights = R.equal_weight_panel(audit_allowed)
selected_weights = R.equal_weight_panel(selected_allowed)

baseline_audit_system = native_system(
    CHOSEN_CARRY_WEIGHT, audit_allowed, baseline_weights
)
selected_audit_system = native_system(
    CHOSEN_CARRY_WEIGHT,
    selected_allowed,
    selected_weights,
    forecast_weights_by_instrument=weights_by_instrument,
)

print("running the two flat-launch audit systems ...")
with capture_output():
    baseline_audit = portfolio_frame(baseline_audit_system).loc[AUDIT_START:CUTOFF]
    selected_audit = portfolio_frame(selected_audit_system).loc[AUDIT_START:CUTOFF]
"""
        ),
        code(
            """
# Prove the launch convention on actual native positions and costs.
entry_checks = []
for instrument in ALL:
    target = baseline_audit_system.accounts.get_buffered_position(
        instrument, roundpositions=True
    ).fillna(0.0)
    curve = baseline_audit_system.accounts.pandl_for_instrument(
        instrument, delayfill=True, roundpositions=True
    )
    calculator = curve.pandl_calculator_with_costs
    actual = calculator.positions.fillna(0.0)
    assert actual.loc[actual.index < AUDIT_START].eq(0.0).all()

    targets = target.index[(target.index >= AUDIT_START) & target.ne(0.0)]
    fills = sorted(
        [fill for fill in calculator.fills
         if pd.Timestamp(fill.date) >= AUDIT_START and abs(fill.qty) > 0],
        key=lambda fill: fill.date,
    )
    if len(targets) and len(fills):
        costs = calculator.costs_from_trading_in_instrument_currency_as_series()
        fill_date = pd.Timestamp(fills[0].date)
        fill_cost = float(costs.loc[fill_date])
        entry_checks.append((instrument, pd.Timestamp(targets[0]),
                             fill_date, fill_cost))

assert entry_checks
first_entry = sorted(entry_checks, key=lambda row: row[2])[0]
assert first_entry[2] > first_entry[1]
assert first_entry[3] < 0
print("first target, delayed fill, native cost:", first_entry)
"""
        ),
        code(
            """
audit_summary = pd.DataFrame({
    "universal": performance(baseline_audit, AUDIT_START, CUTOFF),
    "proven-negative mask": performance(selected_audit, AUDIT_START, CUTOFF),
}).T
display(audit_summary[[
    "net return %", "cost drag %", "net Sharpe", "2x-cost Sharpe",
    "ann vol %", "additive max DD %", "full CAGR %", "full max DD %",
    "half-exposure CAGR %", "half-exposure max DD %",
]])

R.cumulative_from_zero(pd.DataFrame({
    "universal": baseline_audit["net"],
    "proven-negative mask": selected_audit["net"],
})).plot(title="Revealed audit: flat-launch cumulative net P&L")
plt.axhline(0.0, color="black", linewidth=0.7)
plt.show()
"""
        ),
        code(
            """
selected_contributions = []
with capture_output():
    for instrument in ALL:
        frame = accounting_frame(
            selected_audit_system.accounts.pandl_for_instrument(
                instrument, delayfill=True, roundpositions=True
            )
        ).loc[AUDIT_START:CUTOFF]
        selected_contributions.append({
            "instrument": instrument,
            "lineage": LINEAGE[instrument],
            "net contribution %": frame["net"].sum(),
            "gross contribution %": frame["gross"].sum(),
            "cost drag %": -frame["costs"].sum(),
            "net Sharpe": sharpe(frame["net"]),
        })

selected_contributions = pd.DataFrame(selected_contributions).set_index("instrument")
assert abs(
    selected_contributions["net contribution %"].sum()
    - selected_audit["net"].sum()
) < 1e-8
display(pd.concat([
    selected_contributions.nsmallest(15, "net contribution %"),
    selected_contributions.nlargest(15, "net contribution %"),
]))
"""
        ),
        md(
            """
## Decision

The next cell writes the conclusion from the calculated tables so the prose
cannot silently outlive refreshed data.  Remember that low carry/trend
correlation is a diversification benefit, not proof that carry deserves an
unbounded weight.  Carry is exposed to crowded yield harvesting, curve and
roll measurement error, policy interventions, limit moves, seasonality, and
joint liquidation shocks; those dangers show up more clearly in skew,
expected shortfall, joint-tail frequency, and drawdown than in Sharpe alone.
"""
        ),
        code(
            """
post2008_daily = style_diagnostics.loc[("post-2008", "daily")]
post2008_weekly = style_diagnostics.loc[("post-2008", "weekly")]
post2008_monthly = style_diagnostics.loc[("post-2008", "monthly")]
recent_carry = allocation_summary.loc[("revealed last 3 years", 1.0), "net Sharpe"]
recent_trend = allocation_summary.loc[("revealed last 3 years", 0.0), "net Sharpe"]

print(
    f"Trend skew was {post2008_daily['trend skew']:+.2f} daily, "
    f"{post2008_weekly['trend skew']:+.2f} weekly, and "
    f"{post2008_monthly['trend skew']:+.2f} monthly."
)
print(
    f"Carry/trend correlation was {post2008_daily['correlation']:+.2f} daily, "
    f"{post2008_weekly['correlation']:+.2f} weekly, and "
    f"{post2008_monthly['correlation']:+.2f} monthly. Both styles were in "
    f"their own bottom quintile in "
    f"{post2008_monthly['joint bottom-quintile frequency']:.1%} of months "
    "(4% is the independence benchmark)."
)
print(
    f"Recent standalone Sharpe: carry {recent_carry:.2f}, trend {recent_trend:.2f}. "
    f"The pre-2023 rule froze {CHOSEN_CARRY_WEIGHT:.0%} carry."
)
print(
    f"The proof rule excluded {len(EXCLUDED_LINEAGES)} lineages, removed carry "
    f"from {len(REMOVE_CARRY)}, and removed trend from {len(REMOVE_TREND)}."
)
print(
    f"Flat-launch recent Sharpe was {audit_summary.loc['universal', 'net Sharpe']:.2f} "
    f"universally and {audit_summary.loc['proven-negative mask', 'net Sharpe']:.2f} "
    "after the frozen proof rule."
)
print()
print(
    "Recommendation: keep the transparent carry+EWMAC core as the production "
    "baseline, use the pre-2023 global carry budget above, include new markets "
    "by default, and make proven-negative exclusions rare. Keep the 28-rule "
    "system as a shadow diversifier until independent forward evidence pays "
    "for its extra model and operational risk."
)
"""
        ),
    ]
    return cells


def _rule_families_cells() -> list:
    cells = [
        md(
            """
# 19 — From forecast weights to risk; continuous Donchian; useful families

This is a deliberately linear teaching experiment.  It answers three related
questions without adding another research framework:

1. What does a 70% carry / 30% trend **forecast budget** become in realised
   portfolio volatility?
2. Does repository-native, continuous Donchian (`breakout`) beat buffered
   EWMAC when the speed grid and every portfolio control are matched?
3. Which of the 28 optional rules improve a carry/EWMAC core when we change
   one family at a time?

All system construction is visible below.  It uses only stable repository
stages plus the small point-in-time portfolio gate already used by notebooks
9, 10, and 18.  There is no imported research `system.py`, inherited YAML, or
new helper module.

## Frozen protocol

- Data: the 95 reviewed Chinese futures in `dbFuturesSimData`, clipped through
  2026-07-27; CNH/Asia metadata is asserted.
- Core: four native carry horizons and three medium/slow EWMAC horizons.
- Common controls: 100m CNH fixed capital, 16% target, equal weight across
  causally eligible markets, IDM 2.5, fixed source scalars, 10% forecast
  buffer, delayed whole-contract fills, and stored cash/spread costs.
- Risk attribution is descriptive.  It reports native weighted-rule gross
  attribution and a separately costed net-sleeve approximation, then measures
  the residual against the actual buffered portfolio.
- Continuous-Donchian comparison: fixed EWMAC 16/64, 32/128, 64/256 versus
  fixed breakout 40, 80, 160.  Both use equal weights, FDM 1.5, the same
  readiness panel, and the same buffer.  No lookback is fitted.
- Optional-family screen: fit only from 2012-07-28 through 2023-07-27.  Every
  non-momentum candidate receives one fixed 10% sleeve; the remaining 90%
  preserves the 70/30 carry/EWMAC ratio.  Every canonical horizon in that
  family stays.  `momentum16/32/64` are identical to the three core EWMAC
  signals, so momentum is tested honestly as a five-speed **replacement**.
- A family passes only if pre-2023 net Sharpe improves by at least 0.05, the
  median annual-block Sharpe change is positive, at least two-thirds of valid
  blocks improve, total net return improves, and the 2x-cost Sharpe does not
  deteriorate.  These are screening rules, not statistical proof.
- 2023-07-28 through 2026-07-27 is already revealed.  It never votes in the
  screen.  The frozen screened combination is then relaunched flat for that
  period and reported unchanged.

The native broad `AssetClass` grouping is used for the first-pass
cross-sectional rules.  The data object contains only Chinese instruments, so
no international market enters a forecast.  Earlier v00/v01 diagnostics found
the broad-versus-China taxonomy change immaterial at total-system level; a
cross-sectional family that passes here still needs the stricter China-peer
robustness check before production.
"""
        ),
        code(SETUP_CELL),
        code(
            """
import gc
import math
from IPython.utils.io import capture_output

from sysdata.config.configdata import Config
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from sysobjects.adjusted_prices import futuresAdjustedPrices
from sysobjects.multiple_prices import futuresMultiplePrices
from sysobjects.spot_fx_prices import fxPrices
from systems.accounts.accounts_stage import Account
from systems.basesystem import System
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecasting import Rules
from systems.positionsizing import PositionSizing
from systems.provided.rob_system.rawdata import myFuturesRawData as RobRawData

R.limit_blas_threads()

POST_2008 = pd.Timestamp("2008-07-28")
FAMILY_FIT_START = pd.Timestamp("2012-07-28")
FIT_END = pd.Timestamp("2023-07-27")
AUDIT_START = pd.Timestamp("2023-07-28")
CUTOFF = pd.Timestamp("2026-07-27")
TRADING_DAYS = 256.0
CAPITAL = 100_000_000
TARGET_VOL = 16.0
"""
        ),
        md(
            """
## 1. The database boundary

For research, a cutoff must live in the data object rather than in the last
line of a plot.  The subclass below clips adjusted prices, multiple prices,
and FX inclusively.  Adjusted prices are additive Panama levels: native rules
use their differences; this notebook never calls percentage change on them.
"""
        ),
        code(
            """
class CutoffChinaData(dbFuturesSimData):
    def __init__(self, cutoff):
        self.cutoff = (
            pd.Timestamp(cutoff).normalize()
            + pd.Timedelta(days=1)
            - pd.Timedelta(nanoseconds=1)
        )
        manifest = R.TushareInstrumentManifest.from_csv()
        expected = sorted(
            item.instrument_code
            for item in manifest.mappings
            if item.is_stitchable
        )
        assert len(expected) == len(set(expected)) == 95
        self._instruments = tuple(expected)
        super().__init__()

        stored = set(super().get_instrument_list())
        missing = sorted(set(expected) - stored)
        if missing:
            raise ValueError(f"Database is missing reviewed instruments: {missing}")

        metadata = self.get_all_instrument_data_as_df().reindex(expected)
        bad = metadata.index[
            (metadata["Currency"] != "CNH") | (metadata["Region"] != "ASIA")
        ].tolist()
        if bad:
            raise ValueError(f"Non-Chinese instruments exposed: {bad}")

        listed = []
        for instrument in expected:
            raw = self.db_futures_adjusted_prices_data.get_adjusted_prices(
                instrument
            ).dropna()
            if len(raw) and raw.index[0] <= self.cutoff:
                listed.append(instrument)
        self._instruments = tuple(listed)

    def get_instrument_list(self):
        return list(self._instruments)

    def _check(self, instrument):
        if instrument not in self._instruments:
            raise ValueError(f"{instrument} is outside this cutoff universe")

    def get_backadjusted_futures_price(self, instrument_code):
        self._check(instrument_code)
        prices = super().get_backadjusted_futures_price(instrument_code)
        return futuresAdjustedPrices(pd.Series(prices.loc[: self.cutoff]).copy())

    def get_multiple_prices_from_start_date(self, instrument_code, start_date):
        self._check(instrument_code)
        prices = super().get_multiple_prices_from_start_date(
            instrument_code, start_date=start_date
        )
        return futuresMultiplePrices(pd.DataFrame(prices.loc[: self.cutoff]).copy())

    def _get_fx_data_from_start_date(self, currency1, currency2, start_date):
        prices = super()._get_fx_data_from_start_date(
            currency1, currency2, start_date=start_date
        )
        return fxPrices(pd.Series(prices.loc[: self.cutoff]).copy())


data = CutoffChinaData(CUTOFF)
ALL = R.chinese_universe(data)
assert len(ALL) == 95
assert max(data.daily_prices(code).index.max() for code in ALL) <= data.cutoff
print(f"{len(ALL)} Chinese instruments; cutoff {CUTOFF.date()}")
"""
        ),
        md(
            """
## 2. The rules are plain dictionaries

This is the simplest way to register a rule in pysystemtrade: give `Rules` a
function path, the native stage data it consumes, and its arguments.  Scalars
below are the fixed values in `systems/provided/rob_system/config.yaml`; they
normalise forecast amplitude, not expected return.

The 28 optional names are kept in seven economic families.  We do not choose
the winning horizon after seeing Chinese returns.
"""
        ),
        code(
            """
EWMAC_FUNCTION = "systems.provided.rules.ewmac.ewmac"
EWMAC_DATA = ["rawdata.get_daily_prices", "rawdata.daily_returns_volatility"]
CARRY_FUNCTION = "systems.provided.rules.carry.carry"
BREAKOUT_FUNCTION = "systems.provided.rules.breakout.breakout"

CARRY_RULES = ("carry10", "carry30", "carry60", "carry125")
TREND_RULES = ("ewmac16_64", "ewmac32_128", "ewmac64_256")
CORE_RULES = CARRY_RULES + TREND_RULES

TRADING_RULES = {
    "carry10": dict(function=CARRY_FUNCTION, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=10)),
    "carry30": dict(function=CARRY_FUNCTION, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=30)),
    "carry60": dict(function=CARRY_FUNCTION, data=["rawdata.raw_carry"],
                    other_args=dict(smooth_days=60)),
    "carry125": dict(function=CARRY_FUNCTION, data=["rawdata.raw_carry"],
                     other_args=dict(smooth_days=125)),
    "ewmac16_64": dict(function=EWMAC_FUNCTION, data=EWMAC_DATA,
                       other_args=dict(Lfast=16, Lslow=64)),
    "ewmac32_128": dict(function=EWMAC_FUNCTION, data=EWMAC_DATA,
                        other_args=dict(Lfast=32, Lslow=128)),
    "ewmac64_256": dict(function=EWMAC_FUNCTION, data=EWMAC_DATA,
                        other_args=dict(Lfast=64, Lslow=256)),
}

for lookback in (10, 20, 40, 80, 160, 320):
    TRADING_RULES[f"breakout{lookback}"] = dict(
        function=BREAKOUT_FUNCTION,
        data=["rawdata.get_daily_prices"],
        other_args=dict(lookback=lookback),
    )
for fast in (4, 8, 16, 32, 64):
    TRADING_RULES[f"momentum{fast}"] = dict(
        function=EWMAC_FUNCTION,
        data=EWMAC_DATA,
        other_args=dict(Lfast=fast, Lslow=4 * fast),
    )
for fast in (16, 32, 64):
    TRADING_RULES[f"accel{fast}"] = dict(
        function="systems.provided.rules.accel.accel",
        data=EWMAC_DATA,
        other_args=dict(Lfast=fast),
    )
for fast in (2, 4, 8, 16, 32, 64):
    TRADING_RULES[f"assettrend{fast}"] = dict(
        function="systems.provided.rules.ewmac.ewmac_calc_vol",
        data=["rawdata.normalised_price_for_asset_class"],
        other_args=dict(Lfast=fast, Lslow=4 * fast),
    )
for horizon in (10, 20, 40, 80):
    TRADING_RULES[f"relmomentum{horizon}"] = dict(
        function="systems.provided.rules.rel_mom.relative_momentum",
        data=[
            "rawdata.get_cumulative_daily_vol_normalised_returns",
            "rawdata.normalised_price_for_asset_class",
        ],
        other_args=dict(horizon=horizon),
    )
for lookback, smooth in ((180, 45), (365, 90)):
    common = dict(
        function="systems.provided.rules.factors.factor_trading_rule",
        data=["rawdata.get_demeanded_factor_value"],
    )
    TRADING_RULES[f"skewabs{lookback}"] = dict(
        **common,
        other_args=dict(
            smooth=smooth,
            _factor_name="neg_skew",
            _demean_method="historic_average_factor_value_all_assets",
            _lookback_days=lookback,
        ),
    )
    TRADING_RULES[f"skewrv{lookback}"] = dict(
        **common,
        other_args=dict(
            smooth=smooth,
            _factor_name="neg_skew",
            _demean_method="average_factor_value_in_asset_class_for_instrument",
            _lookback_days=lookback,
        ),
    )

RULE_FAMILIES = {
    "momentum": tuple(f"momentum{x}" for x in (4, 8, 16, 32, 64)),
    "accel": tuple(f"accel{x}" for x in (16, 32, 64)),
    "assettrend": tuple(f"assettrend{x}" for x in (2, 4, 8, 16, 32, 64)),
    "breakout": tuple(f"breakout{x}" for x in (10, 20, 40, 80, 160, 320)),
    "relmomentum": tuple(f"relmomentum{x}" for x in (10, 20, 40, 80)),
    "skewabs": ("skewabs180", "skewabs365"),
    "skewrv": ("skewrv180", "skewrv365"),
}
OPTIONAL_RULES = tuple(
    rule for family in RULE_FAMILIES.values() for rule in family
)
assert len(OPTIONAL_RULES) == 28

FORECAST_SCALARS = {
    "carry10": 27.815707053556984,
    "carry30": 28.384062881349813,
    "carry60": 28.40072429176199,
    "carry125": 29.366474500729886,
    "ewmac16_64": 3.75,
    "ewmac32_128": 2.65,
    "ewmac64_256": 1.87,
    "momentum4": 8.539940954709955,
    "momentum8": 5.949404365193165,
    "momentum16": 4.104172020369661,
    "momentum32": 2.786994330124792,
    "momentum64": 1.9093945630747895,
    "accel16": 7.8170710605387095,
    "accel32": 5.563487137713779,
    "accel64": 3.896720541225276,
    "assettrend2": 10.846520114531351,
    "assettrend4": 7.572334583056326,
    "assettrend8": 5.190470936448635,
    "assettrend16": 3.549452858682833,
    "assettrend32": 2.3449234496490723,
    "assettrend64": 1.5465144366886119,
    "breakout10": 0.6031025130185256,
    "breakout20": 0.6742627921625178,
    "breakout40": 0.7036929411910525,
    "breakout80": 0.726260784624834,
    "breakout160": 0.7388310187414805,
    "breakout320": 0.7366197028421859,
    "relmomentum10": 61.24026078373817,
    "relmomentum20": 86.50746400987076,
    "relmomentum40": 117.77937298659975,
    "relmomentum80": 159.87802982511536,
    "skewabs180": 4.590246757939031,
    "skewabs365": 2.351483885205172,
    "skewrv180": 5.244752769697409,
    "skewrv365": 3.002222097593425,
}
assert set(CORE_RULES + OPTIONAL_RULES) == set(FORECAST_SCALARS)

duplicate_map = {
    "momentum16": "ewmac16_64",
    "momentum32": "ewmac32_128",
    "momentum64": "ewmac64_256",
}
for momentum, core_name in duplicate_map.items():
    assert TRADING_RULES[momentum]["other_args"] == TRADING_RULES[core_name]["other_args"]

family_table = pd.DataFrame({
    family: pd.Series(rules) for family, rules in RULE_FAMILIES.items()
})
display(family_table)
print("The three duplicate momentum/core pairs are:", duplicate_map)
"""
        ),
        md(
            """
## 3. One readable native system factory

The only parameters that vary between experiments are the active rule names,
their forecast weights, FDM, and the already-computed daily eligibility and
instrument weights.  Every other control is locked here.

`RobRawData` is the repository stage required by the native skew and
cross-sectional rules.  `R.PointInTimePortfolios` only gates and renormalises
instrument weights; forecasts, volatility, sizing, buffering, fills, and P&L
remain native.
"""
        ),
        code(
            """
def core_weights(carry_budget=0.70):
    return {
        **{rule: carry_budget / len(CARRY_RULES) for rule in CARRY_RULES},
        **{
            rule: (1.0 - carry_budget) / len(TREND_RULES)
            for rule in TREND_RULES
        },
    }


def equal_weights(rule_names):
    return {rule: 1.0 / len(rule_names) for rule in rule_names}


def native_system(rule_names, forecast_weights, eligibility, fixed_weights,
                  fdm=1.0):
    rule_names = tuple(rule_names)
    assert set(rule_names) == set(forecast_weights)
    assert abs(sum(forecast_weights.values()) - 1.0) < 1e-12
    config = Config(dict(
        trading_rules={rule: TRADING_RULES[rule] for rule in rule_names},
        forecast_scalars={rule: FORECAST_SCALARS[rule] for rule in rule_names},
        forecast_weights=forecast_weights,
        use_forecast_scale_estimates=False,
        use_forecast_weight_estimates=False,
        forecast_div_multiplier=fdm,
        use_forecast_div_mult_estimates=False,
        forecast_weight_ewma_span=1,
        forecast_cap=20.0,
        average_absolute_forecast=10.0,
        instruments=ALL,
        instrument_weights={name: 1 / len(ALL) for name in ALL},
        instrument_div_multiplier=2.5,
        use_instrument_weight_estimates=False,
        use_instrument_div_mult_estimates=False,
        instrument_weight_ewma_span=1,
        notional_trading_capital=CAPITAL,
        percentage_vol_target=TARGET_VOL,
        base_currency="CNH",
        capital_multiplier=dict(func="syscore.capital.fixed_capital"),
        buffer_method="forecast",
        buffer_size=0.10,
        buffer_trade_to_edge=True,
        use_SR_costs=False,
        forecast_post_ceiling_cost_SR=999.0,
        vol_normalise_currency_costs=False,
        multiply_roll_costs_by=0.5,
        volatility_calculation=dict(
            func="sysquant.estimators.vol.mixed_vol_calc",
            name_returns_attr_in_rawdata="daily_returns",
            multiplier_to_get_daily_vol=1.0,
            days=35,
            min_periods=10,
            slow_vol_years=20,
            proportion_of_slow_vol=0.35,
            vol_abs_min=0.0000000001,
            backfill=False,
        ),
    ))
    return System(
        [Account(), R.PointInTimePortfolios(eligibility, fixed_weights),
         PositionSizing(), RobRawData(), ForecastCombine(),
         ForecastScaleCap(), Rules()],
        data,
        config,
    )


def accounting_frame(curve):
    frame = pd.concat({
        "gross": curve.percent.gross.as_ts,
        "costs": curve.percent.costs.as_ts,
        "net": curve.percent.as_ts,
    }, axis=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    assert (frame["net"] - frame["gross"] - frame["costs"]).abs().max() < 1e-8
    return frame


def portfolio_frame(system):
    curve = system.accounts.portfolio(delayfill=True, roundpositions=True)
    return accounting_frame(curve)


def sharpe(returns):
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna().astype(float)
    volatility = clean.std(ddof=1)
    if len(clean) < 2 or not np.isfinite(volatility) or volatility <= 0:
        return np.nan
    return clean.mean() / volatility * math.sqrt(TRADING_DAYS)


def compounded_stats(returns, scale=1.0):
    daily = returns.dropna().astype(float) * scale / 100.0
    wealth = (1.0 + daily).cumprod()
    anchor = pd.Series(
        [1.0], index=[daily.index[0] - pd.Timedelta(nanoseconds=1)]
    )
    wealth = pd.concat([anchor, wealth])
    drawdown = wealth / wealth.cummax() - 1.0
    years = (daily.index[-1] - daily.index[0]).days / 365.25
    ending = wealth.iloc[-1]
    cagr = ending ** (1.0 / years) - 1.0 if ending > 0 and years > 0 else np.nan
    return 100 * cagr, 100 * drawdown.min()


def performance(frame, start, end):
    sample = frame.loc[start:end]
    net = sample["net"]
    ann_vol = net.std(ddof=1) * math.sqrt(TRADING_DAYS)
    cagr, drawdown = compounded_stats(net)
    common_scale = TARGET_VOL / ann_vol if ann_vol > 0 else np.nan
    _, common_drawdown = compounded_stats(net, common_scale)
    return {
        "observations": len(sample),
        "net return %": net.sum(),
        "net Sharpe": sharpe(net),
        "2x-cost Sharpe": sharpe(sample["gross"] + 2 * sample["costs"]),
        "ann vol %": ann_vol,
        "cost drag %": -sample["costs"].sum(),
        "full CAGR %": cagr,
        "full max DD %": drawdown,
        "16%-vol max DD %": common_drawdown,
    }
"""
        ),
        md(
            """
## 4. One causal membership panel, many rule-specific readiness masks

Liquidity enters at a trailing 20-observed-session mean of 130 contracts and
exits below 70.  A rule is eligible only after all rules active in that exact
comparison and native volatility are ready.  This prevents pandas' all-NaN
sum from becoming a plausible zero forecast.

Native breakout currently chooses its rolling `min_periods` from the final
length of the supplied series.  We add the economically natural half-window
observation gate, making a short cutoff agree with the prefix of a later
cutoff without modifying the user's `breakout.py`.
"""
        ),
        code(
            """
print("reading held-contract volume ...")
with capture_output():
    held_volume = R.held_contract_volumes(data, ALL)
liquidity = R.liquidity_eligibility(held_volume, force_terminal_close=True)

print("building all 35 native forecasts once for readiness ...")
PROBE_RULES = CORE_RULES + OPTIONAL_RULES
probe = native_system(
    PROBE_RULES,
    equal_weights(PROBE_RULES),
    liquidity,
    R.equal_weight_panel(liquidity),
)

forecast_ready = {}
volatility_ready = {}
with capture_output():
    for instrument in ALL:
        forecasts = probe.combForecast.get_all_forecasts(
            instrument, list(PROBE_RULES)
        )
        forecasts = forecasts.reindex(liquidity.index).ffill()

        observed_prices = data.daily_prices(instrument).dropna()
        count = pd.Series(
            1, index=observed_prices.index, dtype=float
        ).cumsum().reindex(liquidity.index).ffill().fillna(0.0)
        for lookback in (10, 20, 40, 80, 160, 320):
            name = f"breakout{lookback}"
            forecasts[name] = forecasts[name].where(
                count >= math.ceil(lookback / 2)
            )

        forecast_ready[instrument] = forecasts.notna()
        vol = probe.positionSize.get_average_position_at_subsystem_level(
            instrument
        )
        volatility_ready[instrument] = (
            vol.reindex(liquidity.index).ffill().notna()
        )


def eligibility_for(rule_names, flat_before=None):
    allowed = liquidity.copy()
    for instrument in ALL:
        ready = forecast_ready[instrument][list(rule_names)].all(axis=1)
        allowed[instrument] &= ready & volatility_ready[instrument]
    if flat_before is not None:
        allowed.loc[allowed.index < pd.Timestamp(flat_before)] = False
    return allowed


def matched_weights(eligibility):
    weights = R.equal_weight_panel(eligibility)
    active = weights.sum(axis=1) > 0
    assert weights.loc[active].sum(axis=1).sub(1.0).abs().max() < 1e-12
    return weights


core_eligibility = eligibility_for(CORE_RULES)
core_instruments = int((core_eligibility.sum() > 0).sum())
print(
    f"core: {core_instruments} instruments ever eligible; "
    f"{int(core_eligibility.iloc[-1].sum())} at cutoff"
)

del probe
gc.collect()
"""
        ),
    ]

    cells.extend(_rule_family_result_cells())
    return cells


def _rule_family_result_cells() -> list:
    """The result cells are split out only to keep this generator navigable."""

    return [
        md(
            """
## Question 1 — Where did 70/30 go?

There are three different objects people casually call a “weight”:

1. `combForecast.get_forecast_weights()` is the configured signal budget;
2. native weighted-rule P&L is a linear, pre-buffer approximation to each
   sleeve's contribution; and
3. `accounts.portfolio()` is the actual buffered, rounded, fully costed result.

The first is not a volatility allocation.  If two equally volatile sleeves
are uncorrelated, 70/30 already implies a carry variance share of
`0.7² / (0.7² + 0.3²) = 84.5%`.

We build carry-only, trend-only, and the actual 70/30 system under exactly the
same daily market membership.  The standalone systems answer “what if this
sleeve received the whole target”; multiplying their P&L by 70% and 30% gives
a separately costed net approximation.  For cleaner structural attribution,
the native weighted-rule API supplies the relative rule/instrument weights.
Its current implementation normalises their joint sum back to one; with this
study's fixed FDM 1 and IDM 2.5 we restore that one aggregate 2.5 scale for
tracking against the actual portfolio.  We never multiply the 70/30 rule
weights a second time.
"""
        ),
        code(
            """
core_fixed_weights = matched_weights(core_eligibility)

print("running carry-only, trend-only, and 70/30 core ...")
carry_system = native_system(
    CARRY_RULES, equal_weights(CARRY_RULES),
    core_eligibility, core_fixed_weights,
)
trend_system = native_system(
    TREND_RULES, equal_weights(TREND_RULES),
    core_eligibility, core_fixed_weights,
)
core_system = native_system(
    CORE_RULES, core_weights(0.70),
    core_eligibility, core_fixed_weights,
)
with capture_output():
    carry_frame = portfolio_frame(carry_system)
    trend_frame = portfolio_frame(trend_system)
    core_frame = portfolio_frame(core_system)
print("three core systems done")
"""
        ),
        code(
            """
# Native linear rule attribution.  Forecast-level account curves always use
# SR costs, so gross P&L is the clean quantity here.
print("extracting native weighted-rule gross P&L ...")
with capture_output():
    weighted_rules = core_system.accounts.pandl_for_all_trading_rules(
        delayfill=True
    )
    rule_gross = pd.concat(
        {
            rule: weighted_rules[rule].percent.gross.as_ts
            for rule in CORE_RULES
        },
        axis=1,
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)

# accountForecast normalises the sum of joint rule/instrument weights to one.
# Here instrument weights and forecast weights each sum to one, FDM is one,
# and IDM is fixed at 2.5, so restore that aggregate scale exactly once.
RULE_ATTRIBUTION_SCALE = 2.5
rule_gross *= RULE_ATTRIBUTION_SCALE

linear_gross_sleeves = pd.DataFrame({
    "carry": rule_gross[list(CARRY_RULES)].sum(axis=1),
    "trend": rule_gross[list(TREND_RULES)].sum(axis=1),
})

# This approximation uses actual stored costs in two complete standalone
# systems.  Separate sleeves cannot net opposing orders, so use it to describe
# net risk, not to reconcile exact costs.
standalone_net_sleeves = pd.DataFrame({
    "carry": 0.70 * carry_frame["net"],
    "trend": 0.30 * trend_frame["net"],
}).fillna(0.0)


def euler_row(components, actual, start, end, label, window):
    sample = components.loc[start:end].fillna(0.0)
    actual_sample = actual.loc[start:end].fillna(0.0)
    aligned = pd.concat(
        [sample, actual_sample.rename("actual")], axis=1
    ).fillna(0.0)
    sample = aligned[["carry", "trend"]]
    actual_sample = aligned["actual"]

    covariance = sample.cov() * TRADING_DAYS
    ones = pd.Series(1.0, index=covariance.index)
    variance = float(ones @ covariance @ ones)
    component_variance = ones * (covariance @ ones)
    risk_share = component_variance / variance
    linear = sample.sum(axis=1)
    residual = actual_sample - linear
    return {
        "basis": label,
        "window": window,
        "carry standalone vol %": (
            sample["carry"].std(ddof=1) * math.sqrt(TRADING_DAYS)
        ),
        "trend standalone vol %": (
            sample["trend"].std(ddof=1) * math.sqrt(TRADING_DAYS)
        ),
        "carry/trend correlation": sample.corr().loc["carry", "trend"],
        "carry Euler risk share": risk_share["carry"],
        "trend Euler risk share": risk_share["trend"],
        "linear vol %": linear.std(ddof=1) * math.sqrt(TRADING_DAYS),
        "actual buffered vol %": (
            actual_sample.std(ddof=1) * math.sqrt(TRADING_DAYS)
        ),
        "tracking residual vol %": (
            residual.std(ddof=1) * math.sqrt(TRADING_DAYS)
        ),
    }


RISK_WINDOWS = {
    "post-2008": (POST_2008, CUTOFF),
    "fit through 2023": (POST_2008, FIT_END),
    "revealed last 3 years": (AUDIT_START, CUTOFF),
}
risk_rows = []
for window, (start, end) in RISK_WINDOWS.items():
    risk_rows.append(euler_row(
        linear_gross_sleeves, core_frame["gross"], start, end,
        "native weighted-rule gross", window,
    ))
    risk_rows.append(euler_row(
        standalone_net_sleeves, core_frame["net"], start, end,
        "scaled standalone net", window,
    ))

risk_attribution = pd.DataFrame(risk_rows).set_index(["window", "basis"])
assert risk_attribution[[
    "carry Euler risk share", "trend Euler risk share"
]].sum(axis=1).sub(1.0).abs().max() < 1e-10
display(risk_attribution)
"""
        ),
        code(
            """
fig, axes = plt.subplots(1, 2, figsize=(14, 5))
risk_attribution[[
    "carry Euler risk share", "trend Euler risk share"
]].mul(100).unstack("basis").plot.bar(
    ax=axes[0], title="Forecast 70/30 becomes covariance risk shares"
)
risk_attribution[[
    "linear vol %", "actual buffered vol %", "tracking residual vol %"
]].unstack("basis").plot.bar(
    ax=axes[1], title="Linear attribution versus actual buffered portfolio"
)
axes[0].set_ylabel("percent of portfolio variance")
axes[1].set_ylabel("annualised volatility (%)")
plt.tight_layout()
plt.show()
"""
        ),
        md(
            """
### How to inspect the chain manually with native APIs

For one instrument, start at the forecast weights and walk down to the
buffered whole-contract target.  This is usually more educational than
reading a final Sharpe table.
"""
        ),
        code(
            """
instrument = "SHFE_RB"
forecast_weights = core_system.combForecast.get_forecast_weights(instrument)
weighted_forecasts = (
    core_system.combForecast
    .get_weighted_forecasts_without_multiplier(instrument)
)
forecast_path = pd.DataFrame({
    "carry weighted forecast": weighted_forecasts[list(CARRY_RULES)].sum(axis=1),
    "trend weighted forecast": weighted_forecasts[list(TREND_RULES)].sum(axis=1),
    "combined after FDM/cap": (
        core_system.combForecast.get_combined_forecast(instrument)
    ),
    "subsystem position": (
        core_system.positionSize.get_subsystem_position(instrument)
    ),
    "portfolio target": (
        core_system.portfolio.get_notional_position(instrument)
    ),
    "buffered whole-contract target": (
        core_system.accounts.get_buffered_position(
            instrument, roundpositions=True
        )
    ),
})

print("Actual forecast weights for SHFE_RB:")
display(forecast_weights.tail(3))
display(forecast_path.loc["2025":].tail(10))
forecast_path[[
    "carry weighted forecast", "trend weighted forecast",
    "combined after FDM/cap",
]].loc["2023":].plot(
    figsize=(14, 5), title="SHFE_RB: forecast contributions"
)
plt.show()
"""
        ),
        md(
            """
Interpretation:

- forecast weights say how capped/scaled signals are averaged;
- the volatility scalar turns forecast 10 into a risk-targeted subsystem
  position;
- instrument weight and IDM create the portfolio target;
- cap, buffer, opposing signals, and integer contracts make final P&L
  nonlinear.

Use Euler covariance shares to answer “which sleeve supplies portfolio
variance?”  Use the actual complete system for Sharpe, drawdown, and costs.
Never add net forecast-level account curves and call the sum an executable
portfolio.
"""
        ),
        md(
            """
## Question 2 — Buffered EWMAC versus continuous Donchian

The native non-binary Donchian rule is called `breakout`.  It places price
continuously inside the rolling high/low channel and smooths the result over
roughly one quarter of the lookback.  It is not the superseded binary study's persistent
state machine.

The superseded binary study was stronger over the full common span and
2023–2026, but not over its final exact year.  More importantly, it annually
selected one of six lookbacks separately for every instrument and stayed at
forecast ±20, while fixed EWMAC mixed three speeds around mean absolute
forecast 10.  That experiment cannot isolate “binary versus continuous”.

Here the speed mapping is fixed in advance:

| EWMAC | Continuous breakout |
|---|---|
| 16/64 | 40 |
| 32/128 | 80 |
| 64/256 | 160 |

Both systems use source scalars, equal rule weights, FDM 1.5, a 10% forecast
buffer, and exactly the same daily market weights.
"""
        ),
        code(
            """
BREAKOUT_MATCH = ("breakout40", "breakout80", "breakout160")
DONCHIAN_PAIR_RULES = TREND_RULES + BREAKOUT_MATCH
donchian_eligibility = eligibility_for(DONCHIAN_PAIR_RULES)
donchian_weights = matched_weights(donchian_eligibility)

ewmac_compare_system = native_system(
    TREND_RULES, equal_weights(TREND_RULES),
    donchian_eligibility, donchian_weights, fdm=1.5,
)
breakout_compare_system = native_system(
    BREAKOUT_MATCH, equal_weights(BREAKOUT_MATCH),
    donchian_eligibility, donchian_weights, fdm=1.5,
)

print("running matched buffered trend systems ...")
with capture_output():
    ewmac_compare = portfolio_frame(ewmac_compare_system)
    breakout_compare = portfolio_frame(breakout_compare_system)

# A separate flat launch answers the revealed-period implementation question.
flat_donchian_eligibility = eligibility_for(
    DONCHIAN_PAIR_RULES, flat_before=AUDIT_START
)
flat_donchian_weights = matched_weights(flat_donchian_eligibility)
ewmac_flat_system = native_system(
    TREND_RULES, equal_weights(TREND_RULES),
    flat_donchian_eligibility, flat_donchian_weights, fdm=1.5,
)
breakout_flat_system = native_system(
    BREAKOUT_MATCH, equal_weights(BREAKOUT_MATCH),
    flat_donchian_eligibility, flat_donchian_weights, fdm=1.5,
)
with capture_output():
    ewmac_flat = portfolio_frame(ewmac_flat_system).loc[AUDIT_START:CUTOFF]
    breakout_flat = portfolio_frame(breakout_flat_system).loc[AUDIT_START:CUTOFF]
print("matched trend comparison done")
"""
        ),
        code(
            """
trend_rows = []
for period, start, end, frames in [
    ("post-2008 ongoing", POST_2008, CUTOFF,
     {"EWMAC": ewmac_compare, "continuous Donchian": breakout_compare}),
    ("fit through 2023", POST_2008, FIT_END,
     {"EWMAC": ewmac_compare, "continuous Donchian": breakout_compare}),
    ("revealed ongoing", AUDIT_START, CUTOFF,
     {"EWMAC": ewmac_compare, "continuous Donchian": breakout_compare}),
    ("revealed flat launch", AUDIT_START, CUTOFF,
     {"EWMAC": ewmac_flat, "continuous Donchian": breakout_flat}),
]:
    for strategy, frame in frames.items():
        trend_rows.append({
            "period": period,
            "strategy": strategy,
            **performance(frame, start, end),
        })

trend_comparison = pd.DataFrame(trend_rows).set_index(["period", "strategy"])
display(trend_comparison)

with capture_output():
    turnover = pd.Series({
        "EWMAC": ewmac_compare_system.accounts.total_portfolio_level_turnover(
            roundpositions=True
        ),
        "continuous Donchian": (
            breakout_compare_system.accounts.total_portfolio_level_turnover(
                roundpositions=True
            )
        ),
    }, name="full-history native turnover")
display(turnover.to_frame())
"""
        ),
        code(
            """
def forecast_diagnostics(system, rule_names):
    rows = []
    with capture_output():
        for instrument in ALL:
            for rule in rule_names:
                capped = system.forecastScaleCap.get_capped_forecast(
                    instrument, rule
                ).dropna()
                if capped.empty:
                    continue
                rows.append({
                    "instrument": instrument,
                    "rule": rule,
                    "mean abs forecast": capped.abs().mean(),
                    "cap rate": capped.abs().ge(20.0 - 1e-10).mean(),
                    "observations": len(capped),
                })
    return pd.DataFrame(rows)


ewmac_forecasts = forecast_diagnostics(ewmac_compare_system, TREND_RULES)
breakout_forecasts = forecast_diagnostics(
    breakout_compare_system, BREAKOUT_MATCH
)
forecast_scale_check = pd.concat({
    "EWMAC": ewmac_forecasts.groupby("rule").apply(
        lambda x: pd.Series({
            "mean abs forecast": np.average(
                x["mean abs forecast"], weights=x["observations"]
            ),
            "mean cap rate": np.average(
                x["cap rate"], weights=x["observations"]
            ),
        }),
    ),
    "continuous Donchian": breakout_forecasts.groupby("rule").apply(
        lambda x: pd.Series({
            "mean abs forecast": np.average(
                x["mean abs forecast"], weights=x["observations"]
            ),
            "mean cap rate": np.average(
                x["cap rate"], weights=x["observations"]
            ),
        }),
    ),
})
display(forecast_scale_check)

focus = "SHFE_RB"
focus_rule = "breakout80"
native_forecast_chain = pd.concat({
    "raw": breakout_compare_system.rules.get_raw_forecast(focus, focus_rule),
    "scaled": breakout_compare_system.forecastScaleCap.get_scaled_forecast(
        focus, focus_rule
    ),
    "capped": breakout_compare_system.forecastScaleCap.get_capped_forecast(
        focus, focus_rule
    ),
    "combined": breakout_compare_system.combForecast.get_combined_forecast(
        focus
    ),
}, axis=1)
display(native_forecast_chain.loc["2025":].tail(10))
native_forecast_chain.loc["2024":].plot(
    figsize=(14, 5), title="SHFE_RB: native continuous-breakout forecast chain"
)
plt.show()
"""
        ),
        code(
            """
fig, axes = plt.subplots(1, 2, figsize=(15, 5))
R.cumulative_from_zero(pd.DataFrame({
    "EWMAC": ewmac_compare.loc[POST_2008:FIT_END, "net"],
    "continuous Donchian": breakout_compare.loc[POST_2008:FIT_END, "net"],
})).plot(ax=axes[0], title="Fit-period cumulative net P&L")
R.cumulative_from_zero(pd.DataFrame({
    "EWMAC": ewmac_flat["net"],
    "continuous Donchian": breakout_flat["net"],
})).plot(ax=axes[1], title="Revealed audit, both launched flat")
plt.tight_layout()
plt.show()

saved_binary_context = pd.DataFrame({
    "binary Donchian": [1.208, 0.432, 1.321],
    "EWMAC": [0.980, 0.336, 1.427],
}, index=["binary study full", "binary study latest 3y", "binary study final year"])
print("Context only — the superseded study used a different fitted binary experiment:")
display(saved_binary_context)
"""
        ),
        md(
            """
## Question 3 — Which of the 28 rules pays rent beside carry/EWMAC?

The old 28-rule ladder cannot answer this: every candidate always kept all 28
rules, changed their weights, and contained no carry.  Here each row below is
a complete, fully buffered, whole-contract, stored-cost rerun.

For six families the experiment is:

```
baseline  = 70% carry + 30% EWMAC
candidate = 63% carry + 27% EWMAC + 10% one optional family
```

The baseline is rerun on the candidate's exact readiness mask, so a slow rule
cannot win merely by delaying new-market entry.  `momentum` is different:
three of its five rules are byte-for-byte the same signal parameters as the
core EWMAC rules.  Its honest experiment is therefore:

```
baseline  = 70% carry + 30% three-speed EWMAC
candidate = 70% carry + 30% five-speed momentum grid
```

This is add-one screening, not exact rule attribution.  Buffering, caps,
integer contracts, and opposing forecasts are nonlinear; subtracting two
complete portfolio P&Ls answers “what changes if I adopt this family?”
"""
        ),
        code(
            """
def family_candidate_definition(family):
    family_rules = RULE_FAMILIES[family]
    if family == "momentum":
        active = CARRY_RULES + family_rules
        weights = {
            **{rule: 0.70 / len(CARRY_RULES) for rule in CARRY_RULES},
            **{rule: 0.30 / len(family_rules) for rule in family_rules},
        }
        change = "replace EWMAC with five-speed momentum grid"
    else:
        active = CORE_RULES + family_rules
        weights = {
            **{rule: 0.63 / len(CARRY_RULES) for rule in CARRY_RULES},
            **{rule: 0.27 / len(TREND_RULES) for rule in TREND_RULES},
            **{rule: 0.10 / len(family_rules) for rule in family_rules},
        }
        change = "add fixed 10% sleeve"
    assert abs(sum(weights.values()) - 1.0) < 1e-12
    return active, weights, change


def annual_block_sharpe_deltas(base, candidate, eligibility):
    rows = []
    for year in range(2012, 2023):
        start = pd.Timestamp(year, 7, 28)
        end = pd.Timestamp(year + 1, 7, 27)
        active_sessions = int(eligibility.any(axis=1).loc[start:end].sum())
        if active_sessions < 150:
            continue
        base_sr = sharpe(base.loc[start:end, "net"])
        candidate_sr = sharpe(candidate.loc[start:end, "net"])
        if np.isfinite(base_sr) and np.isfinite(candidate_sr):
            rows.append({
                "block": f"{year}-{year + 1}",
                "baseline Sharpe": base_sr,
                "candidate Sharpe": candidate_sr,
                "Sharpe delta": candidate_sr - base_sr,
            })
    return pd.DataFrame(rows).set_index("block")


family_frames = {}
family_blocks = {}
family_rows = []
for family in RULE_FAMILIES:
    active_rules, candidate_weights, change = family_candidate_definition(family)
    comparison_rules = tuple(dict.fromkeys(CORE_RULES + active_rules))
    eligibility = eligibility_for(comparison_rules)
    weights = matched_weights(eligibility)

    print(f"running matched baseline and {family} candidate ...")
    baseline_system = native_system(
        CORE_RULES, core_weights(0.70), eligibility, weights
    )
    candidate_system = native_system(
        active_rules, candidate_weights, eligibility, weights
    )
    with capture_output():
        baseline = portfolio_frame(baseline_system)
        candidate = portfolio_frame(candidate_system)

    blocks = annual_block_sharpe_deltas(
        baseline, candidate, eligibility
    )
    base_fit = performance(baseline, FAMILY_FIT_START, FIT_END)
    candidate_fit = performance(candidate, FAMILY_FIT_START, FIT_END)
    base_audit = performance(baseline, AUDIT_START, CUTOFF)
    candidate_audit = performance(candidate, AUDIT_START, CUTOFF)

    valid_blocks = len(blocks)
    median_delta = blocks["Sharpe delta"].median()
    positive_fraction = blocks["Sharpe delta"].gt(0).mean()
    pooled_delta = candidate_fit["net Sharpe"] - base_fit["net Sharpe"]
    double_cost_delta = (
        candidate_fit["2x-cost Sharpe"] - base_fit["2x-cost Sharpe"]
    )
    return_delta = (
        candidate_fit["net return %"] - base_fit["net return %"]
    )
    passed = bool(
        valid_blocks >= 8
        and pooled_delta >= 0.05
        and median_delta > 0.0
        and positive_fraction >= 2.0 / 3.0
        and return_delta > 0.0
        and double_cost_delta >= 0.0
    )

    family_rows.append({
        "family": family,
        "change": change,
        "rules": len(RULE_FAMILIES[family]),
        "valid blocks": valid_blocks,
        "fit baseline Sharpe": base_fit["net Sharpe"],
        "fit candidate Sharpe": candidate_fit["net Sharpe"],
        "fit Sharpe delta": pooled_delta,
        "median block Sharpe delta": median_delta,
        "positive block fraction": positive_fraction,
        "fit 2x-cost Sharpe delta": double_cost_delta,
        "fit net return delta %": return_delta,
        "fit extra cost drag %": (
            candidate_fit["cost drag %"] - base_fit["cost drag %"]
        ),
        "revealed ongoing Sharpe delta": (
            candidate_audit["net Sharpe"] - base_audit["net Sharpe"]
        ),
        "passed frozen screen": passed,
    })
    family_frames[family] = {"baseline": baseline, "candidate": candidate}
    family_blocks[family] = blocks

    del baseline_system, candidate_system
    gc.collect()

family_screen = pd.DataFrame(family_rows).set_index("family")
family_screen = family_screen.sort_values("fit Sharpe delta", ascending=False)
display(family_screen)
"""
        ),
        code(
            """
fig, axes = plt.subplots(1, 3, figsize=(17, 5))
family_screen[[
    "fit Sharpe delta", "revealed ongoing Sharpe delta"
]].plot.bar(ax=axes[0], title="Add-one Sharpe change")
axes[0].axhline(0.0, color="black", linewidth=0.8)
family_screen["positive block fraction"].plot.bar(
    ax=axes[1], title="Fraction of pre-2023 blocks improved"
)
axes[1].axhline(2 / 3, color="black", linestyle="--", linewidth=0.8)
family_screen[[
    "fit extra cost drag %", "fit net return delta %"
]].plot.bar(ax=axes[2], title="Cost and net-return change")
axes[2].axhline(0.0, color="black", linewidth=0.8)
plt.tight_layout()
plt.show()

incremental_curves = pd.DataFrame({
    family: frames["candidate"]["net"] - frames["baseline"]["net"]
    for family, frames in family_frames.items()
})
fig, axes = plt.subplots(1, 2, figsize=(16, 5))
R.cumulative_from_zero(
    incremental_curves.loc[FAMILY_FIT_START:FIT_END]
).plot(ax=axes[0], title="Pre-2023 incremental net P&L")
R.cumulative_from_zero(
    incremental_curves.loc[AUDIT_START:CUTOFF]
).plot(ax=axes[1], title="Revealed incremental net P&L — no selection vote")
plt.tight_layout()
plt.show()

block_delta_table = pd.concat(
    {family: blocks["Sharpe delta"] for family, blocks in family_blocks.items()},
    axis=1,
)
block_delta_table.plot.bar(
    figsize=(16, 6), title="Annual-block Sharpe change by family"
)
plt.axhline(0.0, color="black", linewidth=0.8)
plt.tight_layout()
plt.show()
"""
        ),
        md(
            """
### Freeze the screen, then form one small candidate

Passing optional families share one 10% sleeve; passing momentum replaces the
three-speed EWMAC grid with all five speeds.  A family receives no more total
budget merely because it has more horizons.  If nothing passes, the candidate
is exactly the original core.

The final comparison uses the union of baseline and candidate readiness, then
relaunches both systems flat on 2023-07-28.  The first non-zero target must be
filled later and pay a stored native cost.
"""
        ),
        code(
            """
PASSED_FAMILIES = family_screen.index[
    family_screen["passed frozen screen"]
].tolist()
USE_MOMENTUM_GRID = "momentum" in PASSED_FAMILIES
PASSED_OPTIONAL = [name for name in PASSED_FAMILIES if name != "momentum"]
SELECTED_TREND_RULES = (
    RULE_FAMILIES["momentum"] if USE_MOMENTUM_GRID else TREND_RULES
)


def selected_weights():
    optional_budget = 0.10 if PASSED_OPTIONAL else 0.0
    core_budget = 1.0 - optional_budget
    weights = {
        **{
            rule: core_budget * 0.70 / len(CARRY_RULES)
            for rule in CARRY_RULES
        },
        **{
            rule: core_budget * 0.30 / len(SELECTED_TREND_RULES)
            for rule in SELECTED_TREND_RULES
        },
    }
    if PASSED_OPTIONAL:
        family_budget = optional_budget / len(PASSED_OPTIONAL)
        for family in PASSED_OPTIONAL:
            for rule in RULE_FAMILIES[family]:
                weights[rule] = family_budget / len(RULE_FAMILIES[family])
    assert abs(sum(weights.values()) - 1.0) < 1e-12
    return weights


SELECTED_WEIGHTS = selected_weights()
SELECTED_RULES = tuple(SELECTED_WEIGHTS)
FINAL_COMPARISON_RULES = tuple(dict.fromkeys(CORE_RULES + SELECTED_RULES))

print("FROZEN PRE-2023 FAMILY SCREEN:", PASSED_FAMILIES or "none")
print("selected trend grid:", "momentum five-speed" if USE_MOMENTUM_GRID else "core EWMAC")
print("selected optional sleeve:", PASSED_OPTIONAL or "none")
display(pd.Series(SELECTED_WEIGHTS, name="forecast weight").to_frame())

final_eligibility = eligibility_for(FINAL_COMPARISON_RULES)
final_fixed_weights = matched_weights(final_eligibility)
baseline_final_system = native_system(
    CORE_RULES, core_weights(0.70), final_eligibility, final_fixed_weights
)
selected_final_system = native_system(
    SELECTED_RULES, SELECTED_WEIGHTS, final_eligibility, final_fixed_weights
)
with capture_output():
    baseline_final = portfolio_frame(baseline_final_system)
    selected_final = portfolio_frame(selected_final_system)

flat_final_eligibility = eligibility_for(
    FINAL_COMPARISON_RULES, flat_before=AUDIT_START
)
flat_final_weights = matched_weights(flat_final_eligibility)
baseline_final_flat_system = native_system(
    CORE_RULES, core_weights(0.70),
    flat_final_eligibility, flat_final_weights,
)
selected_final_flat_system = native_system(
    SELECTED_RULES, SELECTED_WEIGHTS,
    flat_final_eligibility, flat_final_weights,
)
with capture_output():
    baseline_final_flat = portfolio_frame(
        baseline_final_flat_system
    ).loc[AUDIT_START:CUTOFF]
    selected_final_flat = portfolio_frame(
        selected_final_flat_system
    ).loc[AUDIT_START:CUTOFF]
"""
        ),
        code(
            """
final_rows = []
for period, start, end, frames in [
    ("pre-2023 fit", FAMILY_FIT_START, FIT_END,
     {"matched core": baseline_final, "screened candidate": selected_final}),
    ("revealed flat launch", AUDIT_START, CUTOFF,
     {"matched core": baseline_final_flat,
      "screened candidate": selected_final_flat}),
]:
    for strategy, frame in frames.items():
        final_rows.append({
            "period": period,
            "strategy": strategy,
            **performance(frame, start, end),
        })
final_comparison = pd.DataFrame(final_rows).set_index(["period", "strategy"])
display(final_comparison)


def first_costed_entry(system):
    entries = []
    with capture_output():
        for instrument in ALL:
            curve = system.accounts.pandl_for_instrument(
                instrument, delayfill=True, roundpositions=True
            )
            calculator = curve.pandl_calculator_with_costs
            held = calculator.positions.fillna(0.0)
            assert held.loc[held.index < AUDIT_START].eq(0.0).all()
            costs = calculator.costs_from_trading_in_instrument_currency_as_series()
            for fill in calculator.fills:
                fill_date = pd.Timestamp(fill.date)
                if fill_date >= AUDIT_START and abs(fill.qty) > 0:
                    cost = float(costs.reindex([fill_date]).fillna(0.0).iloc[0])
                    entries.append((fill_date, instrument, cost))
                    break
    costed = [entry for entry in entries if entry[2] < 0.0]
    assert costed
    return min(costed)


first_entry = first_costed_entry(selected_final_flat_system)
print("first delayed entry with a stored native cost:", first_entry)

fig, axes = plt.subplots(1, 2, figsize=(15, 5))
R.cumulative_from_zero(pd.DataFrame({
    "matched core": baseline_final.loc[FAMILY_FIT_START:FIT_END, "net"],
    "screened candidate": selected_final.loc[FAMILY_FIT_START:FIT_END, "net"],
})).plot(ax=axes[0], title="Frozen-screen fit comparison")
R.cumulative_from_zero(pd.DataFrame({
    "matched core": baseline_final_flat["net"],
    "screened candidate": selected_final_flat["net"],
})).plot(ax=axes[1], title="Revealed audit, both launched flat")
plt.tight_layout()
plt.show()
"""
        ),
        md(
            """
## What to copy into your own research

For a beginner, the useful native API surface is small:

```python
# Forecasts
system.rules.get_raw_forecast(code, rule)
system.forecastScaleCap.get_capped_forecast(code, rule)
system.combForecast.get_forecast_weights(code)
system.combForecast.get_combined_forecast(code)

# Positions
system.positionSize.get_subsystem_position(code)
system.portfolio.get_notional_position(code)
system.accounts.get_buffered_position(code, roundpositions=True)

# Fully executable P&L
curve = system.accounts.portfolio(delayfill=True, roundpositions=True)
gross = curve.percent.gross.as_ts
costs = curve.percent.costs.as_ts
net = curve.percent.as_ts
```

Use `pandl_for_all_trading_rules()` only for structural, usually gross,
attribution.  It ignores the final combined cap, portfolio buffer, inertia,
and rounding, and forecast-level costs are SR-cost approximations.  Whenever
you ask “should I change the live system?”, construct both complete systems
and compare `accounts.portfolio()` under the same dated eligibility.

Do not select the best rule/instrument pair from a heatmap.  First freeze one
family-level change, keep all its canonical horizons, compare annual blocks,
and reserve a later period that cannot vote.  New instruments should inherit
the pooled system until there is genuinely long evidence against them.
"""
        ),
        code(
            """
net_risk = risk_attribution.loc[
    ("post-2008", "scaled standalone net")
]
fit_trend = trend_comparison.loc["fit through 2023"]
audit_trend = trend_comparison.loc["revealed flat launch"]
fit_winner = fit_trend["net Sharpe"].idxmax()
audit_winner = audit_trend["net Sharpe"].idxmax()

print("DATA-DRIVEN ANSWERS")
print(
    f"1. The 70/30 forecast budget became approximately "
    f"{100 * net_risk['carry Euler risk share']:.1f}% carry / "
    f"{100 * net_risk['trend Euler risk share']:.1f}% trend in post-2008 "
    "net covariance risk."
)
print(
    f"2. In the fair continuous comparison, {fit_winner} had the higher "
    f"pre-2023 net Sharpe ({fit_trend.loc[fit_winner, 'net Sharpe']:.3f}); "
    f"{audit_winner} had the higher flat-launch revealed Sharpe "
    f"({audit_trend.loc[audit_winner, 'net Sharpe']:.3f})."
)
print(
    "3. Families passing the frozen pre-2023 screen: "
    f"{PASSED_FAMILIES or 'none'}."
)
print(
    f"The matched core/candidate flat-launch revealed Sharpes were "
    f"{final_comparison.loc[('revealed flat launch', 'matched core'), 'net Sharpe']:.3f} "
    f"and {final_comparison.loc[('revealed flat launch', 'screened candidate'), 'net Sharpe']:.3f}."
)
print()
print(
    "Research recommendation: treat the transparent carry+EWMAC system as the "
    "benchmark. Add only a family that passes the predeclared family screen, "
    "then shadow it; do not deploy all 28 merely because their standalone "
    "portfolio diversified trend. A passing cross-sectional family still "
    "needs the stricter China-peer robustness check."
)
"""
        ),
    ]


def _carry_donchian_lab_cells() -> list:
    return [
        md(
            r"""
# Carry + Donchian: a small editable native lab

This companion keeps one adjacent system.py and one fully resolved config.yaml.
It deliberately answers only two reusable questions:

1. How does one fixed continuous Donchian rule compare with a fixed persistent
   binary Donchian rule under identical market membership and account controls?
2. How does the carry forecast budget change the fully costed native portfolio?

Edit the constants below, restart the kernel, and run all. The default system
targets 16% annual volatility, uses a 10% forecast buffer, and never backfills
volatility. Each grid point is a fresh native System, so cached stage results
cannot leak between variants. The 2023--2026 period is already revealed and
must not be treated as a fresh holdout.
"""
        ),
        code(
            r"""
%matplotlib inline
import gc
import importlib
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from IPython.utils.io import capture_output

cwd = Path.cwd().resolve()
REPO_ROOT = next(
    candidate for candidate in (cwd, *cwd.parents)
    if (candidate / "examples/chinese_futures/research.py").is_file()
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.chinese_futures import research as R
from sysdata.config.configdata import Config
S = importlib.import_module(
    "examples.chinese_futures.carry_donchian_lab.system"
)
R.set_notebook_style()
R.limit_blas_threads()
"""
        ),
        code(
            r"""
config = Config(str(S.CONFIG_PATH))

TREND_KIND = "continuous"
CARRY_WEIGHT = 0.70
CARRY_WEIGHT_GRID = [0.00, 0.20, 0.40, 0.50, 0.60, 0.70, 0.80, 1.00]
FLAT_LAUNCH_POST_2023 = True
EVALUATION_START = pd.Timestamp("2008-07-28")
FIT_END = pd.Timestamp("2023-07-27")
AUDIT_START = pd.Timestamp("2023-07-28")
CUTOFF = pd.Timestamp("2026-07-27")
FOCUS_INSTRUMENT = "SHFE_RB"
COMPARISON_LOOKBACK = 80
TRADING_DAYS = 256.0

knobs = pd.Series({
    "trend kind": TREND_KIND,
    "chosen carry forecast weight": CARRY_WEIGHT,
    "carry-weight grid": CARRY_WEIGHT_GRID,
    "flat launch after 2023": FLAT_LAUNCH_POST_2023,
}, name="edit these constants")
display(knobs.to_frame())
"""
        ),
        md(
            r"""
## 1. One causal Chinese market panel

`CutoffChinaData` is a small `dbFuturesSimData` subclass.  It exposes only the
reviewed Chinese manifest and clips adjusted prices, multiple prices, and FX
at the requested date.  The pre-2023 system is built from a genuinely shorter
data object, not from a full-history result sliced after the fact.

Liquidity is the same rule used in the numbered series: enter at a 20-observed-
session mean of 130 contracts and exit below 70.  The decision is made at the
close; `delayfill=True` below moves the fill to the next business row.
"""
        ),
        code(
            r"""
fit_data = S.CutoffChinaData(FIT_END)
audit_data = S.CutoffChinaData(CUTOFF)
FIT_INSTRUMENTS = fit_data.get_instrument_list()
AUDIT_INSTRUMENTS = audit_data.get_instrument_list()

print("reading held-contract volume once ...")
with capture_output():
    held_volume = R.held_contract_volumes(audit_data, AUDIT_INSTRUMENTS)
liquidity = R.liquidity_eligibility(
    held_volume,
    lookback=S.LIQUIDITY_LOOKBACK,
    entry_volume=S.LIQUIDITY_ENTRY,
    exit_volume=S.LIQUIDITY_EXIT,
    force_terminal_close=True,
)
fit_liquidity = liquidity.loc[:FIT_END, FIT_INSTRUMENTS]
audit_liquidity = liquidity.loc[:CUTOFF, AUDIT_INSTRUMENTS]

print(
    f"{len(FIT_INSTRUMENTS)} instruments existed by {FIT_END.date()}; "
    f"{len(AUDIT_INSTRUMENTS)} by {CUTOFF.date()}"
)
"""
        ),
        md(
            r"""
### Why a common readiness mask?

A rule that has no forecast yet must not silently change the instruments in
only one side of a comparison.  The short function below asks the native
stages for capped forecasts and volatility-sized positions, then lets every
candidate trade on the intersection.  It is experiment code, not another
framework.
"""
        ),
        code(
            r"""
def common_gate(data, liquid, requests, supplied_config=None):
    supplied_config = config if supplied_config is None else supplied_config
    base_weights = R.equal_weight_panel(liquid)
    probes = [
        S.futures_system(
            data=data, config=supplied_config, eligibility=liquid,
            fixed_weights=base_weights, **request,
        )
        for request in requests
    ]
    ready = {}
    with capture_output():
        for instrument in liquid.columns:
            checks = [
                probe.combForecast.get_all_forecasts(instrument)
                .notna().all(axis=1)
                for probe in probes
            ]
            checks.append(
                probes[0].positionSize
                .get_average_position_at_subsystem_level(instrument)
                .notna()
            )
            aligned = pd.concat(checks, axis=1).reindex(liquid.index)
            ready[instrument] = aligned.ffill().fillna(False).all(axis=1)
    del probes
    gc.collect()
    return liquid & pd.DataFrame(ready, index=liquid.index)


def flat_launch(eligibility):
    launched = eligibility.copy()
    if FLAT_LAUNCH_POST_2023:
        launched.loc[launched.index < AUDIT_START] = False
    return launched
"""
        ),
        code(
            r"""
def portfolio_frame(system):
    curve = system.accounts.portfolio(delayfill=True, roundpositions=True)
    frame = pd.concat({
        "gross": curve.percent.gross.as_ts,
        "costs": curve.percent.costs.as_ts,
        "net": curve.percent.as_ts,
    }, axis=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    assert (frame["net"] - frame["gross"] - frame["costs"]).abs().max() < 1e-8
    return frame


def additive_equity(returns, starting_equity=100.0):
    returns = returns.fillna(0.0).astype(float)
    equity = starting_equity + returns.cumsum()
    anchored = pd.concat([
        pd.Series([starting_equity], index=[
            returns.index[0] - pd.Timedelta(nanoseconds=1)
        ]),
        equity,
    ])
    drawdown = anchored - anchored.cummax()
    return equity, drawdown


def performance(frame, start, end):
    returns = frame.loc[start:end, "net"].dropna().astype(float)
    annual_vol = returns.std(ddof=1) * math.sqrt(TRADING_DAYS)
    sharpe = returns.mean() / returns.std(ddof=1) * math.sqrt(TRADING_DAYS)
    arithmetic_equity, arithmetic_drawdown = additive_equity(returns)
    wealth = (1.0 + returns / 100.0).cumprod()
    anchored = pd.concat([
        pd.Series([1.0], index=[
            returns.index[0] - pd.Timedelta(nanoseconds=1)
        ]),
        wealth,
    ])
    drawdown = anchored / anchored.cummax() - 1.0
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    cagr = wealth.iloc[-1] ** (1.0 / years) - 1.0
    return {
        "Sharpe": sharpe,
        "ann. vol %": annual_vol,
        "ann. arithmetic return %": returns.mean() * TRADING_DAYS,
        "total arithmetic return %": returns.sum(),
        "minimum fixed equity %": arithmetic_equity.min(),
        "additive max drawdown %": arithmetic_drawdown.min(),
        "hypothetical compound CAGR %": 100.0 * cagr,
        "hypothetical compound max drawdown %": 100.0 * drawdown.min(),
    }


def run_system(
    data, eligibility, trend_kind, carry_weight, trend_rules=None,
    supplied_config=None,
):
    supplied_config = config if supplied_config is None else supplied_config
    kwargs = {} if trend_rules is None else {"trend_rules": trend_rules}
    system = S.futures_system(
        data=data,
        config=supplied_config,
        eligibility=eligibility,
        fixed_weights=R.equal_weight_panel(eligibility),
        trend_kind=trend_kind,
        carry_weight=carry_weight,
        **kwargs,
    )
    with capture_output():
        frame = portfolio_frame(system)
    return system, frame
"""
        ),
        md(
            r"""
## 2. Continuous versus binary Donchian

This is the clean shape test.  Both sides use one fixed 80-observed-session
rule, FDM 1, source-frozen amplitude normalisation, common daily instrument
weights, the same 10% forecast buffer, and the same fully costed account.

- `breakout80` continuously measures price inside its rolling channel and
  applies the repository rule's native smoothing.
- `binary80` switches to +1 or -1 only after a strict break of the prior
  observed channel and persists until the opposite break.  Its fixed scalar
  of 10 gives a mature absolute capped forecast of 10.
"""
        ),
        code(
            r"""
lookback = COMPARISON_LOOKBACK
comparison_rules = {
    "continuous": [f"breakout{lookback}"],
    "binary": [f"binary{lookback}"],
}
pair_requests = [
    dict(carry_weight=0.0, trend_kind=kind, trend_rules=rules)
    for kind, rules in comparison_rules.items()
]
fit_pair_gate = common_gate(fit_data, fit_liquidity, pair_requests)
audit_pair_gate = flat_launch(
    common_gate(audit_data, audit_liquidity, pair_requests)
)

pair_systems, pair_frames, rows = {}, {}, []
for period, data, gate, start, end in [
    ("pre-2023", fit_data, fit_pair_gate, EVALUATION_START, FIT_END),
    ("revealed post-2023", audit_data, audit_pair_gate, AUDIT_START, CUTOFF),
]:
    for kind, rules in comparison_rules.items():
        print(f"running {period}: {kind}{lookback} ...")
        system, frame = run_system(data, gate, kind, 0.0, rules)
        pair_systems[(period, kind)] = system
        pair_frames[(period, kind)] = frame
        rows.append({
            "period": period,
            "rule": kind,
            **performance(frame, start, end),
        })

donchian_comparison = pd.DataFrame(rows).set_index(["period", "rule"])
display(donchian_comparison)
"""
        ),
        code(
            r"""
def pooled_forecast_scale(system, rule, eligibility, end):
    observations = []
    with capture_output():
        for instrument in eligibility.columns:
            forecast = system.forecastScaleCap.get_capped_forecast(
                instrument, rule
            ).reindex(eligibility.index)
            observations.append(
                forecast.where(eligibility[instrument]).loc[:end].dropna()
            )
    pooled = pd.concat(observations)
    return {
        "mean abs forecast": pooled.abs().mean(),
        "cap rate": pooled.abs().ge(20.0 - 1e-10).mean(),
        "observations": len(pooled),
    }


scale_check = pd.DataFrame({
    kind: pooled_forecast_scale(
        pair_systems[("pre-2023", kind)], rules[0], fit_pair_gate, FIT_END
    )
    for kind, rules in comparison_rules.items()
}).T
display(scale_check)
amplitude_matched_binary_scalar = (
    config.forecast_scalars[f"binary{lookback}"]
    * scale_check.loc["continuous", "mean abs forecast"]
    / scale_check.loc["binary", "mean abs forecast"]
)
print(
    "The source-frozen scalars are not an exact China amplitude match. "
    f"Using pre-2023 forecasts only, an amplitude-matched binary scalar "
    f"would be {amplitude_matched_binary_scalar:.3f}; it is shown as a "
    "diagnostic and is not substituted after seeing returns."
)

fig, axes = plt.subplots(1, 2, figsize=(13, 4))
for ax, period, start, end in [
    (axes[0], "pre-2023", EVALUATION_START, FIT_END),
    (axes[1], "revealed post-2023", AUDIT_START, CUTOFF),
]:
    curves = pd.concat({
        kind: pair_frames[(period, kind)].loc[start:end, "net"]
        for kind in comparison_rules
    }, axis=1).fillna(0.0)
    R.cumulative_from_zero(curves).plot(
        ax=ax, title=f"{period}: cumulative net return %"
    )
plt.tight_layout()
"""
        ),
        md(
            r"""
## 3. Carry + continuous Donchian weight sweep

For the intended system, carry10/30/60/125 share the carry sleeve equally and
breakout40/80/160 share the trend sleeve equally.  Thus a 70% carry setting is
`70% / 4` per carry rule and `30% / 3` per continuous rule.  The factory omits
zero-weight rules at the 0% and 100% endpoints.

All weights use FDM 1 and the same readiness/membership panel.  This isolates
the forecast ratio: no ratio is helped by a separately fitted diversification
multiplier.  If flat launch is enabled, each post-2023 candidate starts with
zero positions, waits for the normal delayed first fill, and pays entry costs.
"""
        ),
        code(
            r"""
trend_kind = TREND_KIND
grid_request = [dict(carry_weight=0.5, trend_kind=trend_kind)]
fit_grid_gate = common_gate(fit_data, fit_liquidity, grid_request)
audit_grid_gate = flat_launch(
    common_gate(audit_data, audit_liquidity, grid_request)
)

chosen_weight = float(CARRY_WEIGHT)
carry_weights = sorted(
    set(float(value) for value in CARRY_WEIGHT_GRID)
    | {chosen_weight}
)
ratio_frames, ratio_rows = {}, []
chosen_systems = {}

for carry_weight in carry_weights:
    print(f"running {carry_weight:.0%} carry / {1-carry_weight:.0%} {trend_kind} ...")
    fit_system, fit_frame = run_system(
        fit_data, fit_grid_gate, trend_kind, carry_weight
    )
    post_system, post_frame = run_system(
        audit_data, audit_grid_gate, trend_kind, carry_weight
    )
    ratio_frames[("pre-2023", carry_weight)] = fit_frame
    ratio_frames[("revealed post-2023", carry_weight)] = post_frame
    fit_stats = performance(fit_frame, EVALUATION_START, FIT_END)
    post_stats = performance(post_frame, AUDIT_START, CUTOFF)
    ratio_rows.append({
        "carry forecast weight": carry_weight,
        **{f"pre {key}": value for key, value in fit_stats.items()},
        **{f"post {key}": value for key, value in post_stats.items()},
    })
    if carry_weight == chosen_weight:
        chosen_systems = {"pre": fit_system, "post": post_system}
    else:
        del fit_system, post_system
        gc.collect()

ratio_table = pd.DataFrame(ratio_rows).set_index("carry forecast weight")
display(ratio_table)
"""
        ),
        code(
            r"""
ax = ratio_table[["pre Sharpe", "post Sharpe"]].plot(
    marker="o", figsize=(9, 4),
    title=f"carry / {trend_kind} forecast budget",
)
ax.axvline(chosen_weight, color="black", linestyle="--", alpha=0.5)
ax.set_xlabel("carry forecast weight")
ax.set_ylabel("net Sharpe")
plt.tight_layout()

best_pre = ratio_table["pre Sharpe"].idxmax()
print(
    f"Highest displayed pre-2023 Sharpe: {best_pre:.0%} carry. "
    "The revealed post-2023 line is descriptive and cannot vote."
)

fig, axes = plt.subplots(1, 2, figsize=(13, 4))
for ax, period, start, end in [
    (axes[0], "pre-2023", EVALUATION_START, FIT_END),
    (axes[1], "revealed post-2023", AUDIT_START, CUTOFF),
]:
    net = ratio_frames[(period, chosen_weight)].loc[start:end, "net"]
    R.cumulative_from_zero(net).plot(
        ax=ax,
        title=f"configured {chosen_weight:.0%} carry: {period}",
    )
plt.tight_layout()
"""
        ),
        md(
            r"""
## The native API, by hand

These are the useful calls when you want to inspect the result yourself.
Work from forecasts downstream to positions and finally the costed portfolio:

```python
system.combForecast.get_forecast_weights(instrument)
system.combForecast.get_all_forecasts(instrument)
system.combForecast.get_combined_forecast(instrument)
system.positionSize.get_subsystem_position(instrument)
system.portfolio.get_notional_position(instrument)
system.accounts.get_buffered_position(instrument, roundpositions=True)
system.accounts.portfolio(delayfill=True, roundpositions=True)
system.accounts.portfolio(...).percent.to_frame()
system.rawdata.get_daily_vol_normalised_returns(instrument)
```

The first object is the signal budget.  The realised volatility shown above
comes only after forecasts, volatility sizing, changing instrument membership,
IDM, buffering, integer contracts, and costs have all acted.
"""
        ),
        code(
            r"""
system = chosen_systems["post"]
instrument = FOCUS_INSTRUMENT

print("exact fixed forecast weights:")
display(pd.Series(system.config.forecast_weights, name="weight").to_frame())

with capture_output():
    forecast_weights = system.combForecast.get_forecast_weights(instrument)
    forecasts = system.combForecast.get_all_forecasts(instrument)
    combined = system.combForecast.get_combined_forecast(instrument)
    subsystem = system.positionSize.get_subsystem_position(instrument)
    notional = system.portfolio.get_notional_position(instrument)
    buffered = system.accounts.get_buffered_position(
        instrument, roundpositions=True
    )

display(pd.concat({
    "combined forecast": combined,
    "subsystem position": subsystem,
    "notional position": notional,
    "buffered position": buffered,
}, axis=1).dropna(how="all").tail(10))
display(forecast_weights.dropna(how="all").tail(3))
display(forecasts.dropna(how="all").tail(3))

chosen_post = ratio_frames[("revealed post-2023", chosen_weight)]
if FLAT_LAUNCH_POST_2023:
    before = buffered.loc[buffered.index < AUDIT_START].fillna(0.0)
    assert before.eq(0.0).all()
    pre_cost = chosen_post.loc[
        chosen_post.index < AUDIT_START, "costs"
    ].abs().max()
    assert not np.isfinite(pre_cost) or pre_cost < 1e-12
    first_cost = chosen_post.loc[AUDIT_START:, "costs"]
    first_cost = first_cost[first_cost.abs() > 1e-12].head(1)
    assert len(first_cost) == 1 and first_cost.iloc[0] < 0.0
    print("flat before launch: yes")
    print("first delayed entry cost:", first_cost.to_dict())
else:
    print("post-2023 mode: ongoing positions (not a flat launch)")
"""
        ),
        code(
            r"""
pre_binary = donchian_comparison.loc[("pre-2023", "binary"), "Sharpe"]
pre_continuous = donchian_comparison.loc[
    ("pre-2023", "continuous"), "Sharpe"
]
post_binary = donchian_comparison.loc[
    ("revealed post-2023", "binary"), "Sharpe"
]
post_continuous = donchian_comparison.loc[
    ("revealed post-2023", "continuous"), "Sharpe"
]

print("DATA-DRIVEN SUMMARY")
print(
    f"Fixed {lookback}-day trend Sharpe, pre-2023: "
    f"continuous {pre_continuous:.3f}, binary {pre_binary:.3f}."
)
print(
    f"Revealed post-2023: continuous {post_continuous:.3f}, "
    f"binary {post_binary:.3f}."
)
print(
    f"Configured carry/{trend_kind} mix: {chosen_weight:.0%}/"
    f"{1-chosen_weight:.0%}; pre Sharpe "
    f"{ratio_table.loc[chosen_weight, 'pre Sharpe']:.3f}, revealed post Sharpe "
    f"{ratio_table.loc[chosen_weight, 'post Sharpe']:.3f}."
)
print(
    "Controls: 16% volatility target, 10% forecast buffer, "
    "no volatility backfill, native delayed whole-contract accounts."
)
"""
        ),
    ]


def _single_instrument_tail_risk_cells() -> list:
    return [
        md(
            r"""
# 07 — Single-instrument tail risk under native full compounding

Each of the 95 reviewed Chinese futures is run as its own complete native
System with 100 million CNH starting capital, a 16% volatility target, a 10%
forecast buffer, no volatility backfill, and the lab's 70% carry / 30%
continuous-Donchian rule mix.

There is only one capital path: pysystemtrade's full_compounding multiplier.
The notebook does not reconstruct a second cash ledger, simulate margin calls,
fit stress scenarios, or add loss-budget and leverage-cap policies. It asks a
smaller question: what positions, native returns, capital multiplier,
drawdown, and notional leverage did the standard account produce?
"""
        ),
        code(
            r"""
%matplotlib inline
import gc

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from IPython.display import display

from examples.chinese_futures import research as R
from examples.chinese_futures.carry_donchian_lab import system as S
from sysdata.config.configdata import Config
from systems.accounts.accounts_stage import Account
from systems.basesystem import System
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecasting import Rules
from systems.portfolio import Portfolios
from systems.positionsizing import PositionSizing
from systems.rawdata import RawData

R.set_notebook_style()
R.limit_blas_threads()

CAPITAL = 100_000_000.0
VOL_TARGET = 16.0
BUFFER_SIZE = 0.10
CUTOFF = "2026-07-27"

plt.rcParams.update({
    "figure.figsize": (12, 6),
    "figure.dpi": 140,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "legend.frameon": False,
})
"""
        ),
        md(
            r"""
## One native system per instrument

The adjacent lab config supplies the carry and continuous-breakout rules,
fixed forecast scalars, and the 70/30 forecast budget. This factory changes
only the single-instrument portfolio controls and selects the repository's
full_compounding capital multiplier.
"""
        ),
        code(
            r"""
def standalone_system(instrument):
    config = Config(str(S.CONFIG_PATH))
    config.instruments = [instrument]
    config.instrument_weights = {instrument: 1.0}
    config.use_instrument_weight_estimates = False
    config.instrument_div_multiplier = 1.0
    config.use_instrument_div_mult_estimates = False
    config.notional_trading_capital = CAPITAL
    config.percentage_vol_target = VOL_TARGET
    config.buffer_size = BUFFER_SIZE
    config.capital_multiplier = {
        "func": "syscore.capital.full_compounding"
    }

    volatility = dict(config.volatility_calculation)
    volatility["backfill"] = False
    config.volatility_calculation = volatility

    return System(
        [
            Account(),
            Portfolios(),
            PositionSizing(),
            RawData(),
            ForecastCombine(),
            ForecastScaleCap(),
            Rules(),
        ],
        S.CutoffChinaData(CUTOFF),
        config,
    )


data = S.CutoffChinaData(CUTOFF)
instruments = R.chinese_universe(data)
print(
    f"{len(instruments)} reviewed histories through {CUTOFF}; "
    f"CNH {CAPITAL:,.0f} starts each independent full-compounding run"
)
"""
        ),
        md(
            r"""
## Compact native diagnostics

For each instrument, the account stage supplies the buffered position after
capital scaling, the delayed held contracts, the full-compounding portfolio
curve, the capital multiplier, and actual capital used for sizing. Monetary
notional is held contracts times the unadjusted denominator price, point size,
and FX, divided by that native capital path.
"""
        ),
        code(
            r"""
def native_result(instrument):
    system = standalone_system(instrument)

    portfolio = system.accounts.portfolio_with_multiplier(
        delayfill=True,
        roundpositions=True,
    )
    instrument_curve = system.accounts.pandl_for_instrument_with_multiplier(
        instrument,
        delayfill=True,
        roundpositions=True,
    )
    index = pd.DatetimeIndex(portfolio.index)

    price = (
        system.rawdata.daily_denominator_price(instrument)
        .reindex(index)
        .ffill()
    )
    forecast = (
        system.combForecast.get_combined_forecast(instrument)
        .reindex(index)
        .ffill()
    )
    buffered = (
        system.accounts.get_buffered_position_with_multiplier(
            instrument,
            roundpositions=True,
        )
        .reindex(index)
        .ffill()
        .fillna(0.0)
    )
    held = (
        instrument_curve.pandl_calculator_with_costs.positions
        .reindex(index)
        .ffill()
        .fillna(0.0)
    )
    capital_multiplier = (
        system.accounts.capital_multiplier()
        .reindex(index)
        .ffill()
    )
    actual_capital = (
        system.accounts.get_actual_capital()
        .reindex(index)
        .ffill()
    )
    point_size = float(
        system.rawdata.get_value_of_block_price_move(instrument)
    )
    fx = (
        system.positionSize.get_fx_rate(instrument)
        .reindex(index)
        .ffill()
    )

    notional_cnh = held * price * point_size * fx
    leverage = (
        notional_cnh.div(actual_capital.replace(0.0, np.nan)).abs()
    )
    native_return = portfolio.percent.as_ts.reindex(index)
    native_drawdown = portfolio.percent.drawdown().reindex(index)

    frame = pd.DataFrame({
        "price": price,
        "forecast": forecast,
        "buffered_contracts": buffered,
        "held_contracts": held,
        "notional_leverage": leverage,
        "native_return_pct": native_return,
        "capital_multiplier": capital_multiplier,
        "actual_capital_cnh": actual_capital,
        "native_drawdown_pct": native_drawdown,
    }, index=index)

    percent = portfolio.percent
    row = {
        "instrument": instrument,
        "first": frame.index.min(),
        "last": frame.index.max(),
        "observations": len(frame),
        "net Sharpe": percent.sharpe(),
        "annual return %": percent.ann_mean(),
        "annual volatility %": percent.ann_std(),
        "worst drawdown %": percent.worst_drawdown(),
        "final capital multiplier": capital_multiplier.dropna().iloc[-1],
        "minimum native capital m": actual_capital.min() / 1e6,
        "maximum notional leverage": leverage.max(),
        "final held contracts": held.iloc[-1],
    }
    return frame, row
"""
        ),
        md(
            r"""
## Run the reviewed universe

The loop retains only compact frames and one summary row. Every row comes from
a fresh System, so no stage cache or mutable data parent is shared between
instruments.
"""
        ),
        code(
            r"""
frames = {}
summary_rows = []

for number, instrument in enumerate(instruments, start=1):
    print(f"Run {number:02d}/{len(instruments)}: {instrument}")
    frame, row = native_result(instrument)
    frames[instrument] = frame
    summary_rows.append(row)
    gc.collect()

summary = (
    pd.DataFrame(summary_rows)
    .set_index("instrument")
    .sort_index()
)
display(summary)
"""
        ),
        code(
            r"""
display(
    summary.sort_values(
        "maximum notional leverage",
        ascending=False,
    ).head(20)
)
display(
    summary.sort_values("worst drawdown %").head(20)
)

fig, axes = plt.subplots(1, 2, figsize=(14, 5))
summary["maximum notional leverage"].sort_values().tail(25).plot.barh(
    ax=axes[0],
    title="Largest native notional leverage",
)
axes[0].set_xlabel("maximum |notional| / native actual capital")
summary["worst drawdown %"].sort_values().head(25).plot.barh(
    ax=axes[1],
    title="Worst native full-compounding drawdowns",
)
axes[1].set_xlabel("percentage points")
plt.tight_layout()
plt.show()
"""
        ),
        md(
            r"""
## Full-resolution 95-instrument atlas

Every instrument gets the same six-panel receipt: price, combined forecast,
native buffered and delayed held contracts, notional leverage against native
actual capital, daily native percentage return, and the full-compounding
capital multiplier with native account drawdown.
"""
        ),
        code(
            r"""
def instrument_dashboard(instrument, frame, row):
    fig, axes = plt.subplots(3, 2, figsize=(15, 13), sharex=True)
    axes = axes.ravel()

    frame["price"].plot(
        ax=axes[0],
        color="black",
        title="Unadjusted denominator price",
    )
    axes[0].set_ylabel("price")

    frame["forecast"].plot(
        ax=axes[1],
        color="tab:purple",
        title="Combined 70% carry / 30% trend forecast",
    )
    axes[1].axhline(0.0, color="black", linewidth=0.7)
    axes[1].set_ylabel("forecast")

    frame[["buffered_contracts", "held_contracts"]].plot(
        ax=axes[2],
        title="Native full-compounding positions",
    )
    axes[2].axhline(0.0, color="black", linewidth=0.7)
    axes[2].set_ylabel("contracts")

    frame["notional_leverage"].plot(
        ax=axes[3],
        color="tab:orange",
        title="Gross notional / native actual capital",
    )
    axes[3].set_ylabel("times capital")

    frame["native_return_pct"].plot(
        ax=axes[4],
        color="tab:blue",
        alpha=0.8,
        title="Daily native portfolio return",
    )
    axes[4].axhline(0.0, color="black", linewidth=0.7)
    axes[4].set_ylabel("percent")

    frame["capital_multiplier"].plot(
        ax=axes[5],
        color="tab:green",
        label="capital multiplier",
    )
    axes[5].set_title("Native multiplier and account drawdown")
    axes[5].set_ylabel("multiplier")
    twin = axes[5].twinx()
    frame["native_drawdown_pct"].plot(
        ax=twin,
        color="tab:red",
        alpha=0.55,
        label="drawdown",
    )
    twin.set_ylabel("drawdown percentage points", color="tab:red")

    fig.suptitle(
        f"{instrument} | Sharpe {row['net Sharpe']:.2f} | "
        f"ann vol {row['annual volatility %']:.1f}% | "
        f"worst DD {row['worst drawdown %']:.1f}% | "
        f"max leverage {row['maximum notional leverage']:.2f}x",
        y=1.01,
    )
    fig.tight_layout()
    return fig
"""
        ),
        code(
            r"""
atlas_order = (
    summary.sort_values(
        "maximum notional leverage",
        ascending=False,
        kind="mergesort",
        na_position="last",
    )
    .index
    .tolist()
)

for number, instrument in enumerate(atlas_order, start=1):
    print(f"Atlas {number:02d}/{len(atlas_order)}: {instrument}")
    figure = instrument_dashboard(
        instrument,
        frames[instrument],
        summary.loc[instrument],
    )
    display(figure)
    plt.close(figure)
"""
        ),
        md(
            r"""
## Read the atlas conservatively

A 16% model target controls sizing through an estimated volatility path; it
does not cap monetary notional, guarantee realised volatility, or reproduce
an exchange margin process. Full compounding changes future positions through
the native capital multiplier after returns are observed. The diagrams show
that mechanism and its historical result without claiming a liquidation rule
or tail-loss guarantee.
"""
        ),
        code(
            r"""
worst_leverage = summary["maximum notional leverage"].idxmax()
worst_drawdown = summary["worst drawdown %"].idxmin()

print("DATA-DRIVEN SUMMARY")
print(
    f"Highest native notional leverage: {worst_leverage}, "
    f"{summary.loc[worst_leverage, 'maximum notional leverage']:.2f}x."
)
print(
    f"Worst native full-compounding drawdown: {worst_drawdown}, "
    f"{summary.loc[worst_drawdown, 'worst drawdown %']:.2f}%."
)
print(
    "No fixed-notional ledger, margin simulation, stress grid, loss budget, "
    "or leverage-cap policy is applied."
)
"""
        ),
    ]


def _section(builder, heading: str, include_setup: bool) -> list:
    """Retitle one former chapter and avoid repeated setup cells."""

    source_cells = builder()
    introduction = "\n".join(source_cells[0].source.splitlines()[1:]).strip()
    cells = [md(f"## {heading}\n\n{introduction}")]
    for cell in source_cells[1:]:
        is_common_setup = (
            cell.cell_type == "code" and cell.source.strip() == SETUP_CELL.strip()
        )
        if is_common_setup and not include_setup:
            continue
        cells.append(cell)
    return cells


def _merge_sections(title: str, summary: str, sections: list[tuple]) -> list:
    cells = [md(f"# {title}\n\n{summary}")]
    for index, (heading, builder) in enumerate(sections):
        cells.extend(_section(builder, heading, include_setup=index == 0))
    return cells


def build_01() -> list:
    return _merge_sections(
        "01 — Data and universe",
        (
            "Start here: the reviewed 95-market universe, its metadata, and the "
            "single Tushare-to-simulation pipeline that maintains it."
        ),
        [
            ("Universe and metadata", _universe_cells),
            ("Data pipeline and updating", _data_pipeline_cells),
        ],
    )


def build_02() -> list:
    return _merge_sections(
        "02 — Rolls and stitching",
        (
            "Build and review one manual roll calendar, then follow FINAL "
            "contract prices through multiple and additive Panama prices."
        ),
        [("Manual calendars and Panama stitching", _rolls_cells)],
    )


def build_03() -> list:
    return _merge_sections(
        "03 — Backtest basics",
        (
            "Trace one native backtest from data and forecasts to positions, "
            "costs, and a full-universe carry/EWMAC portfolio."
        ),
        [
            ("Anatomy of a native backtest", _backtest_anatomy_cells),
            ("The full Chinese universe", _full_universe_cells),
        ],
    )


def build_04() -> list:
    return _merge_sections(
        "04 — Weights and pooling",
        (
            "Separate forecast weights, instrument weights, diversification "
            "multipliers, estimation, and pooled evidence."
        ),
        [
            ("Weights and fitting", _weights_cells),
            ("Pooling in practice", _pooling_cells),
        ],
    )


def build_05() -> list:
    cells = _merge_sections(
        "05 — Carry and trend",
        (
            "Compare carry and trend with native systems, predeclared windows, "
            "costs, skew, drawdowns, and conservative exclusion evidence."
        ),
        [("Allocation and honest exclusions", _carry_trend_cells)],
    )
    cells.append(
        md(
            """
## Durable lessons from the earlier carry and selection studies

Carry results are sensitive to smoothing, costs, and sample boundaries; a
single full-sample winner is not a rule-selection argument. Likewise, deleting
markets because their completed histories lost money is hindsight. A durable
exclusion must be predeclared, causal, and strong enough to survive the
multiple comparisons across lineages and styles. The familywise test above is
the canonical version of those conclusions.
"""
        )
    )
    return cells


def build_06() -> list:
    cells = _merge_sections(
        "06 — Rule families",
        (
            "Translate forecast budgets into realised risk, compare matched "
            "trend families, and screen optional native rules without a new "
            "research framework."
        ),
        [("Native risk, Donchian, and optional families", _rule_families_cells)],
    )
    cells.append(
        md(
            """
## What survived from the superseded experiments

The older binary-Donchian study used the same 95-history, causal-membership
idea. Over its declared common sample, binary Donchian had net Sharpe 1.208
versus 0.980 for buffered multi-speed EWMAC, and lower gross correlation with
carry (0.253 versus 0.266). Those are historical diagnostics, not a fitted
production choice; the compact lab repeats the cleaner fixed-lookback shape
test.

The v00--v05 ladder selected fast_tilt_80_20 before the holdout. Its guarded
score was 0.0825 versus -0.0493 for the Qoppac prior, and its frozen fitted
payload SHA-256 was:

    2b6f49fd7b771a3b308944ac9da2f68015ec397964d16a4731429c274210e3e0

The untouched 2023-07-28 to 2026-07-27 run reported net Sharpe 0.5183 (0.4219
with one extra observed-session lag). This short receipt preserves the result
and provenance; the six-version scaffold is intentionally no longer a runtime
dependency.
"""
        )
    )
    return cells


BUILDERS = {
    "1": ("01_data_and_universe", build_01),
    "2": ("02_rolls_and_stitching", build_02),
    "3": ("03_backtest_basics", build_03),
    "4": ("04_weights_and_pooling", build_04),
    "5": ("05_carry_and_trend", build_05),
    "6": ("06_rule_families", build_06),
    "7": (
        "07_single_instrument_tail_risk",
        _single_instrument_tail_risk_cells,
    ),
    "lab": ("carry_donchian_lab/research", _carry_donchian_lab_cells),
    "tutorial": (
        "backtesting_tutorial/backtesting_with_chinese_futures",
        _backtesting_tutorial_cells,
    ),
}
DEFAULT_SELECTORS = ("1", "2", "3", "4", "5", "6", "7", "lab")


def normalise_selector(value: str) -> str:
    selector = value.strip().lower()
    if selector.isdigit():
        selector = str(int(selector))
    if selector not in BUILDERS:
        choices = ", ".join(BUILDERS)
        raise SystemExit(f"unknown notebook selector {value!r}; choose {choices}")
    return selector


def write_notebook(selector: str) -> Path:
    name, builder = BUILDERS[selector]
    notebook = new_notebook(cells=builder(), metadata=NOTEBOOK_METADATA)
    path = HERE / f"{name}.ipynb"
    path.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(notebook, str(path))
    return path


def main(argv: list[str]) -> None:
    selectors = (
        [normalise_selector(value) for value in argv]
        if argv
        else list(DEFAULT_SELECTORS)
    )
    for selector in selectors:
        path = write_notebook(selector)
        print(f"wrote {path.relative_to(HERE)}")


if __name__ == "__main__":
    main(sys.argv[1:])
