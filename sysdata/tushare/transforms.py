"""Pure transformations from documented Tushare frames to repository objects."""

from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysobjects.spot_fx_prices import fxPrices
from sysdata.tushare.errors import TushareDataError, TushareTruncationError

TUSHARE_FUT_DAILY_LIMIT = 2000
NOTIONAL_DAILY_CLOSE_HOUR = 23

FUT_DAILY_REQUIRED_COLUMNS = frozenset(
    {"ts_code", "trade_date", "open", "high", "low", "close", "vol"}
)
FUT_DAILY_NUMERIC_COLUMNS = (
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
    "pre_close",
    "pre_settle",
    "delv_settle",
)
CANONICAL_PRICE_COLUMNS = ("OPEN", "HIGH", "LOW", "FINAL", "VOLUME")

FX_DAILY_REQUIRED_COLUMNS = frozenset(
    {"ts_code", "trade_date", "bid_close", "ask_close"}
)
FX_DAILY_NUMERIC_COLUMNS = (
    "bid_open",
    "bid_close",
    "bid_high",
    "bid_low",
    "ask_open",
    "ask_close",
    "ask_high",
    "ask_low",
    "tick_qty",
)


def futures_contract_prices_from_tushare_daily(
    daily_frame: pd.DataFrame,
    expected_ts_code: str,
    price_start_date: date | None = None,
    price_end_date: date | None = None,
) -> futuresContractPrices:
    """Normalize one concrete contract, using the traded close as FINAL."""

    if len(daily_frame) == TUSHARE_FUT_DAILY_LIMIT:
        raise TushareTruncationError(
            "Tushare fut_daily returned exactly its 2000-row limit for %s"
            % expected_ts_code
        )

    _require_columns(daily_frame, FUT_DAILY_REQUIRED_COLUMNS, endpoint="fut_daily")
    if daily_frame.empty:
        return _empty_contract_prices()

    normalized_frame = daily_frame.copy()
    _validate_identity(
        normalized_frame, expected_ts_code=expected_ts_code, endpoint="fut_daily"
    )
    normalized_frame["trade_date"] = _parse_required_trade_dates(
        normalized_frame["trade_date"], endpoint="fut_daily"
    )
    _coerce_numeric_columns(
        normalized_frame,
        FUT_DAILY_NUMERIC_COLUMNS,
        endpoint="fut_daily",
    )

    if price_start_date is not None:
        normalized_frame = normalized_frame[
            normalized_frame["trade_date"].dt.date >= price_start_date
        ]
    if price_end_date is not None:
        normalized_frame = normalized_frame[
            normalized_frame["trade_date"].dt.date <= price_end_date
        ]
    if normalized_frame.empty:
        return _empty_contract_prices()

    normalized_frame = normalized_frame.sort_values("trade_date", kind="mergesort")
    normalized_frame = normalized_frame.drop_duplicates(
        subset=["trade_date"], keep="last"
    )
    close_timestamps = normalized_frame["trade_date"] + pd.Timedelta(
        hours=NOTIONAL_DAILY_CLOSE_HOUR
    )

    canonical_frame = pd.DataFrame(
        {
            "OPEN": normalized_frame["open"].to_numpy(),
            "HIGH": normalized_frame["high"].to_numpy(),
            "LOW": normalized_frame["low"].to_numpy(),
            # FINAL is the last traded price; a missing close stays NaN and is
            # deliberately never replaced with the settlement price.
            "FINAL": normalized_frame["close"].to_numpy(),
            "VOLUME": normalized_frame["vol"].to_numpy(),
        },
        index=pd.DatetimeIndex(close_timestamps.to_numpy()),
        columns=CANONICAL_PRICE_COLUMNS,
    )

    return futuresContractPrices(canonical_frame)


