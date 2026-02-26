"""
Example 04 - Vol Targeting & Position Sizing
=============================================

Walks through the full position sizing chain using real CSV data:
  percentage_vol_target
    -> daily_cash_vol_target
    -> instrument_value_vol
    -> vol_scalar
    -> subsystem_position

Then compares manual calculation to the System pipeline output.

Key files:
  systems/positionsizing.py
  sysdata/sim/csv_futures_sim_data.py
"""

import os
import numpy as np
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
from systems.provided.rules.ewmac import ewmac_forecast_with_defaults as ewmac

# ── 1. Set up data and system config ──────────────────────────────────────
data = csvFuturesSimData()

instruments = ["SOFR", "US10", "CORN", "SP500_micro"]
print("=" * 70)
print("STEP 1: Available instruments and configuration")
print("=" * 70)
print(f"Instruments: {instruments}")

# Configuration
PERCENTAGE_VOL_TARGET = 25.0  # annual % vol target
NOTIONAL_CAPITAL = 500_000    # USD
BASE_CURRENCY = "USD"

print(f"\nVol target: {PERCENTAGE_VOL_TARGET}%")
print(f"Notional capital: ${NOTIONAL_CAPITAL:,.0f}")
print(f"Base currency: {BASE_CURRENCY}")

# ── 2. Manual position sizing chain ──────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 2: Manual position sizing walkthrough (for SOFR)")
print("=" * 70)

instrument = "SOFR"

# Step 2a: Daily cash vol target
daily_cash_vol_target = (NOTIONAL_CAPITAL * PERCENTAGE_VOL_TARGET / 100) / (256 ** 0.5)
print(f"\n  a) Daily cash vol target = capital * %vol / sqrt(256)")
print(f"     = ${NOTIONAL_CAPITAL:,.0f} * {PERCENTAGE_VOL_TARGET}% / {256**0.5:.1f}")
print(f"     = ${daily_cash_vol_target:,.2f} per day")

# Step 2b: Get price and vol data
prices = data.daily_prices(instrument)
fx_rate = data.get_fx_for_instrument(instrument, BASE_CURRENCY)
block_value = data.get_value_of_block_price_move(instrument)  # point value

# Price vol: simplified approximation using EWMA of price changes.
# The actual pipeline (RawData stage) uses a more sophisticated mixed_vol_calc
# that blends EWMA and expanding window estimates, and converts via percentage
# returns. This simplified version illustrates the *concept* of vol estimation.
price_returns = prices.diff()
price_vol = price_returns.ewm(span=35).std()

# Latest values
latest_price = prices.iloc[-1]
latest_vol = price_vol.iloc[-1]
latest_fx = fx_rate.iloc[-1]

print(f"\n  b) Instrument: {instrument}")
print(f"     Latest price: {latest_price:.2f}")
print(f"     Block value (point value): {block_value}")
print(f"     Price vol (35d EWMA): {latest_vol:.4f}")
print(f"     FX rate ({BASE_CURRENCY}): {latest_fx:.4f}")

# Step 2c: Instrument value vol (in base currency)
instrument_value_vol = latest_vol * block_value * latest_fx
print(f"\n  c) Instrument value vol = price_vol * block_value * fx")
print(f"     = {latest_vol:.4f} * {block_value} * {latest_fx:.4f}")
print(f"     = ${instrument_value_vol:,.2f} per contract per day")

# Step 2d: Vol scalar (how many contracts for 1 unit of forecast)
vol_scalar = daily_cash_vol_target / instrument_value_vol
print(f"\n  d) Vol scalar = daily_cash_vol_target / instrument_value_vol")
print(f"     = ${daily_cash_vol_target:,.2f} / ${instrument_value_vol:,.2f}")
print(f"     = {vol_scalar:.2f} contracts (per unit of forecast)")

# Step 2e: Subsystem position = vol_scalar * combined_forecast / 10
# (forecast is scaled to average absolute value of 10)
print(f"\n  e) Subsystem position = vol_scalar * combined_forecast / 10")
print(f"     If forecast = +10 (maximum conviction long): position = {vol_scalar * 10 / 10:.1f} contracts")
print(f"     If forecast = +5  (moderate conviction):     position = {vol_scalar * 5 / 10:.1f} contracts")
print(f"     If forecast = -10 (maximum conviction short): position = {vol_scalar * -10 / 10:.1f} contracts")

