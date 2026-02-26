"""
Shared helper: generate synthetic weekly returns for 5 assets with a
realistic correlation structure.

Asset classes:
  - US_Equity, EU_Equity  (correlated ~0.6)
  - US_Bond               (negative to equities ~-0.2)
  - Gold, Commodity       (moderate ~0.3 to equities, low to bonds)

Used by examples 01-03 and 05.
"""

import numpy as np
import pandas as pd

ASSET_NAMES = ["US_Equity", "EU_Equity", "US_Bond", "Gold", "Commodity"]

# Target correlation matrix (symmetric, positive definite)
TARGET_CORR = np.array(
    [
        [1.00, 0.60, -0.20, 0.10, 0.30],
        [0.60, 1.00, -0.15, 0.15, 0.25],
        [-0.20, -0.15, 1.00, 0.05, -0.10],
        [0.10, 0.15, 0.05, 1.00, 0.35],
        [0.30, 0.25, -0.10, 0.35, 1.00],
    ]
)

# Annualised vols (weekly vol = ann_vol / sqrt(52))
ANNUAL_VOLS = np.array([0.16, 0.18, 0.06, 0.14, 0.20])

# Annualised Sharpe ratios (for mean returns)
ANNUAL_SRS = np.array([0.40, 0.35, 0.20, 0.15, 0.30])


def generate_weekly_returns(
    n_weeks: int = 520, seed: int = 42
) -> pd.DataFrame:
    """
    Generate n_weeks of synthetic weekly returns.

    Returns a DataFrame with DatetimeIndex (weekly frequency) and
    columns = ASSET_NAMES.
    """
    rng = np.random.default_rng(seed)

    weekly_vols = ANNUAL_VOLS / np.sqrt(52)
    weekly_means = (ANNUAL_SRS * ANNUAL_VOLS) / 52

    # Build covariance from correlation + weekly vols
    D = np.diag(weekly_vols)
    cov = D @ TARGET_CORR @ D

    raw = rng.multivariate_normal(weekly_means, cov, size=n_weeks)

    dates = pd.date_range(end="2024-12-31", periods=n_weeks, freq="W-FRI")
    df = pd.DataFrame(raw, index=dates, columns=ASSET_NAMES)

    return df