def cnhusd_prices_from_tushare_fx_daily(
    fx_frames: Iterable[pd.DataFrame],
    expected_ts_code: str = "USDCNH.FXCM",
) -> fxPrices:
    """Invert the USDCNH closing bid/ask midpoint into repository CNHUSD."""

    normalized_frames: list[pd.DataFrame] = []
    for fx_frame in fx_frames:
        _require_columns(fx_frame, FX_DAILY_REQUIRED_COLUMNS, endpoint="fx_daily")
        if fx_frame.empty:
            continue

        normalized_frame = fx_frame.copy()
        _validate_identity(
            normalized_frame,
            expected_ts_code=expected_ts_code,
            endpoint="fx_daily",
        )
        normalized_frame["trade_date"] = _parse_required_trade_dates(
            normalized_frame["trade_date"], endpoint="fx_daily"
        )
        _coerce_numeric_columns(
            normalized_frame,
            FX_DAILY_NUMERIC_COLUMNS,
            endpoint="fx_daily",
        )
        normalized_frames.append(normalized_frame)

    if not normalized_frames:
        return fxPrices.create_empty()

    combined_frame = pd.concat(normalized_frames, ignore_index=True)
    combined_frame = combined_frame.sort_values(
        "trade_date", kind="mergesort"
    ).drop_duplicates(subset=["trade_date"], keep="last")

    closing_quotes = combined_frame[["bid_close", "ask_close"]]
    if ((closing_quotes <= 0.0) & closing_quotes.notna()).any().any():
        raise TushareDataError(
            "Tushare fx_daily contains a non-positive closing bid or ask"
        )

    comparable_quotes = closing_quotes.dropna()
    if (comparable_quotes["ask_close"] < comparable_quotes["bid_close"]).any():
        raise TushareDataError("Tushare fx_daily contains a closing ask below its bid")

    midpoint = (combined_frame["bid_close"] + combined_frame["ask_close"]) / 2.0
    close_timestamps = combined_frame["trade_date"] + pd.Timedelta(
        hours=NOTIONAL_DAILY_CLOSE_HOUR
    )
    inverted_midpoint = pd.Series(
        1.0 / midpoint.to_numpy(),
        index=pd.DatetimeIndex(close_timestamps.to_numpy()),
        dtype=float,
    )

    return fxPrices(inverted_midpoint)


def _empty_contract_prices() -> futuresContractPrices:
    return futuresContractPrices(
        pd.DataFrame(columns=CANONICAL_PRICE_COLUMNS, dtype=float)
    )


def _require_columns(
    frame: pd.DataFrame, required_columns: frozenset[str], endpoint: str
) -> None:
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        raise TushareDataError(
            "Tushare %s response is missing fields: %s"
            % (endpoint, ", ".join(missing_columns))
        )


def _validate_identity(
    frame: pd.DataFrame, expected_ts_code: str, endpoint: str
) -> None:
    external_codes = {
        str(value).strip() for value in frame["ts_code"].dropna().unique().tolist()
    }
    if external_codes != {expected_ts_code}:
        raise TushareDataError(
            "Tushare %s returned data for an unexpected identifier" % endpoint
        )


def _parse_required_trade_dates(trade_dates: pd.Series, endpoint: str) -> pd.Series:
    values_as_strings = trade_dates.astype("string").str.strip()
    parsed_dates = pd.to_datetime(values_as_strings, format="%Y%m%d", errors="coerce")
    if parsed_dates.isna().any():
        raise TushareDataError("Tushare %s contains an invalid trade_date" % endpoint)

    return parsed_dates


def _coerce_numeric_columns(
    frame: pd.DataFrame, column_names: tuple[str, ...], endpoint: str
) -> None:
    for column_name in column_names:
        if column_name not in frame.columns:
            continue

        original_values = frame[column_name]
        converted_values = pd.to_numeric(original_values, errors="coerce")
        invalid_values = original_values.notna() & converted_values.isna()
        if invalid_values.any():
            raise TushareDataError(
                "Tushare %s contains non-numeric %s" % (endpoint, column_name)
            )
        frame[column_name] = converted_values
