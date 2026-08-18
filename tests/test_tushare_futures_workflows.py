"""Behavioural tests for the Tushare seed/update workflows.

Storage-touching tests run end-to-end against a throwaway Mongo database and
a temporary parquet store (set ``PYSYSTEMTRADE_RUN_MONGO_TESTS=1``); only the
vendor source is faked.  Pure helpers are tested unconditionally.
"""

from __future__ import annotations

import datetime
import os
import uuid
from types import SimpleNamespace

import pandas as pd
import pytest

from syscore.dateutils import DAILY_PRICE_FREQ
from sysdata.data_blob import dataBlob
from sysdata.mongodb.mongo_connection import mongoDb
from sysdata.tushare.errors import TushareDataError, TushareTransientError
from sysdata.tushare.source import HistoricalFuturesContract
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysobjects.spot_fx_prices import fxPrices
from sysproduction.data.contracts import dataContracts
from sysproduction.data.prices import diagPrices
import sysproduction.update_tushare_futures as tushare_workflows
from sysproduction.update_tushare_futures import (
    _raise_if_fatal_tushare_error,
    historical_price_correction_dates,
    safe_tushare_error_text,
    seed_tushare_futures,
    update_tushare_cnhusd,
    update_tushare_futures,
)

RUN_MONGO_TESTS = os.environ.get("PYSYSTEMTRADE_RUN_MONGO_TESTS") == "1"
mongo_gated = pytest.mark.skipif(
    not RUN_MONGO_TESTS,
    reason="set PYSYSTEMTRADE_RUN_MONGO_TESTS=1 for the local Mongo workflow tests",
)

AS_OF = datetime.date(2024, 6, 3)


# ---------------------------------------------------------------------------
# pure helpers (always run)
# ---------------------------------------------------------------------------


def test_error_text_redacts_the_active_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "super-secret-token")
    text = safe_tushare_error_text(RuntimeError("failed with super-secret-token"))
    assert "super-secret-token" not in text
    assert "<redacted>" in text


def test_correction_dates_detects_changed_added_and_removed_history():
    old = _prices({"2024-05-28": 100.0, "2024-05-29": 101.0, "2024-05-30": 102.0})
    unchanged = _prices({"2024-05-28": 100.0, "2024-05-29": 101.0, "2024-05-30": 102.0})
    assert historical_price_correction_dates(old, unchanged) == []

    changed = _prices({"2024-05-28": 100.0, "2024-05-29": 999.0, "2024-05-30": 102.0})
    assert historical_price_correction_dates(old, changed) == [
        datetime.date(2024, 5, 29)
    ]

    added = _prices(
        {
            "2024-05-27": 99.0,
            "2024-05-28": 100.0,
            "2024-05-29": 101.0,
            "2024-05-30": 102.0,
        }
    )
    assert historical_price_correction_dates(old, added) == [datetime.date(2024, 5, 27)]

    removed = _prices({"2024-05-28": 100.0, "2024-05-30": 102.0})
    assert historical_price_correction_dates(
        old, removed, comparison_start=datetime.date(2024, 5, 28)
    ) == [datetime.date(2024, 5, 29)]


def test_correction_dates_ignores_genuinely_new_rows():
    old = _prices({"2024-05-28": 100.0, "2024-05-29": 101.0})
    extended = _prices({"2024-05-28": 100.0, "2024-05-29": 101.0, "2024-05-30": 102.0})
    assert historical_price_correction_dates(old, extended) == []


def test_unknown_workflow_errors_are_critically_logged_and_raised():
    critical_messages = []
    log = SimpleNamespace(critical=critical_messages.append)
    error = RuntimeError("unexpected programming failure")

    with pytest.raises(RuntimeError, match="unexpected programming failure"):
        _raise_if_fatal_tushare_error(error, log)

    assert len(critical_messages) == 1
    assert "fatal error" in critical_messages[0]


def test_cnhusd_update_rejects_mutated_overlap(monkeypatch):
    index = pd.to_datetime(["2024-05-29 23:00", "2024-05-30 23:00"])
    existing = fxPrices(pd.Series([0.138, 0.139], index=index))
    downloaded = fxPrices(pd.Series([0.138, 0.999], index=index))

    class FakeCurrencyData:
        def get_fx_prices(self, code):
            assert code == "CNHUSD"
            return existing

        def update_fx_prices_and_return_rows_added(self, *args, **kwargs):
            raise AssertionError("mutated CNHUSD history must not be written")

    monkeypatch.setattr(
        tushare_workflows, "dataCurrency", lambda data: FakeCurrencyData()
    )
    source = SimpleNamespace(
        get_cnhusd_prices=lambda **kwargs: downloaded,
    )

    with pytest.raises(RuntimeError, match="changed stored CNHUSD history"):
        update_tushare_cnhusd(
            data=object(),
            source=source,
            as_of=datetime.date(2024, 5, 30),
            overlap_days=7,
        )


