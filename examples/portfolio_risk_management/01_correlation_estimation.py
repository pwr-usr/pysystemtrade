"""
Example 01 - Correlation Estimation & Cleaning
================================================

Demonstrates pysystemtrade's correlationEstimate class:
  - Building a correlation matrix from returns
  - Shrinkage towards the average
  - Creating "boring" (prior) correlation matrices
  - Cleaning correlations with missing data
  - Checking / enforcing positive semi-definiteness

Key file: sysquant/estimators/correlations.py
"""

import os
import sys
import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from _synthetic_data import generate_weekly_returns, ASSET_NAMES, TARGET_CORR

from sysquant.estimators.correlations import (
    correlationEstimate,
    create_boring_corr_matrix,
)

# ── 1. Generate data and compute sample correlation ────────────────────────
returns = generate_weekly_returns(n_weeks=260)  # ~5 years

sample_corr_values = returns.corr().values
sample_corr = correlationEstimate(sample_corr_values, columns=ASSET_NAMES)

print("=" * 70)
print("STEP 1: Sample correlation matrix (from 5 years of weekly data)")
print("=" * 70)
print(sample_corr.as_pd().round(3))
print(f"\nAverage off-diagonal correlation: {sample_corr.average_corr():.3f}")
print(f"Is positive semi-definite? {sample_corr.is_psd()}")

# ── 2. Compare to the 'true' correlation ──────────────────────────────────
true_corr = correlationEstimate(TARGET_CORR, columns=ASSET_NAMES)
diff = sample_corr.as_pd() - true_corr.as_pd()

print("\n" + "=" * 70)
print("STEP 2: Estimation error (sample - true)")
print("=" * 70)
print(diff.round(3))
print(f"\nMean absolute error: {np.abs(diff.values[np.triu_indices(5, k=1)]).mean():.4f}")

# ── 3. Shrinkage to average ──────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 3: Shrinkage to average correlation")
print("=" * 70)

for shrinkage in [0.0, 0.25, 0.50, 0.75, 1.0]:
    shrunk = sample_corr.shrink_to_average(shrinkage_corr=shrinkage)
    # Measure how close we are to truth
    err = np.abs(
        (shrunk.as_pd() - true_corr.as_pd()).values[np.triu_indices(5, k=1)]
    ).mean()
    print(f"  shrinkage={shrinkage:.2f}  avg_corr={shrunk.average_corr():.3f}  MAE vs true={err:.4f}")

print("\nNote: moderate shrinkage (0.25-0.50) often reduces estimation error")
print("by pulling extreme sample correlations towards the mean.")

# ── 4. Boring (prior) correlation matrix ─────────────────────────────────
print("\n" + "=" * 70)
print("STEP 4: 'Boring' correlation matrix (uniform off-diagonal)")
print("=" * 70)

boring = sample_corr.boring_corr_matrix(offdiag=0.99)
print(f"Default boring matrix (offdiag=0.99) - used for cleaning:")
print(boring.as_pd().round(2))
print(f"\nThis is the fallback when we have no data at all.")

# ── 5. Cleaning correlations with missing data ──────────────────────────
print("\n" + "=" * 70)
print("STEP 5: Cleaning a correlation matrix with missing values")
print("=" * 70)

# Inject NaN to simulate a new asset with no history vs some assets
dirty_values = sample_corr_values.copy()
dirty_values[0, 3] = np.nan  # US_Equity <-> Gold missing
dirty_values[3, 0] = np.nan
dirty_values[0, 4] = np.nan  # US_Equity <-> Commodity missing
dirty_values[4, 0] = np.nan
dirty_corr = correlationEstimate(dirty_values, columns=ASSET_NAMES)

print("Before cleaning (NaN = missing):")
print(dirty_corr.as_pd().round(3))

# Clean: must_haves = all True means we need all assets to have weights
cleaned = dirty_corr.clean_correlations(must_haves=[True] * 5, offdiag=0.99)
print("\nAfter cleaning (must_haves=all True, NaN -> average_corr):")
print(cleaned.as_pd().round(3))

# Clean with some assets not required
cleaned2 = dirty_corr.clean_correlations(
    must_haves=[True, True, True, False, False], offdiag=0.99
)
print("\nAfter cleaning (Gold & Commodity not must-have, NaN -> 0.99):")
print(cleaned2.as_pd().round(3))

# ── 6. Visualization ────────────────────────────────────────────────────
try:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    for ax, (title, mat) in zip(
        axes,
        [
            ("True Correlation", true_corr.as_pd()),
            ("Sample (5yr)", sample_corr.as_pd()),
            ("Shrunk (50%)", sample_corr.shrink_to_average(0.5).as_pd()),
        ],
    ):
        im = ax.imshow(mat.values, cmap="RdBu_r", vmin=-0.5, vmax=1.0)
        ax.set_xticks(range(5))
        ax.set_yticks(range(5))
        ax.set_xticklabels(ASSET_NAMES, rotation=45, ha="right", fontsize=8)
        ax.set_yticklabels(ASSET_NAMES, fontsize=8)
        ax.set_title(title, fontsize=10)

        for i in range(5):
            for j in range(5):
                ax.text(j, i, f"{mat.values[i, j]:.2f}", ha="center", va="center", fontsize=7)

    fig.colorbar(im, ax=axes, shrink=0.6, label="Correlation")
    fig.suptitle("Correlation Estimation: True vs Sample vs Shrunk", fontsize=13)
    plt.tight_layout()
    _plot_path = os.path.join(_SCRIPT_DIR, "01_correlation_heatmaps.png")
    plt.savefig(_plot_path, dpi=150)
    print(f"\nPlot saved to {_plot_path}")
    plt.close()
except ImportError:
    print("\nmatplotlib not installed - skipping visualization")

print("\n" + "=" * 70)
print("KEY TAKEAWAYS")
print("=" * 70)
print("""
1. Sample correlations are noisy - especially with < 10 years of data
2. Shrinkage pulls correlations towards the mean, reducing estimation error
3. The 'boring' matrix (offdiag=0.99) is the ultimate fallback prior
4. Cleaning fills NaN with average_corr (if must-have) or offdiag (if not)
5. Use make_psd() to enforce positive semi-definiteness if needed
""")
