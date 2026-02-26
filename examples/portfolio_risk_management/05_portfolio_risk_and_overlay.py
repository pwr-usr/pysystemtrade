"""
Example 05 - Portfolio Risk Calculation & Risk Overlay
======================================================

Demonstrates:
  - Calculating portfolio risk: sqrt(w' * Sigma * w) via portfolioWeights.portfolio_stdev()
  - The 4-component risk overlay from systems/risk_overlay.py
  - How overlay takes min() across: normal risk, shocked vol, sum-abs risk, leverage

Key files:
  sysquant/portfolio_risk.py
  systems/risk_overlay.py
  sysquant/optimisation/weights.py
"""

import os
import sys
import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from _synthetic_data import generate_weekly_returns, ASSET_NAMES, TARGET_CORR

from sysquant.estimators.correlations import correlationEstimate
from sysquant.optimisation.weights import portfolioWeights
from systems.risk_overlay import get_risk_multiplier, multiplier_given_series_and_limit

# ── 1. Portfolio risk calculation ─────────────────────────────────────────
print("=" * 70)
print("STEP 1: Portfolio risk = sqrt(w' * C * w)")
print("=" * 70)

corr = correlationEstimate(TARGET_CORR, columns=ASSET_NAMES)

# Equal weights
weights_equal = portfolioWeights(
    [(name, 0.2) for name in ASSET_NAMES]
)

risk_equal = weights_equal.portfolio_stdev(corr)
print(f"\nEqual weights (20% each):")
print(f"  Portfolio risk (vol-weighted) = {risk_equal:.4f}")
print(f"  IDM = 1/risk = {1/risk_equal:.3f}")

# Concentrated in equities
weights_equity = portfolioWeights(
    [("US_Equity", 0.40), ("EU_Equity", 0.35), ("US_Bond", 0.05),
     ("Gold", 0.10), ("Commodity", 0.10)]
)
risk_equity = weights_equity.portfolio_stdev(corr)
print(f"\nEquity-heavy weights:")
print(f"  {dict((k, v) for k, v in weights_equity.items())}")
print(f"  Portfolio risk = {risk_equity:.4f}")
print(f"  IDM = {1/risk_equity:.3f}")

# Bond-heavy (should be lower risk due to negative correlations)
weights_bond = portfolioWeights(
    [("US_Equity", 0.15), ("EU_Equity", 0.10), ("US_Bond", 0.50),
     ("Gold", 0.15), ("Commodity", 0.10)]
)
risk_bond = weights_bond.portfolio_stdev(corr)
print(f"\nBond-heavy weights:")
print(f"  {dict((k, v) for k, v in weights_bond.items())}")
print(f"  Portfolio risk = {risk_bond:.4f}")
print(f"  IDM = {1/risk_bond:.3f}")

print(f"""
Note: these are 'risk weights' (vol-normalised). A risk weight of 0.2
means that asset contributes 20% of total risk budget. The portfolio_stdev
gives the fraction of total risk actually achieved (< 1.0 due to diversification).
""")

# ── 2. Risk decomposition ────────────────────────────────────────────────
print("=" * 70)
print("STEP 2: Risk decomposition (marginal contribution)")
print("=" * 70)

w = np.array([weights_equal[n] for n in ASSET_NAMES])
C = TARGET_CORR

# Marginal contribution to risk
Cw = C @ w
port_var = w @ C @ w
mcr = Cw / np.sqrt(port_var)  # marginal contribution to risk
cr = w * mcr  # contribution to risk

print(f"\n{'Asset':<14} {'Weight':>8} {'MCR':>8} {'Risk Contrib':>13} {'% of Risk':>10}")
print("-" * 55)
for i, name in enumerate(ASSET_NAMES):
    print(f"{name:<14} {w[i]:>8.3f} {mcr[i]:>8.4f} {cr[i]:>13.4f} {cr[i]/risk_equal*100:>9.1f}%")
print(f"{'TOTAL':<14} {sum(w):>8.3f} {'':>8} {sum(cr):>13.4f} {sum(cr)/risk_equal*100:>9.1f}%")

# ── 3. Risk overlay demonstration ───────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 3: Risk overlay - the 4 components")
print("=" * 70)
print("""
The risk overlay calculates a multiplier between 0 and 1 that scales
ALL positions proportionally. It takes the MINIMUM across 4 checks:

  1. Normal risk:      Is portfolio risk within limits? (e.g. < 2x target)
  2. Shocked vol:      What if vols jump to their 99th percentile?
  3. Sum-abs risk:     Are positions too large regardless of correlation?
  4. Leverage:         Is total leverage within limits?

Each component: multiplier = min(1, risk_limit / actual_risk)
""")

# Create synthetic risk series (200 business days)
dates = pd.date_range("2024-01-01", periods=200, freq="B")
pct_vol_target = 25.0

# Simulate a scenario where risks gradually increase
np.random.seed(42)
base_risk = 0.20  # starts at 20% annualised (below 25% target)

normal_risk = pd.Series(
    base_risk + np.cumsum(np.random.normal(0, 0.002, 200)),
    index=dates,
)
normal_risk = normal_risk.clip(0.05, 0.60)

shocked_vol_risk = normal_risk * 1.5  # shocked vol is 50% higher
sum_abs_risk = normal_risk * 1.2 + 0.05  # sum-abs is more conservative
leverage = pd.Series(
    np.linspace(1.5, 3.5, 200) + np.random.normal(0, 0.1, 200),
    index=dates,
)
leverage = leverage.clip(0.5, 5.0)

