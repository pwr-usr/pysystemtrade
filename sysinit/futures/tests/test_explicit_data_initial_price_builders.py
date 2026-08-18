import pandas as pd
import pytest

from sysdata.data_blob import dataBlob
from sysdata.parquet.parquet_access import ParquetAccess
from sysdata.parquet.parquet_adjusted_prices import (
    parquetFuturesAdjustedPricesData,
)
from sysdata.parquet.parquet_futures_per_contract_prices import (
    parquetFuturesContractPriceData,
)
from sysdata.parquet.parquet_multiple_prices import (
    parquetFuturesMultiplePricesData,
)
from sysinit.futures import adjustedprices_from_db_multiple_to_db
from sysinit.futures import multipleprices_from_db_prices_and_csv_calendars_to_db
from sysinit.futures import rollcalendars_from_db_prices_to_csv
from sysinit.futures.build_multiple_prices_from_raw_data import (
    create_multiple_price_stack_from_raw_data,
)
from sysobjects.contracts import futuresContract
from sysobjects.dict_of_futures_per_contract_prices import (
    dictFuturesContractFinalPrices,
)
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysobjects.multiple_prices import futuresMultiplePrices
from sysobjects.roll_calendars import rollCalendar
from sysobjects.rolls import rollParameters


INSTRUMENT_CODE = "SHFE_CU_TEST"
CONTRACT_DATES = ("20990100", "20990200", "20990300")


def _write_contract_prices(parquet_store):
    contract_prices = parquetFuturesContractPriceData(ParquetAccess(parquet_store))
    index = pd.date_range("2099-01-01 23:00:00", periods=6, freq="D")
    for position, contract_date in enumerate(CONTRACT_DATES):
        final = pd.Series(
            [100.0 + position + day for day in range(len(index))],
            index=index,
        )
        contract_prices.write_merged_prices_for_contract_object(
            futuresContract(INSTRUMENT_CODE, contract_date),
            futuresContractPrices.create_from_final_prices_only(final),
        )


def _candidate_calendar():
    return rollCalendar(
        pd.DataFrame(
            {
                "current_contract": ("20990100", "20990200", "20990300"),
                "next_contract": ("20990200", "20990300", "20990400"),
                "carry_contract": ("20990200", "20990300", "20990400"),
            },
            index=pd.DatetimeIndex(
                [
                    "2099-01-02 23:00:00",
                    "2099-01-04 23:00:00",
                    "2099-01-06 23:00:00",
                ],
                name="current_roll_date",
            ),
        )
    )


def test_initial_builders_use_injected_parquet_store_not_module_global(
    tmp_path, monkeypatch
):
    parquet_store = str(tmp_path / ".tushare-data" / "parquet")
    _write_contract_prices(parquet_store)
    data = dataBlob(parquet_store_path=parquet_store)
    # diagPrices requires contract metadata in production, but these pure
    # initial-price builders do not use it. Avoid opening a real MongoDB in
    # this storage-routing test.
    data.db_futures_contract = object()

    multiple_diag_prices = (
        multipleprices_from_db_prices_and_csv_calendars_to_db.diagPrices
    )
    adjusted_diag_prices = adjustedprices_from_db_multiple_to_db.diagPrices

    def multiple_prices_for_injected_data(received_data):
        assert received_data is data
        return multiple_diag_prices(received_data)

    def adjusted_prices_for_injected_data(received_data):
        assert received_data is data
        return adjusted_diag_prices(received_data)

    monkeypatch.setattr(
        multipleprices_from_db_prices_and_csv_calendars_to_db,
        "diagPrices",
        multiple_prices_for_injected_data,
    )
    monkeypatch.setattr(
        adjustedprices_from_db_multiple_to_db,
        "diagPrices",
        adjusted_prices_for_injected_data,
    )

    multiple_prices = multipleprices_from_db_prices_and_csv_calendars_to_db.process_multiple_prices_single_instrument(
        INSTRUMENT_CODE,
        adjust_calendar_to_prices=True,
        roll_parameters=rollParameters(
            hold_rollcycle="FGHJKMNQUVXZ",
            priced_rollcycle="FGHJKMNQUVXZ",
            carry_offset=1,
        ),
        roll_calendar=_candidate_calendar(),
        ADD_TO_DB=True,
        data=data,
    )
    adjusted_prices = (
        adjustedprices_from_db_multiple_to_db.process_adjusted_prices_single_instrument(
            INSTRUMENT_CODE,
            ADD_TO_DB=True,
            data=data,
        )
    )

    parquet_access = ParquetAccess(parquet_store)
    stored_multiple = parquetFuturesMultiplePricesData(
        parquet_access
    ).get_multiple_prices(INSTRUMENT_CODE)
    stored_adjusted = parquetFuturesAdjustedPricesData(
        parquet_access
    ).get_adjusted_prices(INSTRUMENT_CODE)

    pd.testing.assert_frame_equal(stored_multiple, multiple_prices)
    pd.testing.assert_series_equal(stored_adjusted, adjusted_prices, check_names=False)
    assert not stored_multiple.empty
    assert stored_multiple["PRICE_CONTRACT"].eq("20990200").all()
    assert stored_adjusted.notna().all()


