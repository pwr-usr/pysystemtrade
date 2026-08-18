"""Thin, read-only Tushare Pro client with retry and rate-limit policy."""

from __future__ import annotations

import importlib
import os
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd

from sysdata.tushare.errors import (
    TushareConfigError,
    TushareDataError,
    TushareTransientError,
)

DEFAULT_REQUESTS_PER_MINUTE = 180
DEFAULT_MAX_ATTEMPTS = 3
TOKEN_ENVIRONMENT_VARIABLE = "TUSHARE_TOKEN"
TOKEN_PRIVATE_CONFIG_KEY = "tushare_token"

FUT_BASIC_FIELDS = "ts_code,symbol,exchange,fut_code,list_date,delist_date,d_month"
FUT_DAILY_FIELDS = (
    "ts_code,trade_date,pre_close,pre_settle,open,high,low,close,settle,"
    "change1,change2,vol,amount,oi,oi_chg,delv_settle"
)
FX_DAILY_FIELDS = (
    "ts_code,trade_date,bid_open,bid_close,bid_high,bid_low,"
    "ask_open,ask_close,ask_high,ask_low,tick_qty"
)


class _RateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        sleep_fn: Callable[[float], None],
        monotonic_fn: Callable[[], float],
    ):
        if requests_per_minute <= 0:
            raise TushareConfigError("Tushare requests_per_minute must be positive")

        self._minimum_interval = 60.0 / requests_per_minute
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._last_request_at: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                wait_seconds = self._minimum_interval - (now - self._last_request_at)
                if wait_seconds > 0:
                    self._sleep(wait_seconds)
                    # Test clocks are not required to advance when sleep is injected.
                    now = max(self._monotonic(), now + wait_seconds)

            self._last_request_at = now


class TushareClient:
    """
    A minimal wrapper around the optional ``tushare`` SDK.

    Supplying ``api`` is intended for deterministic tests.  Normal construction
    resolves the token (``TUSHARE_TOKEN`` env var, falling back to the
    ``tushare_token`` private-config key) and imports the SDK lazily.
    """

    def __init__(
        self,
        api: Any | None = None,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_retry_delay_seconds: float = 1.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ):
        if max_attempts < 1:
            raise TushareConfigError("Tushare max_attempts must be at least one")
        if base_retry_delay_seconds < 0:
            raise TushareConfigError("Tushare retry delay cannot be negative")

        self._api = api if api is not None else _create_tushare_api()
        self._max_attempts = max_attempts
        self._base_retry_delay_seconds = base_retry_delay_seconds
        self._sleep = sleep_fn
        self._rate_limiter = _RateLimiter(
            requests_per_minute=requests_per_minute,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )

    def __repr__(self) -> str:
        return "TushareClient(read-only)"

    def fut_basic(self, exchange: str, fut_type: str = "1") -> pd.DataFrame:
        return self._call(
            "fut_basic",
            exchange=exchange,
            fut_type=fut_type,
            fields=FUT_BASIC_FIELDS,
        )

    def fut_daily(
        self,
        *,
        ts_code: str | None = None,
        trade_date: date | datetime | str | None = None,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        exchange: str | None = None,
    ) -> pd.DataFrame:
        if trade_date is not None and (start_date is not None or end_date is not None):
            raise TushareConfigError(
                "Use trade_date or a start/end range for fut_daily, not both"
            )

        parameters = _drop_none_values(
            dict(
                ts_code=ts_code,
                trade_date=_format_date(trade_date),
                start_date=_format_date(start_date),
                end_date=_format_date(end_date),
                exchange=exchange,
                fields=FUT_DAILY_FIELDS,
            )
        )

        return self._call("fut_daily", **parameters)

    def fx_daily(
        self,
        *,
        ts_code: str,
        trade_date: date | datetime | str | None = None,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
    ) -> pd.DataFrame:
        if trade_date is not None and (start_date is not None or end_date is not None):
            raise TushareConfigError(
                "Use trade_date or a start/end range for fx_daily, not both"
            )

        parameters = _drop_none_values(
            dict(
                ts_code=ts_code,
                trade_date=_format_date(trade_date),
                start_date=_format_date(start_date),
                end_date=_format_date(end_date),
                fields=FX_DAILY_FIELDS,
            )
        )

        return self._call("fx_daily", **parameters)

    def _call(self, endpoint: str, **parameters: Any) -> pd.DataFrame:
        try:
            endpoint_method = getattr(self._api, endpoint)
        except AttributeError:
            raise TushareConfigError(
                "The installed Tushare SDK does not provide endpoint %s" % endpoint
            ) from None

        for attempt_number in range(1, self._max_attempts + 1):
            self._rate_limiter.acquire()
            try:
                result = endpoint_method(**parameters)
            except Exception as error:
                if _is_permission_error(error):
                    raise TushareConfigError(
                        "Tushare denied access to endpoint %s (token/points)" % endpoint
                    ) from None

                if not _is_transient_error(error):
                    raise TushareDataError(
                        "Tushare endpoint %s rejected the request" % endpoint
                    ) from None

                if attempt_number == self._max_attempts:
                    raise TushareTransientError(
                        "Tushare endpoint %s failed after %d transient attempts"
                        % (endpoint, self._max_attempts)
                    ) from None

                retry_delay = self._base_retry_delay_seconds * (
                    2 ** (attempt_number - 1)
                )
                self._sleep(retry_delay)
                continue

            if not isinstance(result, pd.DataFrame):
                raise TushareDataError(
                    "Tushare endpoint %s did not return a DataFrame" % endpoint
                )

            return result.copy()

        raise AssertionError("unreachable")


