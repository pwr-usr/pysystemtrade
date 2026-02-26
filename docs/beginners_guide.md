# pysystemtrade Beginner's Guide

A hands-on, code-first guide to understanding and using pysystemtrade — the open-source systematic futures trading framework by Rob Carver.

> **Prerequisites**: Python 3.10+, basic familiarity with pandas and numpy.
> **What this guide covers**: Everything from "I just cloned the repo" to "I can build and backtest my own trading system."

---

## Table of Contents

1. [What is pysystemtrade?](#1-what-is-pysystemtrade)
2. [Installation & Setup](#2-installation--setup)
3. [Project Structure at a Glance](#3-project-structure-at-a-glance)
4. [Your First 5 Minutes: Loading Data](#4-your-first-5-minutes-loading-data)
5. [Understanding the Data Layer](#5-understanding-the-data-layer)
6. [Writing Your First Trading Rule](#6-writing-your-first-trading-rule)
7. [The System: How Everything Fits Together](#7-the-system-how-everything-fits-together)
8. [Building a Backtest Step by Step](#8-building-a-backtest-step-by-step)
9. [Using Pre-built Systems](#9-using-pre-built-systems)
10. [Configuration: YAML Files Explained](#10-configuration-yaml-files-explained)
11. [Understanding Forecasts, Positions, and P&L](#11-understanding-forecasts-positions-and-pl)
12. [Common Recipes](#12-common-recipes)
13. [Where to Go Next](#13-where-to-go-next)
14. [Glossary](#14-glossary)

---

## 1. What is pysystemtrade?

pysystemtrade is a Python framework for **systematic futures trading**. It lets you:

- **Backtest** trading strategies on historical futures data
- **Combine** multiple trading rules (trend-following, carry, etc.) into a portfolio
- **Size positions** using volatility targeting
- **Run live trading** through Interactive Brokers (advanced)

The framework implements the ideas from Rob Carver's book *"Systematic Trading"* (2015). The core idea: instead of discretionary trading, you define **rules** that generate **forecasts** (numbers from -20 to +20 indicating how bullish/bearish you are), then the system handles scaling, combining, and position sizing.

### Key Concepts in 60 Seconds

| Concept | What it means |
|---------|--------------|
| **Instrument** | A futures contract you can trade (e.g., `SP500`, `CORN`, `US10`) |
| **Forecast** | A number from -20 (max short) to +20 (max long) for how strong a signal is |
| **Trading Rule** | A function that takes price data and returns a forecast (e.g., moving average crossover) |
| **Stage** | A processing step in the pipeline (data → rules → scaling → combining → sizing → portfolio → P&L) |
| **System** | The container that wires all stages together with data and config |

---

## 2. Installation & Setup

```bash
# Clone the repository
git clone https://github.com/pst-group/pysystemtrade.git
cd pysystemtrade

# Install in development mode (recommended for learning)
pip install -e .

# Verify the install works
python -c "from sysdata.sim.csv_futures_sim_data import csvFuturesSimData; print('OK!')"
```

That's it for backtesting. The repo ships with CSV data for ~200 futures instruments, so you can start exploring immediately without any database setup.

> **Note**: MongoDB is only needed for production trading. For learning and backtesting, CSV data is all you need.

---

## 3. Project Structure at a Glance

```
pysystemtrade/
│
├── data/                  # CSV data shipped with the project
│   └── futures/
│       ├── adjusted_prices_csv/   # Back-adjusted price series (~200 instruments)
│       ├── multiple_prices_csv/   # Raw contract prices (PRICE, CARRY, etc.)
│       ├── fx_prices_csv/         # Currency exchange rates
│       └── csvconfig/             # Instrument metadata, roll configs, costs
│
├── systems/               # THE CORE: backtesting engine
│   ├── basesystem.py      # The System class - orchestrates everything
│   ├── stage.py           # Base class for pipeline stages
│   ├── rawdata.py         # Stage: loads prices, calculates volatility
│   ├── forecasting.py     # Stage: runs trading rules
│   ├── trading_rules.py   # TradingRule definition
│   ├── forecast_scale_cap.py  # Stage: scales & caps forecasts to -20..+20
│   ├── forecast_combine.py    # Stage: combines multiple forecasts
│   ├── positionsizing.py      # Stage: converts forecasts to positions
│   ├── portfolio.py           # Stage: combines instruments into portfolio
│   ├── accounts/              # Stage: calculates P&L and performance
│   └── provided/              # Ready-to-use systems and trading rules
│       ├── rules/             # Built-in trading rules (ewmac, carry, etc.)
│       ├── futures_chapter15/ # Full system from the book
│       └── example/           # Simple example system
│
├── sysdata/               # Data access layer (CSV, MongoDB, Parquet)
│   ├── sim/               # Simulation data interfaces
│   └── config/            # Configuration management
│
├── sysobjects/            # Trading objects (contracts, prices, fills)
├── syscore/               # Core utilities (dates, math, caching)
├── sysquant/              # Quantitative tools (optimization, estimation)
├── sysbrokers/            # Broker integration (Interactive Brokers)
├── sysexecution/          # Order execution engine
├── sysproduction/         # Production trading scripts
├── syslogging/            # Logging system
│
├── examples/              # Example scripts (start here!)
│   └── introduction/      # Getting started examples
│
└── docs/                  # Documentation
```

**For beginners, focus on two directories**: `systems/` (the backtesting engine) and `data/` (the sample data).

---

## 4. Your First 5 Minutes: Loading Data

Open a Python shell (or Jupyter notebook) from the project root:

```python
# Load the CSV data interface
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData

data = csvFuturesSimData()

# See what instruments are available
print(data.get_instrument_list()[:10])
# Output: ['AEX', 'AEX_mini', 'ALUMINIUM', 'ALUMINIUM_LME', 'AUD', ...]
```

```python
# Get daily back-adjusted prices for S&P 500
prices = data.get_raw_price("SP500")
print(prices.tail())
# Output:
#                SP500
# 2024-03-18  5163.75
# 2024-03-19  5178.50
# ...
```

```python
# How many instruments do we have?
print(f"Total instruments: {len(data.get_instrument_list())}")

# Get metadata about an instrument
meta = data.get_instrument_object_with_meta_data("SP500")
print(meta)
```

```python
# Get carry data (used for carry trading rule)
carry = data.get_instrument_raw_carry_data("SP500")
print(carry.tail())
# Shows: PRICE, CARRY, CARRY_CONTRACT, PRICE_CONTRACT columns
```

```python
# Get FX rates (needed for cross-currency instruments)
fx = data.get_fx_for_instrument("CORN", "USD")
print(fx.tail())
```

### What's in the price data?

The "adjusted prices" are **back-adjusted** — when a futures contract rolls from one expiry to the next, the prices are stitched together so there are no jumps. This is what you use for generating trading signals. The "multiple prices" contain the raw contract prices.

---

## 5. Understanding the Data Layer

pysystemtrade has a clean data abstraction. Here's the hierarchy:

```
simData (generic)
  └── futuresSimData (futures-specific)
       └── csvFuturesSimData (reads from CSV files)
       └── dbFuturesSimData (reads from MongoDB / Parquet)
```

For backtesting, you almost always use `csvFuturesSimData`. It reads from the `data/` directory:

```python
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData

data = csvFuturesSimData()

# Key methods on the data object:
data.get_instrument_list()              # All available instruments
data.get_raw_price("SP500")             # Back-adjusted daily prices
data.daily_prices("SP500")              # Same as get_raw_price
data.get_instrument_raw_carry_data("SP500")  # Carry data
data.get_raw_cost_data("SP500")         # Trading costs
data.get_value_of_block_price_move("SP500")  # Point value ($ per point)
```

### Instrument Configuration

The file `data/futures/csvconfig/instrumentconfig.csv` defines every instrument:

```
Instrument, Description, Pointsize, Currency, AssetClass, PerBlock, Percentage, PerTrade, Region
SP500,      S&P 500,     50,        USD,      Equity,     0.75,    0,          0,        US
CORN,       Corn,        50,        USD,      Ag,         1.0,     0,          0,        US
US10,       10yr US bond,1000,      USD,      Bond,       0.016,   0,          0,        US
```

- **Pointsize**: How much one point of price movement is worth in the contract currency
- **PerBlock**: Half the bid-ask spread in price points (used for cost estimation)

---

## 6. Writing Your First Trading Rule

A trading rule is just a Python function that takes price data and returns a **forecast** — a pandas Series of numbers.

### The EWMAC Rule (Exponential Weighted Moving Average Crossover)

This is the simplest trend-following rule: buy when the fast moving average is above the slow one.

```python
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData
from sysquant.estimators.vol import robust_vol_calc

data = csvFuturesSimData()

def my_ewmac(price, Lfast=16, Lslow=64):
    """
    Simple trend-following rule.
    Returns a raw (unscaled) forecast.

    Positive forecast = bullish (go long)
    Negative forecast = bearish (go short)
    """
    fast_ewma = price.ewm(span=Lfast).mean()
    slow_ewma = price.ewm(span=Lslow).mean()
    raw_signal = fast_ewma - slow_ewma

    # Divide by volatility so forecasts are comparable across instruments
    vol = robust_vol_calc(price.diff())
    forecast = raw_signal / vol

    return forecast

# Try it on S&P 500
price = data.daily_prices("SP500")
forecast = my_ewmac(price)
print(forecast.tail())
```

```python
# Visualize it
import matplotlib.pyplot as plt

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

price.plot(ax=ax1, title="SP500 Price")
forecast.plot(ax=ax2, title="EWMAC Forecast (16/64)")
ax2.axhline(y=0, color='black', linestyle='--', alpha=0.3)

plt.tight_layout()
plt.show()
```

### Did it make money?

```python
from systems.accounts.account_forecast import pandl_for_instrument_forecast

account = pandl_for_instrument_forecast(forecast=forecast, price=price)
print(account.percent.stats())
# Shows: mean, std, Sharpe ratio, skew, etc.
```

```python
# Plot the equity curve
account.curve().plot(title="EWMAC 16/64 on SP500 - Equity Curve")
plt.show()
```

---

## 7. The System: How Everything Fits Together

In pysystemtrade, a **System** is the main object that wires together:
- **Data**: Where prices come from
- **Config**: Parameters and settings
- **Stages**: Processing steps in a pipeline

```
Data + Config
     │
     ▼
┌─────────┐    ┌─────────────────┐    ┌────────────────┐
│ RawData │ →  │ Rules           │ →  │ ForecastScale  │
│ (prices,│    │ (generate       │    │ Cap            │
│  vol)   │    │  forecasts)     │    │ (scale to ±20) │
└─────────┘    └─────────────────┘    └────────────────┘
                                              │
                                              ▼
┌─────────┐    ┌─────────────────┐    ┌────────────────┐
│ Account │ ←  │ Portfolios      │ ←  │ ForecastCombine│
│ (P&L,   │    │ (instrument     │    │ (weight & merge│
│  stats)  │    │  weights)       │    │  forecasts)    │
└─────────┘    └─────────────────┘    └────────────────┘
                       ↑
               ┌───────────────┐
               │ PositionSizing│
               │ (vol-target   │
               │  sizing)      │
               └───────────────┘
```

### Building a System from Scratch

```python
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData
from systems.basesystem import System
from systems.forecasting import Rules
from systems.provided.rules.ewmac import ewmac_forecast_with_defaults as ewmac

# 1. Get data
data = csvFuturesSimData()

# 2. Define trading rules
my_rules = Rules(dict(ewmac=ewmac))

# 3. Create a minimal system (just data + rules)
system = System([my_rules], data)
print(system)
# System with stages: rules

# 4. Get a forecast
forecast = system.rules.get_raw_forecast("SOFR", "ewmac")
print(forecast.tail())
```

Notice that you access stages through dot notation: `system.rules`, `system.rawdata`, `system.portfolio`, etc. Each stage has a **name** that becomes the attribute name on the system.

---

## 8. Building a Backtest Step by Step

Let's build a complete backtest from scratch, adding one stage at a time.

### Step 1: Data + Rules

```python
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData
from sysdata.config.configdata import Config
from systems.basesystem import System
from systems.forecasting import Rules
from systems.trading_rules import TradingRule
from systems.provided.rules.ewmac import ewmac_forecast_with_defaults as ewmac

data = csvFuturesSimData()

# Define two EWMAC rules with different speeds
ewmac_8 = TradingRule(
    (ewmac, [], dict(Lfast=8, Lslow=32))  # Fast trend
)
ewmac_32 = TradingRule(
    dict(function=ewmac, other_args=dict(Lfast=32, Lslow=128))  # Slow trend
)

my_rules = Rules(dict(ewmac8=ewmac_8, ewmac32=ewmac_32))
system = System([my_rules], data)

# Raw (unscaled) forecasts
print(system.rules.get_raw_forecast("SOFR", "ewmac8").tail())
print(system.rules.get_raw_forecast("SOFR", "ewmac32").tail())
```

### Step 2: Scale & Cap Forecasts

Raw forecasts have arbitrary magnitude. We need to scale them so the average absolute forecast is 10 (the "target" level), and cap them at ±20.

```python
from systems.forecast_scale_cap import ForecastScaleCap

my_config = Config()
my_config.trading_rules = dict(ewmac8=ewmac_8, ewmac32=ewmac_32)

# Use fixed forecast scalars (from the book)
my_config.forecast_scalars = dict(ewmac8=5.3, ewmac32=2.65)
my_config.use_forecast_scale_estimates = False

fcs = ForecastScaleCap()
system = System([fcs, Rules()], data, my_config)

# Scaled and capped forecast (now centered around ±10, capped at ±20)
print(system.forecastScaleCap.get_capped_forecast("SOFR", "ewmac32").tail())
```

### Step 3: Combine Forecasts

If you have multiple rules, you need to combine them with weights.

```python
from systems.forecast_combine import ForecastCombine

my_config.forecast_weights = dict(ewmac8=0.5, ewmac32=0.5)
my_config.forecast_div_multiplier = 1.1  # Diversification bonus
my_config.use_forecast_weight_estimates = False
my_config.use_forecast_div_mult_estimates = False

combiner = ForecastCombine()
system = System([fcs, Rules(), combiner], data, my_config)

# Combined forecast
print(system.combForecast.get_combined_forecast("SOFR").tail())
```

### Step 4: Position Sizing

Convert forecasts into actual positions using volatility targeting.

```python
from systems.positionsizing import PositionSizing
from systems.rawdata import RawData

my_config.percentage_vol_target = 25      # Target 25% annual volatility
my_config.notional_trading_capital = 500000  # $500k capital
my_config.base_currency = "USD"

possizer = PositionSizing()
raw_data = RawData()

system = System(
    [fcs, Rules(), combiner, possizer, raw_data],
    data, my_config
)

# How many contracts to hold
print(system.positionSize.get_subsystem_position("SOFR").tail())

# Volatility target breakdown
print(system.positionSize.get_vol_target_dict())
```

### Step 5: Portfolio (Instrument Weights)

```python
from systems.portfolio import Portfolios

my_config.instruments = ["US10", "SOFR", "CORN", "SP500_micro"]
my_config.instrument_weights = dict(US10=0.1, SOFR=0.4, CORN=0.3, SP500_micro=0.2)
my_config.instrument_div_multiplier = 1.5
my_config.use_instrument_weight_estimates = False
my_config.use_instrument_div_mult_estimates = False

portfolio = Portfolios()

system = System(
    [fcs, Rules(), combiner, possizer, portfolio, raw_data],
    data, my_config
)

# Final notional positions
print(system.portfolio.get_notional_position("SOFR").tail())
```

### Step 6: Calculate P&L

```python
from systems.accounts.accounts_stage import Account

my_account = Account()
system = System(
    [fcs, Rules(), combiner, possizer, portfolio, my_account, raw_data],
    data, my_config
)

# Portfolio-level results
profits = system.accounts.portfolio()
print(profits.percent.stats())

# Breakdown: gross vs net (after costs)
print("--- Gross (before costs) ---")
print(profits.gross.percent.stats())
print("--- Net (after costs) ---")
print(profits.net.percent.stats())
```

### The Complete System in One Shot

Instead of building stage by stage, you can do it all at once with a config dict:

```python
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData
from sysdata.config.configdata import Config
from systems.basesystem import System
from systems.forecasting import Rules
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecast_combine import ForecastCombine
from systems.positionsizing import PositionSizing
from systems.portfolio import Portfolios
from systems.accounts.accounts_stage import Account
from systems.rawdata import RawData
from systems.trading_rules import TradingRule
from systems.provided.rules.ewmac import ewmac_forecast_with_defaults as ewmac

data = csvFuturesSimData()

config = Config(dict(
    trading_rules=dict(
        ewmac8=TradingRule((ewmac, [], dict(Lfast=8, Lslow=32))),
        ewmac32=TradingRule(dict(function=ewmac, other_args=dict(Lfast=32, Lslow=128))),
    ),
    forecast_scalars=dict(ewmac8=5.3, ewmac32=2.65),
    forecast_weights=dict(ewmac8=0.5, ewmac32=0.5),
    forecast_div_multiplier=1.1,
    instrument_weights=dict(US10=0.1, SOFR=0.4, CORN=0.3, SP500_micro=0.2),
    instrument_div_multiplier=1.5,
    percentage_vol_target=25.0,
    notional_trading_capital=500000,
    base_currency="USD",
))

system = System(
    [Account(), Portfolios(), PositionSizing(), ForecastCombine(),
     ForecastScaleCap(), Rules(), RawData()],
    data, config
)

# Run the full backtest
print(system.accounts.portfolio().percent.stats())
```

---

## 9. Using Pre-built Systems

Don't want to build everything from scratch? Use the provided systems.

### The Simple Example System

```python
from systems.provided.example.simplesystem import simplesystem

system = simplesystem()
print(system)

# Get positions and P&L immediately
print(system.portfolio.get_notional_position("SOFR").tail())
```

### The Chapter 15 System (from the book)

This is a fully configured system with 6 EWMAC rules + carry, across 6 instruments:

```python
from systems.provided.futures_chapter15.basesystem import futures_system

system = futures_system()
print(system)

# Get the portfolio Sharpe ratio
print(f"Sharpe: {system.accounts.portfolio().sharpe():.2f}")

# Get all instrument forecasts
for instrument in system.get_instrument_list():
    pos = system.portfolio.get_notional_position(instrument).iloc[-1]
    print(f"  {instrument}: {pos:.1f} contracts")
```

### The Estimated System (parameters estimated from data)

Instead of using fixed parameters from the book, estimate them:

```python
from systems.provided.futures_chapter15.estimatedsystem import (
    futures_system as estimated_futures_system,
)

system = estimated_futures_system()

# This will take longer as it estimates forecast scalars,
# forecast weights, and instrument weights from the data
print(f"Sharpe: {system.accounts.portfolio().sharpe():.2f}")
```

### Customizing a Pre-built System

```python
from systems.provided.futures_chapter15.basesystem import futures_system
from sysdata.config.configdata import Config

# Load the default config and override some values
config = Config("systems.provided.futures_chapter15.futuresconfig.yaml")
config.notional_trading_capital = 1000000  # $1M instead of $250K
config.percentage_vol_target = 15          # Lower risk target

system = futures_system(config=config)
print(system.accounts.portfolio().percent.stats())
```

---

## 10. Configuration: YAML Files Explained

Configuration in pysystemtrade uses a three-level hierarchy:

```
1. System defaults     (sysdata/config/defaults.yaml)
2. System config       (e.g., futuresconfig.yaml)
3. Runtime overrides   (Config object in Python)
```

Lower levels override higher levels.

### Anatomy of a Config File

Here's a minimal config (`systems/provided/example/simplesystemconfig.yaml`):

```yaml
# Capital & Risk
percentage_vol_target: 25          # Target 25% annual portfolio volatility
notional_trading_capital: 500000   # Hypothetical capital for sizing
base_currency: "GBP"              # Base currency for P&L

# Trading Rules
trading_rules:
  ewmac8:
    function: systems.provided.rules.ewmac.ewmac_forecast_with_defaults
    data: data.daily_prices          # Where to get price data
    other_args:
      Lfast: 8                       # Fast EMA lookback
      Lslow: 32                      # Slow EMA lookback
    forecast_scalar: 5.3             # Scale raw forecast
  ewmac32:
    function: systems.provided.rules.ewmac.ewmac_forecast_with_defaults
    other_args:
      Lfast: 32
      Lslow: 128
    forecast_scalar: 2.65

# Forecast Combination
forecast_weights:
  ewmac8: 0.50
  ewmac32: 0.50
forecast_div_multiplier: 1.1        # Diversification multiplier

# Portfolio
instrument_weights:
  SOFR: 0.4
  US10: 0.1
  CORN: 0.3
  SP500: 0.2
instrument_div_multiplier: 1.5
```

### Loading Config in Python

```python
from sysdata.config.configdata import Config

# From a YAML file (dot-separated Python path)
config = Config("systems.provided.example.simplesystemconfig.yaml")

# From a dictionary
config = Config(dict(
    percentage_vol_target=20,
    notional_trading_capital=100000,
    base_currency="USD",
))

# Inspect config values you set
print(config.percentage_vol_target)
# Note: fields like instrument_weights are only available
# if you set them or load a YAML that defines them

# Override at runtime
config.percentage_vol_target = 15
```

### Key Config Parameters Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `percentage_vol_target` | 20.0 | Target annual portfolio volatility (%) |
| `notional_trading_capital` | 250000 | Capital for position sizing |
| `base_currency` | "USD" | Base currency for P&L calculation |
| `forecast_cap` | 20.0 | Maximum absolute forecast value |
| `use_forecast_scale_estimates` | False | Estimate scalars from data vs. use fixed |
| `use_forecast_weight_estimates` | False | Estimate weights from data vs. use fixed |
| `use_instrument_weight_estimates` | False | Estimate instrument weights from data |

---

## 11. Understanding Forecasts, Positions, and P&L

### The Forecast Scale

Forecasts are standardized to a **-20 to +20 scale**:

| Forecast | Meaning |
|----------|---------|
| +20 | Maximum long (strongest bullish signal) |
| +10 | "Average" long signal |
| 0 | Neutral / no position |
| -10 | "Average" short signal |
| -20 | Maximum short (strongest bearish signal) |

A forecast of +10 means "hold a position equal to the volatility target". A forecast of +20 means "hold twice that position".

### From Forecast to Position

```python
from systems.provided.futures_chapter15.basesystem import futures_system

system = futures_system()
instrument = "CORN"

# 1. Raw forecast (unscaled)
raw = system.rules.get_raw_forecast(instrument, "ewmac16_64")
print(f"Raw forecast range: {raw.min():.1f} to {raw.max():.1f}")

# 2. Scaled & capped forecast (-20 to +20)
scaled = system.forecastScaleCap.get_capped_forecast(instrument, "ewmac16_64")
print(f"Scaled forecast range: {scaled.min():.1f} to {scaled.max():.1f}")

# 3. Combined forecast (all rules weighted together)
combined = system.combForecast.get_combined_forecast(instrument)
print(f"Combined forecast: {combined.tail()}")

# 4. Subsystem position (before instrument weighting)
subsys_pos = system.positionSize.get_subsystem_position(instrument)
print(f"Subsystem position: {subsys_pos.tail()}")

# 5. Final notional position (after instrument weights)
notional = system.portfolio.get_notional_position(instrument)
print(f"Final position: {notional.tail()}")
```

### Examining P&L

```python
system = futures_system()

# Portfolio-level P&L
portfolio_pl = system.accounts.portfolio()

# Key statistics
stats = portfolio_pl.percent.stats()
print(stats)

# Individual components
print(f"Sharpe Ratio: {portfolio_pl.sharpe():.2f}")

# Gross vs Net
print(f"Gross Sharpe: {portfolio_pl.gross.sharpe():.2f}")
print(f"Net Sharpe:   {portfolio_pl.net.sharpe():.2f}")
```

---

## 12. Common Recipes

### Recipe 1: Compare Two Trading Rules

```python
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData
from systems.provided.rules.ewmac import ewmac_forecast_with_defaults as ewmac
from systems.accounts.account_forecast import pandl_for_instrument_forecast

data = csvFuturesSimData()
price = data.daily_prices("SP500")

# Fast trend
fast = ewmac(price, Lfast=8, Lslow=32)
# Slow trend
slow = ewmac(price, Lfast=64, Lslow=256)

# Compare performance
for name, forecast in [("Fast (8/32)", fast), ("Slow (64/256)", slow)]:
    account = pandl_for_instrument_forecast(forecast=forecast, price=price)
    print(f"\n{name}:")
    print(account.percent.stats())
```

### Recipe 2: Scan Multiple Instruments

```python
from systems.provided.futures_chapter15.basesystem import futures_system

system = futures_system()

print(f"{'Instrument':<12} {'Position':>10} {'Forecast':>10}")
print("-" * 34)

for instrument in system.get_instrument_list():
    pos = system.portfolio.get_notional_position(instrument).iloc[-1]
    fcst = system.combForecast.get_combined_forecast(instrument).iloc[-1]
    print(f"{instrument:<12} {pos:>10.1f} {fcst:>10.1f}")
```

### Recipe 3: Custom Trading Rule in a System

```python
from sysdata.sim.csv_futures_sim_data import csvFuturesSimData
from sysdata.config.configdata import Config
from systems.basesystem import System
from systems.forecasting import Rules
from systems.trading_rules import TradingRule
from systems.forecast_scale_cap import ForecastScaleCap
from systems.rawdata import RawData

# Define your own rule function
def mean_reversion(price, lookback=90):
    """Buy when price is below its moving average, sell when above."""
    import pandas as pd
    from sysquant.estimators.vol import robust_vol_calc

    ma = price.rolling(lookback).mean()
    raw_signal = ma - price  # Positive when price < MA (buy signal)
    vol = robust_vol_calc(price.diff())
    return raw_signal / vol

data = csvFuturesSimData()

# Register it as a TradingRule
mr_rule = TradingRule(
    dict(
        function=mean_reversion,
        data=["rawdata.get_daily_prices"],
        other_args=dict(lookback=90),
    )
)

config = Config(dict(
    trading_rules=dict(mean_revert=mr_rule),
    forecast_scalars=dict(mean_revert=10.0),
    instruments=["SP500", "US10", "CORN"],
))

system = System(
    [ForecastScaleCap(), Rules(), RawData()],
    data, config
)

# See the forecast
print(system.forecastScaleCap.get_capped_forecast("SP500", "mean_revert").tail())
```

### Recipe 4: Speed Up with Caching

```python
from systems.provided.futures_chapter15.basesystem import futures_system

system = futures_system()

# First call is slow (calculates everything)
sharpe = system.accounts.portfolio().sharpe()
print(f"Sharpe: {sharpe:.2f}")

# Save calculations to disk
system.cache.pickle("private.my_backtest_cache.pck")

# Later, restore them (instant)
system2 = futures_system()
system2.cache.unpickle("private.my_backtest_cache.pck")
sharpe2 = system2.accounts.portfolio().sharpe()  # Instant!
print(f"Sharpe (cached): {sharpe2:.2f}")
```

---

## 13. Where to Go Next

| Topic | Resource |
|-------|----------|
| Full backtesting guide | `docs/backtesting.md` |
| Data management | `docs/data.md` |
| Instrument configuration | `docs/instruments.md` |
| Production trading | `docs/production.md` |
| Interactive Brokers setup | `docs/IB.md` |
| More example scripts | `examples/introduction/` |
| Portfolio risk examples | `examples/portfolio_risk_management/` |
| Rob Carver's blog | [qoppac.blogspot.com](https://qoppac.blogspot.com) |
| The book | *Systematic Trading* by Robert Carver (2015) |

---

## 14. Glossary

| Term | Definition |
|------|-----------|
| **Adjusted price** | A back-adjusted price series where contract rolls are smoothed out so there are no gaps |
| **Back-adjustment** | The process of adjusting historical prices so that a continuous series can be created from sequential futures contracts |
| **Carry** | The return from holding a futures contract (related to the difference between near and far contract prices) |
| **EWMAC** | Exponential Weighted Moving Average Crossover — a trend-following trading rule |
| **Forecast** | A standardized signal (typically -20 to +20) indicating how bullish or bearish a rule is |
| **Forecast scalar** | A multiplier applied to raw forecasts so that the average absolute forecast equals 10 |
| **Instrument** | A tradable futures market (e.g., SP500, CORN, US10) |
| **Notional position** | The theoretical number of contracts you would hold |
| **Point value** | How much money one point of price movement represents (also called "block value") |
| **Roll** | The process of switching from an expiring contract to the next active contract |
| **Stage** | A processing step in the system pipeline |
| **System** | The main object that orchestrates data, config, and stages |
| **Vol target** | The desired annual volatility of the portfolio, used for position sizing |
