"""Read-only Tushare data adapters using pysystemtrade contracts and price objects."""

import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

from syscore.dateutils import Frequency
from syscore.exceptions import ContractNotFound, missingData
from sysdata.futures.contracts import futuresContractData
from sysdata.futures.futures_per_contract_prices import futuresContractPriceData
from sysdata.fx.spotfx import fxPricesData
from sysdata.tushare.client import FUT_BASIC_FIELDS, TushareClient, as_date
from sysdata.tushare.errors import TushareConfigError, TushareDataError
from sysdata.tushare.manifest import SUPPORTED_EXCHANGES, TushareInstrumentManifest
from sysdata.tushare.transforms import (
    cnhusd_prices_from_tushare_fx_daily,
    futures_contract_prices_from_tushare_daily,
)
from syslogging.logger import get_logger
from sysobjects.contract_dates_and_expiries import expiryDate, listOfContractDateStr
from sysobjects.contracts import futuresContract, listOfFuturesContracts
from sysobjects.futures_per_contract_prices import futuresContractPrices

EXCHANGE_SUFFIX = dict(
    CFFEX="CFX", DCE="DCE", CZCE="ZCE", SHFE="SHF", INE="INE", GFEX="GFE"
)
CATALOGUE_REQUIRED_COLUMNS = FUT_BASIC_FIELDS.split(",")
CNHUSD_SOURCE_CODE = "USDCNH.FXCM"
CNHUSD_EARLIEST_DATE = date(2012, 2, 18)


@dataclass(frozen=True)
class HistoricalFuturesContract:
    """Internal vendor mapping evidence, also retained for saved notebook imports."""

    instrument_code: str
    contract_date: str
    external_contract_code: str
    exchange: str
    product_code: str
    first_trade_date: date
    expiry_date: date
    price_start_date: date | None = None
    price_end_date: date | None = None

    def as_futures_contract(self):
        contract = futuresContract(self.instrument_code, self.contract_date)
        expiry = self.expiry_date
        contract.update_single_expiry_date(
            expiryDate(expiry.year, expiry.month, expiry.day)
        )
        return contract


@dataclass(frozen=True)
class TushareCatalogueResult:
    _by_contract: dict
    unmapped_families: tuple

    @property
    def contracts(self):
        return tuple(self._by_contract.values())

    @property
    def is_complete(self):
        return not self.unmapped_families