def _resolve_token() -> str:
    token = os.environ.get(TOKEN_ENVIRONMENT_VARIABLE)
    if token is not None and token.strip():
        return token.strip()

    from sysdata.config.private_config import get_private_config_as_dict

    token = get_private_config_as_dict().get(TOKEN_PRIVATE_CONFIG_KEY)
    if token is not None and str(token).strip():
        return str(token).strip()

    raise TushareConfigError(
        "Set TUSHARE_TOKEN (or private_config key '%s') before constructing "
        "the Tushare client" % TOKEN_PRIVATE_CONFIG_KEY
    )


def _create_tushare_api() -> Any:
    resolved_token = _resolve_token()

    try:
        tushare_sdk = importlib.import_module("tushare")
    except ImportError:
        raise TushareConfigError(
            "Install the optional Tushare dependency to use this data source"
        ) from None

    try:
        return tushare_sdk.pro_api(resolved_token)
    except Exception:
        raise TushareConfigError(
            "The Tushare SDK could not initialise its read-only API client"
        ) from None


def _format_date(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")

    value_as_string = str(value).strip()
    try:
        parsed = datetime.strptime(value_as_string, "%Y%m%d")
    except ValueError:
        raise TushareConfigError("Tushare dates must use YYYYMMDD format") from None

    return parsed.strftime("%Y%m%d")


def _drop_none_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _is_permission_error(error: Exception) -> bool:
    status_code = _response_status_code(error)
    if status_code is not None:
        return status_code in (401, 403)

    message = str(error).lower()
    permission_markers = (
        "permission",
        "unauthor",
        "forbidden",
        "invalid token",
        "token is invalid",
        "权限",
        "积分",
        "401",
        "403",
    )

    return any(marker in message for marker in permission_markers)


def _is_transient_error(error: Exception) -> bool:
    status_code = _response_status_code(error)
    if status_code is not None:
        return status_code in (408, 429) or 500 <= status_code <= 599

    if isinstance(error, (TimeoutError, ConnectionError)):
        return True

    class_names = {error_class.__name__.lower() for error_class in type(error).__mro__}
    if any(
        marker in class_name
        for class_name in class_names
        for marker in ("timeout", "connectionerror", "ratelimit")
    ):
        return True

    message = str(error).lower()
    transient_markers = (
        "timed out",
        "timeout",
        "connection",
        "temporarily unavailable",
        "temporary failure",
        "too many requests",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
        "internal server error",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
    )

    return any(marker in message for marker in transient_markers)


def _response_status_code(error: Exception) -> int | None:
    """Return an HTTP status without importing a particular HTTP client."""

    try:
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        return None if status_code is None else int(status_code)
    except (TypeError, ValueError):
        return None