# ---------------------------------------------------------------------------
# end-to-end workflows (mongo-gated)
# ---------------------------------------------------------------------------


@pytest.fixture()
def blob(tmp_path):
    database_name = "pysystemtrade_tushare_test_" + uuid.uuid4().hex
    mongo = mongoDb(
        mongo_db=database_name,
        mongo_host=os.environ.get("PYSYSTEMTRADE_TUSHARE_MONGO_HOST", "127.0.0.1"),
        mongo_port=int(os.environ.get("PYSYSTEMTRADE_TUSHARE_MONGO_PORT", "27017")),
    )
    data = dataBlob(
        log_name="tushare_workflow_test",
        parquet_store_path=str(tmp_path / "parquet"),
        mongo_db=mongo,
    )
    yield data
    mongo.client.drop_database(database_name)


@mongo_gated
def test_seed_writes_day_and_merged_prices_and_contract_state(blob):
    records, source = _cu_fixture()
    result = seed_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )

    assert result.okay
    assert result.contracts_written == 2
    assert result.contracts_not_yet_listed == 1

    price_store = diagPrices(blob).db_futures_contract_price_data
    expired_contract = records[0].as_futures_contract()
    daily = price_store.get_prices_at_frequency_for_contract_object(
        expired_contract, frequency=DAILY_PRICE_FREQ
    )
    merged = price_store.get_merged_prices_for_contract_object(expired_contract)
    assert pd.DataFrame(daily).equals(pd.DataFrame(merged))
    assert daily.index[0] == pd.Timestamp("2024-01-02 23:00:00")

    contracts = dataContracts(blob)
    stored = {
        contract.date_str: contract
        for contract in contracts.get_all_contract_objects_for_instrument_code(
            "SHFE_CU"
        )
    }
    assert not stored["20240100"].currently_sampling
    assert stored["20240800"].currently_sampling


@mongo_gated
def test_seed_resume_refreshes_active_and_repairs_finalized_checkpoints(blob):
    records, source = _cu_fixture()
    seed_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )
    calls_after_seed = source.calls

    rerun = seed_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )
    assert rerun.okay
    assert rerun.contracts_skipped_complete == 1
    assert rerun.contracts_written == 1
    assert source.calls == calls_after_seed + 1  # active contract was refreshed

    # delete the merged file: resume must repair it from the daily series
    price_store = diagPrices(blob).db_futures_contract_price_data
    expired_contract = records[0].as_futures_contract()
    price_store._delete_merged_prices_for_contract_object_with_no_checks_be_careful(
        expired_contract
    )
    repair = seed_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )
    assert repair.contracts_repaired == 1
    assert repair.contracts_written == 1
    assert source.calls == calls_after_seed + 2
    merged = price_store.get_merged_prices_for_contract_object(expired_contract)
    assert len(merged) == 2


@mongo_gated
def test_seed_no_resume_overwrites_checkpoints(blob):
    records, source = _cu_fixture()
    seed_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )
    rerun = seed_tushare_futures(
        data=blob,
        source=source,
        records=records,
        resume=False,
        as_of=AS_OF,
        update_fx=False,
    )
    assert rerun.okay
    assert rerun.contracts_written == 2
    assert rerun.contracts_skipped_complete == 0


@mongo_gated
def test_update_appends_new_rows_and_is_idempotent(blob):
    records, source = _cu_fixture()
    seed_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )

    source.extend("CU2408.SHF", {"2024-06-04": 103.0})
    next_day = datetime.date(2024, 6, 4)
    result = update_tushare_futures(
        data=blob, source=source, records=records, as_of=next_day, update_fx=False
    )
    assert result.okay
    assert result.rows_written == 1
    assert result.contracts_written == 1  # only the active contract is sampling

    price_store = diagPrices(blob).db_futures_contract_price_data
    active_contract = records[1].as_futures_contract()
    daily = price_store.get_prices_at_frequency_for_contract_object(
        active_contract, frequency=DAILY_PRICE_FREQ
    )
    merged = price_store.get_merged_prices_for_contract_object(active_contract)
    assert daily.index[-1] == pd.Timestamp("2024-06-04 23:00:00")
    assert pd.DataFrame(daily).equals(pd.DataFrame(merged))

    again = update_tushare_futures(
        data=blob, source=source, records=records, as_of=next_day, update_fx=False
    )
    assert again.okay
    assert again.rows_written == 0


