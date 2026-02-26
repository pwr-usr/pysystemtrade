"""
Example 03 - SR Adjustment via Mini-Bootstrap
=============================================

Deep-dive into how pysystemtrade adjusts weights for Sharpe Ratio differences:
  - adjust_weights_for_SR() and the mini-bootstrap method
  - How weight multipliers change with SR difference, correlation, and data length
  - Why this is more conservative than naive mean-variance optimisation

The key insight: with finite data, SR estimates are noisy. The bootstrap
samples from the *distribution of possible true SRs* given the estimate,
then averages the optimal weights across all scenarios.

Key file: sysquant/optimisation/SR_adjustment.py
"""

import os
import numpy as np

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

from sysquant.optimisation.SR_adjustment import (
    adjust_weights_for_SR,
    mini_bootstrap_ratio_given_SR_diff,
)

# ── 1. Basic SR adjustment ──────────────────────────────────────────────
print("=" * 70)
print("STEP 1: How SR adjustment works")
print("=" * 70)
print("""
Given two assets with different estimated Sharpe Ratios:
  - Naive optimisation would heavily overweight the higher-SR asset
  - But SR estimates are noisy! With 10 years of data, the standard
    error of an SR estimate is ~0.32 (= 1/sqrt(10))
  - The mini-bootstrap asks: "across many possible TRUE SR values
    (consistent with our estimate), what's the average optimal weight?"
  - This produces much more conservative tilts than naive optimisation
""")

# 2 assets, equal starting weights
weights = [0.5, 0.5]

for sr_diff in [0.0, 0.1, 0.2, 0.3, 0.5, 1.0]:
    sr_list = [0.5 + sr_diff, 0.5]
    adj = adjust_weights_for_SR(
        weights_as_list=weights,
        SR_list=sr_list,
        avg_correlation=0.5,
        years_of_data=10,
    )
    print(f"  SR=[{sr_list[0]:.1f}, {sr_list[1]:.1f}]  (diff={sr_diff:.1f})  "
          f"-> weights=[{adj[0]:.3f}, {adj[1]:.3f}]")

# ── 2. Effect of data length ────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 2: More data = more confidence = bigger SR adjustment")
print("=" * 70)
print("""
With more years of data, we're more confident the SR difference is real,
so the adjustment becomes larger (further from 1/N).
""")

sr_diff = 0.3
print(f"SR difference = {sr_diff} (asset 1 SR=0.8, asset 2 SR=0.5)\n")

for years in [3, 5, 10, 20, 50, 100]:
    ratio = mini_bootstrap_ratio_given_SR_diff(
        SR_diff=sr_diff, avg_correlation=0.5, years_of_data=years
    )
    # ratio is relative to 1/N weight; use full function for actual weights
    adj = adjust_weights_for_SR(
        weights_as_list=[0.5, 0.5],
        SR_list=[0.5 + sr_diff, 0.5],
        avg_correlation=0.5,
        years_of_data=years,
    )
    print(f"  {years:>3d} years of data -> weight multiplier = {ratio:.3f}"
          f"  (weight on higher-SR asset: {adj[0]*100:.1f}%)")

# ── 3. Effect of correlation ────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 3: Higher correlation = bigger SR adjustment")
print("=" * 70)
print("""
When assets are highly correlated, differentiating between them based on SR
is more valuable (they're substitutes). When uncorrelated, diversification
benefit dominates and we stay closer to equal weights.
""")

sr_diff = 0.3
years = 10
print(f"SR diff={sr_diff}, years={years}\n")

for corr in [0.0, 0.2, 0.4, 0.6, 0.8, 0.95]:
    ratio = mini_bootstrap_ratio_given_SR_diff(
        SR_diff=sr_diff, avg_correlation=corr, years_of_data=years
    )
    # Use adjust_weights_for_SR to get actual normalised weights
    adj = adjust_weights_for_SR(
        weights_as_list=[0.5, 0.5],
        SR_list=[0.5 + sr_diff, 0.5],
        avg_correlation=corr,
        years_of_data=years,
    )
    print(f"  corr={corr:.2f} -> multiplier={ratio:.3f}  "
          f"weights=[{adj[0]:.3f}, {adj[1]:.3f}]")

