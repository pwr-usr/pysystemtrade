"""Position-dependent curves must not reuse a truncated pandas repr as a key."""

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from sysobjects.instruments import instrumentCosts
from systems.accounts.accounts_stage import Account
from systems.system_cache import systemCache


@pytest.mark.parametrize("cost_method", ["cash", "SR"])
def test_curves_preserve_positions_that_differ_in_hidden_rows(monkeypatch, cost_method):
    index = pd.bdate_range("2020-01-01", periods=200)
    fixed = pd.Series(10.0, index=index)
    compounded = fixed.copy()
    compounded.iloc[50:150] = 20.0
    price = pd.Series(100.0 + np.arange(len(index)), index=index)
    constant = pd.Series(1.0, index=index)

    account = Account()
    parent = SimpleNamespace(
        config=SimpleNamespace(
            vol_normalise_currency_costs=False, multiply_roll_costs_by=0.5
        ),
        get_instrument_list=lambda: ["TEST"],
    )
    parent.cache = systemCache(parent)
    account.system_init(parent)
    inputs = {
        "get_instrument_prices_for_position_or_forecast": price,
        "get_fx_rate": constant,
        "get_value_of_block_price_move": 1.0,
        "get_daily_returns_volatility": constant,
        "get_notional_capital": 100_000.0,
        "get_raw_cost_data": instrumentCosts(price_slippage=0.1),
        "get_rolls_per_year": 0.0,
        "instrument_turnover": 1.0,
        "get_SR_cost_given_turnover": 0.01,
        "get_average_position_for_instrument_at_portfolio_level": fixed,
    }
    for name, value in inputs.items():
        monkeypatch.setattr(account, name, Mock(return_value=value))

    method = getattr(account, f"_pandl_for_instrument_with_{cost_method}_costs")
    with pd.option_context("display.max_rows", 10):
        assert str(fixed) == str(compounded)
        first = method("TEST", positions=fixed, delayfill=True, roundpositions=True)
        second = method("TEST", positions=compounded, delayfill=True, roundpositions=True)

    assert first is not second
    pd.testing.assert_series_equal(
        second.pandl_calculator_with_costs.positions, compounded.shift(1)
    )
    assert not first.as_ts.equals(second.as_ts)
