# Chinese futures examples

This directory has one seven-notebook path through the Tushare-backed Chinese
futures data, plus two deliberately separate companions. The numbered series
starts with data mechanics, builds a native costed backtest, and ends with the
rule-family and single-market tail-risk studies. It uses all 95 reviewed,
stitchable histories; dated liquidity decides when a market may receive risk.

| Notebook | Purpose |
|---|---|
| [01 data and universe](01_data_and_universe.ipynb) | Metadata, coverage, storage, Tushare ingestion, updates, and inspection |
| [02 rolls and stitching](02_rolls_and_stitching.ipynb) | Manual roll-calendar review, multiple prices, and additive Panama adjusted prices |
| [03 backtest basics](03_backtest_basics.ipynb) | Native stages, forecasts, sizing, buffering, costs, and a full-universe carry/EWMAC system |
| [04 weights and pooling](04_weights_and_pooling.ipynb) | Forecast and instrument weights, FDM/IDM, estimation, and pooled evidence |
| [05 carry and trend](05_carry_and_trend.ipynb) | Carry/trend allocation, tail behaviour, subperiods, and honest exclusion evidence |
| [06 rule families](06_rule_families.ipynb) | Realised risk attribution, matched Donchian/EWMAC comparisons, optional families, and the archived v00--v05 result receipt |
| [07 single-instrument tail risk](07_single_instrument_tail_risk.ipynb) | Full-resolution 95-instrument atlas for 16% volatility targeting, notional leverage, and the native full-compounding account |

## Companions

[carry_donchian_lab](carry_donchian_lab/) is the small editable experiment. It
contains only a notebook, one native `system.py`, and one resolved
`config.yaml`. The defaults are a 16% volatility target, 10% forecast buffer,
and no volatility backfill. The notebook compares fixed continuous and binary
Donchian under common membership, then sweeps the carry forecast budget. It
does not contain a second ledger, stop framework, or bespoke loss-budget
engine.

[Backtesting with Chinese futures](backtesting_tutorial/backtesting_with_chinese_futures.ipynb)
is the complete standalone tutorial corresponding to `docs/backtesting.md`.
Its adjacent [config](backtesting_tutorial/config.yaml) and
[system](backtesting_tutorial/system.py) are intentionally kept intact. It is
not another numbered research notebook and does not depend on `research.py`.

## Point-in-time membership

Portfolio studies begin with the reviewed manifest rather than a present-day
survivor list. Held-contract volume enters at a trailing 20-observed-session
mean of 130 contracts and exits below 70. The decision is made at the close;
native delayed fills, whole-contract rounding, and configured costs remain in
the account stage. Exchange holidays are not zero-volume observations, while
a missing volume on an observed price row is conservatively zero.

An ended predecessor or known-dead history may need an explicitly labelled
final-close assumption so an open position does not disappear for free. That
boundary convention is separate from the causal liquidity signal. Historical
rebuilds never fill a missing held-contract close across a roll boundary.

## Generate notebooks

Cell sources live in [make_notebooks.py](make_notebooks.py). Update that file,
then regenerate only the notebooks in scope:

```bash
# numbered selectors accept 1--7 (01--07 also work)
uv run --with nbformat python examples/chinese_futures/make_notebooks.py 1 4 7

# named companions
uv run --with nbformat python examples/chinese_futures/make_notebooks.py lab
uv run --with nbformat python examples/chinese_futures/make_notebooks.py tutorial
```

With no selectors, the generator writes notebooks 1--7 and the lab. The full
tutorial is excluded from that default so its saved execution is not cleared
accidentally. Generation writes source-only notebooks; execute a notebook in a
clean kernel when its outputs need refreshing.

## Prerequisites and boundaries

- Populate the parquet, MongoDB, and repository CSV stores described in
  `docs/tushare_chinese_futures.md`.
- Live update cells in notebook 01 need a Tushare token; the rest read the
  configured local stores.
- Additive adjusted-price levels support price differences, not returns.
- The simulations do not reconstruct intraday margin calls, locked limits,
  stressed slippage, or guaranteed liquidation prices.

Shared plumbing is intentionally limited to [research.py](research.py):
manifest selection, held-contract volume, the dated portfolio gate, plotting
style, and small account-curve helpers. Strategy choices and conclusions stay
visible in the notebooks.
