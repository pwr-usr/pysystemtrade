"""Shared plumbing for the Chinese futures notebook series.

Deliberately thin: anything conceptual lives visibly in the notebooks; this
module only holds boring setup and a few helpers used across notebooks.
Import as ``import research as R``.
"""

from __future__ import annotations

import logging
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

# The repo is normally importable because uv installs the project into the
# venv, but make the notebooks robust to being run from a bare kernel too.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from syscore.pandas.strategy_functions import weights_sum_to_one
from sysdata.tushare.manifest import TushareInstrumentManifest
from systems.portfolio import Portfolios
from systems.system_cache import dont_cache

# These are present-day labels for reporting, never a backtest exclusion list.
KNOWN_DEAD_MARKETS = [
    "CZCE_JR",  # japonica rice, dead since ~2014
    "CZCE_LR",  # late indica rice, dead since ~2015
    "CZCE_PM",  # common wheat, dead since ~2014
    "CZCE_RI",  # early indica rice, dead since ~2017
    "CZCE_RS",  # rapeseed, dead since ~2013
    "CZCE_WH",  # strong wheat, moribund since ~2023
    "CZCE_ZC",  # thermal coal, husk after the 2021 price intervention
    "DCE_BB",  # plywood, dead since ~2024
    "SHFE_WR",  # wire rod, dead since ~2018
]

LIQUIDITY_LOOKBACK = 20
LIQUIDITY_ENTRY = 130.0
LIQUIDITY_EXIT = 70.0


def predecessor_instruments() -> list[str]:
    """Reviewed earlier product eras recorded by the Tushare manifest."""

    manifest = TushareInstrumentManifest.from_csv()
    return sorted(
        {
            mapping.predecessor
            for mapping in manifest.mappings
            if mapping.predecessor is not None
        }
    )


# Kept as a descriptive compatibility name.  These instruments are included
# in every point-in-time portfolio; unlike the old notebooks, they are not
# removed from their own history.
LEGACY_PREDECESSORS = predecessor_instruments()
DEAD_MARKETS = KNOWN_DEAD_MARKETS


def set_notebook_style() -> None:
    """Quiet logging and readable defaults for plots and tables."""

    # Configure repository logging before lowering the root level: the first
    # later get_logger() call otherwise reinstalls the DEBUG simulation config.
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        from syslogging.logger import get_logger

        get_logger("chinese_futures_notebook")

    logging.getLogger().setLevel(logging.WARNING)
    for name in ("urllib3", "matplotlib", "arctic", "ib_insync"):
        logging.getLogger(name).setLevel(logging.WARNING)

    plt.rcParams["figure.figsize"] = (10, 5)
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
    plt.rcParams["legend.frameon"] = False

    pd.set_option("display.max_rows", 120)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", lambda value: f"{value:,.4f}")


def limit_blas_threads() -> None:
    """Single-thread BLAS: on ~90x90 problems OpenBLAS threading is ~5x pure
    overhead (measured on this machine in earlier research)."""

    from threadpoolctl import threadpool_limits

    threadpool_limits(limits=1)


def chinese_universe(data) -> list[str]:
    """All reviewed, stitchable Tushare instruments available to the sim data.

    Present-day status is deliberately irrelevant.  Price and liquidity
    history decide when an instrument can enter a backtest.
    """

    manifest = TushareInstrumentManifest.from_csv()
    expected = sorted(
        mapping.instrument_code
        for mapping in manifest.mappings
        if mapping.is_stitchable
    )
    missing = sorted(set(expected) - set(data.get_instrument_list()))
    if missing:
        raise ValueError(
            "Stitchable Tushare instruments missing from simulation data: %s"
            % ", ".join(missing)
        )

    return expected


def terminal_instruments() -> list[str]:
    """Histories for which a final-close assumption may be necessary."""

    return sorted(set(predecessor_instruments()) | set(KNOWN_DEAD_MARKETS))