def test_roll_calendar_builder_does_not_open_default_store_when_prices_are_injected(
    monkeypatch,
):
    class CandidateCalendar:
        def check_is_valid(self, prices):
            return True

    candidate = CandidateCalendar()

    class ExplicitPrices:
        def get_merged_prices_for_instrument(self, instrument_code):
            assert instrument_code == INSTRUMENT_CODE

            class MergedPrices:
                def final_prices(self):
                    return object()

            return MergedPrices()

    monkeypatch.setattr(
        rollcalendars_from_db_prices_to_csv,
        "diagPrices",
        lambda: (_ for _ in ()).throw(AssertionError("default price store was opened")),
    )
    monkeypatch.setattr(
        rollcalendars_from_db_prices_to_csv.rollCalendar,
        "create_from_prices",
        lambda prices, parameters: candidate,
    )
    built = rollcalendars_from_db_prices_to_csv.build_and_write_roll_calendar(
        INSTRUMENT_CODE,
        write=False,
        input_prices=ExplicitPrices(),
        roll_parameters=rollParameters(
            hold_rollcycle="FGHJKMNQUVXZ",
            priced_rollcycle="FGHJKMNQUVXZ",
            carry_offset=1,
        ),
    )

    assert built is candidate


def test_forward_and_carry_prices_cannot_add_dates_without_a_held_price():
    calendar = rollCalendar(
        pd.DataFrame(
            {
                "current_contract": ("20240100", "20240200"),
                "next_contract": ("20240200", "20240300"),
                "carry_contract": ("20240300", "20240400"),
            },
            index=pd.to_datetime(["2024-01-01 23:00", "2024-01-10 23:00"]),
        )
    )
    held_index = pd.date_range("2024-01-02 23:00", periods=3, freq="D")
    expected_index = held_index[[0, 2]]
    prices = dictFuturesContractFinalPrices(
        {
            "20240200": pd.Series([100.0, float("nan"), 101.0], index=held_index),
            "20240300": pd.Series(
                [110.0] * 4,
                index=pd.date_range("2024-01-02 23:00", periods=4, freq="D"),
            ),
            "20240400": pd.Series(
                [120.0] * 5,
                index=pd.date_range("2024-01-02 23:00", periods=5, freq="D"),
            ),
        }
    )

    multiple = create_multiple_price_stack_from_raw_data(calendar, prices)

    assert multiple.index.equals(expected_index)
    assert multiple["PRICE"].notna().all()
    assert multiple["FORWARD"].notna().all()
    assert multiple["CARRY"].notna().all()


def _validation_prices():
    index = pd.date_range("2099-01-01 23:00", periods=5, freq="D")
    return dictFuturesContractFinalPrices(
        {
            "20990100": pd.Series([100.0] * 5, index=index),
            "20990200": pd.Series([101.0] * 5, index=index),
            "20990300": pd.Series([102.0] * 5, index=index),
        }
    )


def _validation_calendar():
    return rollCalendar(
        pd.DataFrame(
            {
                "current_contract": ("20990100", "20990200"),
                "next_contract": ("20990200", "20990300"),
                "carry_contract": ("20990300", "20990100"),
            },
            index=pd.to_datetime(["2099-01-02 23:00", "2099-01-04 23:00"]),
        )
    )


