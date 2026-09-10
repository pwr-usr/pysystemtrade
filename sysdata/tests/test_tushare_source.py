from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from syscore.dateutils import Frequency
from sysdata.tushare.client import TushareClient
from sysdata.tushare.errors import (
    TushareConfigError,
    TushareDataError,
    TushareTransientError,
    TushareTruncationError,
)
from sysdata.tushare.source import (
    HistoricalFuturesContract,
    TushareFuturesPriceSource,
    tushareConnection,
    tushareFuturesContractData,
    tushareFuturesContractPriceData,
    tushareFxPricesData,
)
from sysdata.tushare.manifest import (
    MANIFEST_COLUMNS,
    TushareInstrumentManifest,
)
from sysdata.tushare.transforms import (
    cnhusd_prices_from_tushare_fx_daily,
    futures_contract_prices_from_tushare_daily,
)


def test_client_requires_token_before_importing_optional_sdk(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        "sysdata.config.private_config.get_private_config_as_dict",
        lambda *args, **kwargs: {},
    )

    with pytest.raises(TushareConfigError, match="TUSHARE_TOKEN"):
        TushareClient()


def test_client_dispatches_with_private_config_token(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        "sysdata.config.private_config.get_private_config_as_dict",
        lambda *args, **kwargs: {"tushare_token": "private-config-token"},
    )
    dispatched_tokens = []
    sdk = SimpleNamespace(
        pro_api=lambda token: dispatched_tokens.append(token) or object()
    )
    monkeypatch.setattr("importlib.import_module", lambda module_name: sdk)

    client = TushareClient()

    assert repr(client) == "TushareClient(read-only)"
    assert dispatched_tokens == ["private-config-token"]


def test_client_does_not_accept_credentials_as_a_constructor_argument():
    with pytest.raises(TypeError, match="token"):
        TushareClient(token="must-use-environment", api=object())


@pytest.mark.parametrize("endpoint", ["fut_daily", "fx_daily"])
def test_client_daily_endpoints_format_dates_and_reject_mixed_date_modes(endpoint):
    calls = []
    api = SimpleNamespace(
        **{endpoint: lambda **kwargs: calls.append(kwargs) or pd.DataFrame()}
    )
    client = TushareClient(api=api)
    query = getattr(client, endpoint)
    query(ts_code="TEST", start_date="2024-01-02", end_date=date(2024, 1, 3))
    assert calls[0]["start_date"] == "20240102"
    assert calls[0]["end_date"] == "20240103"
    with pytest.raises(TushareConfigError, match="Choose trade_date"):
        query(ts_code="TEST", trade_date="20240102", start_date="2024-01-01")
    assert len(calls) == 1


def test_client_retries_only_transient_failures_and_redacts_source_message():
    secret = "a-token-that-must-not-appear"

    class TransientApi:
        def __init__(self):
            self.calls = 0

        def fut_basic(self, **kwargs):
            self.calls += 1
            raise ConnectionError("network failed with %s" % secret)

    api = TransientApi()
    sleeps = []
    client = TushareClient(
        api=api,
        requests_per_minute=180,
        max_attempts=3,
        base_retry_delay_seconds=0,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 0.0,
    )

    with pytest.raises(TushareTransientError) as error:
        client.fut_basic("DCE")

    assert api.calls == 3
    assert secret not in str(error.value)