class tushareConnection:
    """One lazy client and one validated catalogue shared by the three data adapters."""

    def __init__(self, client=None, manifest=None):
        self._client = client
        if manifest is None:
            manifest = TushareInstrumentManifest.from_csv()
        elif not isinstance(manifest, TushareInstrumentManifest):
            manifest = TushareInstrumentManifest.from_csv(manifest)
        self.manifest = manifest
        self._catalogue = None

    @property
    def client(self):
        if self._client is None:
            self._client = TushareClient()
        return self._client

    @property
    def supported_frequencies(self):
        return [Frequency.Day]

    def configured_instrument_codes(self):
        return self.manifest.configured_instrument_codes()

    def fetch_contract_catalogue(self):
        result = self.fetch_contract_catalogue_result()
        if result.unmapped_families:
            raise TushareConfigError(
                "Unmapped futures families: " + _families(result.unmapped_families)
            )
        return list(result.contracts)

    def fetch_contract_catalogue_result(self, refresh=False):
        if self._catalogue is not None and not refresh:
            return self._catalogue
        frames = [
            _validated_catalogue(
                self.client.fut_basic(exchange=exchange, fut_type="1"), exchange
            )
            for exchange in SUPPORTED_EXCHANGES
        ]
        frame = pd.concat(frames, ignore_index=True)
        if (
            frame.ts_code.duplicated().any()
            or frame.duplicated(["exchange", "fut_code", "d_month"]).any()
        ):
            raise TushareDataError("Duplicate catalogue identity or delivery month")
        discovered = set(zip(frame.exchange, frame.fut_code))
        configured = {(m.exchange, m.fut_code) for m in self.manifest.mappings}
        if configured - discovered:
            raise TushareConfigError(
                "Configured families absent: " + _families(configured - discovered)
            )
        records, covered = {}, set()
        for row in frame.itertuples(index=False):
            if (row.exchange, row.fut_code) not in configured:
                continue
            mappings = self.manifest.mappings_for_contract(
                row.exchange,
                row.fut_code,
                row.list_date,
                row.delist_date,
            )
            if not mappings:
                raise TushareConfigError("Validity windows do not cover " + row.ts_code)
            for mapping in mappings:
                covered.add(mapping.instrument_code)
                records[
                    (mapping.instrument_code, row.d_month + "00")
                ] = HistoricalFuturesContract(
                    mapping.instrument_code,
                    row.d_month + "00",
                    row.ts_code,
                    row.exchange,
                    row.fut_code,
                    row.list_date,
                    row.delist_date,
                    max(row.list_date, mapping.valid_from or date.min),
                    min(row.delist_date, mapping.valid_to or date.max),
                )
        uncovered = set(self.configured_instrument_codes()) - covered
        if uncovered:
            raise TushareConfigError(
                "Manifest rows without contracts: " + ", ".join(sorted(uncovered))
            )
        self._catalogue = TushareCatalogueResult(
            dict(sorted(records.items())), tuple(sorted(discovered - configured))
        )
        return self._catalogue

    def get_record(self, contract):
        key = (contract.instrument_code, contract.date_str)
        catalogue = self.fetch_contract_catalogue_result()
        try:
            return catalogue._by_contract[key]
        except KeyError:
            raise ContractNotFound(
                f"Unknown Tushare contract: {contract.key}"
            ) from None

    def get_contracts(self, instrument_code=None) -> listOfFuturesContracts:
        records = self.fetch_contract_catalogue_result().contracts
        return listOfFuturesContracts(
            [
                record.as_futures_contract()
                for record in records
                if instrument_code is None or record.instrument_code == instrument_code
            ]
        )

    def get_prices_at_frequency_for_contract(
        self, contract, frequency=Frequency.Day, start_date=None, end_date=None
    ):
        """Compatibility for saved reports using vendor catalogue records."""
        prices = tushareFuturesContractPriceData(self)
        return prices.get_prices_at_frequency_for_contract_object(
            contract.as_futures_contract(),
            frequency=frequency,
            start_date=start_date,
            end_date=end_date,
        )

    def get_cnhusd_prices(self, start_date=CNHUSD_EARLIEST_DATE, end_date=None):
        first, last = as_date(start_date), as_date(end_date) or date.today()
        if first is None or first > last:
            raise TushareConfigError("FX start_date is after end_date")
        frames = []
        while first <= last:
            end = min(date(first.year, 12, 31), last)
            frames.append(
                self.client.fx_daily(
                    ts_code=CNHUSD_SOURCE_CODE, start_date=first, end_date=end
                )
            )
            first = date(first.year + 1, 1, 1)
        return cnhusd_prices_from_tushare_fx_daily(frames, CNHUSD_SOURCE_CODE)


# Compatibility for already-saved reports; all work uses the connection above.
TushareFuturesPriceSource = tushareConnection


class tushareFuturesContractData(futuresContractData):
    def __init__(self, connection=None, log=get_logger("tushareFuturesContractData")):
        super().__init__(log=log)
        self.connection = connection or tushareConnection()

    def get_list_of_all_instruments_with_contracts(self):
        records = self.connection.fetch_contract_catalogue_result().contracts
        return sorted({record.instrument_code for record in records})

    def get_all_contract_objects_for_instrument_code(self, instrument_code):
        return self.connection.get_contracts(instrument_code)

    def get_list_of_contract_dates_for_instrument_code(
        self, instrument_code, allow_expired=False
    ):
        records = self.connection.fetch_contract_catalogue_result().contracts
        return listOfContractDateStr(
            [
                record.contract_date
                for record in records
                if record.instrument_code == instrument_code
                and (allow_expired or record.expiry_date >= date.today())
            ]
        )

    def is_contract_in_data(self, instrument_code, contract_date_str):
        try:
            self.connection.get_record(
                futuresContract(instrument_code, contract_date_str)
            )
            return True
        except ContractNotFound:
            return False

    def _get_contract_data_without_checking(self, instrument_code, contract_date):
        return self.connection.get_record(
            futuresContract(instrument_code, contract_date)
        ).as_futures_contract()


