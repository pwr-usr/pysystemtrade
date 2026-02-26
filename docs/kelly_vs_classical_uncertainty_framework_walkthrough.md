# Kelly versus Classical Portfolio Theory: A Walkthrough of pysystemtrade

## How One Codebase Quietly Resolved Finance's Longest-Running Bar Fight

---

*This document is a guided walkthrough of the pysystemtrade codebase through the lens of Rob Carver's blog post "Kelly versus Classical portfolio theory, and the two kinds of uncertainty premium." It tells the story of how theoretical ideas from financial academia are turned into production-grade code -- and why the messy, pragmatic compromises in that code are often more interesting than the elegant equations they came from.*

---

## Prologue: Two Tribes Go to War

Imagine a conference room in a London quant fund circa 2005. On one side of the table sits a portfolio manager with a PhD in Financial Economics. She's drawn an efficient frontier on the whiteboard. Her world is clean: investors have utility functions, markets have covariance matrices, and the optimal portfolio sits at the tangent point where Sharpe Ratio is maximised. She's a **Utilitarian**.

On the other side sits a former blackjack counter turned algo trader. He doesn't care about utility functions. He worships at the altar of Ed Thorp, and his commandment is simple: maximise the expected logarithm of wealth. He's a **Kellyite**.

They've been arguing for an hour. The Utilitarian says: "My client wants 15% vol. That's the constraint. I optimise within it." The Kellyite fires back: "Your client's risk appetite is irrelevant. The mathematics of compounding dictate the correct leverage. Anything above full Kelly is suicide; anything below it is leaving money on the table."

Meanwhile, in the corner of the room, nobody has noticed the elephant. It's a very large elephant. It is labelled "Parameter Uncertainty." It is about to sit on both of them.

This is the story of how pysystemtrade -- a real, production-grade systematic trading framework -- handles this battle. Spoiler: it doesn't pick a side. It builds a layered fortress that borrows from both camps, and then spends most of its engineering effort on the elephant.

---

## Act I: The Arena -- Understanding the System Architecture

Before we can understand how pysystemtrade handles Kelly vs. Classical, we need to understand how the system works. Think of it as a factory assembly line for trading decisions.

### The Pipeline

The system is built as a modular pipeline of **stages**, defined in `systems/basesystem.py`. Each stage does one job and passes its output to the next:

```
Raw Market Data
  |
  v
[Stage 1: RawData]        -- Clean prices, calculate volatility, FX rates
  |
  v
[Stage 2: Rules]           -- Generate raw forecast signals (trend, carry, etc.)
  |
  v
[Stage 3: ForecastScaleCap] -- Normalise forecasts to a common scale
  |
  v
[Stage 4: ForecastCombine]  -- Blend multiple forecasts per instrument
  |
  v
[Stage 5: PositionSizing]   -- Convert forecasts into contract positions
  |
  v
[Stage 6: Portfolios]       -- Weight across instruments, apply risk limits
  |
  v
[Stage 7: Account]          -- Calculate P&L and performance statistics
```

Every stage inherits from `SystemStage` in `systems/stage.py`. They communicate through a caching system (`systems/system_cache.py`) that prevents expensive recalculations. The entire system is configuration-driven through YAML files, with sensible defaults living in `sysdata/config/defaults.yaml`.

This architecture matters for our story because the Kelly-vs-Classical debate doesn't live in just one place. It's *distributed* across multiple stages, like a theme woven through an entire novel. The system's answer to "how much risk should I take?" emerges from the interaction of at least four different layers -- and understanding each layer is understanding a different chapter of the debate.

### The Key Directories

```
pysystemtrade/
  systems/                          # The 7-stage pipeline
    basesystem.py                   # System orchestrator
    positionsizing.py               # Stage 5: Vol targeting (Utilitarian heartland)
    portfolio.py                    # Stage 6: Instrument weighting + risk overlay
    risk_overlay.py                 # The safety net
    accounts/                       # Stage 7: P&L calculation
      curves/account_curve.py       # Performance metrics (Sharpe, drawdown, etc.)
    provided/rules/                 # Trading rule implementations (ewmac, carry, etc.)
  sysquant/                         # Quantitative engine
    optimisation/                   # The Kelly-vs-Classical battleground
      shared.py                     # Sharpe maximisation (neg_SR objective)
      SR_adjustment.py              # Bootstrap uncertainty adjustment
      optimisers/handcraft.py       # The recommended production optimizer
      optimisers/shrinkage.py       # Bayesian shrinkage
      full_handcrafting.py          # Reference implementation with Fisher transforms
    estimators/                     # Estimation machinery
      vol.py                        # Volatility estimation (fast/slow blend)
      estimates.py                  # SR shrinkage and equalisation
      diversification_multipliers.py # IDM and FDM
      correlations.py               # Correlation estimation + cleaning
  syscore/
    capital.py                      # Capital compounding methods (fixed/full/half)
  sysdata/config/
    defaults.yaml                   # The system's philosophy encoded as configuration
```

---

## Act II: The Utilitarian's Kingdom -- Volatility Targeting

### The First Layer: Explicit Risk Appetite

The Utilitarian gets to speak first in pysystemtrade. Right at the foundation, the system asks the investor a very classical question: **"What volatility do you want?"**

This lives in `systems/positionsizing.py`, and the core logic is beautifully simple:

```python
# systems/positionsizing.py, lines 487-495
def annual_cash_vol_target(self) -> float:
    notional_trading_capital = self.get_notional_trading_capital()
    percentage_vol_target = self.get_percentage_vol_target()
    annual_cash_vol_target = (
        notional_trading_capital * percentage_vol_target / 100.0
    )
    return annual_cash_vol_target
```

The default configuration (`sysdata/config/defaults.yaml`) sets this up:

```yaml
percentage_vol_target: 16.0          # "I want 16% annual volatility"
notional_trading_capital: 1000000    # "I have a million dollars"
```

From these two numbers, everything cascades. The system calculates a daily cash volatility budget:

```python
# systems/positionsizing.py, line 482
daily_cash_vol_target = annual_cash_vol_target / ROOT_BDAYS_INYEAR
```

Where `ROOT_BDAYS_INYEAR` is the square root of 252 (trading days per year), approximately 15.87. So with $1M capital and 16% target vol:
- Annual cash vol target = $1,000,000 x 0.16 = $160,000
- Daily cash vol target = $160,000 / 15.87 = ~$10,082