def test_client_retries_transient_server_errors():
    class ServerErrorApi:
        def __init__(self):
            self.calls = 0

        def fut_basic(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise Exception("HTTP 500 internal server error")
            return _catalogue_by_exchange()["DCE"]

    api = ServerErrorApi()
    client = TushareClient(
        api=api,
        max_attempts=3,
        base_retry_delay_seconds=0,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
    )

    result = client.fut_basic("DCE")

    assert api.calls == 3
    assert not result.empty


@pytest.mark.parametrize("status_code", [408, 429, 500, 503, 599])
def test_client_retries_structured_transient_http_status(status_code):
    class StructuredHttpError(Exception):
        def __init__(self):
            self.response = SimpleNamespace(status_code=status_code)

    class ServerErrorApi:
        def __init__(self):
            self.calls = 0

        def fut_basic(self, **kwargs):
            self.calls += 1
            if self.calls < 2:
                raise StructuredHttpError()
            return _catalogue_by_exchange()["DCE"]

    api = ServerErrorApi()
    client = TushareClient(
        api=api,
        max_attempts=2,
        base_retry_delay_seconds=0,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
    )

    assert not client.fut_basic("DCE").empty
    assert api.calls == 2


@pytest.mark.parametrize(
    "status_code,error_type",
    [(400, TushareDataError), (401, TushareConfigError)],
)
def test_client_does_not_retry_structured_non_transient_http_status(
    status_code, error_type
):
    class StructuredHttpError(Exception):
        def __init__(self):
            self.response = SimpleNamespace(status_code=status_code)

    class RejectedApi:
        def __init__(self):
            self.calls = 0

        def fut_basic(self, **kwargs):
            self.calls += 1
            raise StructuredHttpError()

    api = RejectedApi()
    client = TushareClient(
        api=api,
        max_attempts=3,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
    )

    with pytest.raises(error_type):
        client.fut_basic("DCE")
    assert api.calls == 1


@pytest.mark.parametrize(
    "message,error_type",
    [
        ("抱歉，您没有权限访问该接口", TushareConfigError),
        ("bad parameter", TushareDataError),
    ],
)
def test_client_does_not_retry_permission_or_parameter_errors(message, error_type):
    class RejectedApi:
        def __init__(self):
            self.calls = 0

        def fut_basic(self, **kwargs):
            self.calls += 1
            raise Exception(message)

    api = RejectedApi()
    client = TushareClient(
        api=api,
        max_attempts=3,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
    )

    with pytest.raises(error_type):
        client.fut_basic("DCE")

    assert api.calls == 1


def test_manifest_rejects_overlapping_product_windows():
    manifest_frame = _manifest_frame(
        [
            _manifest_row(
                "DCE_FB_OLD", "DCE", "FB", valid_to="20191202", min_tick="0.05"
            ),
            _manifest_row(
                "DCE_FB",
                "DCE",
                "FB",
                valid_from="20191202",
                min_tick="0.5",
                predecessor="DCE_FB_OLD",
            ),
        ]
    )

    with pytest.raises(TushareConfigError, match="Overlapping"):
        TushareInstrumentManifest.from_dataframe(manifest_frame)


def test_catalogue_uses_d_month_and_splits_fb_by_mapping_validity():
    fake_client = _FakeSourceClient(_catalogue_by_exchange())
    source = TushareFuturesPriceSource(client=fake_client, manifest=_small_manifest())

    assert source.supported_frequencies == [Frequency.Day]

    contracts = source.fetch_contract_catalogue()
    old_fb = next(
        contract for contract in contracts if contract.instrument_code == "DCE_FB_OLD"
    )
    new_fb = next(
        contract for contract in contracts if contract.instrument_code == "DCE_FB"
    )
    zhengzhou_cotton = next(
        contract for contract in contracts if contract.instrument_code == "CZCE_CF"
    )
    monthly_average = next(
        contract for contract in contracts if contract.instrument_code == "DCE_L_F"
    )

    assert old_fb.external_contract_code == new_fb.external_contract_code
    assert old_fb.price_end_date == date(2019, 11, 29)
    assert new_fb.price_start_date == date(2019, 12, 2)
    assert old_fb.expiry_date == new_fb.expiry_date == date(2020, 1, 15)
    assert zhengzhou_cotton.contract_date == "20000100"
    assert monthly_average.external_contract_code == "L2602F.DCE"
    assert [call[1] for call in fake_client.basic_calls] == ["1"] * 6


def test_catalogue_rejects_unknown_family_before_returning_partial_results():
    catalogues = _catalogue_by_exchange()
    catalogues["DCE"] = pd.concat(
        [
            catalogues["DCE"],
            _catalogue_frame(
                [
                    (
                        "NEW2601.DCE",
                        "NEW2601",
                        "DCE",
                        "NEW",
                        "20250101",
                        "20260115",
                        "202601",
                    )
                ]
            ),
        ],
        ignore_index=True,
    )
    fake_client = _FakeSourceClient(catalogues)
    source = TushareFuturesPriceSource(client=fake_client, manifest=_small_manifest())

    with pytest.raises(TushareConfigError, match="DCE/NEW"):
        source.fetch_contract_catalogue()

    partial_result = source.fetch_contract_catalogue_result()
    assert partial_result.unmapped_families == (("DCE", "NEW"),)
    assert not partial_result.is_complete
    assert all(contract.product_code != "NEW" for contract in partial_result.contracts)
    assert len(partial_result.contracts) == 8


@pytest.mark.parametrize(
    "column,value",
    [
        ("ts_code", "IF2406.BAD"),
        ("ts_code", "IF.CFX"),
        ("ts_code", "IF8888.CFX"),
        ("fut_code", "IC"),
        ("symbol", "IC2406"),
        ("d_month", "202407"),
        ("d_month", "IFL1"),
        ("list_date", "2024-01-01"),
    ],
)
def test_catalogue_fails_early_on_identity_or_schema_drift(column, value):
    catalogues = _catalogue_by_exchange()
    catalogues["CFFEX"] = catalogues["CFFEX"].copy()
    catalogues["CFFEX"].loc[0, column] = value
    source = TushareFuturesPriceSource(
        client=_FakeSourceClient(catalogues), manifest=_small_manifest()
    )

    with pytest.raises(TushareDataError):
        source.fetch_contract_catalogue()


def test_daily_transform_uses_close_at_2300_and_deduplicates():
    daily = _daily_frame(
        [
            ("IF2406.CFX", "20240103", 30, 32, 29, 31, 31.5, 10),
            ("IF2406.CFX", "20240102", 20, 22, 19, 21, np.nan, 9),
            ("IF2406.CFX", "20240103", 30, 33, 28, 99, 32.0, 11),
        ]
    )

    prices = futures_contract_prices_from_tushare_daily(
        daily, expected_ts_code="IF2406.CFX"
    )

    assert list(prices.columns) == ["OPEN", "HIGH", "LOW", "FINAL", "VOLUME"]
    assert list(prices.index) == [
        pd.Timestamp("2024-01-02 23:00:00"),
        pd.Timestamp("2024-01-03 23:00:00"),
    ]
    assert prices.loc[pd.Timestamp("2024-01-02 23:00"), "FINAL"] == 21
    assert prices.loc[pd.Timestamp("2024-01-03 23:00"), "FINAL"] == 99
    assert prices.loc[pd.Timestamp("2024-01-03 23:00"), "VOLUME"] == 11


def test_daily_transform_never_falls_back_to_settlement():
    daily = _daily_frame([("IF2406.CFX", "20240103", 30, 32, 29, np.nan, 31.5, 10)])

    prices = futures_contract_prices_from_tushare_daily(
        daily, expected_ts_code="IF2406.CFX"
    )

    assert np.isnan(prices.iloc[0]["FINAL"])


def test_daily_transform_preserves_sparse_ohlc():
    daily = _daily_frame(
        [("IF2406.CFX", "20240103", np.nan, np.nan, np.nan, 31, 31.5, 10)]
    )

    prices = futures_contract_prices_from_tushare_daily(
        daily, expected_ts_code="IF2406.CFX"
    )

    assert prices[["OPEN", "HIGH", "LOW"]].isna().all().all()
    assert prices.iloc[0]["FINAL"] == 31


@pytest.mark.parametrize("field", ["amount", "oi", "oi_chg", "change1", "change2"])
def test_daily_transform_validates_auxiliary_fields_when_present(field):
    daily = _daily_frame([("IF2406.CFX", "20240103", 30, 32, 29, 31, 31.5, 10)])
    daily[field] = daily[field].astype(object)
    daily.loc[0, field] = "not-a-number"

    with pytest.raises(TushareDataError, match=field):
        futures_contract_prices_from_tushare_daily(daily, expected_ts_code="IF2406.CFX")


def test_daily_transform_requires_close_but_tolerates_missing_auxiliaries():
    daily = _daily_frame([("IF2406.CFX", "20240103", 30, 32, 29, 31, 31.5, 10)])

    prices = futures_contract_prices_from_tushare_daily(
        daily.drop(columns=["oi_chg", "amount"]),
        expected_ts_code="IF2406.CFX",
    )
    assert prices.iloc[0]["FINAL"] == 31

    with pytest.raises(TushareDataError, match="close"):
        futures_contract_prices_from_tushare_daily(
            daily.drop(columns="close"),
            expected_ts_code="IF2406.CFX",
        )


def test_daily_transform_validates_identity():
    daily = _daily_frame([("IF2406.CFX", "20240103", 30, 32, 29, 31, 31.5, 10)])
    daily.loc[0, "ts_code"] = "IF2407.CFX"

    with pytest.raises(TushareDataError):
        futures_contract_prices_from_tushare_daily(daily, expected_ts_code="IF2406.CFX")


def test_daily_transform_treats_exact_provider_limit_as_possible_truncation():
    row = ("IF2406.CFX", "20240103", 30, 32, 29, 31, 31.5, 10)
    daily = pd.concat([_daily_frame([row])] * 2000, ignore_index=True)

    with pytest.raises(TushareTruncationError):
        futures_contract_prices_from_tushare_daily(daily, expected_ts_code="IF2406.CFX")


def test_source_intersects_requested_dates_with_mapping_window():
    fake_client = _FakeSourceClient(
        _catalogue_by_exchange(),
        daily_frame=_daily_frame(
            [
                ("FB2001.DCE", "20191129", 100, 101, 99, 100, 100, 5),
                ("FB2001.DCE", "20191202", 110, 111, 109, 110, 110, 6),
            ]
        ),
    )
    source = TushareFuturesPriceSource(client=fake_client, manifest=_small_manifest())
    old_fb = next(
        contract
        for contract in source.fetch_contract_catalogue()
        if contract.instrument_code == "DCE_FB_OLD"
    )

    prices = source.get_prices_at_frequency_for_contract(old_fb)

    assert list(prices.index) == [pd.Timestamp("2019-11-29 23:00:00")]
    assert fake_client.daily_calls[-1]["end_date"] == date(2019, 11, 29)
    with pytest.raises(TushareConfigError):
        source.get_prices_at_frequency_for_contract(old_fb, frequency=Frequency.Hour)


def test_native_reads_keep_both_fibreboard_specification_windows_separate():
    fake_client = _FakeSourceClient(
        _catalogue_by_exchange(),
        daily_frame=_daily_frame(
            [
                ("FB2001.DCE", "20191129", 100, 101, 99, 100, 100, 5),
                ("FB2001.DCE", "20191202", 110, 111, 109, 110, 110, 6),
            ]
        ),
    )
    source = TushareFuturesPriceSource(client=fake_client, manifest=_small_manifest())
    fibreboard = [
        contract
        for contract in source.fetch_contract_catalogue()
        if contract.external_contract_code == "FB2001.DCE"
    ]

    prices = tushareFuturesContractPriceData(connection=source)
    prices_by_contract = {
        contract: prices.get_prices_at_frequency_for_contract_object(
            contract.as_futures_contract()
        )
        for contract in fibreboard
    }

    assert len(fake_client.daily_calls) == 2
    assert {
        (call["ts_code"], call["start_date"], call["end_date"])
        for call in fake_client.daily_calls
    } == {
        ("FB2001.DCE", date(2019, 1, 2), date(2019, 11, 29)),
        ("FB2001.DCE", date(2019, 12, 2), date(2020, 1, 15)),
    }
    old_contract = next(
        contract for contract in fibreboard if contract.instrument_code == "DCE_FB_OLD"
    )
    new_contract = next(
        contract for contract in fibreboard if contract.instrument_code == "DCE_FB"
    )
    assert list(prices_by_contract[old_contract].index) == [
        pd.Timestamp("2019-11-29 23:00:00")
    ]
    assert list(prices_by_contract[new_contract].index) == [
        pd.Timestamp("2019-12-02 23:00:00")
    ]


def test_native_price_read_rejects_foreign_provider_identifier():
    connection = tushareConnection(
        client=_FakeSourceClient(
            _catalogue_by_exchange(),
            daily_frame=_daily_frame(
                [("IF2406.CFX", "20191202", 100, 101, 99, 100, 100, 5)]
            ),
        ),
        manifest=_small_manifest(),
    )
    source = tushareFuturesContractPriceData(connection=connection)
    contract = next(
        item
        for item in connection.get_contracts("DCE_FB")
        if item.date_str == "20200100"
    )

    with pytest.raises(TushareDataError, match="unexpected identifier"):
        source.get_prices_at_frequency_for_contract_object(contract)


def test_native_empty_or_reversed_request_does_not_call_daily_api():
    connection = tushareConnection(
        client=_FakeSourceClient(_catalogue_by_exchange()), manifest=_small_manifest()
    )
    source = tushareFuturesContractPriceData(connection=connection)
    contract = connection.get_contracts("DCE_FB_OLD")[0]
    prices = source.get_prices_at_frequency_for_contract_object(
        contract, start_date="2019-12-02", end_date="2020-01-15"
    )
    assert prices.empty
    with pytest.raises(TushareConfigError, match="after end_date"):
        source.get_prices_at_frequency_for_contract_object(
            contract, start_date="2020-01-15", end_date="2019-12-02"
        )
    assert connection.client.daily_calls == []


def test_cnhusd_helper_segments_by_year_and_inverts_midpoint():
    fake_client = _FakeSourceClient(
        _catalogue_by_exchange(), fx_factory=_fx_frame_for_requested_range
    )
    source = TushareFuturesPriceSource(client=fake_client, manifest=_small_manifest())

    prices = source.get_cnhusd_prices(
        start_date=date(2012, 12, 31), end_date=date(2014, 1, 1)
    )

    assert [
        (call["start_date"], call["end_date"]) for call in fake_client.fx_calls
    ] == [
        (date(2012, 12, 31), date(2012, 12, 31)),
        (date(2013, 1, 1), date(2013, 12, 31)),
        (date(2014, 1, 1), date(2014, 1, 1)),
    ]
    assert prices.index.is_monotonic_increasing
    assert prices.iloc[0] == pytest.approx(1.0 / 7.0)


def test_fx_transform_rejects_crossed_market():
    crossed = pd.DataFrame(
        {
            "ts_code": ["USDCNH.FXCM"],
            "trade_date": ["20240102"],
            "bid_close": [7.2],
            "ask_close": [7.1],
        }
    )

    with pytest.raises(TushareDataError, match="ask below"):
        cnhusd_prices_from_tushare_fx_daily([crossed])


@pytest.mark.parametrize(
    ("bid_close", "ask_close"),
    [(0.0, 7.1), (-0.1, 7.1), (7.1, 0.0), (7.1, -0.1)],
)
def test_fx_transform_rejects_nonpositive_closing_quote(bid_close, ask_close):
    invalid = pd.DataFrame(
        {
            "ts_code": ["USDCNH.FXCM"],
            "trade_date": ["20240102"],
            "bid_close": [bid_close],
            "ask_close": [ask_close],
        }
    )

    with pytest.raises(TushareDataError, match="non-positive closing bid or ask"):
        cnhusd_prices_from_tushare_fx_daily([invalid])


def test_normalized_contract_attaches_exact_expiry():
    record = HistoricalFuturesContract(
        instrument_code="CFFEX_IF",
        contract_date="20240600",
        external_contract_code="IF2406.CFX",
        exchange="CFFEX",
        product_code="IF",
        first_trade_date=date(2023, 7, 24),
        expiry_date=date(2024, 6, 21),
    )

    contract = record.as_futures_contract()

    assert contract.instrument_code == "CFFEX_IF"
    assert contract.date_str == "20240600"
    assert contract.expiry_date.date() == date(2024, 6, 21)


def test_native_adapters_share_a_lazy_blob_connection_without_a_broker():
    from syscore.constants import arg_not_supplied
    from sysdata.data_blob import dataBlob
    from sysdata.futures.contracts import futuresContractData
    from sysdata.futures.futures_per_contract_prices import futuresContractPriceData
    from sysdata.fx.spotfx import fxPricesData

    client = _FakeSourceClient(_catalogue_by_exchange())
    connection = tushareConnection(client=client, manifest=_small_manifest())
    data = dataBlob(
        class_list=[
            tushareFuturesContractData,
            tushareFuturesContractPriceData,
            tushareFxPricesData,
        ],
        tushare_connection=connection,
    )
    assert not client.basic_calls
    assert isinstance(data.tushare_futures_contract, futuresContractData)
    assert isinstance(data.tushare_futures_contract_price, futuresContractPriceData)
    assert isinstance(data.tushare_fx_prices, fxPricesData)
    contracts = data.tushare_futures_contract
    old = contracts.get_contract_object("DCE_FB_OLD", "202001")
    assert old.expiry_date.date() == date(2020, 1, 15)
    assert "20200100" in contracts.get_list_of_contract_dates_for_instrument_code(
        "DCE_FB_OLD", allow_expired=True
    )
    assert data.tushare_futures_contract_price.connection is connection
    assert (
        len(data.tushare_futures_contract_price.get_contracts_with_merged_price_data())
        == 8
    )
    assert len(client.basic_calls) == 6
    assert data._ib_conn is arg_not_supplied


def test_native_price_read_applies_specification_window_and_iso_dates():
    from sysobjects.contracts import futuresContract

    client = _FakeSourceClient(
        _catalogue_by_exchange(),
        daily_frame=_daily_frame(
            [
                ("FB2001.DCE", "20191129", 100, 101, 99, 100, 100, 5),
                ("FB2001.DCE", "20191202", 110, 111, 109, 110, 110, 6),
            ]
        ),
    )
    connection = tushareConnection(client=client, manifest=_small_manifest())
    source = tushareFuturesContractPriceData(connection=connection)
    prices = source.get_prices_at_frequency_for_contract_object(
        futuresContract("DCE_FB", "202001"),
        start_date="2019-11-01",
        end_date="2019-12-10",
    )
    assert list(prices.index) == [pd.Timestamp("2019-12-02 23:00")]
    assert client.daily_calls[-1]["start_date"] == date(2019, 12, 2)
    with pytest.raises(TushareConfigError):
        source.get_prices_at_frequency_for_contract_object(
            futuresContract("DCE_FB", "202001"), Frequency.Hour
        )


def test_native_missing_contract_and_read_only_writes():
    from syscore.exceptions import missingData
    from sysobjects.contracts import futuresContract
    from sysobjects.futures_per_contract_prices import futuresContractPrices

    connection = tushareConnection(
        client=_FakeSourceClient(_catalogue_by_exchange()), manifest=_small_manifest()
    )
    prices = tushareFuturesContractPriceData(connection=connection)
    unknown = futuresContract("SHFE_RB", "209901")
    assert prices.get_prices_at_frequency_for_contract_object(unknown).empty
    with pytest.raises(missingData):
        prices.get_prices_at_frequency_for_contract_object(unknown, return_empty=False)
    with pytest.raises(NotImplementedError):
        prices.write_merged_prices_for_contract_object(
            unknown, futuresContractPrices.create_empty()
        )
    with pytest.raises(NotImplementedError):
        tushareFuturesContractData(connection=connection).add_contract_data(unknown)


def test_native_fx_reads_only_the_requested_incremental_window():
    client = _FakeSourceClient(
        _catalogue_by_exchange(), fx_factory=_fx_frame_for_requested_range
    )
    source = tushareFxPricesData(
        connection=tushareConnection(client=client, manifest=_small_manifest())
    )
    prices = source.get_fx_prices(
        "CNHUSD", start_date="2023-12-29", end_date="2024-01-03"
    )
    assert [(call["start_date"], call["end_date"]) for call in client.fx_calls] == [
        (date(2023, 12, 29), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 1, 3)),
    ]
    assert list(prices.index) == [
        pd.Timestamp("2023-12-29 23:00"),
        pd.Timestamp("2024-01-01 23:00"),
    ]
    assert source.get_fx_prices("EURUSD", start_date="2024-01-01").empty
    assert len(client.fx_calls) == 2