def _held_contract_volume(data, instrument_code: str, price_store) -> pd.Series:
    """Raw volume for the contract supplying PRICE on each observed date."""

    from syscore.dateutils import DAILY_PRICE_FREQ
    from syscore.exceptions import missingData
    from sysobjects.contracts import futuresContract

    multiple = pd.DataFrame(data.get_multiple_prices(instrument_code))[
        ["PRICE", "PRICE_CONTRACT"]
    ].copy()
    multiple.index = pd.to_datetime(multiple.index).normalize()
    multiple = multiple.dropna(subset=["PRICE", "PRICE_CONTRACT"])
    multiple = multiple[~multiple.index.duplicated(keep="last")]
    multiple["PRICE_CONTRACT"] = multiple["PRICE_CONTRACT"].astype(str)

    raw_pieces = []
    for contract_date in multiple["PRICE_CONTRACT"].unique():
        try:
            stored = price_store.get_prices_at_frequency_for_contract_object(
                futuresContract(instrument_code, contract_date),
                frequency=DAILY_PRICE_FREQ,
            )
        except missingData:
            continue

        stored = pd.DataFrame(stored)
        if "VOLUME" not in stored.columns:
            continue
        one_contract = stored[["VOLUME"]].copy()
        one_contract.index = pd.to_datetime(one_contract.index).normalize()
        one_contract["PRICE_CONTRACT"] = str(contract_date)
        raw_pieces.append(one_contract)

    if not raw_pieces:
        return pd.Series(0.0, index=multiple.index, name=instrument_code)

    raw_volume = pd.concat(raw_pieces)
    raw_volume.index.name = "date"
    raw_volume = raw_volume.reset_index().drop_duplicates(
        ["date", "PRICE_CONTRACT"], keep="last"
    )

    multiple.index.name = "date"
    held = multiple.reset_index().merge(
        raw_volume, on=["date", "PRICE_CONTRACT"], how="left"
    )
    # This is an observed held-price row.  A missing raw volume is therefore
    # unknown/unsafe liquidity, not an exchange holiday.
    return held.set_index("date")["VOLUME"].fillna(0.0).rename(instrument_code)


def held_contract_volumes(data, instrument_codes: list[str]) -> pd.DataFrame:
    """Held-contract raw volume, leaving exchange-closed dates missing."""

    from sysdata.data_blob import dataBlob
    from sysproduction.data.prices import diagPrices

    price_blob = dataBlob(log_name="chinese_futures_liquidity")
    try:
        price_store = diagPrices(price_blob).db_futures_contract_price_data
        columns = {
            instrument_code: _held_contract_volume(data, instrument_code, price_store)
            for instrument_code in instrument_codes
        }
    finally:
        price_blob.close()

    return pd.concat(columns, axis=1).sort_index()


def trailing_liquidity(
    volumes: pd.DataFrame, lookback: int = LIQUIDITY_LOOKBACK
) -> pd.DataFrame:
    """Mean of the last ``lookback`` observed sessions for each instrument."""

    averages = {}
    for instrument_code in volumes.columns:
        observed = volumes[instrument_code].dropna()
        averages[instrument_code] = observed.rolling(
            lookback, min_periods=lookback
        ).mean()

    return pd.concat(averages, axis=1).reindex(volumes.index)


def liquidity_eligibility(
    volumes: pd.DataFrame,
    lookback: int = LIQUIDITY_LOOKBACK,
    entry_volume: float = LIQUIDITY_ENTRY,
    exit_volume: float = LIQUIDITY_EXIT,
    force_terminal_close: bool = False,
) -> pd.DataFrame:
    """Causal daily eligibility with the repo's 30% volume hysteresis.

    A decision uses volume observed through that close. Portfolio accounts
    retain their normal one-business-row delayed fill. ``force_terminal_close``
    is a separate, explicitly ex-post liquidation assumption for ended histories.
    """

    if entry_volume <= exit_volume:
        raise ValueError("entry_volume must be greater than exit_volume")
    if lookback < 1:
        raise ValueError("lookback must be positive")

    average_volume = trailing_liquidity(volumes, lookback=lookback)
    terminal_codes = set(terminal_instruments()) if force_terminal_close else set()

    observed_states = {}
    for instrument_code in volumes.columns:
        observed_average = average_volume[instrument_code].dropna()
        is_eligible = False
        states = []
        for value in observed_average:
            if is_eligible and value < exit_volume:
                is_eligible = False
            elif not is_eligible and value >= entry_volume:
                is_eligible = True
            states.append(is_eligible)

        state = pd.Series(states, index=observed_average.index, dtype=bool)

        observed_states[instrument_code] = state

    if volumes.empty:
        return pd.DataFrame(columns=volumes.columns, dtype=bool)

    business_dates = pd.date_range(volumes.index.min(), volumes.index.max(), freq="B")
    daily_states = {}
    for instrument_code, state in observed_states.items():
        daily_state = state.reindex(business_dates).ffill().fillna(False).astype(bool)
        if instrument_code in terminal_codes:
            final_observed_date = volumes[instrument_code].dropna().index[-1]
            final_location = business_dates.get_indexer([final_observed_date])[0]
            needs_terminal_exit = bool(
                final_location >= 1 and daily_state.iloc[final_location - 1]
            )
            # A delayed account shifts desired positions by one business row.
            # The target must therefore become zero on the row immediately
            # before the final quote, including when that row is an exchange
            # holiday. The fill then lands on the real final quote and retains
            # the complete reopening move.
            if needs_terminal_exit:
                daily_state.iloc[final_location - 1 :] = False

        daily_states[instrument_code] = daily_state

    return pd.DataFrame(daily_states, index=business_dates).astype(bool)


