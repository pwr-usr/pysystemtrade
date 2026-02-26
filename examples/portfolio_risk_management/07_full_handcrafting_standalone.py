"""
Example 07 - Full Handcrafting: Standalone Portfolio Class
==========================================================

Uses the standalone Portfolio class from sysquant/optimisation/full_handcrafting.py.
This is the *complete* handcrafting code that can be used independently
(including for long-only portfolios). It is NOT the code used inside pysystemtrade's
System pipeline, but a self-contained implementation of the same algorithm.

Demonstrates:
  - 8 synthetic assets showing hierarchical clustering tree
  - Subportfolio decomposition and diagnostics
  - Vol weights vs cash weights
  - Risk-targeted vs unconstrained portfolios

Key file: sysquant/optimisation/full_handcrafting.py
"""

import os
import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

from sysquant.optimisation.full_handcrafting import Portfolio

# ── 1. Generate synthetic weekly returns for 8 assets ────────────────────
print("=" * 70)
print("STEP 1: Generate 8-asset synthetic universe")
print("=" * 70)

np.random.seed(42)

asset_names = [
    "US_Eq", "EU_Eq", "EM_Eq",   # Equities (correlated cluster)
    "US_Bond", "EU_Bond",          # Bonds (correlated cluster, negative to equities)
    "Gold", "Crude", "Natgas",     # Commodities (mixed correlations)
]

# Build a realistic correlation structure
n = len(asset_names)
corr_matrix = np.eye(n)

# Equity cluster
corr_matrix[0, 1] = corr_matrix[1, 0] = 0.70
corr_matrix[0, 2] = corr_matrix[2, 0] = 0.55
corr_matrix[1, 2] = corr_matrix[2, 1] = 0.60

# Bond cluster
corr_matrix[3, 4] = corr_matrix[4, 3] = 0.80

# Equity-Bond (negative)
for eq in [0, 1, 2]:
    for bd in [3, 4]:
        corr_matrix[eq, bd] = corr_matrix[bd, eq] = -0.20

# Commodity cluster
corr_matrix[6, 7] = corr_matrix[7, 6] = 0.25  # Crude-Natgas
corr_matrix[5, 6] = corr_matrix[6, 5] = 0.15  # Gold-Crude

# Commodity-Equity (moderate positive)
for eq in [0, 1, 2]:
    for cm in [5, 6, 7]:
        corr_matrix[eq, cm] = corr_matrix[cm, eq] = 0.20

# Commodity-Bond (low)
for bd in [3, 4]:
    for cm in [5, 6, 7]:
        corr_matrix[bd, cm] = corr_matrix[cm, bd] = 0.05

# Make PSD via nearest correlation matrix
from statsmodels.stats.correlation_tools import corr_nearest
corr_matrix = corr_nearest(corr_matrix, n_fact=100)

# Annual vols and SRs
annual_vols = np.array([0.16, 0.18, 0.22, 0.06, 0.07, 0.14, 0.25, 0.35])
annual_srs = np.array([0.40, 0.35, 0.30, 0.20, 0.15, 0.10, 0.25, 0.15])
weekly_vols = annual_vols / np.sqrt(52)
weekly_means = (annual_srs * annual_vols) / 52

# Generate returns
n_weeks = 520  # 10 years
D = np.diag(weekly_vols)
cov = D @ corr_matrix @ D
returns = np.random.multivariate_normal(weekly_means, cov, size=n_weeks)
dates = pd.date_range(end="2024-12-31", periods=n_weeks, freq="W-FRI")
returns_df = pd.DataFrame(returns, index=dates, columns=asset_names)

print(f"\nAssets: {asset_names}")
print(f"Data: {n_weeks} weeks ({n_weeks/52:.0f} years)")
print(f"\nAnnual vols:    {dict(zip(asset_names, [f'{v:.0%}' for v in annual_vols]))}")
print(f"Annual SRs:     {dict(zip(asset_names, [f'{v:.2f}' for v in annual_srs]))}")
print(f"\nSample correlation matrix:")
print(returns_df.corr().round(2).to_string())