# ── 4. Multi-asset example ──────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 4: Multi-asset SR adjustment (5 assets)")
print("=" * 70)

sr_list = [0.6, 0.5, 0.3, 0.4, 0.7]
weights_5 = [0.2] * 5
asset_names = ["A(0.6)", "B(0.5)", "C(0.3)", "D(0.4)", "E(0.7)"]

for years in [5, 10, 30]:
    adj = adjust_weights_for_SR(
        weights_as_list=weights_5,
        SR_list=sr_list,
        avg_correlation=0.4,
        years_of_data=years,
    )
    print(f"\n  {years} years:")
    for name, w0, w1 in zip(asset_names, weights_5, adj):
        bar = "#" * int(w1 * 100)
        print(f"    {name}: {w0:.2f} -> {w1:.3f}  {bar}")

# ── 5. Visualization ──────────────────────────────────────────────────
try:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # Panel 1: Multiplier vs SR difference
    sr_diffs = np.linspace(-0.5, 0.5, 50)
    for years in [5, 10, 30]:
        ratios = [
            mini_bootstrap_ratio_given_SR_diff(d, avg_correlation=0.5, years_of_data=years)
            for d in sr_diffs
        ]
        axes[0].plot(sr_diffs, ratios, label=f"{years}yr")
    axes[0].axhline(y=1.0, color="gray", linestyle="--", alpha=0.5)
    axes[0].axvline(x=0.0, color="gray", linestyle="--", alpha=0.5)
    axes[0].set_xlabel("SR Difference")
    axes[0].set_ylabel("Weight Multiplier")
    axes[0].set_title("Multiplier vs SR Diff\n(corr=0.5)")
    axes[0].legend()
    axes[0].grid(alpha=0.3)

    # Panel 2: Multiplier vs correlation
    corrs = np.linspace(0, 0.95, 20)
    for sr_d in [0.1, 0.2, 0.3, 0.5]:
        ratios = [
            mini_bootstrap_ratio_given_SR_diff(sr_d, avg_correlation=c, years_of_data=10)
            for c in corrs
        ]
        axes[1].plot(corrs, ratios, label=f"SR diff={sr_d}")
    axes[1].axhline(y=1.0, color="gray", linestyle="--", alpha=0.5)
    axes[1].set_xlabel("Average Correlation")
    axes[1].set_ylabel("Weight Multiplier")
    axes[1].set_title("Multiplier vs Correlation\n(10yr data)")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)

    # Panel 3: Multiplier vs data length
    years_range = np.arange(3, 51)
    for sr_d in [0.1, 0.2, 0.3, 0.5]:
        ratios = [
            mini_bootstrap_ratio_given_SR_diff(sr_d, avg_correlation=0.5, years_of_data=y)
            for y in years_range
        ]
        axes[2].plot(years_range, ratios, label=f"SR diff={sr_d}")
    axes[2].axhline(y=1.0, color="gray", linestyle="--", alpha=0.5)
    axes[2].set_xlabel("Years of Data")
    axes[2].set_ylabel("Weight Multiplier")
    axes[2].set_title("Multiplier vs Data Length\n(corr=0.5)")
    axes[2].legend(fontsize=8)
    axes[2].grid(alpha=0.3)

    plt.tight_layout()
    _plot_path = os.path.join(_SCRIPT_DIR, "03_sr_adjustment.png")
    plt.savefig(_plot_path, dpi=150)
    print(f"\nPlot saved to {_plot_path}")
    plt.close()
except ImportError:
    print("\nmatplotlib not installed - skipping visualization")

print("\n" + "=" * 70)
print("KEY TAKEAWAYS")
print("=" * 70)
print("""
1. The mini-bootstrap is a parametric simulation: it samples from the
   distribution of possible true SR values given our estimate and data length

2. More data -> more confidence -> larger adjustments away from equal weight

3. Higher correlation -> more useful to differentiate -> larger adjustments

4. Even with large SR differences (0.5 units), adjustments are modest
   with typical data lengths (10-20yr). This prevents overfitting!

5. The method naturally handles the classic problem: "We think this asset
   has SR=0.8 but we only have 10 years of data, so we're not very sure."
""")
