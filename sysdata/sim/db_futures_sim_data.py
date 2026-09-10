"""
Get data from mongo and arctic used for futures trading

"""

from syscore.constants import arg_not_supplied
import pandas as pd

from sysobjects.adjusted_prices import futuresAdjustedPrices
from sysobjects.multiple_prices import futuresMultiplePrices

from sysdata.parquet.parquet_adjusted_prices import parquetFuturesAdjustedPricesData
from sysdata.parquet.parquet_multiple_prices import parquetFuturesMultiplePricesData
from sysdata.parquet.parquet_spotfx_prices import parquetFxPricesData

"""
from sysdata.arctic.arctic_adjusted_prices import arcticFuturesAdjustedPricesData
from sysdata.arctic.arctic_multiple_prices import arcticFuturesMultiplePricesData
from sysdata.arctic.arctic_spotfx_prices import arcticFxPricesData
"""

from sysdata.csv.csv_instrument_data import csvFuturesInstrumentData
from sysdata.csv.csv_roll_parameters import csvRollParametersData
from sysdata.mongodb.mongo_spread_costs import mongoSpreadCostData
from sysdata.data_blob import dataBlob
from sysdata.sim.futures_sim_data_with_data_blob import genericBlobUsingFuturesSimData

from syslogging.logger import *


class dbFuturesSimData(genericBlobUsingFuturesSimData):
    def __init__(
        self,
        data: dataBlob = arg_not_supplied,
        csv_data_paths=arg_not_supplied,
        log=get_logger("dbFuturesSimData"),
        trading_windows=arg_not_supplied,
    ):
        if data is arg_not_supplied:
            data = dataBlob(
                log=log,
                csv_data_paths=csv_data_paths,
                class_list=[
                    get_class_for_data_type(FUTURES_ADJUSTED_PRICE_DATA),
                    get_class_for_data_type(FUTURES_MULTIPLE_PRICE_DATA),
                    get_class_for_data_type(FX_DATA),
                    get_class_for_data_type(FUTURES_INSTRUMENT_DATA),
                    get_class_for_data_type(ROLL_PARAMETERS_DATA),
                    get_class_for_data_type(STORED_SPREAD_DATA),
                ],
            )

        super().__init__(data=data)
        self._trading_windows = None
        if trading_windows is not arg_not_supplied:
            windows = (
                trading_windows.copy()
                if isinstance(trading_windows, pd.DataFrame)
                else pd.read_csv(trading_windows)
            )
            windows[["Start", "End"]] = windows[["Start", "End"]].apply(pd.to_datetime)
            if (
                windows.Instrument.isna().any()
                or (windows.Start.isna() ^ windows.End.isna()).any()
            ):
                raise ValueError("Trading windows require an instrument and both dates")
            # A reviewed instrument with two empty dates has no usable interval.
            windows = windows.dropna(subset=["Start", "End"])
            if (windows.Start > windows.End).any():
                raise ValueError("Trading window Start must precede End")
            windows = windows.sort_values(["Instrument", "Start"])
            previous_end = windows.groupby("Instrument").End.shift()
            if (windows.Start <= previous_end).any():
                raise ValueError("Trading windows must not overlap")
            self._trading_windows = windows

    def get_trading_windows(self, instrument_code):
        """Inclusive observed-price windows; None leaves stored history unchanged.

        An explicitly supplied table excludes instruments with no rows. These
        windows describe reviewed history. Simulation stages must restart their
        warmup in each window and close positions at its reviewed boundary.
        """
        if self._trading_windows is None:
            return None
        rows = self._trading_windows.loc[
            self._trading_windows.Instrument.eq(instrument_code), ["Start", "End"]
        ]
        return list(rows.itertuples(index=False, name=None))

    def _prices_in_trading_windows(self, prices, instrument_code):
        windows = self.get_trading_windows(instrument_code)
        if windows is None:
            return prices
        valid = pd.Series(False, index=prices.index)
        dates = prices.index.normalize()
        for start, end in windows:
            valid |= (dates >= start.normalize()) & (dates <= end.normalize())
        if not valid.any():
            return prices.iloc[:0].copy()
        result = prices.copy()
        result.loc[~valid] = float("nan")
        return result.loc[valid.index[valid][0] : valid.index[valid][-1]]

    def get_backadjusted_futures_price(self, instrument_code):
        prices = super().get_backadjusted_futures_price(instrument_code)
        return futuresAdjustedPrices(
            self._prices_in_trading_windows(prices, instrument_code)
        )

    def get_multiple_prices_from_start_date(self, instrument_code, start_date):
        prices = super().get_multiple_prices_from_start_date(
            instrument_code, start_date
        )
        return futuresMultiplePrices(
            self._prices_in_trading_windows(prices, instrument_code)
        )

    def __repr__(self):
        return "dbFuturesSimData object with %d instruments" % len(
            self.get_instrument_list()
        )


FUTURES_MULTIPLE_PRICE_DATA = "futures_multiple_price_data"
FUTURES_ADJUSTED_PRICE_DATA = "futures_adjusted_price_data"
CAPITAL_DATA = "capital_data"
FX_DATA = "fx_data"
ROLL_PARAMETERS_DATA = "roll_parameters_data"
FUTURES_INSTRUMENT_DATA = "futures_instrument_data"
STORED_SPREAD_DATA = "stored_spread_data"


def get_class_for_data_type(data_type: str):
    return use_sim_classes[data_type]


use_sim_classes = {
    FX_DATA: parquetFxPricesData,
    ROLL_PARAMETERS_DATA: csvRollParametersData,
    FUTURES_INSTRUMENT_DATA: csvFuturesInstrumentData,
    FUTURES_MULTIPLE_PRICE_DATA: parquetFuturesMultiplePricesData,
    FUTURES_ADJUSTED_PRICE_DATA: parquetFuturesAdjustedPricesData,
    STORED_SPREAD_DATA: mongoSpreadCostData,
}


if __name__ == "__main__":
    import doctest

    doctest.testmod()