Then for each instrument, the system computes a **volatility scalar** -- the bridge between the investor's risk appetite and actual position sizes:

```python
# systems/positionsizing.py, lines 164-203
vol_scalar = daily_cash_vol_target / instrument_value_volatility
```

And the final subsystem position (lines 86-133) is:

```python
subsystem_position = vol_scalar * forecast / average_absolute_forecast
```

This is pure Utilitarian thinking. The investor says "I want 16% vol," and the system scales every position inversely to its volatility to deliver that target. If an instrument suddenly becomes twice as volatile, the position automatically halves. There's no Kelly formula here -- just a fixed risk budget, allocated mechanically.

### What This Means in Blog Terms

In the blog's notation, the Utilitarian's position sizing is:

```
f* = s_max / s
s* = s_max
```

Where `s_max` is the `percentage_vol_target` (16%) and `s` is the instrument's standard deviation. The leverage factor `f*` adjusts automatically to deliver the target risk `s*`. The investor's risk appetite is an *input*, and leverage is the *output*. No Kelly involved yet.

### Volatility Estimation: Where Both Camps Agree

Both Kellyites and Utilitarians need volatility estimates, and pysystemtrade invests serious effort here. The `mixed_vol_calc()` function in `sysquant/estimators/vol.py` blends two timescales:

```yaml
# sysdata/config/defaults.yaml
volatility_calculation:
  func: "sysquant.estimators.vol.mixed_vol_calc"
  days: 35                     # Fast EWMA span (about 7 weeks)
  slow_vol_years: 10           # Slow EWMA lookback
  proportion_of_slow_vol: 0.3  # 30% slow, 70% fast
  vol_abs_min: 0.0000000001    # Floor to prevent division by zero
```

The fast component (35-day EWMA) tracks recent volatility changes. The slow component (10-year EWMA) acts as an anchor, preventing the system from being whipsawed by temporary calm. The 70/30 blend means the system responds to regime changes but doesn't panic.

There's also a **volatility floor** (`apply_vol_floor()` in `vol.py`) that uses the 5th percentile of a 500-day rolling window. This prevents a dangerously quiet period from inflating position sizes to absurd levels -- because quiet periods are often followed by explosions.

The blog notes: "Parameter uncertainty isn't such an issue for standard deviation; we are relatively good in finance at predicting risk using past data." The engineering care in `vol.py` reflects this -- volatility is estimated with respect, not suspicion.

---

## Act III: The Kellyite's Influence -- Sharpe Ratio Optimisation

### The Second Layer: Growth-Aware Weighting

The Kellyite doesn't control the overall risk budget (that's the Utilitarian's domain), but they get a powerful say in *how that risk budget is allocated* -- both across trading rules and across instruments.

When the system decides how to weight different trading rules (Stage 4: ForecastCombine) or different instruments (Stage 6: Portfolios), it can use several optimisation methods. The most theoretically ambitious is the **one-period optimizer** in `sysquant/optimisation/shared.py`, and its objective function is the star of this section:

```python
# sysquant/optimisation/shared.py, lines 175-181
def neg_SR(weights: np.array, sigma: np.array, mus: np.array):
    # Returns minus the Sharpe Ratio (as we're minimising)
    estreturn = float(weights.dot(mus))
    std_dev = variance(weights, sigma) ** 0.5
    return -estreturn / std_dev
```

This is minimising *negative* Sharpe Ratio -- which means it's *maximising* Sharpe Ratio. The optimizer (`scipy.optimize.minimize` with SLSQP) finds weights that deliver the best return per unit of risk:

```python
# sysquant/optimisation/shared.py, lines 58-79
bounds = [(0.0, 1.0)] * number_assets      # Long-only
cdict = [{"type": "eq", "fun": addem}]     # Weights sum to 1.0
ans = minimize(neg_SR, start_weights, (sigma, mus),
               method="SLSQP", bounds=bounds, constraints=cdict, tol=0.00001)
```

### The Kelly Connection

Why is maximising Sharpe Ratio a Kelly concept? Remember from the blog:

```
f* = r / s^2          (Kelly optimal leverage)
s* = r / s = SR       (Kelly optimal risk target = Sharpe Ratio)
```

Under Kelly, the optimal risk target *is* the Sharpe Ratio. So when you maximise the portfolio Sharpe Ratio, you're finding the allocation that would deliver the highest Kelly-optimal risk target. You're asking: "If I were to apply Kelly to this portfolio, which combination of assets would give me the most room to lever up?"

The system doesn't then *use* full Kelly leverage (that's the Utilitarian's job, via `percentage_vol_target`). But it uses Kelly's logic to decide *what to put in the portfolio* before the Utilitarian caps the risk.

Think of it this way: the Kellyite picks the car, and the Utilitarian sets the speed limit.

### The Covariance Machinery

The Sharpe optimizer needs a covariance matrix, built from separate correlation and standard deviation estimates:

```python
# sysquant/optimisation/shared.py, lines 53-55
def sigma_from_corr_and_std(stdev_list: list, corrmatrix: list):
    sigma = np.diag(stdev_list).dot(corrmatrix).dot(np.diag(stdev_list))
    return sigma
```

This is the classic formula `Sigma = D * R * D` where `D` is a diagonal matrix of standard deviations and `R` is the correlation matrix. Correlations are estimated with exponentially weighted moving correlations (`sysquant/estimators/exponential_correlation.py`), using a 250-week (5-year) lookback and cleaned via nearest positive semi-definite projection when needed.

### The Diversification Multiplier: Reward for Not Concentrating

One of the most elegant concepts in the system is the **diversification multiplier** (`sysquant/estimators/diversification_multipliers.py`, lines 75-96):

```python
def diversification_mult_single_period(corrmatrix, weights, dm_max=2.5):
    risk = weights.portfolio_stdev(corrmatrix)    # sqrt(W * H * W^T)
    dm = np.min([1.0 / risk, dm_max])             # 1 / normalised portfolio stdev
    return dm
```

For a two-asset portfolio with equal weights and correlation 0.5, the portfolio risk is:
```
risk = sqrt(0.5^2 + 0.5^2 + 2 * 0.5 * 0.5 * 0.5) = sqrt(0.75) = 0.866
dm = 1 / 0.866 = 1.155
```

The DM boosts positions to capture the diversification benefit. Lower correlation = higher DM = bigger positions. But it's capped at 2.5 to prevent dangerous over-leveraging when correlations appear very low (which might just be noise).

