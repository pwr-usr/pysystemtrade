from pathlib import Path

import pandas as pd
import pytest

from sysdata.parquet.parquet_access import ParquetAccess
from sysdata.parquet.parquet_adjusted_prices import parquetFuturesAdjustedPricesData
from sysobjects.adjusted_prices import futuresAdjustedPrices


DATA_TYPE = "test_data"
IDENTIFIER = "prices"


def test_atomic_parquet_write_replaces_file_and_cleans_temporary_file(tmp_path):
    parquet = ParquetAccess(str(tmp_path))
    original_data = pd.DataFrame({"value": [1.0, 2.0]})
    replacement_data = pd.DataFrame({"value": [3.0, 4.0]})

    parquet.write_data_given_data_type_and_identifier(
        original_data, DATA_TYPE, IDENTIFIER
    )
    parquet.write_data_given_data_type_and_identifier(
        replacement_data, DATA_TYPE, IDENTIFIER
    )

    result = parquet.read_data_given_data_type_and_identifier(DATA_TYPE, IDENTIFIER)
    pd.testing.assert_frame_equal(result, replacement_data)
    assert _files_in_data_type_directory(tmp_path) == [f"{IDENTIFIER}.parquet"]


def test_single_adjusted_price_is_read_as_a_series(tmp_path):
    adjusted_price_store = parquetFuturesAdjustedPricesData(
        ParquetAccess(str(tmp_path))
    )
    expected = futuresAdjustedPrices(
        pd.Series([100.0], index=pd.to_datetime(["2024-01-02 23:00"]))
    )

    adjusted_price_store.add_adjusted_prices("ONE_ROW", expected)
    actual = adjusted_price_store.get_adjusted_prices("ONE_ROW")

    assert isinstance(actual, pd.Series)
    assert len(actual) == 1
    assert actual.iloc[0] == 100.0
    assert actual.index[0] == pd.Timestamp("2024-01-02 23:00")


def test_failed_parquet_write_preserves_old_file_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    parquet = ParquetAccess(str(tmp_path))
    original_data = pd.DataFrame({"value": [1.0, 2.0]})
    replacement_data = pd.DataFrame({"value": [3.0, 4.0]})
    parquet.write_data_given_data_type_and_identifier(
        original_data, DATA_TYPE, IDENTIFIER
    )

    def fail_after_partial_write(_dataframe, filename, **_kwargs):
        Path(filename).write_bytes(b"partial parquet data")
        raise RuntimeError("simulated parquet write failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail_after_partial_write)

    with pytest.raises(RuntimeError, match="simulated parquet write failure"):
        parquet.write_data_given_data_type_and_identifier(
            replacement_data, DATA_TYPE, IDENTIFIER
        )

    result = parquet.read_data_given_data_type_and_identifier(DATA_TYPE, IDENTIFIER)
    pd.testing.assert_frame_equal(result, original_data)
    assert _files_in_data_type_directory(tmp_path) == [f"{IDENTIFIER}.parquet"]


def _files_in_data_type_directory(base_path: Path) -> list[str]:
    return sorted(path.name for path in (base_path / DATA_TYPE).iterdir())
