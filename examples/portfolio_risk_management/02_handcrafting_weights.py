"""
Example 02 - Handcrafted Portfolio Weights
==========================================

Demonstrates the handcrafting optimiser from pysystemtrade:
  - Building Estimates (correlation + mean + stdev)
  - Running handcraft_optimisation() with/without SR equalisation
  - Understanding the clustering and sub-portfolio approach
  - Calculating the Instrument Diversification Multiplier (IDM)

Key files:
  sysquant/optimisation/optimisers/handcraft.py
  sysquant/estimators/estimates.py
  sysquant/estimators/diversification_multipliers.py
"""

import os
import sys
import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from _synthetic_data import generate_weekly_returns, ASSET_NAMES

from sysquant.estimators.correlations import correlationEstimate
from sysquant.estimators.estimates import Estimates
from sysquant.estimators.mean_estimator import meanEstimates
from sysquant.estimators.stdev_estimator import stdevEstimates
from sysquant.optimisation.optimisers.handcraft import handcraft_optimisation
from sysquant.optimisation.weights import (
    one_over_n_weights_given_asset_names,
    portfolioWeights,
)
from sysquant.estimators.diversification_multipliers import (
    diversification_mult_single_period,
)

# ── 1. Generate data & build Estimates ─────────────────────────────────────
returns = generate_weekly_returns(n_weeks=520)  # 10 years

corr_values = returns.corr().values
corr = correlationEstimate(corr_values, columns=ASSET_NAMES)

# Annualised estimates (weekly -> annual)
ann_means = returns.mean() * 52
ann_stdevs = returns.std() * np.sqrt(52)

mean_est = meanEstimates(
    [(name, ann_means[name]) for name in ASSET_NAMES]
)
stdev_est = stdevEstimates(
    [(name, ann_stdevs[name]) for name in ASSET_NAMES]
)

estimates = Estimates(
    correlation=corr,
    mean=mean_est,
    stdev=stdev_est,
    data_length=len(returns),
    frequency="W",
)

print("=" * 70)
print("STEP 1: Estimated parameters (annualised)")
print("=" * 70)
print(f"\nMeans:  {dict((k, round(v, 4)) for k, v in mean_est.items())}")
print(f"Stdevs: {dict((k, round(v, 4)) for k, v in stdev_est.items())}")
print(f"Implied SRs: ", end="")
for name in ASSET_NAMES:
    sr = mean_est[name] / stdev_est[name]
    print(f"{name}={sr:.2f}  ", end="")
print(f"\n\nCorrelation:\n{corr.as_pd().round(3)}")
print(f"\nData length: {estimates.data_length} weeks = {estimates.data_length_years:.1f} years")

# ── 2. Handcrafted weights WITH SR equalisation ───────────────────────────
print("\n" + "=" * 70)
print("STEP 2: Handcrafted weights (equalise_SR=True)")
print("=" * 70)

result_eq = handcraft_optimisation(estimates, equalise_SR=True, equalise_vols=True)
weights_eq = result_eq.weights

print("\nWith SR equalisation, all assets are assumed to have the same Sharpe Ratio.")
print("Weights are determined purely by correlation structure (diversification).")
print(f"\nRaw weights (include sub-portfolio IDM, so sum > 1):")
print(f"  {dict((k, round(v, 4)) for k, v in weights_eq.items())}")
print(f"  Sum: {sum(weights_eq.values()):.4f}")

# Normalise for comparison (the system pipeline does this later)
eq_sum = sum(weights_eq.values())
weights_eq_norm = portfolioWeights(
    [(k, v / eq_sum) for k, v in weights_eq.items()]
)
print(f"\nNormalised weights (sum to 1):")
print(f"  {dict((k, round(v, 4)) for k, v in weights_eq_norm.items())}")

# ── 3. Handcrafted weights WITHOUT SR equalisation ─────────────────────────
print("\n" + "=" * 70)
print("STEP 3: Handcrafted weights (equalise_SR=False)")
print("=" * 70)

result_sr = handcraft_optimisation(estimates, equalise_SR=False, equalise_vols=True)
weights_sr = result_sr.weights