This appears in two forms:
- **FDM** (Forecast Diversification Multiplier): rewards using multiple trading rules
- **IDM** (Instrument Diversification Multiplier): rewards trading multiple instruments

Both are computed using the same formula but applied at different levels of the pipeline. They're smoothed with EWMA (span 125 business days) to prevent jumpy adjustments.

---

## Act IV: The Elephant Enters -- Parameter Uncertainty

### The Third Layer: What We Don't Know

Here's where pysystemtrade gets really interesting, and where the blog's central argument comes alive in code. The blog says:

> "We don't know r. Or s. Or the Sharpe Ratio, r/s. And without knowing these figures, we don't have a hope in hell of finding the right leverage factor."

The system takes this *extremely* seriously. It doesn't just acknowledge uncertainty -- it builds multiple engineering safeguards against it. This is where most of the quantitative sophistication lives, and it's the layer that neither pure Kellyites nor pure Utilitarians tend to talk about.

### 4a. Shrinkage: Pulling Estimates Toward Humility

The first line of defence is **Bayesian shrinkage**. The idea is simple: your data-driven estimates of Sharpe Ratios and correlations are noisy. So blend them with a conservative prior.

For Sharpe Ratios (`sysquant/estimators/estimates.py`, lines 234-261):

```python
def shrink_SR_with_lists(mean_list, stdev_list, shrinkage_SR=1.0, target_SR=0.5):
    SR_estimates = [mean / stdev for (mean, stdev) in zip(mean_list, stdev_list)]

    post_SR_list = [
        (shrinkage_SR * target_SR) + (1 - shrinkage_SR) * estimatedSR
        for estimatedSR in SR_estimates
    ]

    post_means = [SR * stdev for (SR, stdev) in zip(post_SR_list, stdev_list)]
    return post_means
```

The formula is a weighted average: `posterior_SR = shrinkage * prior + (1 - shrinkage) * data`. With the default `shrinkage_SR = 0.90` and `target_SR = 0.5`, the system puts 90% weight on the conservative prior and only 10% on the observed data. If your backtest says an instrument has a Sharpe of 2.0, the system quietly revises this down to:

```
0.90 * 0.5 + 0.10 * 2.0 = 0.65
```

This is aggressive shrinkage, and deliberately so. It's the code saying: "I don't trust your backtest much."

For correlations, the shrinkage is less extreme but still present. The `shrink_to_average()` method in `sysquant/estimators/correlations.py` pulls every pairwise correlation toward the average of all correlations:

```
shrunk_corr = (1 - lambda) * sample_corr + lambda * average_corr
```

With default `shrinkage_corr = 0.50`, you get half-and-half: trust your data halfway, trust the average halfway. This prevents the optimizer from over-fitting to noisy correlation estimates.

The shrinkage optimizer in `sysquant/optimisation/optimisers/shrinkage.py` wraps this into a complete method:

```python
def shrinkage_optimisation(estimates, shrinkage_SR=0.90, shrinkage_corr=0.50,
                            ann_target_SR=0.5, **weighting_kwargs):
    estimates = estimates.shrink_correlation_to_average(shrinkage_corr)
    estimates = estimates.shrink_means_to_SR(
        shrinkage_SR=shrinkage_SR, ann_target_SR=ann_target_SR
    )
    return optimise_given_estimates(estimates, **weighting_kwargs)
```

Correlations get shrunk 50% toward their average (Ledoit-Wolf style), and means get shrunk 90% toward a Sharpe of 0.5. Only then does the optimizer run.

### 4b. SR Equalisation: The Nuclear Option

Even more conservative than shrinkage is **SR equalisation**. When `equalise_SR: True` (the default for instrument weights), the system *ignores all observed Sharpe Ratio differences* and assumes every instrument has the same Sharpe Ratio:

```python
# sysquant/optimisation/shared.py, lines 15-33
def optimise_given_estimates(estimates, equalise_SR=True, ann_target_SR=0.5,
                              equalise_vols=True, **_ignored_kwargs):
    estimates = estimates.equalise_estimates(
        equalise_vols=equalise_vols,
        equalise_SR=equalise_SR,
        ann_target_SR=ann_target_SR,
    )
    return optimise_from_processed_estimates(estimates)
```

When you equalise SRs, the Sharpe optimizer degenerates into a minimum-variance optimizer -- it cares only about correlations, not about return differences. This is an *extreme* expression of uncertainty aversion: "I literally cannot tell which instrument will do better, so I'll just focus on diversification."

Why so aggressive? Because the blog's own analysis shows that with realistic data lengths (10-40 years of monthly data), the sampling distribution of the Sharpe Ratio is wide enough to swamp most observed differences between instruments. The code is simply being honest about what the data can actually tell you.

### 4c. The Bootstrap: Sampling the Uncertainty

The most sophisticated uncertainty technique is the **parametric bootstrap** for Sharpe Ratio adjustment, in `sysquant/optimisation/SR_adjustment.py`. This is the code that most directly implements the blog's ideas about the sampling distribution of the Sharpe Ratio.

The key insight: if an instrument has an observed Sharpe Ratio that's higher than average, *how much should we tilt toward it?* The answer depends on how confident we are that the observed SR difference is real -- which depends on how much data we have.

The uncertainty in the SR estimate is captured by this formula (lines 229-236):

```python
def calculate_omega_difference(std, years_of_data, avg_correlation):
    omega_one_asset = std / (years_of_data) ** 0.5
    omega_variance_difference = 2 * (omega_one_asset ** 2) * (1 - avg_correlation)
    omega_difference = omega_variance_difference ** 0.5
    return omega_difference
```

This is the standard error of the *difference* in mean returns between two assets. Let's unpack it:

- `omega_one_asset = std / sqrt(years_of_data)` is the standard error of the mean estimate for a single asset. With 10 years of data and 15% standard deviation: `0.15 / sqrt(10) = 0.047`. Not small.
- The difference in two means has variance `2 * sigma^2 * (1 - correlation)`. If assets are highly correlated, the difference is more precisely estimated (because common factors cancel). If uncorrelated, the uncertainty is larger.
- The final `omega_difference` is the standard error of the mean difference -- the width of the confidence interval around "how much better is asset A than asset B?"

The blog gives the variance of SR estimates as `w = (1 + 0.5*SR^2) / N`. The code uses a slightly different but related formulation that accounts for correlation between the assets being compared.

