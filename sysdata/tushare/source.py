"""Read-only Tushare catalogue and daily price source for Chinese futures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from syscore.dateutils import Frequency
from sysdata.tushare.client import TushareClient
from sysdata.tushare.errors import (
    TushareConfigError,
    TushareDataError,
)
from sysdata.tushare.manifest import (
    DEFAULT_MANIFEST_PATH,
    SUPPORTED_EXCHANGES,
    TushareInstrumentManifest,
    TushareInstrumentMapping,
)
from sysdata.tushare.transforms import (
    cnhusd_prices_from_tushare_fx_daily,
    futures_contract_prices_from_tushare_daily,
)
from sysobjects.contract_dates_and_expiries import expiryDate
from sysobjects.contracts import futuresContract
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysobjects.spot_fx_prices import fxPrices

EXCHANGE_SUFFIX = {
    "CFFEX": "CFX",
    "DCE": "DCE",
    "CZCE": "ZCE",
    "SHFE": "SHF",
    "INE": "INE",
    "GFEX": "GFE",
}
CATALOGUE_REQUIRED_COLUMNS = frozenset(
    {
        "ts_code",
        "symbol",
        "exchange",
        "fut_code",
        "list_date",
        "delist_date",
        "d_month",
    }
)
CNHUSD_SOURCE_CODE = "USDCNH.FXCM"
CNHUSD_EARLIEST_DATE = date(2012, 2, 18)

_PRODUCT_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CONTRACT_MONTH_PATTERN = re.compile(r"^[0-9]{6}$")


@dataclass(frozen=True)
class HistoricalFuturesContract:
    """One internal contract record backed by one vendor contract.

    Most vendor contracts map to exactly one record.  A contract spanning a
    reviewed specification boundary (DCE fibreboard 2019) maps to one record
    per era, with ``price_start_date``/``price_end_date`` clipping each era's
    share of the shared vendor history.
    """

    instrument_code: str
    contract_date: str
    external_contract_code: str
    exchange: str
    product_code: str
    first_trade_date: date
    expiry_date: date
    price_start_date: date | None = None
    price_end_date: date | None = None

    def as_futures_contract(self) -> futuresContract:
        """Return the repository contract object with the exact expiry attached."""

        contract = futuresContract(self.instrument_code, self.contract_date)
        exact_expiry = expiryDate(
            self.expiry_date.year, self.expiry_date.month, self.expiry_date.day
        )
        contract.update_single_expiry_date(exact_expiry)

        return contract


@dataclass(frozen=True)
class TushareCatalogueResult:
    """Mapped records plus any newly discovered, deliberately unconfigured families."""

    contracts: tuple[HistoricalFuturesContract, ...]
    unmapped_families: tuple[tuple[str, str], ...]

    @property
    def is_complete(self) -> bool:
        return not self.unmapped_families


class TushareFuturesPriceSource:
    """Tushare concrete-contract catalogue and daily price source."""

    def __init__(
        self,
        client: TushareClient,
        manifest: TushareInstrumentManifest | str | Path | None = None,
    ):
        self._client = client
        if manifest is None:
            self._manifest = TushareInstrumentManifest.from_csv(DEFAULT_MANIFEST_PATH)
        elif isinstance(manifest, TushareInstrumentManifest):
            self._manifest = manifest
        else:
            self._manifest = TushareInstrumentManifest.from_csv(manifest)

    def __repr__(self) -> str:
        return "Tushare historical futures price source (daily, read-only)"

    @property
    def manifest(self) -> TushareInstrumentManifest:
        return self._manifest

    @property
    def supported_frequencies(self) -> list[Frequency]:
        return [Frequency.Day]

    def configured_instrument_codes(self) -> list[str]:
        return self.manifest.configured_instrument_codes()

    def fetch_contract_catalogue(self) -> list[HistoricalFuturesContract]:
        result = self.fetch_contract_catalogue_result()
        if result.unmapped_families:
            raise TushareConfigError(
                "Tushare discovered unmapped futures families: %s"
                % _format_product_families(result.unmapped_families)
            )

        return list(result.contracts)

    def fetch_contract_catalogue_result(self) -> TushareCatalogueResult:
        """
        Return known mapped records while reporting newly discovered families.

        Schema/identity failures and missing configured families remain fatal.
        This distinction lets a daily process update known instruments and still
        finish unsuccessfully when Tushare introduces a new family.
        """

        catalogue_frames = [
            self._client.fut_basic(exchange=exchange, fut_type="1")
            for exchange in SUPPORTED_EXCHANGES
        ]

        validated_rows: list[dict[str, Any]] = []
        for exchange, catalogue_frame in zip(SUPPORTED_EXCHANGES, catalogue_frames):
            validated_rows.extend(
                _validated_catalogue_rows(catalogue_frame, exchange=exchange)
            )

        _validate_catalogue_uniqueness(validated_rows)
        unmapped_families = _unmapped_manifest_products(
            validated_rows=validated_rows, manifest=self.manifest
        )
        _validate_configured_products_are_present(
            validated_rows=validated_rows, manifest=self.manifest
        )

        contracts: list[HistoricalFuturesContract] = []
        covered_mappings: set[TushareInstrumentMapping] = set()
        uncovered_contracts: list[str] = []
        for row in validated_rows:
            if (row["exchange"], row["fut_code"]) in unmapped_families:
                continue

            mappings = self.manifest.mappings_for_contract(
                exchange=row["exchange"],
                fut_code=row["fut_code"],
                first_trade_date=row["list_date"],
                expiry_date=row["delist_date"],
            )
            if not mappings:
                uncovered_contracts.append(row["ts_code"])
                continue

            for mapping in mappings:
                covered_mappings.add(mapping)
                contracts.append(
                    _historical_contract_from_catalogue_row(row=row, mapping=mapping)
                )

        if uncovered_contracts:
            raise TushareConfigError(
                "Tushare manifest validity windows do not cover contracts: %s"
                % ", ".join(sorted(uncovered_contracts))
            )

        mappings_without_contracts = set(self.manifest.mappings).difference(
            covered_mappings
        )
        if mappings_without_contracts:
            missing_instruments = sorted(
                mapping.instrument_code for mapping in mappings_without_contracts
            )
            raise TushareConfigError(
                "Tushare manifest rows have no matching contracts: %s"
                % ", ".join(missing_instruments)
            )

        sorted_contracts = sorted(
            contracts,
            key=lambda contract: (
                contract.instrument_code,
                contract.contract_date,
                contract.external_contract_code,
                contract.price_start_date or date.min,
            ),
        )

        return TushareCatalogueResult(
            contracts=tuple(sorted_contracts),
            unmapped_families=tuple(sorted(unmapped_families)),
        )

    def get_prices_at_frequency_for_contract(
        self,
        contract: HistoricalFuturesContract,
        frequency: Frequency = Frequency.Day,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> futuresContractPrices:
        return self.get_prices_for_external_contract(
            (contract,),
            frequency=frequency,
            start_date=start_date,
            end_date=end_date,
        )[contract]

    def get_prices_for_external_contract(
        self,
        contracts: Sequence[HistoricalFuturesContract],
        frequency: Frequency = Frequency.Day,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> dict[HistoricalFuturesContract, futuresContractPrices]:
        """
        Fetch one vendor contract and route it to its internal records.

        One provider request covers the union of the records' effective
        windows; the validated response is then sliced per record.
        """

        if frequency != Frequency.Day:
            raise TushareConfigError(
                "Tushare futures currently support only Frequency.Day"
            )

        contracts = tuple(contracts)
        if not contracts:
            raise TushareConfigError(
                "At least one contract is required for a Tushare price request"
            )
        if len(set(contracts)) != len(contracts):
            raise TushareConfigError(
                "A Tushare external-contract request contains duplicate records"
            )

        external_codes = {contract.external_contract_code for contract in contracts}
        if len(external_codes) != 1:
            raise TushareConfigError(
                "A Tushare external-contract request must contain one ts_code"
            )
        external_code = next(iter(external_codes))

        requested_start = _coerce_optional_date(start_date, "start_date")
        requested_end = _coerce_optional_date(end_date, "end_date")
        if (
            requested_start is not None
            and requested_end is not None
            and requested_start > requested_end
        ):
            raise TushareConfigError("Tushare price start_date is after end_date")

        effective_windows = {
            contract: (
                _latest_date(
                    contract.first_trade_date,
                    contract.price_start_date,
                    requested_start,
                ),
                _earliest_date(
                    contract.expiry_date,
                    contract.price_end_date,
                    requested_end,
                ),
            )
            for contract in contracts
        }
        populated_windows = [
            window for window in effective_windows.values() if window[0] <= window[1]
        ]

        if populated_windows:
            daily_frame = self._client.fut_daily(
                ts_code=external_code,
                start_date=min(window[0] for window in populated_windows),
                end_date=max(window[1] for window in populated_windows),
            )
        else:
            daily_frame = _empty_fut_daily_frame()

        prices_by_contract = {}
        for contract, (effective_start, effective_end) in effective_windows.items():
            frame_for_contract = (
                daily_frame
                if effective_start <= effective_end
                else _empty_fut_daily_frame()
            )
            prices_by_contract[contract] = futures_contract_prices_from_tushare_daily(
                daily_frame=frame_for_contract,
                expected_ts_code=external_code,
                price_start_date=effective_start,
                price_end_date=effective_end,
            )

        return prices_by_contract

    def get_cnhusd_prices(
        self,
        start_date: date | str = CNHUSD_EARLIEST_DATE,
        end_date: date | str | None = None,
    ) -> fxPrices:
        resolved_start = _coerce_required_date(start_date, "start_date")
        resolved_end = (
            date.today()
            if end_date is None
            else _coerce_required_date(end_date, "end_date")
        )
        if resolved_start > resolved_end:
            raise TushareConfigError("Tushare FX start_date is after end_date")

        yearly_frames = []
        for segment_start, segment_end in _year_segments(resolved_start, resolved_end):
            yearly_frames.append(
                self._client.fx_daily(
                    ts_code=CNHUSD_SOURCE_CODE,
                    start_date=segment_start,
                    end_date=segment_end,
                )
            )

        return cnhusd_prices_from_tushare_fx_daily(
            yearly_frames, expected_ts_code=CNHUSD_SOURCE_CODE
        )


def _validated_catalogue_rows(
    catalogue_frame: pd.DataFrame, exchange: str
) -> list[dict[str, Any]]:
    missing_columns = sorted(
        CATALOGUE_REQUIRED_COLUMNS.difference(catalogue_frame.columns)
    )
    if missing_columns:
        raise TushareDataError(
            "Tushare fut_basic response for %s is missing fields: %s"
            % (exchange, ", ".join(missing_columns))
        )
    if catalogue_frame.empty:
        raise TushareDataError(
            "Tushare fut_basic returned no contracts for %s" % exchange
        )

    expected_suffix = "." + EXCHANGE_SUFFIX[exchange]
    validated_rows: list[dict[str, Any]] = []
    for row_number, (_, row) in enumerate(catalogue_frame.iterrows(), start=1):
        returned_exchange = _required_catalogue_text(
            row, "exchange", exchange, row_number
        ).upper()
        if returned_exchange != exchange:
            raise TushareDataError(
                "Tushare fut_basic returned an unexpected exchange for %s" % exchange
            )

        ts_code = _required_catalogue_text(row, "ts_code", exchange, row_number)
        if not ts_code.endswith(expected_suffix):
            raise TushareDataError(
                "Tushare contract suffix does not match exchange %s" % exchange
            )

        fut_code = _required_catalogue_text(
            row, "fut_code", exchange, row_number
        ).upper()
        if not _PRODUCT_CODE_PATTERN.fullmatch(fut_code):
            raise TushareDataError(
                "Tushare fut_basic contains an invalid FutCode for %s" % exchange
            )

        d_month = _required_catalogue_text(row, "d_month", exchange, row_number)
        if not _CONTRACT_MONTH_PATTERN.fullmatch(d_month):
            raise TushareDataError(
                "Tushare fut_basic d_month must be YYYYMM for %s" % ts_code
            )
        month_number = int(d_month[-2:])
        if month_number < 1 or month_number > 12:
            raise TushareDataError(
                "Tushare fut_basic contains an invalid d_month for %s" % ts_code
            )

        external_code_without_suffix = ts_code[: -len(expected_suffix)]
        symbol = _required_catalogue_text(row, "symbol", exchange, row_number)
        if not _is_concrete_external_code(
            external_code_without_suffix, symbol, fut_code, d_month, exchange
        ):
            raise TushareDataError(
                "Tushare fut_basic returned an inconsistent or synthetic contract: "
                + ts_code
            )

        list_date = _parse_catalogue_date(row, "list_date", exchange, row_number)
        delist_date = _parse_catalogue_date(row, "delist_date", exchange, row_number)
        if list_date > delist_date:
            raise TushareDataError(
                "Tushare contract %s lists after it delists" % ts_code
            )

        validated_rows.append(
            dict(
                ts_code=ts_code,
                symbol=symbol,
                exchange=returned_exchange,
                fut_code=fut_code,
                list_date=list_date,
                delist_date=delist_date,
                d_month=d_month,
            )
        )

    return validated_rows


def _is_concrete_external_code(
    external_code: str,
    symbol: str,
    fut_code: str,
    d_month: str,
    exchange: str,
) -> bool:
    """Cross-check identity while retaining ``d_month`` as authoritative.

    Handles the CZCE three-digit convention, INE crude TAS contracts, and the
    DCE ``*_F`` monthly-average families.
    """

    if fut_code == "SCTAS":
        pattern = r"SCTAS([0-9]{3,4})"
    elif fut_code.endswith("_F"):
        product_code = re.escape(fut_code[:-2])
        pattern = rf"{product_code}([0-9]{{3,4}})F"
    else:
        pattern = rf"{re.escape(fut_code)}([0-9]{{4}})"

    match = re.fullmatch(pattern, external_code)
    if match is None:
        return False

    delivery_digits = match.group(1)
    if fut_code == "SCTAS":
        expected_symbol = f"SC{delivery_digits}TAS"
    elif exchange == "CZCE":
        expected_symbol = f"{fut_code}{d_month[-3:]}"
    else:
        expected_symbol = external_code
    return (
        symbol == expected_symbol
        and delivery_digits == d_month[-len(delivery_digits) :]
    )


def _validate_catalogue_uniqueness(
    validated_rows: list[dict[str, Any]],
) -> None:
    external_codes: set[str] = set()
    product_months: set[tuple[str, str, str]] = set()
    for row in validated_rows:
        if row["ts_code"] in external_codes:
            raise TushareDataError(
                "Tushare fut_basic contains duplicate ts_code values"
            )
        external_codes.add(row["ts_code"])

        product_month = (row["exchange"], row["fut_code"], row["d_month"])
        if product_month in product_months:
            raise TushareDataError(
                "Tushare fut_basic contains duplicate product delivery months"
            )
        product_months.add(product_month)


def _unmapped_manifest_products(
    validated_rows: list[dict[str, Any]],
    manifest: TushareInstrumentManifest,
) -> set[tuple[str, str]]:
    discovered_products = {(row["exchange"], row["fut_code"]) for row in validated_rows}
    configured_products = {
        (mapping.exchange, mapping.fut_code) for mapping in manifest.mappings
    }

    return discovered_products.difference(configured_products)


def _validate_configured_products_are_present(
    validated_rows: list[dict[str, Any]],
    manifest: TushareInstrumentManifest,
) -> None:
    discovered_products = {(row["exchange"], row["fut_code"]) for row in validated_rows}
    configured_products = {
        (mapping.exchange, mapping.fut_code) for mapping in manifest.mappings
    }
    missing_products = sorted(configured_products.difference(discovered_products))
    if missing_products:
        raise TushareConfigError(
            "Configured Tushare futures families are absent from fut_basic: %s"
            % _format_product_families(missing_products)
        )


def _historical_contract_from_catalogue_row(
    row: dict[str, Any], mapping: TushareInstrumentMapping
) -> HistoricalFuturesContract:
    price_start_date = _latest_date(row["list_date"], mapping.valid_from)
    price_end_date = _earliest_date(row["delist_date"], mapping.valid_to)

    return HistoricalFuturesContract(
        instrument_code=mapping.instrument_code,
        contract_date=row["d_month"] + "00",
        external_contract_code=row["ts_code"],
        exchange=row["exchange"],
        product_code=row["fut_code"],
        first_trade_date=row["list_date"],
        expiry_date=row["delist_date"],
        price_start_date=price_start_date,
        price_end_date=price_end_date,
    )


def _required_catalogue_text(
    row: pd.Series, column: str, exchange: str, row_number: int
) -> str:
    value = row[column]
    if pd.isna(value) or not str(value).strip():
        raise TushareDataError(
            "Tushare fut_basic %s is blank for %s row %d"
            % (column, exchange, row_number)
        )

    return str(value).strip()


def _parse_catalogue_date(
    row: pd.Series, column: str, exchange: str, row_number: int
) -> date:
    value = _required_catalogue_text(row, column, exchange, row_number)
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise TushareDataError(
            "Tushare fut_basic %s must use YYYYMMDD for %s row %d"
            % (column, exchange, row_number)
        ) from None


def _coerce_optional_date(value: date | str | None, field_name: str) -> date | None:
    if value is None:
        return None

    return _coerce_required_date(value, field_name)


def _coerce_required_date(value: date | str, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    try:
        return datetime.strptime(str(value).strip(), "%Y%m%d").date()
    except ValueError:
        raise TushareConfigError("Tushare %s must use YYYYMMDD" % field_name) from None


def _latest_date(*values: date | None) -> date:
    populated_values = [value for value in values if value is not None]
    return max(populated_values)


def _earliest_date(*values: date | None) -> date:
    populated_values = [value for value in values if value is not None]
    return min(populated_values)


def _year_segments(start_date: date, end_date: date):
    segment_start = start_date
    while segment_start <= end_date:
        segment_end = min(date(segment_start.year, 12, 31), end_date)
        yield segment_start, segment_end
        segment_start = date(segment_start.year + 1, 1, 1)


def _empty_fut_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "settle",
            "vol",
            "amount",
            "oi",
            "oi_chg",
            "change1",
            "change2",
        ]
    )


def _format_product_families(
    products: tuple[tuple[str, str], ...] | list[tuple[str, str]],
) -> str:
    return ", ".join("%s/%s" % product for product in products)