def test_roll_calendar_composite_validation_checks_structure_and_prices():
    prices = _validation_prices()
    assert _validation_calendar().check_is_valid(prices)

    empty = rollCalendar(
        pd.DataFrame(columns=["current_contract", "next_contract", "carry_contract"])
    )
    assert not empty.check_is_valid(prices)

    broken_chain = _validation_calendar().copy()
    broken_chain.iloc[1, broken_chain.columns.get_loc("current_contract")] = "20990100"
    assert not rollCalendar(broken_chain).check_is_valid(prices)

    reversed_dates = _validation_calendar().iloc[::-1]
    assert not rollCalendar(reversed_dates).check_is_valid(prices)

    duplicate_dates = _validation_calendar().copy()
    duplicate_dates.index = pd.DatetimeIndex([duplicate_dates.index[0]] * 2)
    assert not rollCalendar(duplicate_dates).check_is_valid(prices)

    prices_with_missing_roll_quote = _validation_prices()
    prices_with_missing_roll_quote["20990300"].loc[
        pd.Timestamp("2099-01-04 23:00")
    ] = float("nan")
    assert not _validation_calendar().check_is_valid(prices_with_missing_roll_quote)


def test_multiple_price_build_rejects_an_invalid_effective_calendar(monkeypatch):
    stored = []

    class MergedPrices:
        def final_prices(self):
            return _validation_prices()

    class ContractPriceStore:
        def get_merged_prices_for_instrument(self, instrument_code):
            return MergedPrices()

    class MultiplePriceStore:
        def add_multiple_prices(self, *args, **kwargs):
            stored.append((args, kwargs))

    monkeypatch.setattr(
        multipleprices_from_db_prices_and_csv_calendars_to_db,
        "_get_data_inputs",
        lambda *args, **kwargs: (
            object(),
            ContractPriceStore(),
            MultiplePriceStore(),
            object(),
        ),
    )

    broken_chain = _validation_calendar().copy()
    broken_chain.iloc[1, broken_chain.columns.get_loc("current_contract")] = "20990100"

    with pytest.raises(ValueError, match="Invalid roll calendar"):
        multipleprices_from_db_prices_and_csv_calendars_to_db.process_multiple_prices_single_instrument(
            INSTRUMENT_CODE,
            adjust_calendar_to_prices=False,
            roll_parameters=object(),
            roll_calendar=rollCalendar(broken_chain),
            ADD_TO_DB=True,
            ADD_TO_CSV=False,
        )
    assert not stored


def _strict_stitch_multiple_prices(held_prices):
    assert len(held_prices) == 2
    index = pd.date_range("2024-01-01 23:00", periods=len(held_prices), freq="D")
    return futuresMultiplePrices(
        pd.DataFrame(
            {
                "PRICE": held_prices,
                "FORWARD": [110.0] * len(index),
                "CARRY": [90.0] * len(index),
                "PRICE_CONTRACT": ["20240100", "20240200"],
                "FORWARD_CONTRACT": ["20240200", "20240300"],
                "CARRY_CONTRACT": ["20231200", "20240100"],
            },
            index=index,
        )
    )


def test_production_adjusted_builder_rejects_missing_held_price(monkeypatch):
    monkeypatch.setattr(
        adjustedprices_from_db_multiple_to_db,
        "_get_data_inputs",
        lambda *args, **kwargs: (object(), object(), object()),
    )

    with pytest.raises(ValueError, match="held PRICE is missing"):
        adjustedprices_from_db_multiple_to_db.process_adjusted_prices_single_instrument(
            INSTRUMENT_CODE,
            multiple_prices=_strict_stitch_multiple_prices([100.0, float("nan")]),
            ADD_TO_DB=False,
            ADD_TO_CSV=False,
        )


def test_production_adjusted_builder_uses_strict_stitch(monkeypatch):
    monkeypatch.setattr(
        adjustedprices_from_db_multiple_to_db,
        "_get_data_inputs",
        lambda *args, **kwargs: (object(), object(), object()),
    )
    captured = {}

    def strict_stitch(multiple_prices, forward_fill):
        captured["forward_fill"] = forward_fill
        return pd.Series(multiple_prices["PRICE"], index=multiple_prices.index)

    monkeypatch.setattr(
        adjustedprices_from_db_multiple_to_db.futuresAdjustedPrices,
        "stitch_multiple_prices",
        strict_stitch,
    )

    adjustedprices_from_db_multiple_to_db.process_adjusted_prices_single_instrument(
        INSTRUMENT_CODE,
        multiple_prices=_strict_stitch_multiple_prices([100.0, 101.0]),
        ADD_TO_DB=False,
        ADD_TO_CSV=False,
    )

    assert captured["forward_fill"] is False