class tushareFuturesContractPriceData(futuresContractPriceData):
    def __init__(
        self, connection=None, log=get_logger("tushareFuturesContractPriceData")
    ):
        super().__init__(log=log)
        self.connection = connection or tushareConnection()

    def get_contracts_with_merged_price_data(self):
        return self.connection.get_contracts()

    def get_contracts_with_price_data_for_frequency(self, frequency):
        return (
            self.get_contracts_with_merged_price_data()
            if frequency == Frequency.Day
            else listOfFuturesContracts([])
        )

    def get_prices_at_frequency_for_contract_object(
        self,
        contract_object,
        frequency=Frequency.Day,
        return_empty=True,
        start_date=None,
        end_date=None,
    ):
        if frequency != Frequency.Day:
            raise TushareConfigError("Tushare supports Frequency.Day")
        try:
            record = self.connection.get_record(contract_object)
        except ContractNotFound:
            if return_empty:
                return futuresContractPrices.create_empty()
            raise missingData from None
        first, last = as_date(start_date), as_date(end_date)
        if first and last and first > last:
            raise TushareConfigError("Price start_date is after end_date")
        first = max(
            first or date.min,
            record.price_start_date or date.min,
            record.first_trade_date,
        )
        last = min(
            last or date.max, record.price_end_date or date.max, record.expiry_date
        )
        if first > last:
            return futuresContractPrices.create_empty()
        frame = self.connection.client.fut_daily(
            ts_code=record.external_contract_code, start_date=first, end_date=last
        )
        return futures_contract_prices_from_tushare_daily(
            frame, record.external_contract_code, first, last
        )

    def get_merged_prices_for_contract_object(self, contract_object, return_empty=True):
        return self.get_prices_at_frequency_for_contract_object(
            contract_object, return_empty=return_empty
        )


class tushareFxPricesData(fxPricesData):
    def __init__(self, connection=None, log=get_logger("tushareFxPricesData")):
        super().__init__(log=log)
        self.connection = connection or tushareConnection()

    def get_list_of_fxcodes(self):
        return ["CNHUSD"]

    def get_fx_prices(self, fx_code, start_date=None, end_date=None):
        if fx_code == "CNHUSD":
            return self.connection.get_cnhusd_prices(
                start_date=CNHUSD_EARLIEST_DATE if start_date is None else start_date,
                end_date=end_date,
            )
        return super().get_fx_prices(fx_code).loc[start_date:end_date]

    def _get_fx_prices_without_checking(self, code):
        return self.connection.get_cnhusd_prices()


def _validated_catalogue(frame, exchange):
    if frame.empty or not set(CATALOGUE_REQUIRED_COLUMNS).issubset(frame):
        raise TushareDataError(f"Empty or incomplete fut_basic response: {exchange}")
    frame = frame.loc[:, CATALOGUE_REQUIRED_COLUMNS].copy()
    for column in CATALOGUE_REQUIRED_COLUMNS:
        if frame[column].isna().any():
            raise TushareDataError(f"Missing fut_basic field: {column}")
        frame[column] = frame[column].astype(str).str.strip()
    if (frame == "").any().any():
        raise TushareDataError("Blank fut_basic identity or date")
    if (
        not frame.exchange.eq(exchange).all()
        or not frame.fut_code.str.fullmatch(r"[A-Z][A-Z0-9_]*").all()
    ):
        raise TushareDataError("Invalid fut_basic exchange or product")
    if (
        not frame.d_month.str.fullmatch(r"\d{6}").all()
        or not frame.d_month.str[-2:].astype(int).between(1, 12).all()
    ):
        raise TushareDataError("fut_basic d_month must be YYYYMM")
    for column in ("list_date", "delist_date"):
        parsed = pd.to_datetime(frame[column], format="%Y%m%d", errors="coerce")
        if parsed.isna().any() or not frame[column].str.fullmatch(r"\d{8}").all():
            raise TushareDataError(f"fut_basic {column} must use YYYYMMDD")
        frame[column] = parsed.dt.date
    if (frame.list_date > frame.delist_date).any():
        raise TushareDataError("Contract lists after expiry")
    for row in frame.itertuples(index=False):
        suffix = "." + EXCHANGE_SUFFIX[exchange]
        if not row.ts_code.endswith(suffix) or not _is_concrete_external_code(
            row.ts_code[: -len(suffix)],
            row.symbol,
            row.fut_code,
            row.d_month,
            exchange,
        ):
            raise TushareDataError("Inconsistent or synthetic contract: " + row.ts_code)
    return frame


def _is_concrete_external_code(external_code, symbol, fut_code, d_month, exchange):
    if fut_code == "SCTAS":
        pattern = r"SCTAS(\d{3,4})"
    elif fut_code.endswith("_F"):
        pattern = re.escape(fut_code[:-2]) + r"(\d{3,4})F"
    else:
        pattern = re.escape(fut_code) + r"(\d{4})"
    match = re.fullmatch(pattern, external_code)
    if match is None:
        return False
    digits = match.group(1)
    expected_symbol = (
        "SC" + digits + "TAS"
        if fut_code == "SCTAS"
        else fut_code + d_month[-3:]
        if exchange == "CZCE"
        else external_code
    )
    return symbol == expected_symbol and digits == d_month[-len(digits) :]


def _families(values):
    return ", ".join("/".join(value) for value in sorted(values))
