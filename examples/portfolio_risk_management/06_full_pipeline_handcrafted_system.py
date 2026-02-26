"""
Example 06 - Full Pipeline: Handcrafted System
===============================================

Builds a complete pysystemtrade System with handcrafted instrument weights:
  - 4 instruments (SOFR, US10, CORN, SP500_micro)
  - Estimated instrument weights using handcrafting
  - Extracts weights, IDM, positions, and PnL stats over time
  - Compares handcrafted vs equal weights

Key files:
  systems/portfolio.py
  systems/basesystem.py
  systems/provided/rules/ewmac.py
"""

import os
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

from sysdata.sim.csv_futures_sim_data import csvFuturesSimData
from sysdata.config.configdata import Config
from systems.basesystem import System
from systems.rawdata import RawData
from systems.forecasting import Rules
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecast_combine import ForecastCombine
from systems.positionsizing import PositionSizing
from systems.portfolio import Portfolios
from systems.accounts.accounts_stage import Account

data = csvFuturesSimData()

instruments = ["SOFR", "US10", "CORN", "SP500_micro"]

# ── 1. Build system with handcrafted weights ──────────────────────────────
print("=" * 70)
print("STEP 1: Build system with handcrafted instrument weights")
print("=" * 70)

handcraft_config = Config(
    dict(
        trading_rules=dict(
            ewmac8=dict(
                function="systems.provided.rules.ewmac.ewmac_forecast_with_defaults",
                other_args=dict(Lfast=8, Lslow=32),
            ),
            ewmac32=dict(
                function="systems.provided.rules.ewmac.ewmac_forecast_with_defaults",
                other_args=dict(Lfast=32, Lslow=128),
            ),
        ),
        instruments=instruments,
        forecast_scalars=dict(ewmac8=5.3, ewmac32=2.65),
        forecast_weights=dict(ewmac8=0.5, ewmac32=0.5),
        forecast_div_multiplier=1.1,
        percentage_vol_target=25.0,
        notional_trading_capital=500_000,
        base_currency="USD",
        # Handcrafted instrument weights (estimated)
        use_instrument_weight_estimates=True,
        instrument_weight_estimate=dict(
            method="handcraft",
            date_method="in_sample",
            equalise_SR=True,
        ),
        use_instrument_div_mult_estimates=True,
    )
)

print(f"\nInstruments: {instruments}")
print(f"Forecast rules: ewmac8, ewmac32 (equal weight)")
print(f"Instrument weights: estimated via handcrafting")
print(f"Vol target: 25%")

handcraft_system = System(
    [
        Account(),
        Portfolios(),
        PositionSizing(),
        ForecastCombine(),
        ForecastScaleCap(),
        Rules(),
        RawData(),
    ],
    data,
    handcraft_config,
)

# ── 2. Extract handcrafted weights and IDM ────────────────────────────────
print("\n" + "=" * 70)
print("STEP 2: Handcrafted instrument weights and IDM")
print("=" * 70)

hc_weights = handcraft_system.portfolio.get_instrument_weights()
hc_idm = handcraft_system.portfolio.get_instrument_diversification_multiplier()

print(f"\nInstrument weights (final values):")
print(hc_weights.tail(3).round(3).to_string())
print(f"\nIDM (final values):")
print(hc_idm.tail(3).round(3).to_string())

# ── 3. Build comparison system with equal weights ────────────────────────
print("\n" + "=" * 70)
print("STEP 3: Comparison - equal weights system")
print("=" * 70)

equal_config = Config(
    dict(
        trading_rules=dict(
            ewmac8=dict(
                function="systems.provided.rules.ewmac.ewmac_forecast_with_defaults",
                other_args=dict(Lfast=8, Lslow=32),
            ),
            ewmac32=dict(
                function="systems.provided.rules.ewmac.ewmac_forecast_with_defaults",
                other_args=dict(Lfast=32, Lslow=128),
            ),
        ),
        instruments=instruments,
        forecast_scalars=dict(ewmac8=5.3, ewmac32=2.65),
        forecast_weights=dict(ewmac8=0.5, ewmac32=0.5),
        forecast_div_multiplier=1.1,
        percentage_vol_target=25.0,
        notional_trading_capital=500_000,
        base_currency="USD",
        # Fixed equal weights
        use_instrument_weight_estimates=False,
        instrument_weights=dict(
            SOFR=0.25, US10=0.25, CORN=0.25, SP500_micro=0.25
        ),
        instrument_div_multiplier=1.5,
        use_instrument_div_mult_estimates=False,
    )
)

