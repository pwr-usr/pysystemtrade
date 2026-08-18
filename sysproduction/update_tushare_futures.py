"""Daily update and shared workflow helpers for Tushare Chinese futures.

Tushare is a read-only historical source.  MongoDB holds mutable contract
state (exact expiries, sampling flags); Parquet holds canonical price series.
Tushare provides daily bars only, so the merged (MIXED) series per contract is
simply a copy of the daily series.

The full-history bootstrap lives in
``sysinit.futures.seed_price_data_from_tushare`` and reuses the helpers here.
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from syscore.dateutils import DAILY_PRICE_FREQ
from syscore.pandas.merge_data_keeping_past_data import SPIKE_IN_DATA, mergeError
from sysdata.csv.csv_instrument_data import csvFuturesInstrumentData
from sysdata.csv.csv_spread_costs import csvSpreadCostData
from sysdata.data_blob import dataBlob
from sysdata.tools.cleaner import apply_price_cleaning, get_config_for_price_filtering
from sysdata.tushare.client import (
    TOKEN_ENVIRONMENT_VARIABLE,
    TOKEN_PRIVATE_CONFIG_KEY,
    TushareClient,
)
from sysdata.tushare.errors import (
    TushareTransientError,
    TushareTruncationError,
)
from sysdata.tushare.source import (
    CNHUSD_EARLIEST_DATE,
    CNHUSD_SOURCE_CODE,
    HistoricalFuturesContract,
    TushareFuturesPriceSource,
)
from sysdata.tushare.transforms import (
    CANONICAL_PRICE_COLUMNS,
    NOTIONAL_DAILY_CLOSE_HOUR,
)
from sysobjects.contracts import futuresContract
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysproduction.data.contracts import dataContracts
from sysproduction.data.currency_data import dataCurrency
from sysproduction.data.prices import diagPrices, updatePrices

DEFAULT_OVERLAP_DAYS = 7
PROGRESS_EVERY_VENDOR_CONTRACTS = 200


class _HistoricalPriceMutationError(RuntimeError):
    """A provider revision would change already-stored contract history."""


class _PriceSpikeError(RuntimeError):
    """The repository spike guard rejected one contract update."""


@dataclass(frozen=True)
class ContractFailure:
    instrument_code: str
    contract_date: str
    external_code: str
    reason: str


@dataclass
class TushareRunResult:
    """Outcome counts; contract counts are for internal contract records."""

    vendor_contracts_seen: int = 0
    contracts_seen: int = 0
    contracts_written: int = 0
    contracts_skipped_complete: int = 0
    contracts_repaired: int = 0
    contracts_not_yet_listed: int = 0
    no_data: list[str] = field(default_factory=list)
    failures: list[ContractFailure] = field(default_factory=list)
    rows_written: int = 0
    fx_rows_added: int = 0

    @property
    def okay(self) -> bool:
        return not self.failures

    def add_failure(
        self, record: HistoricalFuturesContract, error: BaseException | str
    ) -> None:
        self.failures.append(
            ContractFailure(
                instrument_code=record.instrument_code,
                contract_date=record.contract_date,
                external_code=record.external_contract_code,
                reason=safe_tushare_error_text(error),
            )
        )

    def summary(self) -> str:
        return (
            f"vendor={self.vendor_contracts_seen} internal={self.contracts_seen} "
            f"written={self.contracts_written} "
            f"skipped_complete={self.contracts_skipped_complete} "
            f"repaired={self.contracts_repaired} "
            f"not_yet_listed={self.contracts_not_yet_listed} "
            f"rows={self.rows_written} no_data={len(self.no_data)} "
            f"failures={len(self.failures)} fx_rows={self.fx_rows_added}"
        )


def validate_tushare_csv_configuration(source: TushareFuturesPriceSource) -> None:
    """Require one instrument-config and one spread-cost row per instrument."""

    configured_instruments = set(source.configured_instrument_codes())

    instrument_frame = pd.read_csv(csvFuturesInstrumentData().config_file)
    _require_one_row_per_instrument(
        instrument_frame, configured_instruments, "instrument configuration"
    )
    configured_rows = instrument_frame[
        instrument_frame["Instrument"].isin(configured_instruments)
    ]
    point_sizes = pd.to_numeric(configured_rows["Pointsize"], errors="coerce")
    if point_sizes.isna().any() or (point_sizes <= 0.0).any():
        raise RuntimeError("Tushare point sizes must be positive and numeric")

    spread_frame = pd.read_csv(csvSpreadCostData().config_file)
    _require_one_row_per_instrument(
        spread_frame, configured_instruments, "spread configuration"
    )
    configured_spreads = spread_frame[
        spread_frame["Instrument"].isin(configured_instruments)
    ]
    spread_costs = pd.to_numeric(configured_spreads["SpreadCost"], errors="coerce")
    if spread_costs.isna().any() or (spread_costs <= 0.0).any():
        raise RuntimeError("Tushare spread costs must be positive and numeric")


def _require_one_row_per_instrument(
    frame: pd.DataFrame, instruments: set[str], description: str
) -> None:
    counts = frame["Instrument"].value_counts()
    invalid = sorted(
        instrument for instrument in instruments if int(counts.get(instrument, 0)) != 1
    )
    if invalid:
        raise RuntimeError(
            f"Tushare {description} must map exactly once: " + ", ".join(invalid)
        )


def _get_tushare_price_cleaning_config(data: dataBlob):
    """
    Retain repository cleaning policy except its wall-clock future check.

    Tushare requests are already bounded by ``as_of`` trading date, while daily
    rows are deliberately timestamped at 23:00.  A run earlier that evening
    must therefore not discard an already-complete Chinese trading day.
    """

    return get_config_for_price_filtering(data)._replace(ignore_future_prices=False)


def seed_tushare_futures(
    data: dataBlob,
    source: TushareFuturesPriceSource,
    records: Iterable[HistoricalFuturesContract] | None = None,
    resume: bool = True,
    as_of: datetime.date | None = None,
    update_fx: bool = True,
    report_progress: bool = True,
) -> TushareRunResult:
    """Seed all records, using one provider request per external contract code."""

    if records is None:
        records = source.fetch_contract_catalogue()
        validate_tushare_csv_configuration(source)
    record_list = list(records)
    groups = _group_records_by_external_code(record_list)
    result = TushareRunResult(
        vendor_contracts_seen=len(groups), contracts_seen=len(record_list)
    )
    as_of = as_of or datetime.date.today()

    contracts = dataContracts(data)
    prices = diagPrices(data)
    updates = updatePrices(data)
    price_store = prices.db_futures_contract_price_data
    cleaning_config = _get_tushare_price_cleaning_config(data)
    started_at = time.monotonic()

    for group_number, group in enumerate(groups, start=1):
        pending: list[tuple[HistoricalFuturesContract, futuresContract]] = []
        for record in group:
            try:
                contract = _upsert_contract_record(contracts, record, as_of)
                checkpoint = (
                    _resume_checkpoint(
                        price_store, updates, contract, record, as_of, result
                    )
                    if resume
                    else None
                )
                if checkpoint is not None:
                    continue
                if _effective_start(record) > as_of:
                    result.contracts_not_yet_listed += 1
                else:
                    pending.append((record, contract))
            except Exception as error:
                _raise_if_fatal_tushare_error(error, data.log)
                result.add_failure(record, error)

        if pending:
            pending_records = [record for record, _ in pending]
            try:
                downloaded = source.get_prices_for_external_contract(
                    pending_records,
                    frequency=DAILY_PRICE_FREQ,
                    end_date=as_of,
                )
            except Exception as error:
                _raise_if_fatal_tushare_error(error, data.log)
                for record in pending_records:
                    result.add_failure(record, error)
            else:
                for record, contract in pending:
                    try:
                        contract_prices = apply_price_cleaning(
                            data=data,
                            broker_prices_raw=downloaded[record],
                            cleaning_config=cleaning_config,
                            daily_data=True,
                        )
                        if len(contract_prices) == 0:
                            result.no_data.append(contract.key)
                            continue
                        updates.overwrite_prices_at_frequency_for_contract(
                            contract,
                            contract_prices,
                            frequency=DAILY_PRICE_FREQ,
                        )
                        updates.overwrite_merged_prices_for_contract(
                            contract, contract_prices
                        )
                        result.contracts_written += 1
                        result.rows_written += len(contract_prices)
                    except Exception as error:
                        _raise_if_fatal_tushare_error(error, data.log)
                        result.add_failure(record, error)

        if report_progress and (
            group_number % PROGRESS_EVERY_VENDOR_CONTRACTS == 0
            or group_number == len(groups)
        ):
            _print_seed_progress(group_number, len(groups), result, started_at)

    _update_fx_and_capture_failure(result, data, source, as_of, update_fx)
    return result


def update_tushare_futures(
    data: dataBlob,
    source: TushareFuturesPriceSource,
    records: Iterable[HistoricalFuturesContract] | None = None,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
    as_of: datetime.date | None = None,
    update_fx: bool = True,
) -> TushareRunResult:
    """Refresh contract metadata and append all currently listed contracts."""

    if overlap_days < 0:
        raise ValueError("overlap_days must be non-negative")

    unmapped_families: tuple[tuple[str, str], ...] = ()
    if records is None:
        catalogue = source.fetch_contract_catalogue_result()
        record_list = list(catalogue.contracts)
        unmapped_families = catalogue.unmapped_families
        validate_tushare_csv_configuration(source)
    else:
        record_list = list(records)
    as_of = as_of or datetime.date.today()
    result = TushareRunResult(
        vendor_contracts_seen=len(
            {record.external_contract_code for record in record_list}
        ),
        contracts_seen=len(record_list),
    )
    for exchange, fut_code in unmapped_families:
        result.failures.append(
            ContractFailure(
                instrument_code=f"{exchange}_{fut_code}",
                contract_date="",
                external_code="",
                reason=(
                    f"Unmapped Tushare family {exchange}/{fut_code}; "
                    "known families were updated"
                ),
            )
        )

    contracts = dataContracts(data)
    price_store = diagPrices(data).db_futures_contract_price_data
    updates = updatePrices(data)
    cleaning_config = _get_tushare_price_cleaning_config(data)

    contracts_by_record: dict[HistoricalFuturesContract, futuresContract] = {}
    for record in record_list:
        try:
            contract = _upsert_contract_record(contracts, record, as_of)
            if _is_sampling(record, as_of):
                contracts_by_record[record] = contract
        except Exception as error:
            _raise_if_fatal_tushare_error(error, data.log)
            result.add_failure(record, error)

    active_records = [record for record in record_list if record in contracts_by_record]
    for record_group in _group_records_by_external_code(active_records):
        group: list[tuple[HistoricalFuturesContract, futuresContract]] = []
        starts: dict[HistoricalFuturesContract, datetime.date] = {}
        old_by_record: dict[HistoricalFuturesContract, futuresContractPrices] = {}
        for record in record_group:
            contract = contracts_by_record[record]
            try:
                old = price_store.get_prices_at_frequency_for_contract_object(
                    contract, frequency=DAILY_PRICE_FREQ
                )
            except Exception as error:
                _raise_if_fatal_tushare_error(error, data.log)
                result.add_failure(record, error)
                continue
            old_by_record[record] = old
            overlap_start = (
                old.index.max().date() - datetime.timedelta(days=overlap_days)
                if len(old)
                else _effective_start(record)
            )
            starts[record] = max(overlap_start, _effective_start(record))
            group.append((record, contract))

        if not group:
            continue
        group_records = [record for record, _ in group]
        try:
            downloaded = source.get_prices_for_external_contract(
                group_records,
                frequency=DAILY_PRICE_FREQ,
                start_date=min(starts.values()),
                end_date=as_of,
            )
        except Exception as error:
            _raise_if_fatal_tushare_error(error, data.log)
            for record in group_records:
                result.add_failure(record, error)
            continue

        for record, contract in group:
            try:
                new = apply_price_cleaning(
                    data=data,
                    broker_prices_raw=downloaded[record],
                    cleaning_config=cleaning_config,
                    daily_data=True,
                )
                corrections = historical_price_correction_dates(
                    old_by_record[record],
                    new,
                    comparison_start=starts[record],
                )
                if corrections:
                    raise _HistoricalPriceMutationError(
                        "Tushare changed stored history on "
                        + ",".join(value.isoformat() for value in corrections[:10])
                        + "; re-seed this contract to accept the new history"
                    )
                if len(new) == 0:
                    result.no_data.append(contract.key)
                    continue
                rows_added = updates.update_prices_at_frequency_for_contract(
                    contract_object=contract,
                    frequency=DAILY_PRICE_FREQ,
                    new_prices=new,
                    check_for_spike=True,
                    max_price_spike=cleaning_config.max_price_spike,
                )
                if rows_added is SPIKE_IN_DATA:
                    raise _PriceSpikeError("price spike check failed")
                complete_daily = (
                    price_store.get_prices_at_frequency_for_contract_object(
                        contract,
                        frequency=DAILY_PRICE_FREQ,
                        return_empty=False,
                    )
                )
                updates.overwrite_merged_prices_for_contract(contract, complete_daily)
                result.rows_written += int(rows_added)
                result.contracts_written += 1
            except Exception as error:
                _raise_if_fatal_tushare_error(error, data.log)
                result.add_failure(record, error)

    _update_fx_and_capture_failure(result, data, source, as_of, update_fx)
    data.log.debug("Tushare update: " + result.summary())
    return result


def update_tushare_cnhusd(
    data: dataBlob,
    source: TushareFuturesPriceSource,
    as_of: datetime.date | None = None,
    full_backfill_start: datetime.date = CNHUSD_EARLIEST_DATE,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
) -> int:
    """Backfill or overlap-update inverse-midpoint CNHUSD prices."""

    if overlap_days < 0:
        raise ValueError("overlap_days must be non-negative")

    as_of = as_of or datetime.date.today()
    currency_data = dataCurrency(data)
    existing = currency_data.get_fx_prices("CNHUSD")
    start_date = (
        max(
            full_backfill_start,
            existing.index.max().date() - datetime.timedelta(days=overlap_days),
        )
        if len(existing)
        else full_backfill_start
    )
    new_prices = source.get_cnhusd_prices(start_date=start_date, end_date=as_of)
    corrections = historical_price_correction_dates(
        existing,
        new_prices,
        comparison_start=start_date,
    )
    if corrections:
        raise _HistoricalPriceMutationError(
            "Tushare changed stored CNHUSD history on "
            + ",".join(value.isoformat() for value in corrections[:10])
            + "; run a reviewed full FX replacement to accept the new history"
        )
    rows_added = currency_data.update_fx_prices_and_return_rows_added(
        "CNHUSD", new_prices, check_for_spike=True
    )
    if rows_added is SPIKE_IN_DATA:
        raise _PriceSpikeError("CNHUSD price spike check failed")
    return int(rows_added)


def historical_price_correction_dates(
    old_prices: pd.Series | pd.DataFrame,
    downloaded_prices: pd.Series | pd.DataFrame,
    comparison_start: datetime.date | None = None,
) -> list[datetime.date]:
    """Dates where the vendor download disagrees with already-stored history."""

    if len(old_prices) == 0:
        return []

    old_last = old_prices.index.max()
    historical = (
        downloaded_prices
        if len(downloaded_prices) == 0
        else downloaded_prices[downloaded_prices.index <= old_last]
    )
    correction_dates = (
        []
        if len(historical) == 0
        else list(historical.index.difference(old_prices.index).date)
    )
    if comparison_start is not None:
        stored_overlap = old_prices[
            (old_prices.index.date >= comparison_start) & (old_prices.index <= old_last)
        ]
        correction_dates.extend(stored_overlap.index.difference(historical.index).date)
    common_dates = (
        old_prices.index[:0]
        if len(historical) == 0
        else historical.index.intersection(old_prices.index)
    )
    if len(common_dates):
        old_common = pd.DataFrame(old_prices.loc[common_dates]).astype(float)
        new_common = pd.DataFrame(downloaded_prices.loc[common_dates]).astype(float)
        equal = np.isclose(
            old_common.to_numpy(),
            new_common.to_numpy(),
            rtol=1e-10,
            atol=1e-12,
            equal_nan=True,
        ).all(axis=1)
        correction_dates.extend(common_dates[~equal].date)
    return sorted(set(correction_dates))


def safe_tushare_error_text(error: BaseException | str) -> str:
    """Format an error while removing any active Tushare credential."""

    text = str(error)
    for token in _candidate_tokens():
        text = text.replace(token, "<redacted>")
    return text


def _candidate_tokens() -> set[str]:
    tokens = set()
    env_token = os.environ.get(TOKEN_ENVIRONMENT_VARIABLE, "").strip()
    if env_token:
        tokens.add(env_token)
    try:
        from sysdata.config.private_config import get_private_config_as_dict

        private_token = str(
            get_private_config_as_dict().get(TOKEN_PRIVATE_CONFIG_KEY) or ""
        ).strip()
        if private_token:
            tokens.add(private_token)
    except Exception:
        pass
    return tokens


def _upsert_contract_record(
    contract_data: dataContracts,
    record: HistoricalFuturesContract,
    as_of: datetime.date,
) -> futuresContract:
    desired = record.as_futures_contract()
    if _is_sampling(record, as_of):
        desired.sampling_on()
    else:
        desired.sampling_off()

    if contract_data.is_contract_in_data(desired):
        existing = contract_data.get_contract_from_db(desired)
        if (
            existing.expiry_date == desired.expiry_date
            and existing.currently_sampling == desired.currently_sampling
        ):
            return existing
    contract_data.add_contract_data(desired, ignore_duplication=True)
    return desired


def _resume_checkpoint(
    price_store,
    updates: updatePrices,
    contract: futuresContract,
    record: HistoricalFuturesContract,
    as_of: datetime.date,
    result: TushareRunResult,
) -> str | None:
    """Skip a contract whose stored prices already form a valid checkpoint.

    Returns "skipped_complete", "repaired" (merged series re-derived from the
    daily series) or None when the contract must be downloaded.
    """

    # A seed is also a refresh for contracts whose effective trading window is
    # still open.  Only expired provider records are immutable checkpoints.
    if as_of <= _effective_end(record):
        return None

    has_daily = price_store.has_price_data_for_contract_at_frequency(
        contract, DAILY_PRICE_FREQ
    )
    if not has_daily:
        return None
    try:
        daily = price_store.get_prices_at_frequency_for_contract_object(
            contract, frequency=DAILY_PRICE_FREQ, return_empty=False
        )
        _validate_checkpoint(daily, contract, record=record, as_of=as_of)
    except Exception:
        return None

    if price_store.has_merged_price_data_for_contract(contract):
        try:
            mixed = price_store.get_merged_prices_for_contract_object(
                contract, return_empty=False
            )
            _validate_checkpoint(mixed, contract, record=record, as_of=as_of)
            if pd.DataFrame(daily).equals(pd.DataFrame(mixed)):
                result.contracts_skipped_complete += 1
                return "skipped_complete"
        except Exception:
            pass

    updates.overwrite_merged_prices_for_contract(contract, daily)
    result.contracts_repaired += 1
    result.rows_written += len(daily)
    return "repaired"


def _validate_checkpoint(
    prices: futuresContractPrices,
    contract: futuresContract,
    *,
    record: HistoricalFuturesContract,
    as_of: datetime.date,
) -> None:
    if len(prices) == 0:
        raise RuntimeError(f"Stored checkpoint for {contract.key} is empty")
    if tuple(prices.columns) != CANONICAL_PRICE_COLUMNS:
        raise RuntimeError(
            f"Stored checkpoint for {contract.key} has non-canonical columns"
        )
    if not isinstance(prices.index, pd.DatetimeIndex) or prices.index.tz is not None:
        raise RuntimeError(
            f"Stored checkpoint for {contract.key} has a non-canonical index"
        )
    if prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise RuntimeError(
            f"Stored checkpoint for {contract.key} is unsorted or duplicated"
        )
    expected_timestamps = prices.index.normalize() + pd.Timedelta(
        hours=NOTIONAL_DAILY_CLOSE_HOUR
    )
    if not (prices.index == expected_timestamps).all():
        raise RuntimeError(
            f"Stored checkpoint for {contract.key} is not normalized to 23:00"
        )

    first_date = prices.index[0].date()
    last_date = prices.index[-1].date()
    latest_allowed_date = min(_effective_end(record), as_of)
    if first_date < _effective_start(record) or last_date > latest_allowed_date:
        raise RuntimeError(
            f"Stored checkpoint for {contract.key} is outside its effective window"
        )


def _effective_start(record: HistoricalFuturesContract) -> datetime.date:
    return record.price_start_date or record.first_trade_date


def _effective_end(record: HistoricalFuturesContract) -> datetime.date:
    return record.price_end_date or record.expiry_date


def _is_sampling(record: HistoricalFuturesContract, as_of: datetime.date) -> bool:
    return _effective_start(record) <= as_of <= _effective_end(record)


def _group_records_by_external_code(
    records: Sequence[HistoricalFuturesContract],
) -> list[list[HistoricalFuturesContract]]:
    groups: dict[str, list[HistoricalFuturesContract]] = defaultdict(list)
    for record in records:
        groups[record.external_contract_code].append(record)
    return list(groups.values())


def _print_seed_progress(
    done: int, total: int, result: TushareRunResult, started_at: float
) -> None:
    elapsed_minutes = (time.monotonic() - started_at) / 60.0
    rate = done / elapsed_minutes if elapsed_minutes > 0 else 0.0
    print(
        f"{done}/{total} vendor contracts | "
        f"written {result.contracts_written} "
        f"skipped {result.contracts_skipped_complete} "
        f"repaired {result.contracts_repaired} "
        f"no_data {len(result.no_data)} "
        f"failed {len(result.failures)} | "
        f"{rate:.0f}/min elapsed {elapsed_minutes:.0f}m",
        flush=True,
    )


def _update_fx_and_capture_failure(
    result: TushareRunResult,
    data: dataBlob,
    source: TushareFuturesPriceSource,
    as_of: datetime.date,
    update_fx: bool,
) -> None:
    if not update_fx:
        return
    try:
        result.fx_rows_added = update_tushare_cnhusd(data, source, as_of)
    except Exception as error:
        _raise_if_fatal_tushare_error(error, data.log)
        result.failures.append(
            ContractFailure(
                instrument_code="CNHUSD",
                contract_date="",
                external_code=CNHUSD_SOURCE_CODE,
                reason=safe_tushare_error_text(error),
            )
        )


def _raise_if_fatal_tushare_error(error: BaseException, log=None) -> None:
    """Continue isolated failures, but stop systemic storage/source failures."""

    isolated_errors = (
        TushareTransientError,
        TushareTruncationError,
        _HistoricalPriceMutationError,
        _PriceSpikeError,
        mergeError,
    )
    if isinstance(error, isolated_errors):
        return

    if log is not None:
        log.critical(
            "Aborting Tushare workflow after fatal error: "
            + safe_tushare_error_text(error)
        )
    raise error


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlap-days", type=int, default=DEFAULT_OVERLAP_DAYS)
    parser.add_argument("--skip-fx", action="store_true")
    arguments = parser.parse_args()

    run_data = dataBlob(log_name="update_tushare_futures")
    run_source = TushareFuturesPriceSource(TushareClient())
    run_result = update_tushare_futures(
        data=run_data,
        source=run_source,
        overlap_days=arguments.overlap_days,
        update_fx=not arguments.skip_fx,
    )
    print(run_result.summary())
    for failure in run_result.failures[:20]:
        print(
            f"FAILED {failure.instrument_code}/{failure.contract_date} "
            f"{failure.external_code}: {failure.reason}"
        )
    sys.exit(0 if run_result.okay else 1)