Then comes the bootstrap (lines 108-155):

```python
def mini_bootstrap_ratio_given_SR_diff(SR_diff, avg_correlation, years_of_data,
                                        avg_SR=0.5, std=0.15, how_many_assets=2,
                                        p_step=0.2):
    dist_points = np.arange(p_step, stop=(1 - p_step) + 0.00000001, step=p_step)
    list_of_weights = [
        weights_given_SR_diff(SR_diff, avg_correlation, confidence_interval,
                              years_of_data, avg_SR=avg_SR, std=std,
                              how_many_assets=how_many_assets)
        for confidence_interval in dist_points
    ]
    array_of_weights = np.array(list_of_weights)
    average_weights = np.nanmean(array_of_weights, axis=0)
    ratio_of_weights = weight_ratio(average_weights)
    return ratio_of_weights
```

What's happening here is beautifully connected to the blog's "uncertainty appetite" concept. Instead of using a single point estimate of the Sharpe Ratio, the code:

1. Steps through multiple points of the sampling distribution (at 20%, 40%, 60%, 80% confidence with `p_step=0.2`)
2. For each confidence level, asks: "If the true SR difference were at this percentile of the distribution, what would the optimal weights be?"
3. Averages all these weights together

This is equivalent to integrating over the uncertainty -- a Bayesian approach that automatically penalises estimates from short data histories. When you have 5 years of data, the sampling distribution is wide, and the averaged weights stay close to equal. When you have 50 years, the distribution narrows, and the weights tilt more toward the asset with the higher observed SR.

For each confidence level, the code computes a "confident mean difference" using the normal distribution's inverse CDF (lines 214-226):

```python
def calculate_confident_mean_difference(std, years_of_data, mean_difference,
                                         confidence_interval, avg_correlation):
    omega_difference = calculate_omega_difference(std, years_of_data, avg_correlation)
    confident_mean_difference = stats.norm(mean_difference, omega_difference).ppf(
        confidence_interval
    )
    return confident_mean_difference
```

At confidence = 0.2 (quite uncertain), the adjusted mean difference will be much lower than the observed one -- possibly even negative. At confidence = 0.8, it'll be higher. Averaging across all levels gives you a "certainty-equivalent" mean that's always less extreme than the raw estimate.

The blog's table of "what half Kelly corresponds to" (a confidence interval of about 22%) finds its code equivalent here. But the code goes further by not picking any single confidence level -- it integrates over all of them.

### 4d. Correlation Uncertainty: Fisher Transforms

The reference implementation in `sysquant/optimisation/full_handcrafting.py` goes even deeper, applying uncertainty analysis to correlations themselves using Fisher transforms:

```python
# full_handcrafting.py, lines 143-175
def fisher_transform(corr):
    return 0.5 * np.log((1 + corr) / (1 - corr))

def fisher_stdev(data_points):
    return 1 / (data_points - 3) ** 0.5
```

The Fisher transform converts correlations (bounded between -1 and +1) to an approximately normal distribution, where you can compute standard errors properly. The standard error is `1 / sqrt(N-3)` where N is the number of data points. For 10 years of weekly data (520 observations): `1 / sqrt(517) = 0.044`. Not tiny.

The code then samples multiple points from this distribution, re-optimises for each, and averages the resulting weights. This is a parametric bootstrap over correlation uncertainty -- acknowledging that even if your point estimate of correlation is 0.6, the true value could easily be anywhere from 0.4 to 0.8, and each value implies different optimal weights.

### 4e. The Handcrafted Optimizer: Putting It All Together

The recommended production optimizer is the **handcrafted** method (`sysquant/optimisation/optimisers/handcraft.py`), and it's the system's pragmatic answer to the whole debate. Rather than running a fragile mean-variance optimisation on noisy estimates, it:

1. **Clusters** instruments into pairs based on correlation (using `sysquant/estimators/clustering_correlations.py`)
2. Assigns **equal risk-weights** within each cluster (1/N -- the Bayesian prior of "I don't know which is better")
3. Computes a **diversification multiplier** within each cluster
4. Adjusts weights slightly for **SR differences** using the bootstrap method above
5. Aggregates bottom-up into a full portfolio

The clustering is limited to pairs (cluster size = 2) to keep the optimization simple and robust. Within each pair, the starting point is always equal weights. Only then does the SR adjustment nudge things -- and it can only nudge, not revolutionise, because the bootstrap ensures that noisy SR differences produce small adjustments.

This is the engineering philosophy in a nutshell: start from something you're confident in (equal weights), then adjust toward what the data says -- but only to the degree that the data is trustworthy.

---

## Act V: The Safety Net -- Risk Overlay

### The Fourth Layer: Don't Blow Up

Even after all the careful estimation, shrinkage, and bootstrapping, the system has one more layer of defence. The **risk overlay** (`systems/risk_overlay.py`) is the last line between your portfolio and catastrophe.

```python
# systems/risk_overlay.py, lines 4-73
def get_risk_multiplier(risk_overlay_config, normal_risk, shocked_vol_risk,
                         sum_abs_risk, leverage, percentage_vol_target):
    # Four independent risk checks, each producing a multiplier between 0 and 1

    risk_multiplier_for_normal_risk = multiplier_given_series_and_limit(
        risk_measure=normal_risk, risk_limit=risk_limit_for_normal_risk)

    risk_multiplier_for_shocked_stdev = multiplier_given_series_and_limit(
        risk_measure=shocked_vol_risk, risk_limit=risk_limit_for_shocked_risk)

    risk_multiplier_for_sum_abs_risk = multiplier_given_series_and_limit(
        risk_measure=sum_abs_risk, risk_limit=risk_limit_for_sum_abs_risk)

    risk_multiplier_for_leverage = multiplier_given_series_and_limit(
        risk_measure=leverage, risk_limit=risk_limit_for_leverage)

    # Take the MINIMUM (most conservative) across all four
    joint_mult = all_mult.min(axis=1)
    return joint_mult
```

Each multiplier is computed simply:

```python
# systems/risk_overlay.py, lines 76-86
def multiplier_given_series_and_limit(risk_measure, risk_limit):
    max_value = max(risk_limit, risk_measure)
    return risk_limit / max_value
```

When risk is below the limit, the multiplier is 1.0 (no change). When risk exceeds the limit, the multiplier shrinks proportionally. If your portfolio's risk is twice the limit, every position gets halved. Four independent checks guard against four different failure modes:

