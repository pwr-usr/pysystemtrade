"""Unified updater behaviour using native temporary Parquet and an in-memory catalogue."""

import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from syscore.dateutils import DAILY_PRICE_FREQ
from sysdata.data_blob import dataBlob
from sysdata.tushare.errors import TushareTransientError
from sysdata.tushare.source import HistoricalFuturesContract
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysproduction.data.prices import diagPrices
import sysproduction.update_tushare_futures as workflow


@pytest.fixture(autouse=True)
def no_mongo_connections(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Workflow tests must use in-memory contract metadata")

    monkeypatch.setattr(dataBlob, "_add_mongo_class", fail)


def prices(values):
    frame = pd.DataFrame(
        {name: list(values.values()) for name in ["OPEN", "HIGH", "LOW", "FINAL"]},
        index=pd.to_datetime(list(values)) + pd.Timedelta(hours=23),
    )
    frame["VOLUME"] = 1000.0
    return futuresContractPrices(frame)


def record(month="20240800", expiry="2024-08-15"):
    return HistoricalFuturesContract(
        "SHFE_CU",
        month,
        "CU" + month[2:6] + ".SHF",
        "SHFE",
        "CU",
        datetime.date(2023, 8, 1),
        pd.Timestamp(expiry).date(),
    )


class Source:
    def __init__(self):
        self.values = {"2024-05-29": 100.0, "2024-05-30": 102.0}
        self.calls = []
        self.error = None
        self.client = self

    def fetch_contract_catalogue_result(self, refresh=False):
        assert refresh
        return SimpleNamespace(contracts=self.records, unmapped_families=())

    def configured_instrument_codes(self):
        return ["SHFE_CU"]

    def get_record(self, contract):
        return next(r for r in self.records if r.contract_date == contract.date_str)

    def fut_daily(self, ts_code, start_date, end_date):
        contract = next(r for r in self.records if r.external_contract_code == ts_code)
        self.calls.append((contract.contract_date, start_date, end_date))
        if self.error:
            raise self.error
        frame = prices(
            {
                k: v
                for k, v in self.values.items()
                if start_date <= pd.Timestamp(k).date() <= end_date
            }
        )
        frame = pd.DataFrame(frame).rename(
            columns={
                "OPEN": "open",
                "HIGH": "high",
                "LOW": "low",
                "FINAL": "close",
                "VOLUME": "vol",
            }
        )
        frame["trade_date"] = frame.index.strftime("%Y%m%d")
        frame["ts_code"] = ts_code
        return frame.reset_index(drop=True)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = Source()
    data = dataBlob(
        log_name="test_tushare",
        parquet_store_path=str(tmp_path / "parquet"),
        tushare_connection=source,
    )
    contracts = {}
    metadata = SimpleNamespace(
        is_contract_in_data=lambda r: r.key in contracts,
        get_contract_from_db=lambda r: contracts[r.key],
        add_contract_data=lambda r, **kwargs: contracts.update({r.key: r}),
    )
    data.db_futures_contract = metadata
    monkeypatch.setattr(workflow, "dataContracts", lambda data: metadata)
    yield data, source, contracts
    data.close()


def run(setup, records=None, **kwargs):
    data, source, _ = setup
    source.records = records or [record()]
    return workflow.update_tushare_futures(
        data=data,
        end_date=kwargs.pop("end_date", "2024-06-03"),
        update_fx=False,
        rebuild=False,
        **kwargs,
    )


def stored(setup, r=None):
    return diagPrices(
        setup[0]
    ).db_futures_contract_price_data.get_prices_at_frequency_for_contract_object(
        (r or record()).as_futures_contract(), frequency=DAILY_PRICE_FREQ
    )


def test_empty_store_bootstrap_overlap_and_day_merged_equality(setup):
    first = run(setup)
    assert isinstance(
        setup[0].tushare_futures_contract, workflow.tushareFuturesContractData
    )
    assert isinstance(
        setup[0].tushare_futures_contract_price,
        workflow.tushareFuturesContractPriceData,
    )
    assert first.loc["SHFE_CU", "rows_added"] == 2
    setup[1].values["2024-06-04"] = 103.0
    second = run(setup, end_date="2024-06-04")
    assert second.loc["SHFE_CU", "rows_added"] == 1
    assert setup[1].calls[-1][1] == datetime.date(2024, 5, 23)
    store = diagPrices(setup[0]).db_futures_contract_price_data
    merged = store.get_merged_prices_for_contract_object(record().as_futures_contract())
    pd.testing.assert_frame_equal(pd.DataFrame(stored(setup)), pd.DataFrame(merged))
    assert run(setup, end_date="2024-06-04").loc["SHFE_CU", "rows_added"] == 0


def test_expired_gap_contract_is_fetched_and_only_verified_coverage_skips(setup):
    expired = record(expiry="2024-05-31")
    run(setup, records=[expired])
    assert setup[1].calls[-1][1] == expired.first_trade_date
    calls = len(setup[1].calls)
    run(setup, records=[expired], end_date="2024-06-10")
    assert len(setup[1].calls) == calls
    receipt = {
        "start": "2024-05-01",
        "end": "2024-05-31",
        "digest": workflow._price_digest(stored(setup, expired)),
    }
    first, last = workflow._request_window(
        expired, stored(setup, expired), receipt, None, datetime.date(2024, 6, 10)
    )
    assert (
        first == expired.first_trade_date
    )  # a late receipt cannot certify the early history
    assert last == expired.expiry_date


def test_expired_during_update_gap_refreshes_metadata_and_missing_merged(setup):
    expired = record(expiry="2024-05-31")
    run(setup, records=[expired], end_date="2024-05-30")
    assert setup[2][expired.as_futures_contract().key].currently_sampling
    run(setup, records=[expired], end_date="2024-06-10")
    assert not setup[2][expired.as_futures_contract().key].currently_sampling
    store = diagPrices(setup[0]).db_futures_contract_price_data
    store._delete_merged_prices_for_contract_object_with_no_checks_be_careful(
        expired.as_futures_contract()
    )
    run(setup, records=[expired], end_date="2024-06-11")
    assert (
        len(store.get_merged_prices_for_contract_object(expired.as_futures_contract()))
        == 2
    )


def test_dry_run_does_not_download_write_or_create_receipts(setup, tmp_path):
    del setup[0].db_futures_contract
    result = run(setup, dry_run=True)
    assert result.loc["SHFE_CU", "status"] == "planned"
    assert setup[1].calls == []
    assert setup[2] == {}
    assert not hasattr(setup[0], "db_futures_contract")
    assert not list(tmp_path.rglob("*.parquet"))
    assert not (tmp_path / "tushare_updates").exists()


def test_revision_is_rejected_then_dated_repair_backs_up_and_accepts(setup, tmp_path):
    run(setup)
    setup[1].values["2024-05-30"] = 104.0
    assert run(setup).loc["SHFE_CU", "status"] == "failed"
    assert stored(setup).FINAL.iloc[-1] == 102.0
    result = run(setup, start_date="2024-05-29")
    assert result.loc["SHFE_CU", "status"] == "updated"
    assert stored(setup).FINAL.iloc[-1] == 104.0
    assert list(tmp_path.rglob("*_day_before.parquet"))
    assert result.loc["SHFE_CU", "revisions"] == 4


def test_empty_repair_response_preserves_existing_history(setup):
    run(setup)
    before = stored(setup).copy()
    setup[1].values = {}
    assert run(setup, start_date="2024-05-29").loc["SHFE_CU", "status"] == "failed"
    pd.testing.assert_frame_equal(pd.DataFrame(stored(setup)), pd.DataFrame(before))


def test_changed_stored_early_history_invalidates_active_receipt(setup):
    run(setup)
    old = stored(setup)
    valid = dict(
        start="2023-08-01", end="2024-06-03", digest=workflow._price_digest(old)
    )
    old.loc[old.index[0], "FINAL"] = 99.0
    first, _ = workflow._request_window(
        record(), old, valid, None, datetime.date(2024, 6, 4)
    )
    assert first == record().first_trade_date


def test_merged_write_failure_rolls_back_day_and_logs_critical(setup, monkeypatch):
    run(setup)
    before = stored(setup).copy()
    setup[1].values["2024-06-04"] = 103.0
    real_updates = workflow.updatePrices(setup[0])
    original_write = real_updates.overwrite_merged_prices_for_contract
    attempts = []

    def fail_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary disk failure")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(real_updates, "overwrite_merged_prices_for_contract", fail_once)
    monkeypatch.setattr(workflow, "updatePrices", lambda data: real_updates)
    messages = []
    monkeypatch.setattr(setup[0].log, "critical", messages.append)
    with pytest.raises(RuntimeError, match="disk failure"):
        run(setup, end_date="2024-06-04")
    pd.testing.assert_frame_equal(pd.DataFrame(stored(setup)), pd.DataFrame(before))
    assert "fatal error" in messages[0]


def test_completed_history_does_not_create_redundant_backups(setup):
    expired = record(expiry="2024-05-31")
    run(setup, records=[expired])
    result = run(setup, records=[expired], end_date="2024-06-10")
    from pathlib import Path

    assert not list(Path(result.loc["SHFE_CU", "backup"]).glob("*.parquet"))


def test_failed_contract_has_explicit_reason_and_no_checkpoint(setup, tmp_path):
    setup[1].error = TushareTransientError("temporary source failure")
    result = run(setup)
    assert result.loc["SHFE_CU", "status"] == "failed"
    assert "temporary" in result.loc["SHFE_CU", "reason"]
    assert not (tmp_path / "tushare_updates" / "request_coverage.json").exists()


def test_invalid_cutoff_and_zero_prices_are_rejected(setup):
    with pytest.raises(ValueError, match="start_date"):
        run(setup, start_date="2024-06-04")
    with pytest.raises(ValueError, match="非正值"):
        workflow._validate_prices(
            prices({"2024-05-29": 0.0}),
            datetime.date(2024, 5, 1),
            datetime.date(2024, 6, 1),
        )


def test_history_comparison_and_secret_redaction(monkeypatch):
    old = prices({"2024-05-28": 100.0, "2024-05-29": 101.0})
    new = prices({"2024-05-29": 102.0, "2024-05-30": 103.0})
    changes = workflow._revision_rows(
        old,
        new,
        "SHFE_CU",
        "20240800",
        datetime.date(2024, 5, 28),
        datetime.date(2024, 5, 30),
    )
    assert {pd.Timestamp(row["date"]).date() for row in changes} == {
        datetime.date(2024, 5, 28),
        datetime.date(2024, 5, 29),
    }
    monkeypatch.setenv("TUSHARE_TOKEN", "super-secret-token")
    assert "super-secret-token" not in workflow.safe_tushare_error_text(
        "bad super-secret-token"
    )


def test_native_fx_adapter_overlap_and_revision_protection(tmp_path):
    from sysdata.tushare.source import tushareConnection

    class Client:
        quotes = {"2024-05-29": 7.2, "2024-05-30": 7.21}
        calls = []

        def fx_daily(self, ts_code, start_date, end_date):
            self.calls.append((start_date, end_date))
            return pd.DataFrame(
                [
                    dict(
                        ts_code=ts_code,
                        trade_date=day.replace("-", ""),
                        bid_close=value,
                        ask_close=value,
                    )
                    for day, value in self.quotes.items()
                    if start_date <= pd.Timestamp(day).date() <= end_date
                ]
            )

    client = Client()
    with dataBlob(
        parquet_store_path=str(tmp_path / "parquet"),
        tushare_connection=tushareConnection(client=client),
    ) as blob:
        assert (
            workflow.update_tushare_cnhusd(
                blob,
                as_of=datetime.date(2024, 6, 3),
                full_backfill_start=datetime.date(2024, 1, 1),
            )
            == 2
        )
        assert isinstance(blob.tushare_fx_prices, workflow.tushareFxPricesData)
        client.quotes["2024-06-04"] = 7.22
        assert (
            workflow.update_tushare_cnhusd(blob, as_of=datetime.date(2024, 6, 4)) == 1
        )
        assert client.calls[-1][0] == datetime.date(2024, 5, 23)
        before = workflow.dataCurrency(blob).get_fx_prices("CNHUSD").copy()
        client.quotes["2024-05-30"] = 7.5
        with pytest.raises(RuntimeError, match="changed stored CNHUSD"):
            workflow.update_tushare_cnhusd(blob, as_of=datetime.date(2024, 6, 4))
        pd.testing.assert_series_equal(
            before, pd.Series(workflow.dataCurrency(blob).get_fx_prices("CNHUSD"))
        )
