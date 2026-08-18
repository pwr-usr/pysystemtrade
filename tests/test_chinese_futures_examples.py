import json
from pathlib import Path

import pandas as pd
import pytest

from examples.chinese_futures import research as R
from examples.chinese_futures.carry_donchian_lab import system as carry_lab
from sysdata.config.configdata import Config
from systems.accounts.pandl_calculators.pandl_using_fills import (
    pandlCalculationWithFills,
)
from systems.portfolio import Portfolios


HERE = Path(__file__).resolve().parents[1] / "examples/chinese_futures"


def test_universe_is_exactly_the_stitchable_manifest_set():
    expected = sorted(
        mapping.instrument_code
        for mapping in R.TushareInstrumentManifest.from_csv().mappings
        if mapping.is_stitchable
    )

    class FakeData:
        def get_instrument_list(self):
            return expected + ["NOT_TUSHARE"]

    assert R.chinese_universe(FakeData()) == expected
    assert len(expected) == 95


def test_universe_rejects_a_missing_stitchable_instrument():
    expected = R.chinese_universe(
        type(
            "CompleteData",
            (),
            {
                "get_instrument_list": lambda self: [
                    mapping.instrument_code
                    for mapping in R.TushareInstrumentManifest.from_csv().mappings
                    if mapping.is_stitchable
                ]
            },
        )()
    )

    class IncompleteData:
        def get_instrument_list(self):
            return expected[1:]

    with pytest.raises(ValueError, match=expected[0]):
        R.chinese_universe(IncompleteData())


def test_missing_volume_on_an_observed_price_row_becomes_zero():
    dates = pd.to_datetime(["2024-01-02 23:00", "2024-01-03 23:00", "2024-01-04 23:00"])

    class FakeData:
        def get_multiple_prices(self, instrument_code):
            return pd.DataFrame(
                {
                    "PRICE": [100.0, 101.0, 102.0],
                    "PRICE_CONTRACT": ["20240500"] * 3,
                },
                index=dates,
            )

    class FakeStore:
        def get_prices_at_frequency_for_contract_object(self, *args, **kwargs):
            return pd.DataFrame({"VOLUME": [200.0, 220.0]}, index=dates[[0, 2]])

    result = R._held_contract_volume(FakeData(), "TEST", FakeStore())

    assert result.tolist() == [200.0, 0.0, 220.0]


def test_liquidity_rule_is_causal_and_has_hysteresis_and_reentry():
    dates = pd.bdate_range("2024-01-02", periods=7)
    volumes = pd.DataFrame(
        {"TEST": [130.0, 130.0, 100.0, 60.0, 100.0, 140.0, 140.0]},
        index=dates,
    )

    eligibility = R.liquidity_eligibility(
        volumes, lookback=1, force_terminal_close=False
    )

    assert eligibility["TEST"].tolist() == [True, True, True, False, False, True, True]

    prefix = R.liquidity_eligibility(
        volumes.iloc[:5], lookback=1, force_terminal_close=False
    )
    pd.testing.assert_series_equal(
        prefix["TEST"], eligibility.loc[prefix.index, "TEST"]
    )


def test_exchange_holiday_does_not_count_as_zero_volume():
    volumes = pd.DataFrame(
        {"TEST": [150.0, 150.0]},
        index=pd.to_datetime(["2024-01-05", "2024-01-09"]),
    )

    eligibility = R.liquidity_eligibility(
        volumes, lookback=2, force_terminal_close=False
    )

    assert not bool(eligibility.loc[pd.Timestamp("2024-01-08"), "TEST"])
    assert bool(eligibility.loc[pd.Timestamp("2024-01-09"), "TEST"])