def test_catalogue_refresh_invalidates_native_record_lookup():
    from sysobjects.contracts import futuresContract

    client = _FakeSourceClient(_catalogue_by_exchange())
    connection = tushareConnection(client=client, manifest=_small_manifest())
    contract = futuresContract("CFFEX_IF", "202406")
    first = connection.get_record(contract)
    client.catalogues["CFFEX"].loc[0, "delist_date"] = "20240628"
    assert connection.get_record(contract) == first
    connection.fetch_contract_catalogue_result(refresh=True)
    assert connection.get_record(contract).expiry_date == date(2024, 6, 28)
    assert len(client.basic_calls) == 12


def test_daily_transform_rejects_infinite_price():
    frame = _daily_frame([("IF2406.CFX", "20240103", 30, 32, 29, np.inf, 31.5, 10)])
    with pytest.raises(TushareDataError, match="close"):
        futures_contract_prices_from_tushare_daily(frame, "IF2406.CFX")


class _FakeSourceClient:
    def __init__(self, catalogues, daily_frame=None, fx_factory=None):
        self.catalogues = catalogues
        self.daily_frame = _daily_frame([]) if daily_frame is None else daily_frame
        self.fx_factory = fx_factory
        self.basic_calls = []
        self.daily_calls = []
        self.fx_calls = []

    def fut_basic(self, exchange, fut_type):
        self.basic_calls.append((exchange, fut_type))
        return self.catalogues[exchange].copy()

    def fut_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        return self.daily_frame.copy()

    def fx_daily(self, **kwargs):
        self.fx_calls.append(kwargs)
        if self.fx_factory is None:
            return pd.DataFrame(
                columns=["ts_code", "trade_date", "bid_close", "ask_close"]
            )
        return self.fx_factory(**kwargs)