equal_system = System(
    [
        Account(),
        Portfolios(),
        PositionSizing(),
        ForecastCombine(),
        ForecastScaleCap(),
        Rules(),
        RawData(),
    ],
    data,
    equal_config,
)

eq_weights = equal_system.portfolio.get_instrument_weights()
print(f"\nEqual instrument weights:")
print(eq_weights.tail(3).round(3).to_string())

# ── 4. Compare positions ────────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 4: Position comparison (last row)")
print("=" * 70)

print(f"\n{'Instrument':<14} {'HC Position':>12} {'EQ Position':>12}")
print("-" * 40)
for inst in instruments:
    hc_pos = handcraft_system.portfolio.get_notional_position(inst).iloc[-1]
    eq_pos = equal_system.portfolio.get_notional_position(inst).iloc[-1]
    print(f"{inst:<14} {hc_pos:>12.1f} {eq_pos:>12.1f}")

# ── 5. Performance comparison ────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 5: Performance comparison")
print("=" * 70)

hc_account = handcraft_system.accounts.portfolio()
eq_account = equal_system.accounts.portfolio()

print("\nHandcrafted weights - portfolio stats:")
print(hc_account.percent.stats())

print("\nEqual weights - portfolio stats:")
print(eq_account.percent.stats())

# ── 6. Visualization ────────────────────────────────────────────────────
try:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Panel 1: Instrument weights over time (handcrafted)
    ax = axes[0][0]
    hc_weights.plot(ax=ax, linewidth=1)
    ax.set_title("Handcrafted Instrument Weights Over Time")
    ax.set_ylabel("Weight")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # Panel 2: IDM over time
    ax = axes[0][1]
    hc_idm.plot(ax=ax, linewidth=1, color="darkblue")
    ax.set_title("Instrument Diversification Multiplier")
    ax.set_ylabel("IDM")
    ax.grid(alpha=0.3)

    # Panel 3: Cumulative returns comparison
    ax = axes[1][0]
    hc_cum = hc_account.percent.cumsum()
    eq_cum = eq_account.percent.cumsum()
    hc_cum.plot(ax=ax, label="Handcrafted", linewidth=1)
    eq_cum.plot(ax=ax, label="Equal Weights", linewidth=1)
    ax.set_title("Cumulative Returns (%)")
    ax.set_ylabel("Cumulative Return %")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # Panel 4: Rolling 1-year Sharpe
    ax = axes[1][1]
    window = 256
    hc_rolling_sr = (
        hc_account.percent.rolling(window).mean()
        / hc_account.percent.rolling(window).std()
    ) * (256 ** 0.5)
    eq_rolling_sr = (
        eq_account.percent.rolling(window).mean()
        / eq_account.percent.rolling(window).std()
    ) * (256 ** 0.5)
    hc_rolling_sr.plot(ax=ax, label="Handcrafted", linewidth=0.8)
    eq_rolling_sr.plot(ax=ax, label="Equal Weights", linewidth=0.8)
    ax.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    ax.set_title("Rolling 1-Year Sharpe Ratio")
    ax.set_ylabel("Sharpe Ratio")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    plt.suptitle("Handcrafted vs Equal Weight Portfolio", fontsize=13)
    plt.tight_layout()
    _plot_path = os.path.join(_SCRIPT_DIR, "06_full_pipeline.png")
    plt.savefig(_plot_path, dpi=150)
    print(f"\nPlot saved to {_plot_path}")
    plt.close()
except ImportError:
    print("\nmatplotlib not installed - skipping visualization")

print("\n" + "=" * 70)
print("KEY TAKEAWAYS")
print("=" * 70)
print("""
1. The full pipeline: data -> rules -> forecast -> vol_target -> weights -> position
2. Handcrafted weights adapt to the correlation structure of instruments
3. IDM compensates for diversification: with 4 instruments, IDM is typically 1.2-1.8
4. Estimated weights change over time as rolling correlations update
5. Performance differences between handcrafted and equal weights are often small
   - this is expected! Handcrafting is about robustness, not alpha generation
""")
