# pysystemtrade for Factor Quants: A Paradigm Shift Guide

> If you come from bt, alphalens, zipline, or quantconnect -- where you rank stocks by PE/PB into quantiles, rebalance monthly, and measure factor returns -- **this repo will feel alien**. This guide explains why, and maps your existing mental model onto pysystemtrade's way of thinking.

---

## Table of Contents

1. [The 30-Second Summary](#the-30-second-summary)
2. [Two Completely Different Worlds](#two-completely-different-worlds)
3. [What You Trade: Futures, Not Stocks](#what-you-trade-futures-not-stocks)
4. [Signals: Continuous Forecasts, Not Quantile Rankings](#signals-continuous-forecasts-not-quantile-rankings)
5. [Position Sizing: Volatility Targeting, Not Equal Weight](#position-sizing-volatility-targeting-not-equal-weight)
6. [The Seven-Stage Pipeline](#the-seven-stage-pipeline)
7. [Trading Rules: The Equivalent of Your "Factors"](#trading-rules-the-equivalent-of-your-factors)
8. [Combining Signals: Weighted Average, Not Quantile Intersection](#combining-signals-weighted-average-not-quantile-intersection)
9. [Portfolio Construction: Risk Parity, Not Market Cap](#portfolio-construction-risk-parity-not-market-cap)
10. [Rebalancing: Continuous With Buffers, Not Calendar-Based](#rebalancing-continuous-with-buffers-not-calendar-based)
11. [If You Need Monthly/Quarterly/Yearly Rebalancing](#if-you-need-monthlyquarterlyyearly-rebalancing)
12. [Data Model: Futures Roll Mechanics](#data-model-futures-roll-mechanics)
13. [The Full Pipeline: A Worked Example](#the-full-pipeline-a-worked-example)
14. [System Architecture and Code Layout](#system-architecture-and-code-layout)
15. [From Backtest to Live Trading](#from-backtest-to-live-trading)
16. [The Philosophy: Rob Carver's Framework](#the-philosophy-rob-carvers-framework)
17. [Concept Translation Table](#concept-translation-table)
18. [Where to Go Next](#where-to-go-next)

---

## The 30-Second Summary

| What you know (bt / alphalens) | What pysystemtrade does |
|---|---|
| Rank 500 stocks by PE ratio | Generate a continuous forecast for each of ~100 futures markets |
| Sort into quintiles (Q1 = cheap, Q5 = expensive) | Scale the forecast to a standard range (-20 to +20) |
| Go long Q1, short Q5 | Go long or short each individual instrument, sized proportionally to forecast strength |
| Rebalance monthly/quarterly | Adjust continuously, trade only when position drifts outside a buffer band |
| Equal-weight or cap-weight the portfolio | Volatility-target each position so the whole portfolio hits a risk budget (e.g. 16% annual vol) |
| Measure factor returns with IC, turnover, quintile spread | Measure Sharpe ratio, account curve, drawdowns per rule and per instrument |

**The core philosophical difference:** Your framework asks *"which stocks are cheap?"* and builds a portfolio from the answer. This framework asks *"what is each market telling me, and how hard?"* and sizes positions proportionally to the signal strength, across asset classes, while targeting a fixed risk level.

---

## Two Completely Different Worlds

### The Factor World (what you know)

```
Universe: 500-3000 stocks
Signal:   PE ratio, PB ratio, momentum score, quality score
Method:   Rank stocks, form quantile portfolios
Position: Equal-weight within each quantile
Sizing:   1/N or market-cap weighted
Horizon:  Rebalance monthly or quarterly
Shorting: Often long-only, or synthetic L/S via quantile spread
```

### The Systematic Futures World (pysystemtrade)

```
Universe: ~100-400 futures contracts (equity indices, bonds, FX, commodities, rates)
Signal:   Trend strength, carry, breakout, acceleration (continuous values)
Method:   Each signal produces a forecast (-20 to +20) per instrument
Position: Sized proportionally to forecast * volatility target / instrument volatility
Sizing:   Volatility-targeted (each position contributes equal risk)
Horizon:  Continuous; trade when position drifts outside buffer
Shorting: Native -- futures are symmetric, long and short are equally easy
```

### Why futures?

- **Symmetric long/short**: Going short S&P futures is as easy as going long. No borrow costs, no short squeezes.
- **Built-in leverage**: Futures use margin, so you can diversify across 50+ markets with moderate capital.
- **Multi-asset class**: One unified framework trades equity indices, government bonds, currencies, gold, oil, wheat, interest rates.
- **Carry is observable**: The price difference between contract months directly tells you the cost/benefit of holding.
- **Liquid and standardized**: No worrying about small-cap illiquidity or corporate actions.

---

## What You Trade: Futures, Not Stocks

### Instruments

pysystemtrade ships with data for **400+ futures instruments** across 9 asset classes:

| Asset Class | Examples | Count |
|---|---|---|
| **Equity indices** | S&P 500, DAX, NIKKEI, EUROSTOXX, Hang Seng | ~50 |
| **Bonds** | US 10Y, Bund, BTP, Gilts, JGB | ~30 |
| **FX** | EUR, GBP, JPY, AUD, MXP, BRL | ~50 |
| **Metals** | Gold, Silver, Copper, Aluminium, Platinum | ~15 |
| **Energy** | Crude Oil, Brent, Natural Gas, Gasoline | ~10 |
| **Agriculture** | Corn, Wheat, Soybeans, Coffee, Sugar, Cotton | ~40 |
| **Short-term rates** | SOFR, Euribor, SONIA | ~15 |
| **Volatility** | VIX, V2X, VNKI | ~5 |
| **Crypto** | Bitcoin, Ethereum | ~2 |

Configuration: `data/futures/csvconfig/instrumentconfig.csv`

### The data you don't have in stock land

Futures expire. A "March 2025 Corn" contract becomes worthless after March. To create a continuous price series, pysystemtrade:

1. Tracks **individual contract prices** (each expiring contract has its own OHLCV series)
2. Maintains a **roll calendar** that says when to switch from one contract to the next
3. Stitches contracts together using **panama back-adjustment** (adding/subtracting the price gap at each roll)
4. Produces an **adjusted price** series -- one continuous line you can feed to a trading rule

This is handled automatically. You work with `system.data.daily_prices("CORN")` and get a clean series. But understanding that this isn't a simple stock close price is important: the adjusted price can go negative, and the absolute level is meaningless (only changes matter).

#### The Three-Tier Price Stack

```
Layer 1: Raw Contract Prices
         CORN_20250300.csv  →  OHLCV for March 2025 Corn
         CORN_20250500.csv  →  OHLCV for May 2025 Corn
         (stored in MongoDB/Parquet in production)
              ↓
Layer 2: Multiple Prices  (data/futures/multiple_prices_csv/CORN.csv)
         Tracks 3 contracts simultaneously:
         PRICE (current held), CARRY (for carry calc), FORWARD (next contract)
         + which contract ID each refers to
              ↓
Layer 3: Adjusted Prices  (data/futures/adjusted_prices_csv/CORN.csv)
         Single continuous price series via panama stitching
         This is what trading rules consume
```

#### Carry Data: Something stocks don't have

The price difference between near and far futures contracts is **carry** -- it tells you the cost or benefit of holding a position. This is a major alpha source in futures and has no stock equivalent.

```python
# pysystemtrade gives you carry data:
raw_carry = system.rawdata.raw_carry("CORN")
# Returns annualized carry signal: (PRICE - CARRY) / (contract distance in years)
```

---

## Signals: Continuous Forecasts, Not Quantile Rankings

This is the single biggest mental shift. Let's compare directly.

### How you generate signals (factor world)

```python
# Alphalens / bt style:
pe_ratios = get_fundamentals("PE_RATIO")  # 500 stocks
ranked = pe_ratios.rank(pct=True)          # Percentile rank: 0.0 to 1.0
quintile = pd.qcut(ranked, 5, labels=[1,2,3,4,5])

# Position:
long  = stocks where quintile == 1  # Cheapest 20%
short = stocks where quintile == 5  # Most expensive 20%
# All positions same size within each quintile
```

**Information lost**: A stock at the 1st percentile gets the same weight as one at the 19th percentile. You threw away the gradient.

### How pysystemtrade generates signals

```python
# pysystemtrade style:
# A trading rule produces a CONTINUOUS forecast for each instrument

def ewmac_forecast(price, vol, Lfast=16, Lslow=64):
    """Exponentially weighted moving average crossover"""
    fast_ewma = price.ewm(span=Lfast).mean()
    slow_ewma = price.ewm(span=Lslow).mean()
    raw_forecast = (fast_ewma - slow_ewma) / vol
    return raw_forecast

# Output for CORN on a given day: +7.3
# Output for GOLD on the same day: -12.1
# Output for S&P 500: +2.8
```

**Key properties of a forecast:**

| Property | Value |
|---|---|
| Range | Continuous. Raw values are unbounded |
| After scaling | Mean absolute value normalized to 10 |
| After capping | Clipped to [-20, +20] |
| Meaning of +10 | "Average conviction, go long" |
| Meaning of +20 | "Maximum conviction, go long as much as the system allows" |
| Meaning of 0 | "No view, flat position" |
| Meaning of -15 | "Strong conviction, go short" |

**Information preserved**: A forecast of +18 produces a position 1.8x larger than a forecast of +10. The gradient is the signal.

### The Scaling Pipeline

Raw forecasts from different rules have different magnitudes. An EWMAC crossover might output values in the range [-2, +2], while a breakout rule might output [-40, +40]. To make them comparable:

```
Raw Forecast  →  × Forecast Scalar  →  Scaled Forecast  →  clip(-20, +20)  →  Capped Forecast
   (any range)      (estimated or         (avg |x| ≈ 10)      (hard limits)       (final signal)
                      fixed)
```

The **forecast scalar** is calibrated so the average absolute value of the scaled forecast is 10. This means:
- A forecast of +10 = "average strength signal, go long"
- A forecast of +20 = "maximum strength signal, go long at 2x average"
- This scaling makes every rule's output directly comparable

Code: `systems/forecast_scale_cap.py`

---

## Position Sizing: Volatility Targeting, Not Equal Weight

### How you size positions (factor world)

```python
# Typical factor portfolio:
n_stocks = len(long_basket)
weight_per_stock = 1.0 / n_stocks  # Equal weight
# or
weight_per_stock = market_cap / total_market_cap  # Cap-weighted

# Problem: TSLA (60% vol) gets same weight as JNJ (15% vol)
# Your portfolio vol is dominated by the high-vol names
```

### How pysystemtrade sizes positions

The system works backwards from a **risk budget**:

```
"I want my portfolio to have 16% annual volatility."

Therefore:
  Annual cash vol target = $1,000,000 × 16% = $160,000
  Daily cash vol target  = $160,000 / √252 ≈ $10,082

For each instrument:
  How many contracts make one day's P&L = $10,082?
  → That's the "volatility scalar"
  → Then multiply by (forecast / 10) to scale by signal strength
```

**The formula:**

```
subsystem_position = vol_scalar × (combined_forecast / average_absolute_forecast)

where:
  vol_scalar = daily_cash_vol_target / instrument_value_vol
  instrument_value_vol = price × point_value × daily_%_vol × fx_rate
  average_absolute_forecast = 10  (by convention)
```

**Example:**
- S&P E-mini: price = 5000, point value = $50, daily vol = 1.2%, fx = 1.0
  - instrument_value_vol = 5000 × 50 × 0.012 × 1 = $3,000/day
  - vol_scalar = 10,082 / 3,000 = 3.36 contracts
  - If forecast = +15: position = 3.36 × (15/10) = **5.04 contracts long**
  - If forecast = -5: position = 3.36 × (-5/10) = **-1.68 contracts short**

**Why this matters:**
- Every instrument contributes roughly equal risk to the portfolio
- Volatile instruments get smaller positions; calm instruments get larger positions
- Position sizes adjust automatically as volatility changes
- This is "risk parity" at the instrument level -- something factor frameworks rarely do

Code: `systems/positionsizing.py`

---

## The Seven-Stage Pipeline

pysystemtrade processes data through 7 sequential stages. Each stage is a Python class that plugs into a `System` object. Think of it as an assembly line:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        THE SYSTEM PIPELINE                         │
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌────────────┐  │
│  │ Stage 1  │   │ Stage 2  │   │   Stage 3    │   │  Stage 4   │  │
│  │ RawData  │──▶│  Rules   │──▶│ForecastScale │──▶│ Forecast   │  │
│  │          │   │          │   │    Cap       │   │  Combine   │  │
│  │ prices,  │   │ generate │   │ normalize to │   │ weighted   │  │
│  │ vol, FX  │   │ raw      │   │ avg|x|=10,  │   │ average +  │  │
│  │          │   │ forecasts│   │ cap at ±20   │   │ FDM boost  │  │
│  └──────────┘   └──────────┘   └──────────────┘   └─────┬──────┘  │
│                                                          │         │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐         │         │
│  │ Stage 7  │   │ Stage 6  │   │   Stage 5    │◀────────┘         │
│  │ Accounts │◀──│Portfolio │◀──│  Position    │                    │
│  │          │   │          │   │   Sizing     │                    │
│  │ P&L,     │   │ instrmnt │   │ vol target + │                    │
│  │ Sharpe,  │   │ weights, │   │ forecast →   │                    │
│  │ stats    │   │ IDM,     │   │ contracts    │                    │
│  │          │   │ risk     │   │              │                    │
│  └──────────┘   └──────────┘   └──────────────┘                    │
└─────────────────────────────────────────────────────────────────────┘
```

### Stage 1: RawData -- "Get clean prices and volatility"
- Loads adjusted prices, calculates daily returns and percentage volatility
- Blends fast (35-day) and slow (10-year) volatility estimates
- Converts prices to base currency using FX rates
- **Factor world equivalent**: Loading your stock universe and computing trailing volatility

### Stage 2: Rules -- "Generate raw forecasts"
- Runs each trading rule function on each instrument
- Example: Run EWMAC(16,64) on Corn → raw forecast of +3.7
- Example: Run Carry on Corn → raw forecast of +0.8
- **Factor world equivalent**: Computing your factor scores (PE rank, momentum score, etc.)

### Stage 3: ForecastScaleCap -- "Normalize and cap"
- Multiplies raw forecast by a scalar so average |forecast| = 10
- Clips to [-20, +20]
- **Factor world equivalent**: Z-scoring your factor, then winsorizing at ±3

### Stage 4: ForecastCombine -- "Blend multiple rules"
- Weighted average of all rule forecasts for each instrument
- Multiplied by **Forecast Diversification Multiplier** (FDM) -- a boost for using uncorrelated rules
- **Factor world equivalent**: Combining your PE, momentum, and quality scores into a composite alpha

### Stage 5: PositionSizing -- "Convert signal to contracts"
- Uses the volatility targeting formula to turn forecast into a number of contracts
- **Factor world equivalent**: No direct equivalent. Factor frameworks typically don't do this -- they just equal-weight stocks in each bucket

### Stage 6: Portfolios -- "Allocate across instruments"
- Applies instrument weights (how much of your capital goes to Corn vs S&P vs Bund)
- Applies **Instrument Diversification Multiplier** (IDM) -- a boost for trading uncorrelated markets
- Applies risk overlay (reduce if aggregate leverage too high)
- **Factor world equivalent**: Sector/country allocation, but based on risk contribution rather than market cap

### Stage 7: Accounts -- "Measure performance"
- Calculates P&L at every level: per rule, per instrument, per rule-instrument combo, whole portfolio
- Reports Sharpe ratio, drawdown, skew, costs
- **Factor world equivalent**: Your alphalens tear sheet, but for positions that change daily with varying sizes

### In code:

```python
from systems.provided.futures_chapter15.basesystem import futures_system
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData

# Build the system -- all 7 stages assembled automatically
system = futures_system()

# Pull results from any stage:
system.rules.get_raw_forecast("CORN", "ewmac16_64")       # Stage 2
system.forecastScaleCap.get_capped_forecast("CORN", "ewmac16_64")  # Stage 3
system.combForecast.get_combined_forecast("CORN")           # Stage 4
system.positionSize.get_subsystem_position("CORN")          # Stage 5
system.portfolio.get_notional_position("CORN")              # Stage 6
system.accounts.portfolio()                                  # Stage 7: full P&L
```

Code: `systems/basesystem.py`, `systems/provided/futures_chapter15/basesystem.py`

---

## Trading Rules: The Equivalent of Your "Factors"

If you think of PE ratio, momentum, quality, and value as "factors," then pysystemtrade's equivalent is **trading rules**. But they work differently.

### Built-in trading rules

| Rule | What it does | Factor analogy | Key difference |
|---|---|---|---|
| **EWMAC** (trend) | Fast EMA - Slow EMA, divided by vol | Price momentum (12-1) | Continuous signal, not a rank. Multiple speed variants (2/8, 4/16, 8/32, 16/64, 32/128, 64/256) |
| **Carry** | Annualized roll yield from futures term structure | Dividend yield? But not really | Unique to futures. Measures cost/benefit of holding. No stock equivalent |
| **Breakout** | Price relative to recent range, vol-adjusted | 52-week high momentum | Built-in scaling to [-20, +20] range |
| **Acceleration** | Rate of change of EWMAC signal | Momentum of momentum | Second-derivative signal |
| **Relative momentum** | Cross-sectional: instrument vs asset-class peers | Relative strength (industry-neutral momentum) | Compares futures to each other within same asset class |
| **Mean reversion** | Fade extreme EWMAC signals beyond 3-sigma | Short-term reversal | Only active at extremes, otherwise zero |
| **Factor rule** | Generic: normalizes any input factor by volatility | Your actual factor signals! | Adapter for bringing stock-style factors into the framework |

### How a TradingRule works

```python
from systems.trading_rules import TradingRule

# A rule wraps: (function, data_sources, parameters)
my_rule = TradingRule(
    dict(
        function="systems.provided.rules.ewmac.ewmac_forecast_with_defaults",
        data=["rawdata.get_daily_prices", "rawdata.daily_returns_volatility"],
        other_args=dict(Lfast=16, Lslow=64),
    )
)
```

The function signature for a trading rule is simple -- it takes pandas Series/DataFrames as input and returns a pandas Series as output:

```python
def ewmac_forecast_with_defaults(price, vol, Lfast=16, Lslow=64):
    fast = price.ewm(span=Lfast).mean()
    slow = price.ewm(span=Lslow).mean()
    return (fast - slow) / vol
```

**You can write your own rules.** If you have a factor that works on futures (e.g., COT positioning, term structure slope, macro indicators), you wrap it as a `TradingRule` and plug it in. The system handles all the scaling, combining, and position sizing.

Code: `systems/trading_rules.py`, `systems/provided/rules/`

---

## Combining Signals: Weighted Average, Not Quantile Intersection

### How you combine factors (factor world)

```python
# Typical approach: composite score
composite = 0.3 * value_rank + 0.3 * momentum_rank + 0.4 * quality_rank
# Then sort into quantiles again
quintile = pd.qcut(composite, 5)
```

Or the intersection approach: "stocks that are in the top quintile of BOTH value AND momentum."

### How pysystemtrade combines forecasts

```python
# For each instrument, independently:
combined_forecast = (w1 * ewmac_forecast + w2 * carry_forecast + w3 * breakout_forecast) * FDM
```

Where:
- `w1 + w2 + w3 = 1.0` (weights sum to 1)
- `FDM` = Forecast Diversification Multiplier (typically 1.0 to 2.5)

**The FDM is clever**: When your rules are uncorrelated, combining them reduces portfolio variance. The FDM boosts the combined forecast to account for this diversification benefit. Two uncorrelated rules with equal weight and 0.5 correlation give FDM ≈ 1.15. This prevents the combined forecast from being systematically weaker than any individual forecast.

**Weight estimation approaches:**

| Method | Description | Recommended? |
|---|---|---|
| Equal weights | Just average all forecasts | Yes, safest default |
| Fixed weights | Manually specify in config | Yes, for production |
| Estimated (shrinkage) | Optimize Sharpe, shrink toward equal weights | Cautiously |
| Estimated (bootstrap) | Account for sampling uncertainty in Sharpe estimates | Best but slow |

The system is deeply skeptical of estimated weights because **Sharpe ratio estimation has enormous uncertainty**. With 10 years of data and a true Sharpe of 0.5, the 95% confidence interval is roughly [0.0, 1.0]. The system defaults to aggressive shrinkage toward equal weights.

Code: `systems/forecast_combine.py`

---

## Portfolio Construction: Risk Parity, Not Market Cap

### How you build a portfolio (factor world)

```python
# Equal weight the long basket:
positions = {stock: 1/N for stock in long_basket}

# Or cap-weight:
positions = {stock: market_cap[stock] / total_cap for stock in long_basket}

# Rebalance monthly
```

### How pysystemtrade builds a portfolio

The portfolio layer applies two things:

**1. Instrument weights**: What fraction of risk budget goes to each market?

```yaml
# In config (fixed weights example):
instrument_weights:
    SP500: 0.10
    EUROSTOXX: 0.10
    US10: 0.10
    BUND: 0.10
    CORN: 0.08
    GOLD: 0.08
    CRUDE_W: 0.08
    EUR: 0.08
    JPY: 0.08
    # ... etc, summing to 1.0
```

**2. Instrument Diversification Multiplier (IDM)**: Boost positions because diversification reduces portfolio risk.

```
notional_position = subsystem_position × instrument_weight × IDM
```

If you trade 20 uncorrelated instruments, the portfolio standard deviation is much less than the sum of individual standard deviations. The IDM compensates for this, allowing the system to take larger positions while still hitting its risk target.

**Typical IDM values:**
- 2 instruments: ~1.2
- 5 instruments: ~1.5
- 10 instruments: ~2.0
- 20+ instruments: ~2.5 (capped)

**Position formula (complete):**

```
actual_position = (vol_scalar × forecast / 10) × instrument_weight × IDM × risk_scalar × capital_multiplier

Where:
  vol_scalar         = daily_cash_vol_target / instrument_value_vol
  forecast           = combined, scaled, capped forecast [-20, +20]
  instrument_weight  = fraction of risk budget (0 to 1, sum to 1)
  IDM                = diversification boost (1.0 to 2.5)
  risk_scalar        = 1.0 normally, reduced if risk limits breached
  capital_multiplier = 1.0 for fixed capital, or scales with accumulated P&L
```

Code: `systems/portfolio.py`

---

## Rebalancing: Continuous With Buffers, Not Calendar-Based

### How you rebalance (factor world)

```python
# Monthly rebalance:
if today.is_month_end():
    new_ranks = compute_factor_ranks()
    new_portfolio = build_quantile_portfolio(new_ranks)
    trades = new_portfolio - current_portfolio
    execute(trades)
```

Problems:
- Between rebalances, positions drift
- On rebalance day, everyone trades the same names (crowding)
- A stock that barely crosses the quintile boundary gets fully traded

### How pysystemtrade rebalances

There is **no rebalance date**. The system runs daily (or even intraday) and asks: "has my optimal position changed enough to warrant a trade?"

**Buffer system:**

```
optimal_position = 47.3 contracts

buffer = average_position × buffer_size    (default 10%)
       = 47.3 × 0.10 = 4.73

upper_bound = 47.3 + 4.73 = 52.03
lower_bound = 47.3 - 4.73 = 42.57

Current position = 49 contracts
→ 42.57 < 49 < 52.03  →  NO TRADE (within buffer)

Next day, optimal_position shifts to 38.1
→ upper = 42.83, lower = 33.37
→ 49 > 42.83  →  TRADE: sell from 49 down to 42.83 (the buffer edge)
```

**Why this is better:**
- No arbitrary rebalance dates
- No "big bang" trading days with crowding
- Avoids unnecessary trading when positions are close enough
- Reduces turnover and transaction costs
- Responds immediately to large signal changes (e.g., a crash)

**Three buffer methods:**
1. **Forecast** (default): Buffer proportional to average position (uses vol scalar and weights)
2. **Position**: Buffer proportional to current position size
3. **None**: Trade to exact optimal every day (high turnover)

Code: `systems/buffering.py`

---

## If You Need Monthly/Quarterly/Yearly Rebalancing

If your mental model is "rebalance on a calendar", here is the closest translation:

### Option A: Keep the system continuous, but trade on a schedule

- Run the backtest/risk engine daily as usual (so targets stay realistic).
- Run the order-generation and execution processes only monthly/quarterly/yearly.
- Operationally, this creates sparse trading dates while still using a risk-aware target engine.

This is usually the least invasive way to get calendar behavior.

### Option B: Keep trading daily, but make the system less sensitive

- Increase `buffer_size` above the default `0.10` so fewer updates cross the trading band.
- Keep `buffer_trade_to_edge: True` so when you do trade, you only trade to the edge of the buffer.
- This can mimic lower-turnover "rebalance-like" behavior without hard calendar gates.

### Option C: Freeze some estimated parameters at lower frequency

- Forecast/instrument weights are estimated at lower frequency and then smoothed/forward-filled.
- You can configure slower estimation windows and update cadence for weight-like parameters.
- This is not equivalent to "all positions rebalance monthly", but it reduces drift from parameter churn.

### Important conceptual point

In factor frameworks, "rebalance frequency" usually defines when the whole portfolio is recomputed and traded.

In pysystemtrade, the portfolio is always defined as a continuous risk-targeted state. Calendar scheduling controls *when you act on that state*, not whether the state exists.

---

## The Full Pipeline: A Worked Example

Let's trace a single instrument (Corn futures, code `CORN`) through the entire system.

### Step 1: Raw Data

```python
system.rawdata.get_daily_prices("CORN")
# Returns: pandas Series of back-adjusted daily Corn prices
# e.g., 2024-01-15: 487.25, 2024-01-16: 491.50, ...

system.rawdata.daily_returns_volatility("CORN")
# Returns: pandas Series of daily volatility estimate
# e.g., 2024-01-15: 8.2 (cents/day)
```

### Step 2: Raw Forecasts

```python
system.rules.get_raw_forecast("CORN", "ewmac16_64")
# Returns: e.g., +2.31  (Corn is trending up, moderate strength)

system.rules.get_raw_forecast("CORN", "carry")
# Returns: e.g., +0.45  (Corn carry is slightly positive)
```

### Step 3: Scale and Cap

```python
system.forecastScaleCap.get_capped_forecast("CORN", "ewmac16_64")
# Raw +2.31 × scalar 5.3 = +12.2  →  capped at +12.2 (within ±20)

system.forecastScaleCap.get_capped_forecast("CORN", "carry")
# Raw +0.45 × scalar 22.0 = +9.9  →  capped at +9.9
```

### Step 4: Combine Forecasts

```python
system.combForecast.get_combined_forecast("CORN")
# = (0.5 × 12.2 + 0.5 × 9.9) × FDM(1.1)
# = 11.05 × 1.1 = +12.16

# Interpretation: "Moderately strong long signal for Corn"
```

### Step 5: Position Sizing

```python
system.positionSize.get_subsystem_position("CORN")
# vol_scalar = daily_cash_vol_target / instrument_value_vol
#            = 10,082 / 1,850  ≈  5.45 contracts (at average forecast)
# subsystem_position = 5.45 × (12.16 / 10) = 6.63 contracts
```

### Step 6: Portfolio Position

```python
system.portfolio.get_notional_position("CORN")
# = subsystem_position × instrument_weight × IDM
# = 6.63 × 0.08 × 2.0 = 1.06 contracts

# After rounding and buffering:
# Target: 1 contract long Corn
# Buffer: [0.84, 1.28]
# If currently holding 1 contract → no trade needed
```

### Step 7: Performance

```python
system.accounts.portfolio().sharpe()      # e.g., 0.83
system.accounts.portfolio().max_drawdown() # e.g., -0.12  (-12%)
system.accounts.portfolio().stats()        # Full statistical summary
```

---

## System Architecture and Code Layout

### The System Object

Everything is assembled into a `System`:

```python
from systems.basesystem import System

system = System(
    stage_list=[
        RawData(),              # Stage 1
        Rules(trading_rules),   # Stage 2
        ForecastScaleCap(),     # Stage 3
        ForecastCombine(),      # Stage 4
        PositionSizing(),       # Stage 5
        Portfolios(),           # Stage 6
        Account(),              # Stage 7
    ],
    data=csvFuturesSimData(),   # Data source
    config=Config("my_config.yaml"),  # Configuration
)
```

Each stage:
- Is a Python class inheriting from `SystemStage`
- Gets a reference to the parent `System` via `self.parent`
- Can access any other stage: `self.parent.rawdata.get_daily_prices("CORN")`
- Has its results **cached automatically** -- computed once, reused everywhere

### Caching: Lazy Evaluation

The system uses a decorator-based caching system. Methods decorated with `@output`, `@diagnostic`, or `@input` cache their results:

```python
@output()
def get_capped_forecast(self, instrument_code, rule_variation_name):
    # First call: computes and caches
    # Subsequent calls: returns cached result instantly
    ...
```

This matters for backtesting: computing 30 years of daily forecasts across 100 instruments and 6 rules = millions of data points. The cache ensures each is computed only once.

### Directory Structure

```
pysystemtrade/
├── systems/                    # THE CORE: backtest engine
│   ├── basesystem.py          # System class (orchestrator)
│   ├── stage.py               # SystemStage base class
│   ├── system_cache.py        # Caching engine
│   ├── rawdata.py             # Stage 1: prices, vol, FX
│   ├── forecasting.py         # Stage 2: run trading rules
│   ├── trading_rules.py       # TradingRule container class
│   ├── forecast_scale_cap.py  # Stage 3: scale and cap
│   ├── forecast_combine.py    # Stage 4: blend forecasts
│   ├── positionsizing.py      # Stage 5: vol targeting
│   ├── portfolio.py           # Stage 6: instrument weights + IDM
│   ├── buffering.py           # Position buffers
│   ├── risk_overlay.py        # Risk limits
│   ├── accounts/              # Stage 7: P&L calculation
│   └── provided/              # Pre-built systems and rules
│       ├── rules/             # Built-in trading rules (ewmac, carry, etc.)
│       └── futures_chapter15/ # Example system from Rob Carver's book
│
├── data/futures/              # SHIPPED DATA (CSV)
│   ├── adjusted_prices_csv/   # Back-adjusted continuous prices
│   ├── multiple_prices_csv/   # Multi-contract price stacks
│   ├── roll_calendars_csv/    # When to roll contracts
│   ├── fx_prices_csv/         # Currency exchange rates
│   └── csvconfig/             # Instrument metadata, roll rules, costs
│
├── sysdata/                   # DATA ACCESS LAYER
│   ├── sim/                   # Simulation data interfaces
│   ├── csv/                   # CSV file handlers
│   ├── mongodb/               # MongoDB handlers
│   ├── parquet/               # Parquet handlers
│   └── config/defaults.yaml   # Default system configuration
│
├── sysobjects/                # DATA OBJECT DEFINITIONS
│   ├── instruments.py         # futuresInstrument, instrumentMetaData
│   ├── adjusted_prices.py     # Back-adjusted price series
│   ├── multiple_prices.py     # Multi-contract prices
│   ├── roll_calendars.py      # Roll calendar objects
│   └── rolls.py               # Roll cycle definitions
│
├── sysquant/                  # QUANTITATIVE TOOLS
│   └── optimisation/          # Sharpe optimization, shrinkage, bootstrap
│
├── sysproduction/             # LIVE TRADING
│   ├── run_systems.py         # Nightly backtest runner
│   ├── run_strategy_order_generator.py  # Generate trades
│   ├── run_stack_handler.py   # Execute trades
│   └── data/                  # Production data access
│
├── sysexecution/              # ORDER MANAGEMENT
│   ├── orders/                # Order object hierarchy
│   ├── stack_handler/         # Order processing pipeline
│   ├── algos/                 # Execution algorithms
│   └── strategies/            # Strategy order generators
│
├── sysbrokers/                # BROKER INTERFACE
│   └── IB/                    # Interactive Brokers integration
│
└── syscontrol/                # PROCESS SCHEDULING
    └── run_process.py         # Timer-based process manager
```

---

## From Backtest to Live Trading

### The same system, different data

pysystemtrade is designed so **the exact same System object** runs in backtest mode (with CSV data) and in production (with live data from Interactive Brokers). The difference is just the data source:

```python
# Backtest (CSV files):
system = futures_system(data=csvFuturesSimData())

# Production (MongoDB + IB):
system = futures_system(data=dbFuturesSimData())
```

### The production cycle

```
┌─ DAILY CYCLE ──────────────────────────────────────────────┐
│                                                             │
│  07:05  Update prices from IB + external sources            │
│  07:05  Update FX rates and contract info                   │
│  20:30  Run overnight backtest → compute optimal positions  │
│  20:45  Generate orders (optimal - current = trade)         │
│  15:00  Stack handler executes orders via IB (continuous)   │
│  21:00  Cleanup, backups, reports                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Three-tier order management

When the system decides to trade, it doesn't just fire a market order. Orders cascade through three levels:

```
Instrument Order: "Buy 3 CORN"
     ↓
Contract Order:   "Buy 3 CORN May 2025"  (picks the specific contract)
     ↓
Broker Order:     "Buy 3 CK5 on CBOT via limit algo"  (sent to Interactive Brokers)
```

This handles complexities like:
- **Rolling**: "Buy 3 CORN" might mean "sell 3 March, buy 3 May" if we're rolling
- **Execution algos**: Limit orders, adaptive algorithms, market orders
- **Fill tracking**: Partial fills propagate back up the stack

---

## The Philosophy: Rob Carver's Framework

pysystemtrade implements the ideas from Rob Carver's three books:

### 1. "Systematic Trading" (2015)
The foundational framework. Key ideas:
- **Don't overfit**: Use simple rules, few parameters, lots of data
- **Diversify everything**: rules, instruments, timeframes
- **Volatility target**: Fix your risk, don't fix your return
- **Forecast, don't predict**: Express views as continuous signals, not binary bets

### 2. "Leveraged Trading" (2019)
Extends the framework for leveraged instruments (futures, CFDs). Key ideas:
- **Kelly criterion for allocation**: Optimal risk target = Sharpe ratio
- **But don't go full Kelly**: Half-Kelly or less due to parameter uncertainty
- **Carry is a unique alpha source** in futures

### 3. "Advanced Futures Trading Strategies" (2023)
The latest evolution. Key ideas:
- **More instruments = better** (diversification is the only free lunch)
- **Dynamic optimization** for portfolio construction
- **Cross-sectional strategies** (relative value within asset classes)

### The core philosophy in one paragraph

> **"Be humble about your ability to predict returns. Be rigorous about measuring and targeting risk. Use simple, robust signals across many markets. Diversify across rules and instruments. Let the math handle position sizing. Don't trade more than you need to."**

### Kelly vs. Classical: How pysystemtrade resolves the debate

The system synthesizes two schools of thought:

- **Classical (Utilitarian)**: "I want X% volatility" → sets the risk budget
- **Kelly (Optimal Growth)**: "Maximize Sharpe ratio" → determines allocation within that budget

In practice:
1. **Stage 5** (Position Sizing) is pure Classical: "My portfolio should have 16% vol"
2. **Stages 4 & 6** (Forecast and Instrument Weights) use Kelly logic: "Allocate to maximize Sharpe"
3. **Everywhere**: Parameter uncertainty management via shrinkage: "I don't trust my Sharpe estimates"

As Rob Carver puts it: *"The Kellyite picks the car, the Utilitarian sets the speed limit."*

---

## Concept Translation Table

| Your concept (bt / alphalens) | pysystemtrade equivalent | Key difference |
|---|---|---|
| Factor (PE, PB, momentum) | Trading rule (EWMAC, carry, breakout) | Continuous forecast, not a rank |
| Factor score / z-score | Raw forecast | Unbounded, pre-scaling |
| Quantile ranking (Q1-Q5) | Scaled + capped forecast (-20 to +20) | Preserves gradient; +15 gets 1.5x the position of +10 |
| Composite alpha | Combined forecast | Weighted average × FDM, not re-ranked |
| Stock universe (S&P 500) | Instrument list (100+ futures) | Multi-asset class: equities + bonds + FX + commodities |
| Long/short portfolio | Single instrument position (long or short) | Each instrument is independent; no "long basket" vs "short basket" |
| Equal weight within quintile | Volatility-targeted position size | Position proportional to signal strength AND inversely proportional to volatility |
| Monthly/quarterly rebalance | Continuous with buffer bands | Trade only when position drifts outside ±10% buffer |
| Turnover | Cost-aware buffering | Buffers explicitly reduce turnover; costs modeled in backtest |
| Factor return (IC, quintile spread) | Rule P&L, Sharpe ratio per rule | Measures actual P&L, not just cross-sectional predictability |
| Benchmark (SPY) | No benchmark | Absolute return framework; no tracking error concept |
| Transaction costs | Spread costs per instrument | Modeled per-contract from bid-ask spread data |
| Market cap weighting | Instrument weight × IDM | Risk-parity-inspired, not size-based |
| Sector neutrality | Asset class diversification | Diversify across asset classes, not neutralize within one |
| Backtest.run() | system.accounts.portfolio() | Lazy-evaluated; each data point computed on first access |
| Rebalance function | Buffering + stack handler | No discrete rebalance; continuous position management |
| Paper trading | Same system, same data, same code | Production uses same System class with different data source |
| Broker API (alpaca, etc.) | Interactive Brokers via ib-insync | Three-tier order management with execution algos |

---

## Where to Go Next

### Recommended reading order

1. **This document** -- you're here
2. **`docs/introduction.md`** -- Quick tour with code examples
3. **`docs/backtesting.md`** -- Comprehensive guide to all configuration options
4. **`docs/data.md`** -- Understanding futures data, rolls, and price adjustment
5. **Rob Carver's books** -- The theory behind the code:
   - *Systematic Trading* (start here)
   - *Leveraged Trading*
   - *Advanced Futures Trading Strategies*

### Try it yourself

```python
# Minimal working example:
from systems.provided.futures_chapter15.basesystem import futures_system

system = futures_system()

# See the combined forecast for S&P 500 futures:
print(system.combForecast.get_combined_forecast("SP500"))

# See the position the system wants:
print(system.portfolio.get_notional_position("SP500"))

# See portfolio performance:
print(system.accounts.portfolio().stats())
```

### Key configuration file

All defaults live in `sysdata/config/defaults.yaml`. The most important parameters:

```yaml
percentage_vol_target: 16.0          # Annual portfolio volatility target
notional_trading_capital: 1000000    # Simulated account size
base_currency: "USD"                 # Reference currency
forecast_cap: 20.0                   # Maximum absolute forecast
average_absolute_forecast: 10.0      # Forecast normalization target
buffer_method: forecast              # How position buffers work
buffer_size: 0.10                    # Buffer width (10% of average position)
```

### If you want to bring your stock factors into this framework

pysystemtrade has a built-in `factor_trading_rule` (`systems/provided/rules/factors.py`) that converts any demeaned factor into a forecast:

```python
def factor_trading_rule(demean_factor_value, smooth=90):
    from sysquant.estimators.vol import robust_vol_calc

    vol = robust_vol_calc(demean_factor_value)
    normalised_factor = demean_factor_value / vol
    smoothed_normalised_factor = normalised_factor.ewm(span=smooth).mean()
    return smoothed_normalised_factor
```

You could, in principle:
1. Compute your PE/PB/momentum factor for a universe of equity index futures
2. Wrap it as a `TradingRule`
3. Let pysystemtrade handle the scaling, combining, position sizing, and execution

The system doesn't care where the signal comes from -- it just needs a pandas Series of forecast values per instrument.

---

*This guide was written for newcomers coming from factor-based equity frameworks. pysystemtrade is Rob Carver's open-source implementation of systematic futures trading, originally developed in 2015 and actively maintained. For the definitive reference, see Rob Carver's books and the project documentation at `docs/`.*
