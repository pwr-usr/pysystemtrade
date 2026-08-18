"""A small pre-baked system used by the Chinese-futures backtesting tutorial."""

from pathlib import Path

from syscore.constants import arg_not_supplied
from sysdata.config.configdata import Config
from sysdata.sim.db_futures_sim_data import dbFuturesSimData
from systems.accounts.accounts_stage import Account
from systems.basesystem import System
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecasting import Rules
from systems.portfolio import Portfolios
from systems.positionsizing import PositionSizing
from systems.rawdata import RawData

CONFIG_PATH = Path(__file__).with_name("config.yaml")


def futures_system(
    data=arg_not_supplied,
    config=arg_not_supplied,
    trading_rules=arg_not_supplied,
) -> System:
    """Build the tutorial system, allowing data, config, or rules overrides."""

    if data is arg_not_supplied:
        data = dbFuturesSimData()

    if config is arg_not_supplied:
        config = Config(str(CONFIG_PATH))

    return System(
        [
            Account(),
            Portfolios(),
            PositionSizing(),
            RawData(),
            ForecastCombine(),
            ForecastScaleCap(),
            Rules(trading_rules),
        ],
        data,
        config,
    )
