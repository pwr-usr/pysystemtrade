"""Reviewed history windows preserve missing intervals and native price types."""

from types import SimpleNamespace

import pandas as pd
import pytest

from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from syslogging.logger import get_logger


def test_windows_keep_internal_gap_and_include_evening_close():
    windows = pd.DataFrame(
        {
            "Instrument": ["X", "X"],
            "Start": ["2020-01-01", "2020-01-07"],
            "End": ["2020-01-03", "2020-01-09"],
        }
    )
    data = dbFuturesSimData(
        data=SimpleNamespace(log=get_logger("windows_test")), trading_windows=windows
    )
    prices = pd.Series(range(12), index=pd.date_range("2019-12-31 23:00", periods=12))
    result = data._prices_in_trading_windows(prices, "X")
    assert result.index.min() == pd.Timestamp("2020-01-01 23:00")
    assert result.index.max() == pd.Timestamp("2020-01-09 23:00")
    assert result.loc["2020-01-04":"2020-01-06"].isna().all()
    assert result.loc["2020-01-07":"2020-01-09"].notna().all()
    assert data._prices_in_trading_windows(prices, "ABSENT").empty
    assert prices.notna().all()


def test_default_history_unchanged_and_overlapping_windows_rejected():
    blob = SimpleNamespace(log=get_logger("windows_test"))
    data = dbFuturesSimData(data=blob)
    prices = pd.Series([10, 11], index=pd.date_range("2020-01-01", periods=2))
    pd.testing.assert_series_equal(data._prices_in_trading_windows(prices, "X"), prices)
    windows = pd.DataFrame(
        {
            "Instrument": ["X", "X"],
            "Start": ["2020-01-01", "2020-01-03"],
            "End": ["2020-01-03", "2020-01-05"],
        }
    )
    with pytest.raises(ValueError, match="overlap"):
        dbFuturesSimData(data=blob, trading_windows=windows)


def test_reviewed_empty_interval_excludes_instrument():
    windows = pd.DataFrame({"Instrument": ["X"], "Start": [None], "End": [None]})
    data = dbFuturesSimData(
        data=SimpleNamespace(log=get_logger("windows_test")), trading_windows=windows
    )
    assert data.get_trading_windows("X") == []
    prices = pd.Series([10.0], index=pd.to_datetime(["2020-01-01 23:00"]))
    assert data._prices_in_trading_windows(prices, "X").empty