print("\nWithout SR equalisation, assets with higher estimated SR get more weight,")
print("but the adjustment is *conservative* (mini-bootstrap accounts for uncertainty).")
print(f"\nWeights (normalised by SR adjustment): {dict((k, round(v, 4)) for k, v in weights_sr.items())}")
print(f"Sum: {sum(weights_sr.values()):.4f}")

# ── 4. Compare to equal weights ──────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 4: Comparison (all normalised to sum to 1)")
print("=" * 70)

equal_weights = one_over_n_weights_given_asset_names(ASSET_NAMES)

print(f"\n{'Asset':<14} {'Equal':>8} {'HC(SR=)':>8} {'HC(SR≠)':>8}")
print("-" * 40)
for name in ASSET_NAMES:
    print(
        f"{name:<14} {equal_weights[name]:>8.3f} {weights_eq_norm[name]:>8.3f} {weights_sr[name]:>8.3f}"
    )

# ── 5. Instrument Diversification Multiplier (IDM) ───────────────────────
print("\n" + "=" * 70)
print("STEP 5: Instrument Diversification Multiplier (IDM)")
print("=" * 70)
print("""
IDM = 1 / sqrt(W' * C * W)

where W = weights, C = correlation matrix.

IDM > 1 means the portfolio is diversified: combined risk < sum of parts.
IDM = 1 means perfect correlation (no diversification benefit).
""")

for label, w in [
    ("Equal weights", equal_weights),
    ("Handcrafted (SR=)", weights_eq_norm),
    ("Handcrafted (SR≠)", weights_sr),
]:
    idm = diversification_mult_single_period(corrmatrix=corr, weights=w)
    risk = w.portfolio_stdev(corr)
    print(f"  {label:<25} IDM={idm:.3f}  portfolio_risk={risk:.3f}")

print("\nThe IDM amplifies positions to 'use up' the diversification benefit.")
print("A typical futures portfolio with 20+ instruments might have IDM ~ 2.0-2.5.")

# ── 6. Visualization ────────────────────────────────────────────────────
try:
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Bar chart of weights
    x = np.arange(len(ASSET_NAMES))
    width = 0.25
    ax1.bar(x - width, [equal_weights[n] for n in ASSET_NAMES], width, label="Equal (1/N)")
    ax1.bar(x, [weights_eq_norm[n] for n in ASSET_NAMES], width, label="Handcrafted (SR=)")
    ax1.bar(x + width, [weights_sr[n] for n in ASSET_NAMES], width, label="Handcrafted (SR≠)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(ASSET_NAMES, rotation=45, ha="right")
    ax1.set_ylabel("Weight")
    ax1.set_title("Portfolio Weights: Equal vs Handcrafted")
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", alpha=0.3)

    # Show cumulative returns of the three portfolios
    cum_eq = (returns @ np.array([equal_weights[n] for n in ASSET_NAMES])).cumsum()
    cum_hc_sr = (returns @ np.array([weights_eq_norm[n] for n in ASSET_NAMES])).cumsum()
    cum_hc_nosr = (returns @ np.array([weights_sr[n] for n in ASSET_NAMES])).cumsum()

    ax2.plot(cum_eq.index, cum_eq.values, label="Equal (1/N)", linewidth=1)
    ax2.plot(cum_hc_sr.index, cum_hc_sr.values, label="Handcrafted (SR=)", linewidth=1)
    ax2.plot(cum_hc_nosr.index, cum_hc_nosr.values, label="Handcrafted (SR≠)", linewidth=1)
    ax2.set_title("Cumulative Returns (synthetic)")
    ax2.set_ylabel("Cumulative Return")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    _plot_path = os.path.join(_SCRIPT_DIR, "02_handcrafting_weights.png")
    plt.savefig(_plot_path, dpi=150)
    print(f"\nPlot saved to {_plot_path}")
    plt.close()
except ImportError:
    print("\nmatplotlib not installed - skipping visualization")

print("\n" + "=" * 70)
print("KEY TAKEAWAYS")
print("=" * 70)
print("""
1. Handcrafting uses hierarchical clustering to split assets into groups of 2-3
2. Within each group, equal (risk) weights are used
3. SR adjustment is conservative: a mini-bootstrap accounts for estimation uncertainty
4. With SR equalisation, weights depend only on correlations (safer default)
5. IDM captures diversification benefit; higher IDM = more diversified portfolio
""")