def liquidity_event_table(
    eligibility: pd.DataFrame,
    volumes: pd.DataFrame,
    lookback: int = LIQUIDITY_LOOKBACK,
    exit_volume: float = LIQUIDITY_EXIT,
) -> pd.DataFrame:
    """Entry and exit dates, including clearly labelled terminal exits."""

    average_volume = trailing_liquidity(volumes, lookback=lookback)
    terminal_codes = set(terminal_instruments())
    rows = []

    for instrument_code in eligibility.columns:
        state = eligibility[instrument_code].astype(bool)
        previous = state.shift(1, fill_value=False)
        entries = state & ~previous
        exits = ~state & previous
        observed_dates = volumes[instrument_code].dropna().index
        final_observed_date = observed_dates[-1] if len(observed_dates) else None
        final_location = (
            state.index.get_indexer([final_observed_date])[0]
            if final_observed_date is not None
            else -1
        )
        forced_date = state.index[final_location - 1] if final_location >= 1 else None
        displayed_average = average_volume[instrument_code].reindex(state.index).ffill()

        for date in state.index[entries | exits]:
            entering = bool(entries.loc[date])
            mean_volume = displayed_average.get(date, np.nan)
            forced = bool(
                not entering
                and instrument_code in terminal_codes
                and forced_date is not None
                and date == forced_date
                and (pd.isna(mean_volume) or mean_volume >= exit_volume)
            )
            rows.append(
                {
                    "date": date,
                    "instrument": instrument_code,
                    "event": (
                        "entry"
                        if entering
                        else "forced terminal exit"
                        if forced
                        else "liquidity exit"
                    ),
                    "mean_volume_20": mean_volume,
                    "terminal_assumption": forced,
                }
            )

    columns = [
        "date",
        "instrument",
        "event",
        "mean_volume_20",
        "terminal_assumption",
    ]
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["date", "instrument"], ignore_index=True
    )


def equal_weight_panel(eligibility: pd.DataFrame) -> pd.DataFrame:
    """Equal weights across eligible instruments on each date."""

    weights = eligibility.astype(float)
    active_count = weights.sum(axis=1).replace(0.0, np.nan)
    return weights.div(active_count, axis=0).fillna(0.0)


def cumulative_from_zero(returns: pd.Series | pd.DataFrame):
    """Cumulative arithmetic returns with an explicit zero observation."""

    if returns.empty:
        return returns.copy()

    cumulative = returns.cumsum()
    anchor = cumulative.iloc[[0]].copy()
    anchor.iloc[0] = 0.0
    anchor.index = pd.DatetimeIndex(
        [pd.Timestamp(cumulative.index[0]) - pd.Timedelta(nanoseconds=1)]
    )
    return pd.concat([anchor, cumulative])


class PointInTimePortfolios(Portfolios):
    """Native portfolio stage with one research-only dated eligibility gate."""

    def __init__(
        self,
        eligibility: pd.DataFrame,
        fixed_weights: pd.DataFrame | None = None,
    ):
        self._eligibility = eligibility.sort_index().astype(bool)
        self._fixed_weights = (
            None if fixed_weights is None else fixed_weights.sort_index().copy()
        )

    @dont_cache
    def get_instrument_weights(self) -> pd.DataFrame:
        if self._fixed_weights is None:
            weights = super().get_instrument_weights()
        else:
            weights = self._fixed_weights.copy()

        allowed = self._eligibility.reindex(index=weights.index).ffill()
        allowed = allowed.reindex(columns=weights.columns, fill_value=False)
        allowed = allowed.fillna(False).astype(bool)
        return weights_sum_to_one(weights.where(allowed, 0.0))


def rewrap(returns: pd.Series, capital: float = 100.0):
    """Turn a plain sliced returns Series back into an accountCurve.

    Slicing an accountCurve (``acc['2020':]``) silently degrades it to a plain
    pandas Series, losing .sharpe()/.stats()/.percent.  This is the supported
    way back (see notebook 04).

    Slice *percentage* returns (``curve.percent.as_ts[...]``); with the
    default capital of 100 the rewrapped curve's ``.percent`` view then means
    the same thing as the original's.
    """

    from systems.accounts.from_returns import account_curve_from_returns

    return account_curve_from_returns(returns, capital=capital)


def stats_row(curve, name: str) -> dict:
    """One summary row for comparison tables (uses percentage returns)."""

    percent_curve = curve.percent
    return dict(
        name=name,
        ann_mean=round(percent_curve.ann_mean(), 2),
        ann_std=round(percent_curve.ann_std(), 2),
        sharpe=round(percent_curve.sharpe(), 3),
        skew=round(percent_curve.skew(), 2),
        avg_drawdown=round(percent_curve.avg_drawdown(), 2),
        worst_drawdown=round(percent_curve.worst_drawdown(), 2),
        t_stat=round(percent_curve.t_stat(), 2),
        p_value=round(percent_curve.p_value(), 4),
    )


def stats_table(named_curves: dict) -> pd.DataFrame:
    """Comparison table from {name: accountCurve}."""

    return pd.DataFrame(
        [stats_row(curve, name) for name, curve in named_curves.items()]
    ).set_index("name")