def _small_manifest():
    rows = [
        _manifest_row("CFFEX_IF", "CFFEX", "IF", min_tick="0.2"),
        _manifest_row("CZCE_CF", "CZCE", "CF", min_tick="5"),
        _manifest_row(
            "DCE_FB_OLD",
            "DCE",
            "FB",
            valid_to="20191129",
            min_tick="0.05",
        ),
        _manifest_row(
            "DCE_FB",
            "DCE",
            "FB",
            valid_from="20191202",
            min_tick="0.5",
            predecessor="DCE_FB_OLD",
        ),
        _manifest_row(
            "DCE_L_F",
            "DCE",
            "L_F",
            min_tick="1",
            stitch_mode="catalog_only",
        ),
        _manifest_row("GFEX_SI", "GFEX", "SI", min_tick="5"),
        _manifest_row(
            "INE_SCTAS",
            "INE",
            "SCTAS",
            min_tick="0.1",
            stitch_mode="catalog_only",
        ),
        _manifest_row("SHFE_AU", "SHFE", "AU", min_tick="0.02"),
    ]
    return TushareInstrumentManifest.from_dataframe(_manifest_frame(rows))


def _manifest_row(
    instrument,
    exchange,
    fut_code,
    *,
    valid_from="",
    valid_to="",
    min_tick="1",
    predecessor="",
    stitch_mode="stitch",
):
    return dict(
        Instrument=instrument,
        Exchange=exchange,
        FutCode=fut_code,
        ValidFrom=valid_from,
        ValidTo=valid_to,
        MinTick=min_tick,
        Predecessor=predecessor,
        StitchMode=stitch_mode,
    )


