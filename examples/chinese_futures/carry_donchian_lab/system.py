"""Small native carry/Donchian system used by the adjacent research notebook."""

from pathlib import Path

import numpy as np
import pandas as pd

from examples.chinese_futures import research as R
from syscore.constants import arg_not_supplied
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
from systems.provided.rules.breakout import breakout
from systems.rawdata import RawData

CONFIG_PATH = Path(__file__).with_name("config.yaml")

CARRY_RULES = ("carry10", "carry30", "carry60", "carry125")
CONTINUOUS_RULES = ("breakout40", "breakout80", "breakout160")
BINARY_RULES = ("binary40", "binary80", "binary160")
TREND_RULES = {
    "continuous": CONTINUOUS_RULES,
    "binary": BINARY_RULES,
}

DEFAULT_CARRY_WEIGHT = 0.70
DEFAULT_TREND_KIND = "continuous"
DEFAULT_DATA_CUTOFF = "2026-07-27"

LIQUIDITY_LOOKBACK = 20
LIQUIDITY_ENTRY = 130.0
LIQUIDITY_EXIT = 70.0


def causal_breakout(price, lookback=80):
    """Native continuous breakout with an explicit prefix-safe warm-up."""

    forecast = breakout(price, lookback=lookback)
    observed = pd.Series(price).notna().cumsum()
    return forecast.where(observed >= int(np.ceil(lookback / 2)))


def persistent_binary_donchian(price, lookback=80):
    """Fixed +/-1 state using the previous ``lookback`` observed closes."""

    original = pd.Series(price, copy=True).astype(float)
    observed = original.dropna()
    prior = observed.shift(1).rolling(int(lookback), min_periods=int(lookback))
    prior_high, prior_low = prior.max(), prior.min()

    event = pd.Series(np.nan, index=observed.index)
    event.loc[observed > prior_high] = 1.0
    event.loc[observed < prior_low] = -1.0
    return event.ffill().reindex(original.index).ffill()


class CutoffChinaData(dbFuturesSimData):
    """Reviewed Chinese DB universe with one inclusive research cutoff."""

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
            prices = self.db_futures_adjusted_prices_data.get_adjusted_prices(
                instrument
            ).dropna()
            if len(prices) and prices.index[0] <= self.cutoff:
                listed.append(instrument)
        self._instruments = tuple(listed)

    def get_instrument_list(self):
        return list(self._instruments)

    def _check(self, instrument_code):
        if instrument_code not in self._instruments:
            raise ValueError(f"{instrument_code} is outside this cutoff universe")

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


def futures_system(
    data=arg_not_supplied,
    config=arg_not_supplied,
    eligibility=arg_not_supplied,
    fixed_weights=arg_not_supplied,
    carry_weight=arg_not_supplied,
    trend_kind=arg_not_supplied,
    trend_rules=arg_not_supplied,
) -> System:
    """Build one complete native system; only style and carry weight vary."""

    if config is arg_not_supplied:
        config = Config(str(CONFIG_PATH))
    if data is arg_not_supplied:
        data = CutoffChinaData(DEFAULT_DATA_CUTOFF)
    if carry_weight is arg_not_supplied:
        carry_weight = DEFAULT_CARRY_WEIGHT
    if trend_kind is arg_not_supplied:
        trend_kind = DEFAULT_TREND_KIND
    if trend_kind not in TREND_RULES:
        raise ValueError("trend_kind must be continuous or binary")
    carry_weight = float(carry_weight)
    if not 0.0 <= carry_weight <= 1.0:
        raise ValueError("carry_weight must be between zero and one")

    carry_rules = CARRY_RULES
    if trend_rules is arg_not_supplied:
        trend_rules = TREND_RULES[trend_kind]
    else:
        trend_rules = tuple(trend_rules)
    unknown = sorted(set(trend_rules) - set(TREND_RULES[trend_kind]))
    if unknown:
        raise ValueError(f"Rules do not belong to {trend_kind}: {unknown}")

    local = Config(config)
    weights = {}
    if carry_weight > 0.0:
        weights.update(
            {name: float(carry_weight) / len(carry_rules) for name in carry_rules}
        )
    if carry_weight < 1.0:
        weights.update(
            {
                name: (1.0 - float(carry_weight)) / len(trend_rules)
                for name in trend_rules
            }
        )
    local.forecast_weights = weights

    manifest = R.TushareInstrumentManifest.from_csv()
    reviewed = {
        item.instrument_code for item in manifest.mappings if item.is_stitchable
    }
    instruments = sorted(reviewed.intersection(data.get_instrument_list()))
    if not instruments:
        raise ValueError("No reviewed Chinese instruments are available")
    local.instruments = instruments
    if eligibility is arg_not_supplied:
        volumes = R.held_contract_volumes(data, instruments)
        eligibility = R.liquidity_eligibility(
            volumes,
            lookback=LIQUIDITY_LOOKBACK,
            entry_volume=LIQUIDITY_ENTRY,
            exit_volume=LIQUIDITY_EXIT,
            force_terminal_close=True,
        )
    eligibility = eligibility.reindex(columns=instruments, fill_value=False)
    if fixed_weights is arg_not_supplied:
        fixed_weights = R.equal_weight_panel(eligibility)
    portfolio = R.PointInTimePortfolios(eligibility, fixed_weights)

    return System(
        [
            Account(),
            portfolio,
            PositionSizing(),
            RawData(),
            ForecastCombine(),
            ForecastScaleCap(),
            Rules(),
        ],
        data,
        local,
    )
