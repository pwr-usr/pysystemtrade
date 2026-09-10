"""Reviewed vendor identities and specification eras; trading windows live separately."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
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
STITCH_MODE, CATALOG_ONLY_MODE = "stitch", "catalog_only"
SUPPORTED_STITCH_MODES = (STITCH_MODE, CATALOG_ONLY_MODE)
DEFAULT_MANIFEST_PATH = Path(__file__).parent / "config" / "futures_instruments.csv"


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
    def is_stitchable(self):
        return self.stitch_mode == STITCH_MODE

    def overlaps(self, first_date, last_date):
        return (self.valid_from or date.min) <= last_date and (
            self.valid_to or date.max
        ) >= first_date


class TushareInstrumentManifest:
    def __init__(self, mappings):
        self.mappings = tuple(mappings)
        codes = [mapping.instrument_code for mapping in self.mappings]
        if not codes or len(codes) != len(set(codes)):
            raise TushareConfigError("Empty manifest or duplicate internal instruments")
        for mapping in self.mappings:
            if mapping.predecessor and mapping.predecessor not in codes:
                raise TushareConfigError("Unknown predecessor: " + mapping.predecessor)
        for exchange, code in {(m.exchange, m.fut_code) for m in self.mappings}:
            ordered = sorted(
                self.mappings_for_product(exchange, code),
                key=lambda m: m.valid_from or date.min,
            )
            for previous, current in zip(ordered, ordered[1:]):
                if (previous.valid_to or date.max) >= (current.valid_from or date.min):
                    raise TushareConfigError(
                        f"Overlapping validity windows: {exchange}/{code}"
                    )

    @classmethod
    def from_csv(cls, filename=DEFAULT_MANIFEST_PATH):
        try:
            frame = pd.read_csv(filename, dtype=str, keep_default_na=False)
        except (OSError, pd.errors.ParserError) as error:
            raise TushareConfigError(f"Cannot read manifest: {filename}") from error
        return cls.from_dataframe(frame)

    @classmethod
    def from_dataframe(cls, frame):
        if tuple(frame.columns) != MANIFEST_COLUMNS:
            raise TushareConfigError(
                "Manifest columns must be: " + ", ".join(MANIFEST_COLUMNS)
            )
        frame = (
            frame.fillna("").astype(str).apply(lambda column: column.str.strip()).copy()
        )
        for column in ("Instrument", "Exchange", "FutCode", "Predecessor"):
            frame[column] = frame[column].str.upper()
        frame["StitchMode"] = frame.StitchMode.str.lower()
        valid = frame.Exchange.isin(SUPPORTED_EXCHANGES)
        for column in ("Instrument", "FutCode"):
            valid &= frame[column].str.fullmatch(r"[A-Z][A-Z0-9_]*")
        valid &= pd.Series(
            [
                code.startswith(exchange + "_")
                for code, exchange in zip(frame.Instrument, frame.Exchange)
            ],
            index=frame.index,
        )
        valid &= frame.Predecessor.eq("") | frame.Predecessor.str.fullmatch(
            r"[A-Z][A-Z0-9_]*"
        )
        valid &= frame.StitchMode.isin(SUPPORTED_STITCH_MODES)
        ticks = pd.to_numeric(frame.MinTick, errors="coerce")
        valid &= np.isfinite(ticks) & ticks.gt(0)
        if not valid.all():
            raise TushareConfigError(
                "Invalid manifest rows: " + str(frame.index[~valid].tolist())
            )
        for column in ("ValidFrom", "ValidTo"):
            populated = frame[column].ne("")
            parsed = pd.to_datetime(
                frame[column].where(populated), format="%Y%m%d", errors="coerce"
            )
            if (
                populated & (~frame[column].str.fullmatch(r"\d{8}") | parsed.isna())
            ).any():
                raise TushareConfigError(f"{column} must use YYYYMMDD")
            frame[column] = parsed.dt.date.where(parsed.notna(), None)
        if any(
            start and end and start > end
            for start, end in zip(frame.ValidFrom, frame.ValidTo)
        ):
            raise TushareConfigError("ValidFrom is after ValidTo")
        return cls(
            [
                TushareInstrumentMapping(
                    row.Instrument,
                    row.Exchange,
                    row.FutCode,
                    row.ValidFrom,
                    row.ValidTo,
                    float(row.MinTick),
                    row.Predecessor or None,
                    row.StitchMode,
                )
                for row in frame.itertuples(index=False)
            ]
        )

    def configured_instrument_codes(self):
        return sorted(mapping.instrument_code for mapping in self.mappings)

    def mappings_for_product(self, exchange, fut_code):
        return tuple(
            m for m in self.mappings if (m.exchange, m.fut_code) == (exchange, fut_code)
        )

    def mappings_for_contract(self, exchange, fut_code, first_trade_date, expiry_date):
        return tuple(
            m
            for m in self.mappings_for_product(exchange, fut_code)
            if m.overlaps(first_trade_date, expiry_date)
        )