@mongo_gated
def test_update_refuses_silently_mutated_history(blob):
    records, source = _cu_fixture()
    seed_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )

    source.mutate("CU2408.SHF", "2024-05-30", 999.0)
    result = update_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )
    assert not result.okay
    assert len(result.failures) == 1
    assert "changed stored history" in result.failures[0].reason

    # stored prices must be untouched
    price_store = diagPrices(blob).db_futures_contract_price_data
    active_contract = records[1].as_futures_contract()
    daily = price_store.get_prices_at_frequency_for_contract_object(
        active_contract, frequency=DAILY_PRICE_FREQ
    )
    assert daily["FINAL"].iloc[-1] == 102.0


@mongo_gated
def test_transient_source_failure_is_contract_level_but_data_error_is_fatal(blob):
    records, source = _cu_fixture()

    source.fail_with = TushareTransientError("provider flaked")
    result = seed_tushare_futures(
        data=blob, source=source, records=records, as_of=AS_OF, update_fx=False
    )
    assert not result.okay
    assert len(result.failures) == 2  # both fetchable contracts failed, run completed

    source.fail_with = TushareDataError("schema drift")
    with pytest.raises(TushareDataError):
        seed_tushare_futures(
            data=blob,
            source=source,
            records=records,
            resume=False,
            as_of=AS_OF,
            update_fx=False,
        )

    source.fail_with = RuntimeError("unexpected programming failure")
    with pytest.raises(RuntimeError, match="unexpected programming failure"):
        seed_tushare_futures(
            data=blob,
            source=source,
            records=records,
            resume=False,
            as_of=AS_OF,
            update_fx=False,
        )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _cu_fixture():
    """Three SHFE copper contracts: expired, active, and not yet listed."""

    expired = _record("20240100", "CU2401.SHF", "2023-01-03", "2024-01-15")
    active = _record("20240800", "CU2408.SHF", "2023-08-01", "2024-08-15")
    unlisted = _record("20250100", "CU2501.SHF", "2024-07-01", "2025-01-15")
    source = _FakeSource(
        {
            "CU2401.SHF": {"2024-01-02": 68000.0, "2024-01-03": 68100.0},
            "CU2408.SHF": {"2024-05-29": 100.0, "2024-05-30": 102.0},
        }
    )
    return [expired, active, unlisted], source


def _record(contract_date, external_code, first_trade, expiry):
    return HistoricalFuturesContract(
        instrument_code="SHFE_CU",
        contract_date=contract_date,
        external_contract_code=external_code,
        exchange="SHFE",
        product_code="CU",
        first_trade_date=pd.Timestamp(first_trade).date(),
        expiry_date=pd.Timestamp(expiry).date(),
    )


def _prices(finals: dict[str, float]) -> futuresContractPrices:
    index = pd.to_datetime(list(finals.keys())) + pd.Timedelta(hours=23)
    values = list(finals.values())
    return futuresContractPrices(
        pd.DataFrame(
            {
                "OPEN": values,
                "HIGH": values,
                "LOW": values,
                "FINAL": values,
                "VOLUME": [1000.0] * len(values),
            },
            index=index,
        )
    )


class _FakeSource:
    """Vendor stand-in: per-ts_code daily finals, window-sliced like Tushare."""

    def __init__(self, finals_by_code: dict[str, dict[str, float]]):
        self.finals_by_code = finals_by_code
        self.calls = 0
        self.fail_with: Exception | None = None

    def extend(self, external_code: str, finals: dict[str, float]) -> None:
        self.finals_by_code[external_code].update(finals)

    def mutate(self, external_code: str, day: str, value: float) -> None:
        self.finals_by_code[external_code][day] = value

    def get_prices_for_external_contract(
        self, records, frequency, start_date=None, end_date=None
    ):
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with

        records = tuple(records)
        external_code = records[0].external_contract_code
        finals = self.finals_by_code.get(external_code, {})
        result = {}
        for record in records:
            selected = {
                day: value
                for day, value in finals.items()
                if (start_date is None or pd.Timestamp(day).date() >= start_date)
                and (end_date is None or pd.Timestamp(day).date() <= end_date)
            }
            result[record] = _prices(selected)
        return result
