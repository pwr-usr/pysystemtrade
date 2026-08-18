"""Opt-in Parquet/Mongo integration check for the Tushare simulation path."""

import os
import uuid

import pandas as pd
import pytest

from sysdata.data_blob import dataBlob
from sysdata.mongodb.mongo_connection import mongoDb
from sysdata.sim.db_futures_sim_data import dbFuturesSimData, use_sim_classes
from sysobjects.adjusted_prices import futuresAdjustedPrices
from sysobjects.multiple_prices import futuresMultiplePrices
from sysobjects.spot_fx_prices import fxPrices


RUN_MONGO_TESTS = os.environ.get("PYSYSTEMTRADE_RUN_MONGO_TESTS") == "1"


def _test_mongo(database_name: str) -> mongoDb:
    return mongoDb(
        mongo_db=database_name,
        mongo_host=os.environ.get("PYSYSTEMTRADE_TUSHARE_MONGO_HOST", "127.0.0.1"),
        mongo_port=int(os.environ.get("PYSYSTEMTRADE_TUSHARE_MONGO_PORT", "27017")),
    )


@pytest.mark.skipif(
    not RUN_MONGO_TESTS,
    reason="set PYSYSTEMTRADE_RUN_MONGO_TESTS=1 for the local Mongo integration test",
)
def test_db_futures_sim_data_reads_tushare_storage_conventions(tmp_path):
    database_name = "pysystemtrade_tushare_test_" + uuid.uuid4().hex
    mongo = _test_mongo(database_name)
    data = dataBlob(
        class_list=list(use_sim_classes.values()),
        log_name="tushare_sim_integration_test",
        parquet_store_path=str(tmp_path / ".parquet-store"),
        mongo_db=mongo,
    )

    dates = pd.to_datetime(["2024-01-02 23:00", "2024-01-03 23:00"])
    multiple = futuresMultiplePrices(
        pd.DataFrame(
            {
                "PRICE": [68000.0, 68100.0],
                "CARRY": [67900.0, 68000.0],
                "FORWARD": [68200.0, 68300.0],
                "PRICE_CONTRACT": ["20240200", "20240200"],
                "CARRY_CONTRACT": ["20240100", "20240100"],
                "FORWARD_CONTRACT": ["20240300", "20240300"],
            },
            index=dates,
        )
    )
    adjusted = futuresAdjustedPrices(pd.Series([68000.0, 68100.0], index=dates))
    cnhusd = fxPrices(pd.Series([0.139, 0.140], index=dates))

    try:
        data.db_futures_multiple_prices.add_multiple_prices("SHFE_CU", multiple)
        data.db_futures_adjusted_prices.add_adjusted_prices("SHFE_CU", adjusted)
        data.db_fx_prices.add_fx_prices("CNHUSD", cnhusd)
        data.db_spread_cost.update_spread_cost("SHFE_CU", 2.5)

        simulation = dbFuturesSimData(data=data)

        assert simulation.get_instrument_list() == ["SHFE_CU"]
        pd.testing.assert_frame_equal(
            pd.DataFrame(simulation.get_multiple_prices("SHFE_CU")).reset_index(
                drop=True
            ),
            pd.DataFrame(multiple).reset_index(drop=True),
        )
        pd.testing.assert_series_equal(
            pd.Series(simulation.get_backadjusted_futures_price("SHFE_CU")).reset_index(
                drop=True
            ),
            pd.Series(adjusted).reset_index(drop=True),
            check_names=False,
        )
        metadata = simulation.get_instrument_meta_data("SHFE_CU")
        assert metadata.meta_data.Currency == "CNH"
        assert metadata.meta_data.Pointsize == 5
        assert simulation.get_spread_cost("SHFE_CU") == pytest.approx(2.5)
        pd.testing.assert_series_equal(
            pd.Series(simulation.get_fx_for_instrument("SHFE_CU", "USD")).reset_index(
                drop=True
            ),
            pd.Series(cnhusd).reset_index(drop=True),
            check_names=False,
        )
    finally:
        mongo.client.drop_database(database_name)