# ── 2. Create Portfolio (unconstrained) ──────────────────────────────────
print("\n" + "=" * 70)
print("STEP 2: Unconstrained Portfolio (no risk target)")
print("=" * 70)

portfolio = Portfolio(returns_df, use_SR_estimates=True)

print(f"\nPortfolio contains {len(portfolio.instruments)} instruments")
print(f"Years of data: {portfolio.years_of_data:.1f}")

# ── 3. Show subportfolio tree ────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 3: Subportfolio clustering tree")
print("=" * 70)
print("""
The handcrafting algorithm recursively splits the portfolio into clusters
of at most 3 assets, forming a tree. Equal (risk) weights are used within
each leaf cluster, then aggregated up with diversification multipliers.
""")

tree = portfolio.show_subportfolio_tree()


def print_tree(tree_list, indent=0):
    for item in tree_list:
        if isinstance(item, list):
            print_tree(item, indent + 2)
        else:
            print(" " * indent + str(item))


print_tree(tree)

# ── 4. Diagnostics ─────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 4: Portfolio diagnostics")
print("=" * 70)

diags = portfolio.diags
print(f"\n{diags.description}")
print(f"\n{diags.calcs}")

if hasattr(diags, "aggregate") and not isinstance(diags.aggregate, str):
    print(f"\n{diags.aggregate.description}")
    print(f"\n{diags.aggregate.calcs}")

if hasattr(diags, "cash") and not isinstance(diags.cash, str):
    print(f"\n{diags.cash.description}")
    print(f"\n{diags.cash.calcs}")

# ── 5. Vol weights and cash weights ─────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 5: Vol weights vs Cash weights")
print("=" * 70)
print("""
Vol weights: risk-based allocation (each asset contributes X% of risk budget)
Cash weights: actual capital allocation (adjusted for different volatilities)
  cash_weight[i] = vol_weight[i] / vol[i]  (then normalised)
""")

vol_weights = portfolio.volatility_weights
cash_weights = portfolio.cash_weights

print(f"\n{'Asset':<10} {'Vol Wt':>8} {'Cash Wt':>8} {'Ann Vol':>8} {'Ann SR':>8}")
print("-" * 44)
for i, name in enumerate(portfolio.instruments):
    print(
        f"{name:<10} {vol_weights[i]:>8.3f} {cash_weights[i]:>8.3f} "
        f"{portfolio.vol_vector[i]:>8.2%} {portfolio.sharpe_ratio[i]:>8.2f}"
    )

print(f"\n  Sum vol weights:  {sum(vol_weights):.3f}")
print(f"  Sum cash weights: {sum(cash_weights):.3f}")
print(f"  IDM:              {portfolio.div_mult:.3f}")

# ── 6. Without SR estimates ──────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 6: Without SR estimates (pure diversification)")
print("=" * 70)

portfolio_nosr = Portfolio(returns_df, use_SR_estimates=False)
vol_weights_nosr = portfolio_nosr.volatility_weights

print(f"\n{'Asset':<10} {'With SR':>10} {'Without SR':>12}")
print("-" * 34)
for i, name in enumerate(portfolio_nosr.instruments):
    vw_sr = vol_weights[portfolio.instruments.index(name)]
    print(f"{name:<10} {vw_sr:>10.3f} {vol_weights_nosr[i]:>12.3f}")

# ── 7. Risk-targeted portfolio ──────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 7: Risk-targeted portfolio (target=12% annual vol)")
print("=" * 70)
print("""
When a risk target is set and leverage is not allowed:
  - If natural risk < target: partition into high-vol and low-vol groups,
    then overweight the high-vol group to hit the target
  - If natural risk > target: blend with cash to reduce risk
""")

natural_std = portfolio.portfolio_std
print(f"\nNatural portfolio std (unconstrained): {natural_std:.2%}")