| Check | What It Guards Against | How It's Measured |
|-------|----------------------|-------------------|
| **Normal risk** | Predicted volatility exceeding target | Portfolio vol from weights + covariance |
| **Shocked vol** | Volatility jumping to extreme levels | 99th percentile of 10-year vol distribution |
| **Sum absolute risk** | Extreme concentrations ignoring correlation | Sum of abs(weight) * vol per instrument |
| **Leverage** | Gross leverage exceeding safe bounds | Sum of abs(weights) |

The key design decisions:

1. **Take the minimum**: The most conservative check wins. One danger is enough.
2. **Only scale down, never up**: The overlay is a one-way valve. It can reduce your positions when danger is detected, but it will never increase them.
3. **Multiple independent checks**: Different failure modes are caught by different metrics. Correlation breakdowns are caught by sum-abs-risk (which ignores correlation). Volatility shocks are caught by the shocked-vol check. Leverage drift is caught directly.

This is the blog's "uncertainty premium" made concrete. Even if your model says everything is fine, the risk overlay checks: "Yes, but what if the model is wrong?"

Configuration (commented out by default in `defaults.yaml`):

```yaml
# risk_overlay:
#   max_risk_fraction_normal_risk: 0.9
#   max_risk_fraction_stdev_risk: 0.6
#   max_risk_limit_sum_abs_risk: 0.9
#   max_risk_leverage: 2.5
```

When enabled, these say: "Normal risk can be 90% of my target, but shocked risk only 60%. And never use more than 2.5x leverage."

---

## Act VI: The Compounding Question -- Half Kelly in Production

### Capital Management: Three Philosophies in 49 Lines

The blog's battle comes to its most direct expression in `syscore/capital.py` -- three short functions that encode three different philosophies of capital management.

**Fixed Capital (The Pure Utilitarian):**
```python
# syscore/capital.py, lines 20-24
def fixed_capital(system, **ignored_args):
    multiplier = copy(system.accounts.portfolio().percent)
    multiplier[:] = 1.0
    return multiplier
```
No compounding at all. Your position size stays constant regardless of P&L. If you lose 50%, you now have twice the leverage relative to your remaining equity. The system's own documentation (`docs/production.md`) warns: "This isn't recommended as it isn't 'Kelly compatible', and if you lose money you will make exponentially increasing losses as a % of your account value."

**Full Compounding (The Pure Kellyite):**
```python
# syscore/capital.py, lines 27-32
def full_compounding(system, **ignored_args):
    pandl = system.accounts.portfolio().percent
    multiplier = 1.0 + (pandl / 100.0)
    multiplier = multiplier.cumprod().ffill()
    return multiplier
```
Full reinvestment. Every dollar of profit increases your position size. Every dollar of loss decreases it. Mathematically optimal for compound growth, but the volatility of the multiplier itself can be stomach-churning. A 30% drawdown followed by a 30% recovery doesn't get you back to even -- it gets you to 0.7 * 1.3 = 0.91. The path matters.

**Half Compounding (The Compromise):**
```python
# syscore/capital.py, lines 35-48
def half_compounding(system, **ignored_args):
    pandl = system.accounts.portfolio().percent.curve().ffill().diff()
    multiplier = 1.0
    multiplier_list = []
    for daily_return in pandl:
        actual_return = multiplier * daily_return / 100.0
        multiplier = multiplier * (1.0 + actual_return)
        multiplier = np.nanmin([multiplier, 1.0])  # <-- THE KEY LINE
        multiplier_list.append(multiplier)
    return pd.Series(multiplier_list, index=pandl.index).ffill()
```

Notice line 43: `multiplier = np.nanmin([multiplier, 1.0])`. The multiplier can *decrease* when you lose money (Kelly-compatible -- reduce leverage after losses), but it's *capped at 1.0* (never increase leverage above the original). Profits accrue to your account but don't increase position sizes. Losses do reduce position sizes.

This is the blog's compromise formula made real. In production documentation: "This is 'Kelly compatible' because losses reduce capital, but your returns will not be compounded. It's the method I use myself."

Why is this "half" Kelly? Consider what full Kelly would do:
- Win 10% -> multiply capital by 1.1 -> positions grow 10%
- Lose 10% -> multiply capital by 0.9 -> positions shrink 10%

Half compounding does:
- Win 10% -> capital stays at 1.0 (capped) -> positions unchanged
- Lose 10% -> multiply capital by 0.9 -> positions shrink 10%

You get the protective downside (smaller positions after losses) without the volatile upside (bigger positions after wins). It's asymmetric, and deliberately so. The blog's formula `s* = min(r/s, s_max)` is echoed here: never let compound growth push you above your risk target.

---

## Act VII: The Performance Scoreboard -- How Do We Know It's Working?

### Arithmetic Returns: A Deliberate Choice

The system calculates P&L using **arithmetic returns**, not geometric (log) returns:

```python
# systems/accounts/curves/account_curve.py, line 200-201
def curve(self):
    return self.cumsum().ffill()
```

Daily P&L is summed, not compounded multiplicatively. The P&L chain in `systems/accounts/pandl_calculators/pandl_calculation.py` works like this:

1. **Points P&L**: `positions.shift(1) * price_returns` (yesterday's position times today's price change)
2. **Instrument currency**: multiply by value per point
3. **Base currency**: multiply by FX rate
4. **Percentage**: divide by capital, multiply by 100

This might seem surprising given the blog's emphasis on geometric growth, but it's the right choice for a system that uses volatility targeting. With vol targeting, position sizes adjust to keep dollar-volatility roughly constant. In this regime, arithmetic returns are the natural unit -- each day's P&L is drawn from (approximately) the same distribution, so summing them makes sense.

### The Full Statistical Toolkit

The `accountCurve` class in `systems/accounts/curves/account_curve.py` computes an impressive suite of performance metrics:

```python
# account_curve.py, lines 234-241
def sharpe(self):
    mean_return = self.ann_mean()
    vol = self.ann_std()
    return mean_return / vol
```

