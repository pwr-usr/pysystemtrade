"""Convert vendor rows once, at the source boundary, into native price objects."""

import numpy as np
import pandas as pd

from sysdata.tushare.client import FUT_DAILY_FIELDS, FX_DAILY_FIELDS
from sysdata.tushare.errors import TushareDataError, TushareTruncationError
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysobjects.spot_fx_prices import fxPrices

TUSHARE_FUT_DAILY_LIMIT = 2000
NOTIONAL_DAILY_CLOSE_HOUR = 23
CANONICAL_PRICE_COLUMNS = ("OPEN", "HIGH", "LOW", "FINAL", "VOLUME")
FUT_DAILY_REQUIRED_COLUMNS = frozenset(
    {"ts_code", "trade_date", "open", "high", "low", "close", "vol"}
)
FUT_DAILY_NUMERIC_COLUMNS = set(FUT_DAILY_FIELDS.split(",")) - {"ts_code", "trade_date"}
FX_DAILY_REQUIRED_COLUMNS = frozenset(
    {"ts_code", "trade_date", "bid_close", "ask_close"}
)
FX_DAILY_NUMERIC_COLUMNS = set(FX_DAILY_FIELDS.split(",")) - {"ts_code", "trade_date"}


def futures_contract_prices_from_tushare_daily(
    daily_frame,
    expected_ts_code,
    price_start_date=None,
    price_end_date=None,
):
    if len(daily_frame) >= TUSHARE_FUT_DAILY_LIMIT:
        raise TushareTruncationError(
            f"fut_daily reached its 2000-row limit: {expected_ts_code}"
        )
    frame = _normalize(
        daily_frame,
        expected_ts_code,
        FUT_DAILY_REQUIRED_COLUMNS,
        FUT_DAILY_NUMERIC_COLUMNS,
    )
    if price_start_date is not None:
        frame = frame[frame.trade_date.dt.date >= price_start_date]
    if price_end_date is not None:
        frame = frame[frame.trade_date.dt.date <= price_end_date]
    # FINAL uses the traded close. NaN close and sparse OHLC remain explicit.
    values = frame.loc[:, ["open", "high", "low", "close", "vol"]].to_numpy()
    return futuresContractPrices(
        pd.DataFrame(
            values,
            columns=CANONICAL_PRICE_COLUMNS,
            index=pd.DatetimeIndex(frame.trade_date.to_numpy())
            + pd.Timedelta(hours=NOTIONAL_DAILY_CLOSE_HOUR),
        )
    )


def cnhusd_prices_from_tushare_fx_daily(fx_frames, expected_ts_code="USDCNH.FXCM"):
    frames = [
        _normalize(
            frame, expected_ts_code, FX_DAILY_REQUIRED_COLUMNS, FX_DAILY_NUMERIC_COLUMNS
        )
        for frame in fx_frames
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return fxPrices.create_empty()
    frame = (
        pd.concat(frames)
        .sort_values("trade_date", kind="stable")
        .drop_duplicates("trade_date", keep="last")
    )
    quotes = frame[["bid_close", "ask_close"]]
    if quotes.le(0).any().any():
        raise TushareDataError("fx_daily contains a non-positive closing bid or ask")
    if frame.ask_close.lt(frame.bid_close).any():
        raise TushareDataError("fx_daily contains a closing ask below its bid")
    midpoint = (frame.bid_close + frame.ask_close) / 2
    return fxPrices(
        pd.Series(
            1 / midpoint.to_numpy(),
            index=pd.DatetimeIndex(frame.trade_date.to_numpy())
            + pd.Timedelta(hours=NOTIONAL_DAILY_CLOSE_HOUR),
        )
    )


def _normalize(frame, expected_code, required, numeric):
    missing = required.difference(frame.columns)
    if missing:
        raise TushareDataError("Missing source fields: " + ", ".join(sorted(missing)))
    frame = frame.copy()
    identities = frame.ts_code.astype("string").str.strip()
    if not identities.eq(expected_code).fillna(False).all():
        raise TushareDataError("Source returned an unexpected identifier")
    dates = frame.trade_date.astype("string").str.strip()
    parsed = pd.to_datetime(dates, format="%Y%m%d", errors="coerce")
    if parsed.isna().any() or not dates.str.fullmatch(r"\d{8}").all():
        raise TushareDataError("Invalid trade_date")
    frame["trade_date"] = parsed
    for column in set(numeric).intersection(frame.columns):
        values = pd.to_numeric(frame[column], errors="coerce")
        if (frame[column].notna() & values.isna()).any() or np.isinf(values).any():
            raise TushareDataError(f"Non-numeric or infinite {column}")
        frame[column] = values
    return frame.sort_values("trade_date", kind="stable").drop_duplicates(
        "trade_date", keep="last"
    )
