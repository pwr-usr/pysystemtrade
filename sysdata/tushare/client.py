"""Tushare SDK transport: credentials, bounded retries and a shared request rate."""

import importlib
import os
import re
import threading
import time
from datetime import date, datetime

import pandas as pd

from sysdata.tushare.errors import (
    TushareConfigError,
    TushareDataError,
    TushareTransientError,
)

DEFAULT_REQUESTS_PER_MINUTE, DEFAULT_MAX_ATTEMPTS = 180, 3
TOKEN_ENVIRONMENT_VARIABLE, TOKEN_PRIVATE_CONFIG_KEY = "TUSHARE_TOKEN", "tushare_token"
FUT_BASIC_FIELDS = "ts_code,symbol,exchange,fut_code,list_date,delist_date,d_month"
FUT_DAILY_FIELDS = (
    "ts_code,trade_date,pre_close,pre_settle,open,high,low,close,settle,"
    "change1,change2,vol,amount,oi,oi_chg,delv_settle"
)
FX_DAILY_FIELDS = (
    "ts_code,trade_date,bid_open,bid_close,bid_high,bid_low,"
    "ask_open,ask_close,ask_high,ask_low,tick_qty"
)


class TushareClient:
    """Optional SDK; inject api and clocks for deterministic, offline tests."""

    def __init__(
        self,
        api=None,
        requests_per_minute=DEFAULT_REQUESTS_PER_MINUTE,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        base_retry_delay_seconds=1.0,
        sleep_fn=time.sleep,
        monotonic_fn=time.monotonic,
    ):
        if requests_per_minute <= 0 or max_attempts < 1 or base_retry_delay_seconds < 0:
            raise TushareConfigError("Invalid request rate or retry parameters")
        self._api = api if api is not None else _create_tushare_api()
        self._interval = 60.0 / requests_per_minute
        self._attempts, self._retry_delay = max_attempts, base_retry_delay_seconds
        self._sleep, self._clock = sleep_fn, monotonic_fn
        self._last_request = None
        self._lock = threading.Lock()

    def __repr__(self):
        return "TushareClient(read-only)"

    def fut_basic(self, exchange, fut_type="1"):
        return self._call(
            "fut_basic", exchange=exchange, fut_type=fut_type, fields=FUT_BASIC_FIELDS
        )

    def fut_daily(self, **parameters):
        return self._call("fut_daily", fields=FUT_DAILY_FIELDS, **parameters)

    def fx_daily(self, *, ts_code, **parameters):
        return self._call(
            "fx_daily", ts_code=ts_code, fields=FX_DAILY_FIELDS, **parameters
        )

    def _wait_for_request_slot(self):
        with self._lock:
            now = self._clock()
            wait = (
                0
                if self._last_request is None
                else max(0, self._last_request + self._interval - now)
            )
            if wait:
                self._sleep(wait)
            self._last_request = max(self._clock(), now + wait)

    def _call(self, endpoint, **parameters):
        dates = {"trade_date", "start_date", "end_date"}
        parameters = {
            key: _format_date(value) if key in dates else value
            for key, value in parameters.items()
            if value is not None
        }
        if "trade_date" in parameters and dates.intersection(parameters) != {
            "trade_date"
        }:
            raise TushareConfigError("Choose trade_date or a start/end range")
        try:
            method = getattr(self._api, endpoint)
        except AttributeError:
            raise TushareConfigError(
                f"SDK does not provide endpoint {endpoint}"
            ) from None
        for attempt in range(self._attempts):
            self._wait_for_request_slot()
            try:
                frame = method(**parameters)
            except Exception as error:
                # Source messages can contain the credential. Expose only a fixed diagnosis.
                kind = _error_kind(error)
                if kind == "permission":
                    raise TushareConfigError(
                        f"Tushare denied access to {endpoint} (token/points)"
                    ) from None
                if kind != "transient":
                    raise TushareDataError(
                        f"Tushare {endpoint} rejected the request"
                    ) from None
                if attempt == self._attempts - 1:
                    raise TushareTransientError(
                        f"Tushare {endpoint} failed after {self._attempts} attempts"
                    ) from None
                self._sleep(self._retry_delay * 2**attempt)
                continue
            if not isinstance(frame, pd.DataFrame):
                raise TushareDataError(f"Tushare {endpoint} did not return a DataFrame")
            return frame.copy()


def _resolve_token():
    token = os.environ.get(TOKEN_ENVIRONMENT_VARIABLE, "").strip()
    if not token:
        from sysdata.config.private_config import get_private_config_as_dict

        token = str(
            get_private_config_as_dict().get(TOKEN_PRIVATE_CONFIG_KEY) or ""
        ).strip()
    if not token:
        raise TushareConfigError("Set TUSHARE_TOKEN or private_config tushare_token")
    return token


def _create_tushare_api():
    token = _resolve_token()
    try:
        sdk = importlib.import_module("tushare")
    except ImportError:
        raise TushareConfigError("Install the optional tushare dependency") from None
    try:
        return sdk.pro_api(token)
    except Exception:
        raise TushareConfigError("Could not initialise the Tushare SDK") from None


def as_date(value):
    """Public Python/CLI dates accept date objects, YYYYMMDD and YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = str(value).strip()
    pattern = "%Y%m%d" if re.fullmatch(r"\d{8}", value) else "%Y-%m-%d"
    try:
        return datetime.strptime(value, pattern).date()
    except ValueError:
        raise TushareConfigError("Dates must use YYYYMMDD or YYYY-MM-DD") from None


def _format_date(value):
    parsed = as_date(value)
    return parsed.strftime("%Y%m%d") if parsed else None


def _error_kind(error):
    try:
        status = int(getattr(getattr(error, "response", None), "status_code", 0) or 0)
    except (TypeError, ValueError):
        status = 0
    if status in (401, 403):
        return "permission"
    if status:
        return "transient" if status in (408, 429) or 500 <= status <= 599 else "data"
    message = str(error).lower()
    permission = (
        r"permission|unauthor|forbidden|invalid.*token|token.*invalid|权限|积分|\b40[13]\b"
    )
    if re.search(permission, message):
        return "permission"
    names = " ".join(cls.__name__.lower() for cls in type(error).__mro__)
    transient = (
        r"timed out|timeout|connection|temporar|too many requests|rate.?limit|每分钟|"
        r"429|500|502|503|504|internal server error|bad gateway|service unavailable|gateway timeout"
    )
    return "transient" if re.search(transient, message + " " + names) else "data"
