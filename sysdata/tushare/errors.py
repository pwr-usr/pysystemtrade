"""Failures raised by the Tushare historical-data source.

Workflow rule of thumb: ``TushareTransientError`` and ``TushareTruncationError``
affect a single contract and let a bulk run continue; every other error is
systemic (bad config, bad token, provider schema drift) and should abort.
"""


class TushareError(Exception):
    """Base class for all Tushare source errors."""


class TushareConfigError(TushareError):
    """Local configuration is invalid: token, SDK, manifest, coverage, params."""


class TushareDataError(TushareError):
    """Provider data does not match its documented schema or identity."""


class TushareTransientError(TushareError):
    """A transient provider failure remained after bounded retries."""


class TushareTruncationError(TushareDataError):
    """A response hit the provider row limit and may be incomplete."""