risk_target = 0.12
try:
    portfolio_targeted = Portfolio(
        returns_df, risk_target=risk_target, allow_leverage=False, use_SR_estimates=True
    )
    targeted_cash = portfolio_targeted.cash_weights
    targeted_std = portfolio_targeted.portfolio_std

    print(f"Risk target: {risk_target:.0%}")
    print(f"Targeted portfolio std: {targeted_std:.2%}")

    print(f"\n{'Asset':<10} {'Uncons Cash':>12} {'Targeted Cash':>14}")
    print("-" * 38)
    for i, name in enumerate(portfolio_targeted.instruments):
        uc = cash_weights[portfolio.instruments.index(name)]
        print(f"{name:<10} {uc:>12.3f} {targeted_cash[i]:>14.3f}")
    print(f"\n  Sum targeted cash weights: {sum(targeted_cash):.3f}")
    print("  (Sum < 1.0 means remainder is in cash)")
except Exception as e:
    print(f"\nRisk targeting note: {e}")
    print("(This can happen when the target is between min and max instrument vols)")

# ── 8. Visualization ────────────────────────────────────────────────────
try:
    import matplotlib.pyplot as plt
    import scipy.cluster.hierarchy as sch

    fig, axes = plt.subplots(2, 2, figsize=(15, 10))

    # Panel 1: Correlation heatmap
    ax = axes[0][0]
    corr_df = returns_df.corr()
    im = ax.imshow(corr_df.values, cmap="RdBu_r", vmin=-0.5, vmax=1.0)
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(asset_names, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(asset_names, fontsize=8)
    for i in range(n):
        for j in range(n):
            ax.text(j, i, f"{corr_df.values[i, j]:.2f}", ha="center", va="center", fontsize=6)
    ax.set_title("Correlation Matrix")
    fig.colorbar(im, ax=ax, shrink=0.8)

    # Panel 2: Dendrogram
    ax = axes[0][1]
    d = sch.distance.pdist(corr_df.values)
    L = sch.linkage(d, method="complete")
    sch.dendrogram(L, labels=asset_names, ax=ax, leaf_rotation=45, leaf_font_size=8)
    ax.set_title("Hierarchical Clustering Dendrogram")
    ax.set_ylabel("Distance")

    # Panel 3: Vol weights comparison
    ax = axes[1][0]
    x = np.arange(len(portfolio.instruments))
    width = 0.35
    ax.bar(x - width / 2, vol_weights, width, label="With SR adj")
    ax.bar(x + width / 2, vol_weights_nosr, width, label="Without SR adj")
    ax.set_xticks(x)
    ax.set_xticklabels(portfolio.instruments, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Vol Weight")
    ax.set_title("Volatility Weights: With vs Without SR Adjustment")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    # Panel 4: Cash weights
    ax = axes[1][1]
    ax.bar(range(len(portfolio.instruments)), cash_weights, color="steelblue")
    ax.set_xticks(range(len(portfolio.instruments)))
    ax.set_xticklabels(portfolio.instruments, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Cash Weight")
    ax.set_title("Cash Weights (Unconstrained)")
    ax.grid(axis="y", alpha=0.3)

    plt.suptitle("Full Handcrafting: 8-Asset Portfolio", fontsize=13)
    plt.tight_layout()
    _plot_path = os.path.join(_SCRIPT_DIR, "07_full_handcrafting.png")
    plt.savefig(_plot_path, dpi=150)
    print(f"\nPlot saved to {_plot_path}")
    plt.close()
except ImportError:
    print("\nmatplotlib not installed - skipping visualization")

print("\n" + "=" * 70)
print("KEY TAKEAWAYS")
print("=" * 70)
print("""
1. The standalone Portfolio class implements the FULL handcrafting algorithm
2. It recursively clusters assets into groups of <= 3, using correlation distance
3. Within each cluster: weights from uncertainty-based correlation optimisation
4. Between clusters: equal (risk) weight * diversification multiplier
5. Vol weights = risk allocation; Cash weights = capital allocation
6. Risk targeting adjusts cash weights to hit a specific annual vol target
7. Without leverage: it partitions into high/low vol groups if risk is too low
8. SR adjustment is conservative, using a mini-bootstrap across possible true SRs
""")