def _manifest_frame(rows):
    return pd.DataFrame(rows, columns=MANIFEST_COLUMNS)


def _catalogue_by_exchange():
    return {
        "CFFEX": _catalogue_frame(
            [
                (
                    "IF2406.CFX",
                    "IF2406",
                    "CFFEX",
                    "IF",
                    "20230724",
                    "20240621",
                    "202406",
                )
            ]
        ),
        "DCE": _catalogue_frame(
            [
                (
                    "FB2001.DCE",
                    "FB2001",
                    "DCE",
                    "FB",
                    "20190102",
                    "20200115",
                    "202001",
                ),
                (
                    "L2602F.DCE",
                    "L2602F",
                    "DCE",
                    "L_F",
                    "20250201",
                    "20260213",
                    "202602",
                ),
            ]
        ),
        "CZCE": _catalogue_frame(
            [
                (
                    "CF0001.ZCE",
                    "CF001",
                    "CZCE",
                    "CF",
                    "19990105",
                    "19991215",
                    "200001",
                )
            ]
        ),
        "SHFE": _catalogue_frame(
            [
                (
                    "AU2406.SHF",
                    "AU2406",
                    "SHFE",
                    "AU",
                    "20230616",
                    "20240617",
                    "202406",
                )
            ]
        ),
        "INE": _catalogue_frame(
            [
                (
                    "SCTAS2406.INE",
                    "SC2406TAS",
                    "INE",
                    "SCTAS",
                    "20240301",
                    "20240520",
                    "202406",
                )
            ]
        ),
        "GFEX": _catalogue_frame(
            [
                (
                    "SI2406.GFE",
                    "SI2406",
                    "GFEX",
                    "SI",
                    "20230619",
                    "20240617",
                    "202406",
                )
            ]
        ),
    }


def _catalogue_frame(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "ts_code",
            "symbol",
            "exchange",
            "fut_code",
            "list_date",
            "delist_date",
            "d_month",
        ],
    )


def _daily_frame(rows):
    frame = pd.DataFrame(
        rows,
        columns=[
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "settle",
            "vol",
        ],
    )
    frame["amount"] = pd.Series([1000] * len(frame), dtype=float)
    frame["oi"] = pd.Series([100] * len(frame), dtype=float)
    frame["oi_chg"] = pd.Series([1] * len(frame), dtype=float)
    frame["change1"] = pd.Series([1] * len(frame), dtype=float)
    frame["change2"] = pd.Series([1] * len(frame), dtype=float)
    return frame


def _fx_frame_for_requested_range(**kwargs):
    trade_date = kwargs["start_date"].strftime("%Y%m%d")
    return pd.DataFrame(
        {
            "ts_code": [kwargs["ts_code"]],
            "trade_date": [trade_date],
            "bid_close": [6.0],
            "ask_close": [8.0],
        }
    )