The Sharpe Ratio -- the Kellyite's favourite metric, and the Utilitarian's objective function. Risk-free rate is assumed zero (consistent with the blog's treatment).

Beyond Sharpe, you get:
- **Sortino ratio** (lines 266-277): Uses only downside deviation -- penalises losses more than volatility per se
- **Calmar ratio** (lines 260-261): Annual return / worst drawdown -- how well does growth compensate for the worst pain?
- **Average return to drawdown** (lines 263-264): A smoother version of Calmar
- **Hit rate** (lines 318-323): Percentage of positive days
- **Profit factor** (lines 315-316): Total gains / total losses
- **t-statistic and p-value** (lines 332-336): Is this return statistically distinguishable from zero?
- **Quantile ratios** (lines 338-348): Do tails look Gaussian, or are there surprises?

The t-test is particularly relevant to the blog's argument. With a Sharpe of 0.5 and 10 years of data, the t-stat is approximately `0.5 * sqrt(10) = 1.58`. That's not even significant at the 10% level. The data is literally telling you: "I can't be confident this strategy makes money." The code measures this honestly.

### Cost Modeling: Reality Bites

The system models costs in two ways:

1. **SR costs** (`pandl_SR_cost.py`): Expresses costs as a reduction in Sharpe Ratio. A cost of 0.1 SR means that after costs, your strategy's effective Sharpe drops by 0.1.
2. **Cash costs** (`pandl_cash_costs.py`): Models actual per-trade and per-contract costs, including holding costs for rolling futures.

Both affect the net account curve, which is what you'd actually experience:

```python
# pandl_generic_costs.py, lines 105-108
def _add_gross_and_costs(gross, costs):
    net = gross.add(costs, fill_value=0)
    return net
```

Costs are negative, so net = gross + costs < gross. The three views (gross, net, costs) give you a complete picture of where your returns come from and where they go.

---

## Act VIII: Connecting It All -- The Four Layers in Concert

Let's trace the complete journey of a single trading position, showing where each layer of the Kelly-vs-Classical framework operates.

### Step 1: Raw Data -> Volatility (Common Ground)

The `RawData` stage (`systems/rawdata.py`) produces clean prices and volatility estimates. The `mixed_vol_calc()` blends fast (35-day EWMA) and slow (10-year EWMA) volatility at 70/30 weights, with a floor at the 5th percentile of 500-day rolling vol.

**Blog connection:** Both camps agree you need good vol estimates. The system invests heavily here because vol is relatively predictable (R^2 ~ 0.6 for monthly vol predictions).

### Step 2: Trading Rules -> Forecasts (The Signal)

Trading rules like EWMAC (exponentially weighted moving average crossover) in `systems/provided/rules/ewmac.py` generate raw forecasts. These are scaled to a common scale (average absolute value of 10) by `ForecastScaleCap` using the `forecast_scalar()` function in `sysquant/estimators/forecast_scalar.py`.

This normalisation is itself an uncertainty-aware design -- it ensures that no single rule can dominate just because it happens to produce larger numbers. A forecast of +20 means "I'm twice as bullish as average" regardless of which rule generated it.

### Step 3: Forecast Combination (Kellyite Weighing In)

The `ForecastCombine` stage weights multiple forecasts per instrument. The default method is handcrafted optimisation with aggressive uncertainty controls:

```yaml
# defaults.yaml
forecast_weight_estimate:
   method: handcraft
   shrinkage_SR: 0.9          # 90% prior, 10% data for means
   shrinkage_corr: 0.5        # 50/50 for correlations
   ann_target_SR: 0.5         # Conservative Sharpe prior
   equalise_SR: False          # Allow some SR differentiation here
   equalise_vols: True         # But normalise volatilities
```

This is where Kelly's influence enters through the back door. The weights are SR-adjusted, meaning rules with higher backtested Sharpe Ratios get *slightly* more weight -- but only to the degree that the data supports it after shrinkage and bootstrap adjustment.

The Forecast Diversification Multiplier (FDM) then boosts the combined forecast to capture diversification between rules, computed as `1 / sqrt(W * H * W^T)` and capped at 2.5.

### Step 4: Position Sizing (The Utilitarian's Turn)

The `PositionSizing` stage applies the investor's risk appetite:

```
subsystem_position = (daily_cash_vol_target / instr_value_vol) * (forecast / 10)
```

The forecast is divided by 10 (the target absolute value) to normalise it. A forecast of +10 means "hold an average-sized long position" and the vol scalar determines what "average-sized" means in contract terms.

**Blog connection:** This is `f* = s_max / s` -- leverage determined by risk tolerance, not Kelly.

### Step 5: Portfolio Weighting (The Full Debate)

The `Portfolios` stage (`systems/portfolio.py`) determines instrument weights. Here the uncertainty controls are at maximum:

```yaml
# defaults.yaml
instrument_weight_estimate:
   method: handcraft
   equalise_SR: True           # COMPLETELY ignore SR differences!
   ann_target_SR: 0.5
   shrinkage_corr: 0.50
```

With `equalise_SR: True`, the system assumes every instrument has the same Sharpe Ratio. The optimizer becomes a pure minimum-variance / risk-parity allocator.

Why so conservative at the instrument level but not at the forecast level? Because you typically have much more data about the relative performance of trading rules (which can be pooled across instruments) than about the relative performance of different futures markets. The blog's uncertainty argument bites harder at the instrument level.

The Instrument Diversification Multiplier (IDM) then scales up positions to capture cross-instrument diversification benefits, again capped at 2.5.

### Step 6: Risk Overlay (The Uncertainty Premium)

The risk overlay checks four independent risk measures and scales everything down if any limit is breached. This is pure uncertainty premium -- protection against model failure, correlation breakdown, and volatility shocks.

### Step 7: Capital Multiplier (Kelly's Final Word)

The capital multiplier adjusts for actual P&L. With half compounding: losses reduce capital (Kelly-compatible), profits don't increase it beyond the starting point (conservative). With full compounding: both directions adjust. With fixed capital: no adjustment at all.

### The Complete Formula

Putting it all together, the actual position for instrument `i` at time `t` is:

```
position = (vol_scalar * combined_forecast / 10)
           * instrument_weight
           * IDM
           * risk_overlay_multiplier
           * capital_multiplier
```

Where:
- `vol_scalar` = Utilitarian risk appetite
- `combined_forecast` = signal strength (Kelly-influenced through SR-aware rule weighting)
- `instrument_weight` = portfolio allocation (heavily uncertainty-dampened)
- `IDM` = diversification reward (capped at 2.5)
- `risk_overlay_multiplier` = safety net (0 to 1, only down)
- `capital_multiplier` = compounding method (fixed / full / half)

Every multiplicative layer either expresses risk appetite, captures Kelly-like growth optimisation, or applies an uncertainty premium. The debate is resolved not by choosing a side but by layering them.

---

## Act IX: The Numbers -- What This Looks Like in Practice

Let's work through the blog's examples with the system's defaults.

### Case One: Low Sharpe Ratio (Long-only asset allocation)

Blog says: SR = 0.20, expected return = 2%, std = 10%.
- Kelly optimal risk: SR = 20%, leverage = 2
- Gung-ho Utilitarian (30% risk appetite): leverage = 3

**pysystemtrade's answer:**
- `percentage_vol_target: 16.0` -> leverage = 16/10 = 1.6
- SR equalisation means the system doesn't even look at the 0.20 SR for instrument weighting
- Risk overlay prevents any accidental over-gearing
- Half-compounding prevents profits from pushing leverage higher

The system would use leverage of about 1.6 -- below Kelly (2.0), below the gung-ho Utilitarian (3.0), and in line with the blog's compromise: `s* = min(SR, s_max) = min(0.20, 0.16) = 0.16`.

### Case Two: High Sharpe Ratio (Sophisticated quant fund)

Blog says: SR = 1.0, expected return = 5%, std = 5%.
- Kelly optimal risk: SR = 100%, leverage = 20 (!!)
- Conservative Utilitarian (15% risk appetite): leverage = 3

**pysystemtrade's answer:**
- `percentage_vol_target: 16.0` -> leverage = 16/5 = 3.2
- Even with SR adjustment, 90% shrinkage toward 0.5 SR prior means the system doesn't aggressively tilt
- Risk overlay's leverage cap (default 2.5 if enabled) would pull this back further
- Half-compounding caps upside leverage

The system stays well below full Kelly (leverage 20). The 16% vol target acts as the binding constraint. Blog's formula: `s* = min(1.0, 0.16) = 0.16`.

### The Blog's Uncertainty Table

The blog gives this table for varying degrees of uncertainty appetite (assuming SR = 0.5, std = 10%, 10 years of data):

| Confidence | Optimal Risk | Optimal Leverage |
|-----------|-------------|-----------------|
| 10% | 9.2% | 0.93 |
| 15% | 17.1% | 1.71 |
| 20% | 23.2% | 2.32 |
| 30% | 33.3% | 3.33 |
| 50% (full Kelly) | 50.0% | 5.00 |

The system's `mini_bootstrap_ratio_given_SR_diff()` implements exactly this kind of confidence-adjusted estimation. With `p_step=0.2`, it samples at the 20th, 40th, 60th, and 80th percentiles of the confidence distribution and averages the results. This effectively integrates over the uncertainty rather than picking any single confidence level.

Combined with 90% shrinkage toward SR of 0.5 and the 16% vol target cap, the system naturally operates in the 10-20% risk range -- the blog's "sensible uncertainty-averse" sweet spot.

The blog notes that "the famous half Kelly (leverage of 2.5) corresponds to a confidence interval of about 22%." The system doesn't explicitly use half Kelly for leverage, but the combination of its conservative defaults produces similar results through a different, more robust mechanism.

---

## Act X: The Configuration as Philosophy

The defaults in `sysdata/config/defaults.yaml` encode an entire philosophy. Let's read them as a manifesto:

```yaml
# "I'm a Utilitarian who acknowledges Kelly"
percentage_vol_target: 16.0
capital_multiplier:
   func: syscore.capital.fixed_capital       # Conservative default

# "I respect return estimates but don't trust them"
forecast_weight_estimate:
   method: handcraft                          # Not pure mean-variance
   shrinkage_SR: 0.9                          # 90% weight on the conservative prior
   shrinkage_corr: 0.5                        # Blend sample correlation with average
   ann_target_SR: 0.5                         # Prior: everything has SR of 0.5
   equalise_SR: False                         # Allow some differentiation for rules

# "I really don't trust instrument-level return estimates"
instrument_weight_estimate:
   method: handcraft
   equalise_SR: True                          # Ignore ALL SR differences!
   ann_target_SR: 0.5
   shrinkage_corr: 0.50

# "And I have a safety net just in case" (commented out by default)
# risk_overlay:
#   max_risk_fraction_normal_risk: 0.9
#   max_risk_fraction_stdev_risk: 0.6
#   max_risk_limit_sum_abs_risk: 0.9
#   max_risk_leverage: 2.5
```

The gradient of trust is clear:

| What | Trust Level | Why |
|------|------------|-----|
| **Volatility estimates** | High (used directly) | Vol is predictable from past data |
| **Correlation estimates** | Medium (50% shrinkage) | Correlations are measurable but noisy |
| **Forecast-level SR differences** | Low (90% shrinkage to prior) | Rule performance can be pooled across instruments |
| **Instrument-level SR differences** | None (equalised) | Can't reliably predict which market will do better |

This exactly mirrors the blog's observation: "we are relatively good in finance at predicting risk using past data... The Sharpe Ratio is the key factor in working out the optimal leverage and risk target... The sampling distribution of Sharpe Ratio is highly uncertain."

---

## Act XI: The Dynamic System -- Real-World Execution

### Transaction Cost Awareness

For production use, the system includes a dynamic optimizer (`systems/provided/dynamic_small_system_optimise/`) that adds real-world constraints:

```
objective = tracking_error_vs_optimal + transaction_costs + constraint_penalties
```

The tracking error term (`sqrt((w_opt - w_current)^T * Sigma * (w_opt - w_current))`) penalises deviation from the theoretically optimal portfolio. The transaction cost term penalises trading. The optimizer finds the sweet spot: get close to optimal without trading too much.

This is deeply relevant to the Kelly story because transaction costs are an additional drag on compound growth. A portfolio that rebalances to "optimal" every day might spend so much in costs that it underperforms a more patient portfolio. The dynamic optimizer explicitly models this trade-off.

### Order Simulation

The system can simulate realistic order execution with:
- Round-trip costs per contract
- Slippage from market impact
- Holding costs for futures rolls
- Position rounding to integer contracts

This matters because a theoretical Kelly leverage of 5.0 is meaningless if you can only hold integer contracts and each trade costs 0.1% in slippage. The gap between theoretical and achievable compounds over time.

---

## Epilogue: Neither Side Won, and That's the Point

The blog's conclusion is: "Both the Kellyites and the Utilitarians have good points to make... But both are missing the real point, which is that there is a lot of uncertainty about what the Sharpe Ratio and hence optimal leverage really is."

pysystemtrade embodies this conclusion in code. It doesn't pick a side. Instead, it builds a system where:

1. **The Utilitarian** sets the risk budget (`percentage_vol_target`) and provides the ceiling
2. **The Kellyite** influences allocation within that budget (Sharpe maximisation for weights)
3. **The Uncertainty-Aware Engineer** dampens everything through shrinkage, bootstrapping, and equalisation
4. **The Risk Manager** adds a final safety net (risk overlay)

The result is a system where the theoretical debate becomes almost moot. With 90% shrinkage toward a Sharpe of 0.5, a 16% vol target, and SR equalisation across instruments, the system's actual leverage is always well below full Kelly. The "battle" between Kelly and Classical disappears into a fog of parameter uncertainty -- which is exactly what the blog predicted.

And that might be the most profound lesson: in a world of deep uncertainty about future returns, the precise theoretical framework you use for optimisation matters much less than your humility about the inputs. The system's engineering effort is overwhelmingly concentrated not on *optimising* but on *not being wrong* -- shrinkage, bootstrapping, flooring, capping, overlaying. The sexy equation `f* = r/s^2` gets one function. The machinery to make sure you don't trust that equation too much gets a dozen files and thousands of lines of code.

The elephant in the room won. And the codebase is better for it.

---

## Appendix A: Quick Reference -- Where to Find Things

### Key Files by Blog Concept

| Blog Concept | Primary Code Location | What to Look For |
|-------------|----------------------|-----------------|
| Utilitarian risk targeting | `systems/positionsizing.py:480-495` | `annual_cash_vol_target()`, `percentage_vol_target` |
| Kelly optimal leverage | `sysquant/optimisation/shared.py:175-181` | `neg_SR()` -- Sharpe maximisation |
| Sharpe Ratio estimation | `systems/accounts/curves/account_curve.py:234-241` | `sharpe()` method |
| SR variance / sampling distribution | `sysquant/optimisation/SR_adjustment.py:229-236` | `calculate_omega_difference()` |
| Uncertainty appetite / confidence | `sysquant/optimisation/SR_adjustment.py:108-155` | `mini_bootstrap_ratio_given_SR_diff()` |
| Half Kelly | `syscore/capital.py:35-48` | `half_compounding()` |
| Risk appetite parameter | `sysdata/config/defaults.yaml` | `percentage_vol_target: 16.0` |
| Shrinkage to conservative prior | `sysquant/optimisation/optimisers/shrinkage.py` | `shrinkage_optimisation()` |
| SR equalisation | `sysquant/estimators/estimates.py:234-261` | `shrink_SR_with_lists()` |
| Correlation uncertainty | `sysquant/optimisation/full_handcrafting.py:132-175` | Fisher transform bootstrap |
| Risk overlay (uncertainty premium) | `systems/risk_overlay.py` | `get_risk_multiplier()` |
| Diversification multiplier | `sysquant/estimators/diversification_multipliers.py:75-96` | `diversification_mult_single_period()` |
| Handcrafted optimisation | `sysquant/optimisation/optimisers/handcraft.py` | `handcraft_optimisation()` |
| Full portfolio pipeline | `systems/portfolio.py:178-227` | `get_notional_position()` |
| Account curves / P&L | `systems/accounts/curves/account_curve.py` | `accountCurve` class |
| Volatility estimation | `sysquant/estimators/vol.py` | `mixed_vol_calc()`, `robust_vol_calc()` |
| Covariance construction | `sysquant/optimisation/shared.py:53-55` | `sigma_from_corr_and_std()` |
| Position sizing formula | `systems/positionsizing.py:86-133` | `get_subsystem_position()` |
| Transaction cost modelling | `systems/accounts/pandl_calculators/pandl_SR_cost.py` | SR-based cost model |
| Dynamic production optimizer | `systems/provided/dynamic_small_system_optimise/` | Tracking error + costs |

### System Architecture Files

| Component | File |
|-----------|------|
| Main system orchestrator | `systems/basesystem.py` |
| Stage base class | `systems/stage.py` |
| System cache | `systems/system_cache.py` |
| Configuration class | `sysdata/config/configdata.py` |
| Default configuration | `sysdata/config/defaults.yaml` |
| Data abstraction | `sysdata/sim/sim_data.py` |

### The Seven Stages

| Stage | File | Purpose |
|-------|------|---------|
| RawData | `systems/rawdata.py` | Prices, volatility, FX |
| Rules | `systems/forecasting.py` | Trading rule forecasts |
| ForecastScaleCap | `systems/forecast_scale_cap.py` | Normalise and cap forecasts |
| ForecastCombine | `systems/forecast_combine.py` | Blend forecasts per instrument |
| PositionSizing | `systems/positionsizing.py` | Convert forecasts to positions |
| Portfolios | `systems/portfolio.py` | Weight instruments, apply IDM |
| Account | `systems/accounts/accounts_stage.py` | Calculate P&L and statistics |

## Appendix B: Key Formulas Cross-Referenced

### From the Blog to the Code

| Blog Formula | Code Implementation | File:Lines |
|-------------|-------------------|------------|
| `f* = r / s^2` (Kelly leverage) | `neg_SR()` maximises return/risk | `shared.py:175-181` |
| `s* = r / s` (Kelly risk target = SR) | SR as objective function | `shared.py:175-181` |
| `f* = s_max / s` (Utilitarian leverage) | `vol_scalar = cash_vol_target / instr_value_vol` | `positionsizing.py:198-203` |
| `s* = min(r/s, s_max)` (Compromise) | Vol target caps Kelly; half-compounding caps growth | `positionsizing.py` + `capital.py` |
| `w = (1 + 0.5*SR^2) / N` (SR variance) | `omega = std / sqrt(years_of_data)` | `SR_adjustment.py:229-236` |
| Confidence-adjusted Kelly | `norm(mean_diff, omega).ppf(confidence)` | `SR_adjustment.py:214-226` |
| Portfolio risk `sqrt(w^T Sigma w)` | `weights.portfolio_stdev(corrmatrix)` | `weights.py:154-168` |
| Diversification multiplier `1/risk` | `dm = min(1.0 / risk, dm_max)` | `diversification_multipliers.py:94` |

---

*"The right amount of complexity is the minimum needed for the current task." -- But when the task is surviving uncertainty, that minimum is more than you'd think.*