def test_terminal_target_closes_on_final_delayed_fill_and_keeps_final_move():
    observed_dates = pd.to_datetime(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-09"]
    )
    volumes = pd.DataFrame({"CZCE_JR": [150.0] * 5}, index=observed_dates)
    eligibility = R.liquidity_eligibility(
        volumes, lookback=1, force_terminal_close=True
    )
    target = eligibility["CZCE_JR"].astype(float)
    prices = pd.Series([100.0, 101.0, 102.0, 103.0, 105.0], index=observed_dates)

    calculator = pandlCalculationWithFills(
        prices, positions=target, delayfill=True, roundpositions=True
    )
    actual_position = calculator.positions
    final_fills = [fill for fill in calculator.fills if fill.date == observed_dates[-1]]

    assert target.loc[pd.Timestamp("2024-01-05")] == 1.0
    assert target.loc[pd.Timestamp("2024-01-08")] == 0.0
    assert actual_position.iloc[-1] == 0.0
    assert any(fill.qty == -1.0 for fill in final_fills)
    assert calculator.pandl_in_points().iloc[-1] == 2.0

    events = R.liquidity_event_table(
        eligibility, volumes, lookback=1, exit_volume=R.LIQUIDITY_EXIT
    )
    terminal_event = events[events["terminal_assumption"]].iloc[0]
    assert terminal_event["date"] == pd.Timestamp("2024-01-08")
    assert terminal_event["event"] == "forced terminal exit"


def test_final_volume_drop_still_closes_on_the_last_quote():
    dates = pd.bdate_range("2024-01-02", periods=3)
    volumes = pd.DataFrame({"CZCE_JR": [150.0, 150.0, 0.0]}, index=dates)
    prices = pd.Series([100.0, 101.0, 102.0], index=dates)

    eligibility = R.liquidity_eligibility(
        volumes, lookback=1, force_terminal_close=True
    )
    calculator = pandlCalculationWithFills(
        prices,
        positions=eligibility["CZCE_JR"].astype(float),
        delayfill=True,
        roundpositions=True,
    )

    assert eligibility.loc[dates[-2], "CZCE_JR"] == 0.0
    assert calculator.positions.iloc[-1] == 0.0
    assert any(fill.date == dates[-1] and fill.qty == -1.0 for fill in calculator.fills)


def test_point_in_time_portfolio_masks_and_normalises_native_weights(monkeypatch):
    dates = pd.bdate_range("2024-01-02", periods=3)
    native = pd.DataFrame(
        {"A": [0.25, 0.25, 0.25], "B": [0.75, 0.75, 0.75]}, index=dates
    )
    eligibility = pd.DataFrame(
        {"A": [True, True, False], "B": [False, True, False]}, index=dates
    )
    monkeypatch.setattr(Portfolios, "get_instrument_weights", lambda self: native)

    result = R.PointInTimePortfolios(eligibility).get_instrument_weights()

    expected = pd.DataFrame({"A": [1.0, 0.25, 0.0], "B": [0.0, 0.75, 0.0]}, index=dates)
    pd.testing.assert_frame_equal(result, expected)


def test_fixed_daily_weights_are_identical_after_the_same_gate():
    dates = pd.bdate_range("2024-01-02", periods=2)
    eligibility = pd.DataFrame({"A": [True, False], "B": [True, True]}, index=dates)
    fixed = R.equal_weight_panel(eligibility)

    first = R.PointInTimePortfolios(eligibility, fixed).get_instrument_weights()
    second = R.PointInTimePortfolios(eligibility, fixed).get_instrument_weights()

    pd.testing.assert_frame_equal(first, second)
    pd.testing.assert_series_equal(first.sum(axis=1), pd.Series(1.0, index=dates))


def test_cumulative_plot_has_a_zero_anchor_and_keeps_every_return():
    returns = pd.Series([2.0, 0.0, -1.0], index=pd.bdate_range("2024-01-02", periods=3))

    cumulative = R.cumulative_from_zero(returns)

    assert cumulative.iloc[0] == 0.0
    assert cumulative.iloc[-1] == returns.sum()
    assert len(cumulative) == len(returns) + 1


def test_chinese_futures_tree_has_one_numbered_series():
    expected = {
        "01_data_and_universe.ipynb",
        "02_rolls_and_stitching.ipynb",
        "03_backtest_basics.ipynb",
        "04_weights_and_pooling.ipynb",
        "05_carry_and_trend.ipynb",
        "06_rule_families.ipynb",
        "07_single_instrument_tail_risk.ipynb",
    }
    assert {path.name for path in HERE.glob("[0-9][0-9]_*.ipynb")} == expected

    removed = (
        "own_research",
        "china_multi_rule_research",
        "mongodb_api.ipynb",
        "carry_shared_capital.py",
        "test_carry_shared_capital.py",
        "21_carry_one_x_notional_exposure.ipynb",
        "21_carry_shared_capital_inverse_volatility.ipynb",
    )
    assert not [name for name in removed if (HERE / name).exists()]