# Example overlay config (loosely based on pysystemtrade defaults;
# see systems/provided/rob_system/config.yaml for actual production values)
risk_overlay_config = dict(
    max_risk_fraction_normal_risk=2.0,
    max_risk_fraction_stdev_risk=2.0 * 1.4,
    max_risk_limit_sum_abs_risk=5.4,
    max_risk_leverage=6.0,
)

print("Risk overlay configuration:")
for k, v in risk_overlay_config.items():
    print(f"  {k}: {v}")

# Calculate the combined multiplier
joint_mult = get_risk_multiplier(
    risk_overlay_config=risk_overlay_config,
    normal_risk=normal_risk,
    shocked_vol_risk=shocked_vol_risk,
    sum_abs_risk=sum_abs_risk,
    leverage=leverage,
    percentage_vol_target=pct_vol_target,
)

# Show some key dates
print(f"\nRisk limits (derived from config * vol_target):")
print(f"  Normal risk limit:     {risk_overlay_config['max_risk_fraction_normal_risk'] * pct_vol_target / 100:.2%}")
print(f"  Shocked vol limit:     {risk_overlay_config['max_risk_fraction_stdev_risk'] * pct_vol_target / 100:.2%}")
print(f"  Sum-abs risk limit:    {risk_overlay_config['max_risk_limit_sum_abs_risk'] * pct_vol_target / 100:.2%}")
print(f"  Max leverage:          {risk_overlay_config['max_risk_leverage']:.1f}x")

print(f"\nSample dates:")
for d in [dates[0], dates[50], dates[100], dates[150], dates[-1]]:
    nr = normal_risk.loc[d]
    sv = shocked_vol_risk.loc[d]
    sa = sum_abs_risk.loc[d]
    lv = leverage.loc[d]
    m = joint_mult.loc[d]
    print(f"  {d.date()}: normal={nr:.3f} shocked={sv:.3f} "
          f"sum_abs={sa:.3f} lev={lv:.1f}x -> mult={m:.3f}")

# ── 4. Individual multiplier components ──────────────────────────────────
print("\n" + "=" * 70)
print("STEP 4: How each component multiplier works")
print("=" * 70)

risk_limit = 0.50  # 50%
test_risks = [0.20, 0.30, 0.40, 0.50, 0.60, 0.80, 1.00]

print(f"\nExample: risk_limit = {risk_limit:.0%}")
print(f"{'Actual Risk':>12} {'Multiplier':>12} {'Effective Position':>18}")
print("-" * 44)
for r in test_risks:
    # multiplier = risk_limit / max(risk_limit, actual_risk)
    mult = min(1.0, risk_limit / r)
    print(f"{r:>12.0%} {mult:>12.3f} {mult*100:>17.0f}%")

# ── 5. Visualization ────────────────────────────────────────────────────
try:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    # Panel 1: Risk measures
    ax = axes[0]
    ax.plot(dates, normal_risk, label="Normal risk", linewidth=1)
    ax.plot(dates, shocked_vol_risk, label="Shocked vol risk", linewidth=1)
    ax.plot(dates, sum_abs_risk, label="Sum-abs risk", linewidth=1)
    ax.plot(dates, leverage / 10, label="Leverage / 10", linewidth=1, linestyle="--")

    normal_limit = risk_overlay_config["max_risk_fraction_normal_risk"] * pct_vol_target / 100
    ax.axhline(y=normal_limit, color="red", linestyle=":", alpha=0.5, label=f"Normal limit ({normal_limit:.0%})")
    ax.set_ylabel("Risk Level")
    ax.set_title("Portfolio Risk Measures Over Time")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)

    # Panel 2: Overlay multiplier
    ax = axes[1]
    ax.fill_between(dates, 0, joint_mult, alpha=0.3, color="blue")
    ax.plot(dates, joint_mult, color="blue", linewidth=1, label="Risk Multiplier")
    ax.axhline(y=1.0, color="green", linestyle="--", alpha=0.5, label="No reduction")
    ax.set_ylabel("Position Multiplier")
    ax.set_xlabel("Date")
    ax.set_title("Risk Overlay Multiplier (min of 4 components)")
    ax.set_ylim(0, 1.1)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    _plot_path = os.path.join(_SCRIPT_DIR, "05_risk_overlay.png")
    plt.savefig(_plot_path, dpi=150)
    print(f"\nPlot saved to {_plot_path}")
    plt.close()
except ImportError:
    print("\nmatplotlib not installed - skipping visualization")

print("\n" + "=" * 70)
print("KEY TAKEAWAYS")
print("=" * 70)
print("""
1. Portfolio risk = sqrt(w' * C * w) where w = risk weights, C = correlation
2. The risk overlay is a SAFETY NET, not a primary risk management tool
3. It takes the MINIMUM of 4 multipliers (most conservative wins)
4. Normal operation: multiplier = 1.0 (no reduction)
5. Stressed markets: multiplier < 1 proportionally reduces ALL positions
6. The 4 components catch different failure modes:
   - Normal risk: gradual vol increase
   - Shocked vol: sudden vol spikes
   - Sum-abs: concentrated positions regardless of correlation
   - Leverage: raw exposure limits
""")
