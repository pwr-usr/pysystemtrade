import numpy as np
import pandas as pd
import pytest

from systems.basesystem import System
from systems.rawdata import RawData
from sysdata.config.configdata import Config
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData
from systems.accounts.pandl_calculators.pandl_cash_costs import (
    pandlCalculationWithCashCostsAndFills,
)
from sysobjects.instruments import instrumentCosts


@pytest.fixture(scope="module")
def system():
    data = csvFuturesSimData()
    config = Config("systems.provided.example.exampleconfig.yaml")
    return System([RawData()], data, config)


def test_perpetual_detector_is_scoped_to_crypto_perpetual():
    data = csvFuturesSimData()

    assert data.is_perpetual_crypto_instrument("BTCUSDT")
    assert not data.is_perpetual_crypto_instrument("COPPER")


def test_zero_roll_kept_for_crypto_perpetual(system):
    carrydata = system.rawdata.get_instrument_raw_carry_data("BNBUSDT")
    zero_roll_index = (carrydata.price - carrydata.carry).loc[
        lambda s: s == 0
    ].index

    assert len(zero_roll_index) > 0

    raw_roll = system.rawdata.raw_futures_roll("BNBUSDT")
    common_index = raw_roll.index.intersection(zero_roll_index)

    assert len(common_index) > 0
    assert raw_roll.loc[common_index].notna().all()


def test_zero_roll_masked_for_non_crypto(system):
    carrydata = system.rawdata.get_instrument_raw_carry_data("COPPER")
    zero_roll_index = (carrydata.price - carrydata.carry).loc[
        lambda s: s == 0
    ].index

    assert len(zero_roll_index) > 0

    raw_roll = system.rawdata.raw_futures_roll("COPPER")
    common_index = raw_roll.index.intersection(zero_roll_index)

    assert len(common_index) > 0
    assert raw_roll.loc[common_index].isna().all()


# --- Funding rate tests ---


def test_funding_rate_nonempty_for_crypto(system):
    fr = system.rawdata.daily_funding_rate("BTCUSDT")
    assert len(fr) > 0
    assert fr.notna().any()


def test_funding_rate_empty_for_non_crypto(system):
    fr = system.rawdata.daily_funding_rate("COPPER")
    assert len(fr) == 0


def test_funding_rate_on_calendar_days(system):
    fr = system.rawdata.daily_funding_rate("BTCUSDT")
    diffs = pd.Series(fr.index).diff().dropna()
    median_gap = diffs.median()
    assert median_gap <= pd.Timedelta(days=1), (
        f"Funding rate should be on calendar days, median gap={median_gap}"
    )


def test_funding_rate_reasonable_magnitude(system):
    fr = system.rawdata.daily_funding_rate("BTCUSDT")
    ann_rate = fr.dropna().median() * 365.25
    assert abs(ann_rate) < 1.0, (
        f"Annualized funding rate {ann_rate:.4f} seems unreasonably large"
    )


def test_funding_rate_no_inf(system):
    fr = system.rawdata.daily_funding_rate("BTCUSDT")
    assert not np.isinf(fr).any(), "Funding rate contains inf values"


# --- P&L calculator funding cost tests ---


def _make_calculator(funding_rate=None):
    dates = pd.bdate_range("2024-01-02", periods=100, freq="B")
    price = pd.Series(100.0, index=dates)
    positions = pd.Series(1.0, index=dates)
    raw_costs = instrumentCosts(price_slippage=0.01)

    return pandlCalculationWithCashCostsAndFills(
        price,
        raw_costs=raw_costs,
        positions=positions,
        capital=100000.0,
        value_per_point=1.0,
        delayfill=False,
        fx=pd.Series(1.0, index=dates),
        roundpositions=False,
        vol_normalise_currency_costs=False,
        rolls_per_year=0,
        multiply_roll_costs_by=0.0,
        funding_rate=funding_rate,
    )


def test_funding_cost_sign_positive_rate_long_position():
    dates = pd.date_range("2024-01-02", periods=100, freq="1D")
    funding_rate = pd.Series(0.0001, index=dates)
    calc = _make_calculator(funding_rate=funding_rate)

    funding_costs = calc.funding_costs_in_instrument_currency()
    total = funding_costs.sum()
    assert total < 0, (
        f"Positive funding rate + long position should produce negative P&L impact, got {total}"
    )


def test_funding_cost_sign_negative_rate_long_position():
    dates = pd.date_range("2024-01-02", periods=100, freq="1D")
    funding_rate = pd.Series(-0.0001, index=dates)
    calc = _make_calculator(funding_rate=funding_rate)

    funding_costs = calc.funding_costs_in_instrument_currency()
    total = funding_costs.sum()
    assert total > 0, (
        f"Negative funding rate + long position should produce positive P&L impact, got {total}"
    )


def test_no_funding_cost_when_empty_series():
    calc = _make_calculator(funding_rate=pd.Series(dtype=float))
    funding_costs = calc.funding_costs_in_instrument_currency()
    assert len(funding_costs) == 0


def test_no_funding_cost_when_none():
    calc = _make_calculator(funding_rate=None)
    funding_costs = calc.funding_costs_in_instrument_currency()
    assert len(funding_costs) == 0


def test_funding_costs_included_in_total_costs():
    dates = pd.date_range("2024-01-02", periods=100, freq="1D")
    funding_rate = pd.Series(0.001, index=dates)

    calc_with = _make_calculator(funding_rate=funding_rate)
    calc_without = _make_calculator(funding_rate=pd.Series(dtype=float))

    costs_with = calc_with.costs_pandl_in_instrument_currency().sum()
    costs_without = calc_without.costs_pandl_in_instrument_currency().sum()

    assert costs_with < costs_without, (
        f"Costs with funding ({costs_with}) should be more negative than without ({costs_without})"
    )