# ── 3. System pipeline comparison ─────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 3: System pipeline output (for comparison)")
print("=" * 70)

my_config = Config(
    dict(
        trading_rules=dict(ewmac=ewmac),
        forecast_scalars=dict(ewmac=5.7),
        forecast_weights=dict(ewmac=1.0),
        forecast_div_multiplier=1.0,
        percentage_vol_target=PERCENTAGE_VOL_TARGET,
        notional_trading_capital=NOTIONAL_CAPITAL,
        base_currency=BASE_CURRENCY,
        instruments=instruments,
    )
)

my_system = System(
    [
        Rules(),
        ForecastScaleCap(),
        ForecastCombine(),
        PositionSizing(),
        RawData(),
    ],
    data,
    my_config,
)

# Extract pipeline values
vol_target_dict = my_system.positionSize.get_vol_target_dict()
print(f"\nVol target dict: {vol_target_dict}")

sys_price_vol = my_system.positionSize.get_price_volatility(instrument)
sys_inst_value_vol = my_system.positionSize.get_instrument_value_vol(instrument)
sys_vol_scalar = my_system.positionSize.get_average_position_at_subsystem_level(instrument)
sys_position = my_system.positionSize.get_subsystem_position(instrument)

print(f"\nPipeline for {instrument} (latest values):")
print(f"  Price vol:           {sys_price_vol.iloc[-1]:.4f}")
print(f"  Instrument value vol:{sys_inst_value_vol.iloc[-1]:,.2f}")
print(f"  Vol scalar:          {sys_vol_scalar.iloc[-1]:.2f}")
print(f"  Subsystem position:  {sys_position.iloc[-1]:.2f}")

# ── 4. Cross-instrument comparison ───────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 4: Position sizing across all instruments")
print("=" * 70)

print(f"\n{'Instrument':<14} {'Price':>10} {'PriceVol':>10} {'InstValVol':>12} {'VolScalar':>10} {'Position':>10}")
print("-" * 68)

for inst in instruments:
    p = data.daily_prices(inst).iloc[-1]
    pvol = my_system.positionSize.get_price_volatility(inst).iloc[-1]
    ivv = my_system.positionSize.get_instrument_value_vol(inst).iloc[-1]
    vs = my_system.positionSize.get_average_position_at_subsystem_level(inst).iloc[-1]
    pos = my_system.positionSize.get_subsystem_position(inst).iloc[-1]
    print(f"{inst:<14} {p:>10.2f} {pvol:>10.4f} {ivv:>12,.0f} {vs:>10.1f} {pos:>10.1f}")

# ── 5. Visualization ────────────────────────────────────────────────────
try:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    for idx, inst in enumerate(instruments):
        ax = axes[idx // 2][idx % 2]

        position = my_system.positionSize.get_subsystem_position(inst)
        position.plot(ax=ax, linewidth=0.8)
        ax.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
        ax.set_title(f"{inst}: Subsystem Position (vol-targeted)")
        ax.set_ylabel("Contracts")
        ax.grid(alpha=0.3)

    plt.suptitle(
        f"Vol-Targeted Positions ({PERCENTAGE_VOL_TARGET}% target, "
        f"${NOTIONAL_CAPITAL:,.0f} capital)",
        fontsize=13,
    )
    plt.tight_layout()
    _plot_path = os.path.join(_SCRIPT_DIR, "04_vol_targeting.png")
    plt.savefig(_plot_path, dpi=150)
    print(f"\nPlot saved to {_plot_path}")
    plt.close()
except ImportError:
    print("\nmatplotlib not installed - skipping visualization")

print("\n" + "=" * 70)
print("KEY TAKEAWAYS")
print("=" * 70)
print(f"""
1. Vol targeting normalises risk across instruments with different volatilities
2. The chain: %vol_target -> cash_vol_target -> vol_scalar -> position
3. Vol scalar = how many contracts give you target risk per unit of forecast
4. Higher instrument volatility -> fewer contracts to hit same risk target
5. Forecast of 10 = full position = vol_scalar contracts
6. All positions automatically adjust as volatility changes over time
""")
