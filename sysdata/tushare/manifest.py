"""Reviewed mapping between Tushare product families and internal instruments.

One row per internal instrument.  ``ValidFrom``/``ValidTo`` split a single
vendor family across a reviewed specification change (DCE fibreboard 2019),
``Predecessor`` records product-rename chains, and ``StitchMode`` separates
stitchable outrights from catalogue-only families (monthly-average and TAS
contracts) that get price data but never a roll policy.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from sysdata.tushare.errors import TushareConfigError

MANIFEST_COLUMNS = (
    "Instrument",
    "Exchange",
    "FutCode",
    "ValidFrom",
    "ValidTo",
    "MinTick",
    "Predecessor",
    "StitchMode",
)

SUPPORTED_EXCHANGES = ("CFFEX", "DCE", "CZCE", "SHFE", "INE", "GFEX")
STITCH_MODE = "stitch"
CATALOG_ONLY_MODE = "catalog_only"
SUPPORTED_STITCH_MODES = (STITCH_MODE, CATALOG_ONLY_MODE)

DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "config" / "futures_instruments.csv"
)

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True)
class TushareInstrumentMapping:
    instrument_code: str
    exchange: str
    fut_code: str
    valid_from: date | None
    valid_to: date | None
    min_tick: float
    predecessor: str | None
    stitch_mode: str

    @property
    def is_stitchable(self) -> bool:
        return self.stitch_mode == STITCH_MODE

    def overlaps(self, first_date: date, last_date: date) -> bool:
        starts_before_contract_ends = (
            self.valid_from is None or self.valid_from <= last_date
        )
        ends_after_contract_starts = (
            self.valid_to is None or self.valid_to >= first_date
        )

        return starts_before_contract_ends and ends_after_contract_starts


class TushareInstrumentManifest:
    def __init__(self, mappings: list[TushareInstrumentMapping]):
        if not mappings:
            raise TushareConfigError("The Tushare instrument manifest is empty")

        self._mappings = tuple(mappings)
        self._validate_unambiguous_windows()

    @classmethod
    def from_csv(
        cls, filename: str | Path = DEFAULT_MANIFEST_PATH
    ) -> "TushareInstrumentManifest":
        manifest_path = Path(filename)
        try:
            manifest_frame = pd.read_csv(
                manifest_path, dtype=str, keep_default_na=False
            )
        except (OSError, pd.errors.ParserError):
            raise TushareConfigError(
                "Cannot read Tushare instrument manifest at %s" % manifest_path
            ) from None

        return cls.from_dataframe(manifest_frame)

    @classmethod
    def from_dataframe(
        cls, manifest_frame: pd.DataFrame
    ) -> "TushareInstrumentManifest":
        actual_columns = tuple(manifest_frame.columns)
        if actual_columns != MANIFEST_COLUMNS:
            raise TushareConfigError(
                "Tushare manifest columns must be exactly %s"
                % ", ".join(MANIFEST_COLUMNS)
            )

        mappings = [
            _mapping_from_row(row_number=index + 2, row=row)
            for index, (_, row) in enumerate(manifest_frame.iterrows())
        ]

        return cls(mappings)

    @property
    def mappings(self) -> tuple[TushareInstrumentMapping, ...]:
        return self._mappings

    def configured_instrument_codes(self) -> list[str]:
        return sorted({mapping.instrument_code for mapping in self._mappings})

    def mappings_for_product(
        self, exchange: str, fut_code: str
    ) -> tuple[TushareInstrumentMapping, ...]:
        return tuple(
            mapping
            for mapping in self._mappings
            if mapping.exchange == exchange and mapping.fut_code == fut_code
        )

    def mappings_for_contract(
        self,
        exchange: str,
        fut_code: str,
        first_trade_date: date,
        expiry_date: date,
    ) -> tuple[TushareInstrumentMapping, ...]:
        product_mappings = self.mappings_for_product(exchange, fut_code)

        return tuple(
            mapping
            for mapping in product_mappings
            if mapping.overlaps(first_trade_date, expiry_date)
        )

    def _validate_unambiguous_windows(self) -> None:
        seen_instruments: set[str] = set()
        mappings_by_product: dict[tuple[str, str], list[TushareInstrumentMapping]] = {}

        for mapping in self._mappings:
            if mapping.instrument_code in seen_instruments:
                raise TushareConfigError(
                    "Duplicate internal instrument in Tushare manifest: %s"
                    % mapping.instrument_code
                )
            seen_instruments.add(mapping.instrument_code)

            mappings_by_product.setdefault(
                (mapping.exchange, mapping.fut_code), []
            ).append(mapping)

        for product_key, product_mappings in mappings_by_product.items():
            ordered_mappings = sorted(
                product_mappings,
                key=lambda item: item.valid_from or date.min,
            )
            previous_end: date | None = None
            previous_has_open_end = False
            for position, mapping in enumerate(ordered_mappings):
                if position > 0 and (
                    previous_has_open_end
                    or mapping.valid_from is None
                    or (previous_end is not None and mapping.valid_from <= previous_end)
                ):
                    raise TushareConfigError(
                        "Overlapping validity windows for Tushare product %s/%s"
                        % product_key
                    )

                previous_end = mapping.valid_to
                previous_has_open_end = mapping.valid_to is None

        unknown_predecessors = sorted(
            {
                mapping.predecessor
                for mapping in self._mappings
                if mapping.predecessor is not None
                and mapping.predecessor not in seen_instruments
            }
        )
        if unknown_predecessors:
            raise TushareConfigError(
                "Unknown predecessor instruments in Tushare manifest: %s"
                % ", ".join(unknown_predecessors)
            )


def _mapping_from_row(row_number: int, row: pd.Series) -> TushareInstrumentMapping:
    instrument_code = _required_text(row, "Instrument", row_number).upper()
    exchange = _required_text(row, "Exchange", row_number).upper()
    fut_code = _required_text(row, "FutCode", row_number).upper()
    predecessor = _optional_text(row, "Predecessor")
    stitch_mode = _required_text(row, "StitchMode", row_number).lower()

    if exchange not in SUPPORTED_EXCHANGES:
        raise TushareConfigError(
            "Unsupported exchange in Tushare manifest row %d" % row_number
        )
    if not _CODE_PATTERN.fullmatch(instrument_code) or not instrument_code.startswith(
        exchange + "_"
    ):
        raise TushareConfigError(
            "Instrument must be a valid exchange-qualified code in Tushare "
            "manifest row %d" % row_number
        )
    if not _CODE_PATTERN.fullmatch(fut_code):
        raise TushareConfigError(
            "Invalid FutCode in Tushare manifest row %d" % row_number
        )
    if stitch_mode not in SUPPORTED_STITCH_MODES:
        raise TushareConfigError(
            "Invalid StitchMode in Tushare manifest row %d" % row_number
        )

    valid_from = _optional_date(row, "ValidFrom", row_number)
    valid_to = _optional_date(row, "ValidTo", row_number)
    if valid_from is not None and valid_to is not None and valid_from > valid_to:
        raise TushareConfigError(
            "ValidFrom is after ValidTo in Tushare manifest row %d" % row_number
        )

    try:
        min_tick = float(_required_text(row, "MinTick", row_number))
    except ValueError:
        min_tick = math.nan
    if not math.isfinite(min_tick) or min_tick <= 0:
        raise TushareConfigError(
            "MinTick must be a positive number in Tushare manifest row %d" % row_number
        )

    if predecessor is not None:
        predecessor = predecessor.upper()
        if not _CODE_PATTERN.fullmatch(predecessor):
            raise TushareConfigError(
                "Invalid Predecessor in Tushare manifest row %d" % row_number
            )

    return TushareInstrumentMapping(
        instrument_code=instrument_code,
        exchange=exchange,
        fut_code=fut_code,
        valid_from=valid_from,
        valid_to=valid_to,
        min_tick=min_tick,
        predecessor=predecessor,
        stitch_mode=stitch_mode,
    )


def _required_text(row: pd.Series, column: str, row_number: int) -> str:
    value = _optional_text(row, column)
    if value is None:
        raise TushareConfigError(
            "%s is blank in Tushare manifest row %d" % (column, row_number)
        )

    return value


def _optional_text(row: pd.Series, column: str) -> str | None:
    value = row[column]
    if pd.isna(value):
        return None
    value_as_string = str(value).strip()

    return value_as_string or None


def _optional_date(row: pd.Series, column: str, row_number: int) -> date | None:
    value = _optional_text(row, column)
    if value is None:
        return None

    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise TushareConfigError(
            "%s must use YYYYMMDD in Tushare manifest row %d" % (column, row_number)
        ) from None