def test_carry_donchian_lab_binary_is_fixed_causal_and_prefix_invariant():
    dates = pd.date_range("2024-01-01", periods=8, freq="D")
    prices = pd.Series(
        [10.0, float("nan"), 9.0, 8.0, 11.0, 10.0, float("nan"), 7.0],
        index=dates,
    )

    result = carry_lab.persistent_binary_donchian(prices, lookback=3)
    prefix = carry_lab.persistent_binary_donchian(prices.iloc[:7], lookback=3)

    assert result.iloc[:4].isna().all()
    assert result.loc[dates[4]] == 1.0
    assert result.loc[dates[5]] == 1.0
    assert result.loc[dates[6]] == 1.0
    assert result.loc[dates[7]] == -1.0
    pd.testing.assert_series_equal(prefix, result.loc[prefix.index])


def test_carry_donchian_lab_continuous_warmup_is_prefix_invariant():
    dates = pd.bdate_range("2024-01-02", periods=100)
    prices = pd.Series(
        100.0 + pd.Series(range(100), index=dates) / 10.0,
        index=dates,
    )

    full = carry_lab.causal_breakout(prices, lookback=80)
    prefix = carry_lab.causal_breakout(prices.iloc[:55], lookback=80)

    pd.testing.assert_series_equal(prefix, full.loc[prefix.index])
    assert full.iloc[:39].isna().all()


def test_carry_donchian_lab_factory_expands_only_positive_sleeve_weights(
    monkeypatch,
):
    config = Config(str(carry_lab.CONFIG_PATH))
    instrument = next(
        item.instrument_code
        for item in R.TushareInstrumentManifest.from_csv().mappings
        if item.is_stitchable
    )
    data = type(
        "OneChineseInstrument",
        (),
        {"get_instrument_list": lambda self: [instrument]},
    )()
    dates = pd.bdate_range("2024-01-02", periods=2)
    eligibility = pd.DataFrame({instrument: True}, index=dates)
    fixed_weights = R.equal_weight_panel(eligibility)
    monkeypatch.setattr(
        carry_lab,
        "System",
        lambda stages, supplied_data, local_config: local_config,
    )

    mixed = carry_lab.futures_system(
        data=data,
        config=config,
        eligibility=eligibility,
        fixed_weights=fixed_weights,
        carry_weight=0.7,
        trend_kind="continuous",
    )
    pure_trend = carry_lab.futures_system(
        data=data,
        config=config,
        eligibility=eligibility,
        fixed_weights=fixed_weights,
        carry_weight=0.0,
        trend_kind="continuous",
        trend_rules=["breakout80"],
    )

    assert sum(mixed.forecast_weights.values()) == pytest.approx(1.0)
    assert sum(
        mixed.forecast_weights[rule] for rule in carry_lab.CARRY_RULES
    ) == pytest.approx(0.7)
    assert sum(
        mixed.forecast_weights[rule] for rule in carry_lab.CONTINUOUS_RULES
    ) == pytest.approx(0.3)
    assert pure_trend.forecast_weights == {"breakout80": 1.0}

    with pytest.raises(ValueError, match="between zero and one"):
        carry_lab.futures_system(
            data=data,
            config=config,
            eligibility=eligibility,
            fixed_weights=fixed_weights,
            carry_weight=1.01,
        )


def test_carry_donchian_lab_is_compact_and_uses_the_reviewed_config():
    project = HERE / "carry_donchian_lab"
    notebook = json.loads((project / "research.ipynb").read_text(encoding="utf-8"))

    assert {path.name for path in project.iterdir() if path.is_file()} == {
        "config.yaml",
        "research.ipynb",
        "system.py",
    }
    assert len(notebook["cells"]) == 17

    config = Config(str(project / "config.yaml"))
    assert config.percentage_vol_target == 16.0
    assert config.buffer_size == 0.10
    assert config.volatility_calculation["backfill"] is False


def test_full_backtesting_tutorial_is_intact():
    path = HERE / "backtesting_tutorial/backtesting_with_chinese_futures.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))

    assert len(notebook["cells"]) == 108
    assert (path.parent / "config.yaml").is_file()
    assert (path.parent / "system.py").is_file()
