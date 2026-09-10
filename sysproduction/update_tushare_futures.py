"""Download Tushare daily prices and build the native continuous price stores.

One entry point handles a new store, daily overlap updates and a dated repair.
Raw prices stay complete; research eligibility belongs to sim data.
"""

import datetime
import hashlib
import json
import os
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from syscore.constants import arg_not_supplied
from syscore.dateutils import DAILY_PRICE_FREQ
from syscore.fileutils import resolve_path_and_filename_for_package
from syscore.pandas.merge_data_keeping_past_data import SPIKE_IN_DATA, mergeError
from sysdata.csv.csv_instrument_data import csvFuturesInstrumentData
from sysdata.csv.csv_roll_calendars import csvRollCalendarData
from sysdata.csv.csv_roll_parameters import csvRollParametersData
from sysdata.csv.csv_spread_costs import csvSpreadCostData
from sysdata.data_blob import dataBlob
from sysdata.tools.cleaner import apply_price_cleaning, get_config_for_price_filtering
from sysdata.tushare.errors import TushareTransientError, TushareTruncationError
from sysdata.tushare.source import (
    tushareFuturesContractData,
    tushareFuturesContractPriceData,
    tushareFxPricesData,
)
from sysobjects.adjusted_prices import futuresAdjustedPrices
from sysobjects.contract_dates_and_expiries import contractDate
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysobjects.multiple_prices import futuresMultiplePrices
from sysobjects.roll_calendars import rollCalendar
from sysobjects.roll_parameters_with_price_data import (
    contractWithRollParametersAndPrices,
)
from sysobjects.rolls import contractDateWithRollParameters
from sysproduction.data.contracts import dataContracts
from sysproduction.data.currency_data import dataCurrency
from sysproduction.data.prices import diagPrices, updatePrices
from sysproduction.data.production_data_objects import (
    FUTURES_CONTRACT_PRICE_DATA,
    get_class_for_data_type,
)

DEFAULT_OVERLAP_DAYS = 7


def update_tushare_futures(
    instrument_code=None,
    start_date=None,
    end_date=None,
    dry_run=False,
    data=arg_not_supplied,
    *,
    update_fx=True,
    overlap_days=DEFAULT_OVERLAP_DAYS,
    rebuild=True,
):
    """Return one summary row per instrument. Dates accept ISO strings.

    An explicit start_date repairs that interval, with a recovery copy and
    field-level revision log. Otherwise historical changes await review.
    dry_run reads the catalogue, publication probe and stores without importing prices.
    Inject a dataBlob to select storage and the native Tushare connection.
    """
    with (
        dataBlob(log_name="update_tushare_futures")
        if data is arg_not_supplied
        else nullcontext(data)
    ) as data:
        if overlap_days < 0:
            raise ValueError("overlap_days must be non-negative")
        data.add_class_object(tushareFuturesContractData)
        data.add_class_object(tushareFuturesContractPriceData)
        source_contracts = data.tushare_futures_contract
        source_prices = data.tushare_futures_contract_price
        source = data.tushare_connection
        catalogue = source.fetch_contract_catalogue_result(refresh=True)
        records = list(catalogue.contracts)
        validate_tushare_csv_configuration(source)
        if not records:
            raise ValueError("Tushare 合约目录为空")
        if instrument_code:
            records = [r for r in records if r.instrument_code == instrument_code]
            if not records:
                raise ValueError("Unknown Tushare instrument: " + instrument_code)
        last = (
            pd.Timestamp(end_date).date() if end_date else _latest_date(source, records)
        )
        first = pd.Timestamp(start_date).date() if start_date else None
        if first and first > last:
            raise ValueError("start_date must not follow end_date")
        data.add_class_object(get_class_for_data_type(FUTURES_CONTRACT_PRICE_DATA))
        store = data.db_futures_contract_price
        if not dry_run:
            updates, contracts = updatePrices(data), dataContracts(data)
        root = Path(data.parquet_root_directory).parent / "tushare_updates"
        state_file = root / "request_coverage.jsonl"
        state = (
            dict(json.loads(line) for line in state_file.read_text().splitlines())
            if state_file.exists()
            else {}
        )
        run = root / pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%S%fZ")
        cleaning = _get_tushare_price_cleaning_config(data)
        details, summaries = [], []
        stitchable = (
            {m.instrument_code for m in source.manifest.mappings if m.is_stitchable}
            if hasattr(source, "manifest")
            else {r.instrument_code for r in records}
        )
        for code in sorted({r.instrument_code for r in records}):
            failed = False
            for record in [r for r in records if r.instrument_code == code]:
                contract = source_contracts.get_contract_object(
                    code, record.contract_date
                )
                active_first = record.price_start_date or record.first_trade_date
                active_last = record.price_end_date or record.expiry_date
                contract.sampling_on() if active_first <= last <= active_last else contract.sampling_off()
                old = store.get_prices_at_frequency_for_contract_object(
                    contract, frequency=DAILY_PRICE_FREQ
                )
                request_first, request_last = _request_window(
                    record, old, state.get(contract.key), first, last, overlap_days
                )
                row = dict(
                    instrument=code,
                    contract=record.contract_date,
                    start=request_first,
                    end=request_last,
                    rows_added=0,
                    revisions=0,
                    status="complete",
                    reason="",
                )
                if request_first > request_last:
                    if not dry_run:
                        run.mkdir(parents=True, exist_ok=True)
                        merged = store.get_merged_prices_for_contract_object(contract)
                        if not pd.DataFrame(old).equals(pd.DataFrame(merged)):
                            metadata = (
                                contracts.get_contract_from_db(contract)
                                if contracts.is_contract_in_data(contract)
                                else None
                            )
                            _save_contract_backup(run, contract, old, store, metadata)
                            updates.overwrite_merged_prices_for_contract(contract, old)
                            row["status"] = "repaired_merged"
                        _upsert_contract_record(contracts, contract, backup=run)
                    details.append(row)
                    continue
                if dry_run:
                    row["status"] = "planned"
                    details.append(row)
                    continue
                run.mkdir(parents=True, exist_ok=True)
                try:
                    fetched = source_prices.get_prices_at_frequency_for_contract_object(
                        contract,
                        frequency=DAILY_PRICE_FREQ,
                        return_empty=False,
                        start_date=request_first,
                        end_date=request_last,
                    )
                    new = apply_price_cleaning(
                        data, fetched, cleaning_config=cleaning, daily_data=True
                    )
                    _validate_prices(new, request_first, request_last)
                    if (
                        not len(new)
                        and len(old)
                        and (
                            (old.index.date >= request_first)
                            & (old.index.date <= request_last)
                        ).any()
                    ):
                        raise ValueError("供应方返回空数据，已保留请求区间的原始报价")
                    changes = _revision_rows(
                        old,
                        new,
                        code,
                        record.contract_date,
                        request_first,
                        request_last,
                    )
                    row["revisions"] = len(changes)
                    if changes and first is None:
                        raise ValueError(
                            "历史报价发生修订；检查 revisions.csv，再用 start_date 指定修复区间"
                        )
                    if first is None:
                        combined = old.add_rows_to_existing_data(
                            new,
                            check_for_spike=True,
                            max_price_spike=cleaning.max_price_spike,
                        )
                        if combined is SPIKE_IN_DATA:
                            raise ValueError("原生价格跳变检查失败，请检查下载报价")
                    else:
                        outside = (
                            old[
                                (old.index.date < request_first)
                                | (old.index.date > request_last)
                            ]
                            if len(old)
                            else old
                        )
                        combined = (
                            futuresContractPrices(
                                pd.concat([outside, new]).sort_index()
                            )
                            if len(outside)
                            else new
                        )
                    previous_metadata = (
                        contracts.get_contract_from_db(contract)
                        if contracts.is_contract_in_data(contract)
                        else None
                    )
                    merged = _save_contract_backup(
                        run, contract, old, store, previous_metadata
                    )
                    try:
                        updates.overwrite_prices_at_frequency_for_contract(
                            contract, combined, frequency=DAILY_PRICE_FREQ
                        )
                        updates.overwrite_merged_prices_for_contract(contract, combined)
                        _upsert_contract_record(contracts, contract)
                    except Exception:
                        updates.overwrite_prices_at_frequency_for_contract(
                            contract, old, frequency=DAILY_PRICE_FREQ
                        )
                        updates.overwrite_merged_prices_for_contract(contract, merged)
                        if previous_metadata is not None:
                            contracts.add_contract_data(
                                previous_metadata, ignore_duplication=True
                            )
                        raise
                    row.update(
                        rows_added=len(combined.index.difference(old.index)),
                        status="updated" if len(new) else "no_data",
                    )
                    previous = state.get(contract.key, {})
                    if previous.get("digest") != _price_digest(old):
                        previous = {}
                    covered_first = min(
                        request_first,
                        pd.Timestamp(previous.get("start", request_first)).date(),
                    )
                    # A receipt covers only contiguous requests and the exact stored content.
                    if previous and request_first > pd.Timestamp(
                        previous["end"]
                    ).date() + datetime.timedelta(days=1):
                        covered_first = request_first
                    state[contract.key] = dict(
                        start=str(covered_first),
                        end=str(request_last),
                        digest=_price_digest(combined),
                    )
                    with state_file.open("a") as receipt_file:
                        receipt_file.write(
                            json.dumps([contract.key, state[contract.key]]) + "\n"
                        )
                except (
                    ValueError,
                    TushareTransientError,
                    TushareTruncationError,
                    mergeError,
                ) as error:
                    row.update(status="failed", reason=safe_tushare_error_text(error))
                    failed = True
                except Exception as error:
                    data.log.critical(
                        "Tushare fatal error: " + safe_tushare_error_text(error)
                    )
                    raise
                details.append(row)
                pd.DataFrame([row]).to_csv(
                    run / "contracts.csv",
                    mode="a",
                    header=not (run / "contracts.csv").exists(),
                    index=False,
                )
                pd.DataFrame(
                    changes if row["revisions"] else [],
                    columns=["instrument", "contract", "date", "field", "old", "new"],
                ).to_csv(
                    run / "revisions.csv",
                    mode="a",
                    header=not (run / "revisions.csv").exists(),
                    index=False,
                )
            selected = pd.DataFrame([r for r in details if r["instrument"] == code])
            summary = dict(
                instrument=code,
                cutoff=last,
                contracts=len(selected),
                rows_added=int(selected.rows_added.sum()),
                revisions=int(selected.revisions.sum()),
                status="planned" if dry_run else "failed" if failed else "updated",
                rolls_added=0,
                reason="; ".join(
                    selected.loc[selected.status.eq("failed"), "reason"].unique()
                ),
                backup="" if dry_run else str(run),
            )
            if rebuild and code in stitchable and not dry_run and not failed:
                try:
                    summary.update(
                        rebuild_tushare_prices(
                            code,
                            data=data,
                            end_date=last,
                            backup=run,
                            repair_start=first,
                        )
                    )
                except Exception as error:
                    summary.update(
                        status="retained_previous",
                        reason=safe_tushare_error_text(error),
                    )
            summaries.append(summary)
            print(code, summary["status"], "新增行", summary["rows_added"], flush=True)
        if update_fx and not dry_run and instrument_code is None:
            run.mkdir(parents=True, exist_ok=True)
            existing_fx = dataCurrency(data).get_fx_prices("CNHUSD")
            pd.DataFrame(existing_fx).to_parquet(run / "CNHUSD_before.parquet")
            try:
                update_tushare_cnhusd(data=data, as_of=last)
            except Exception as error:
                summaries.append(
                    dict(
                        instrument="CNHUSD",
                        status="failed",
                        reason=safe_tushare_error_text(error),
                    )
                )
        if instrument_code is None:
            summaries.extend(
                dict(
                    instrument=exchange + "_" + code,
                    status="failed",
                    reason="供应方新增品种尚未配置",
                )
                for exchange, code in catalogue.unmapped_families
            )
        result = pd.DataFrame(summaries).set_index("instrument")
        if not dry_run:
            run.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(details).to_csv(run / "contracts.csv", index=False)
            result.to_csv(run / "summary.csv")
        return result


def _request_window(record, old, receipt, first, last, overlap_days=7):
    earliest = record.price_start_date or record.first_trade_date
    latest = min(record.price_end_date or record.expiry_date, last)
    if first is not None:
        return max(first, earliest), latest
    valid_receipt = (
        receipt
        and receipt.get("digest") == _price_digest(old)
        and pd.Timestamp(receipt["start"]).date() <= earliest
    )
    if (
        valid_receipt
        and pd.Timestamp(receipt["end"]).date() >= latest
        and latest < last
    ):
        return latest + datetime.timedelta(days=1), latest
    # Old stored endpoints alone cannot prove coverage of an expired contract.
    if not valid_receipt:
        return earliest, latest
    start = (
        max(earliest, old.index.max().date() - datetime.timedelta(days=overlap_days))
        if len(old)
        else earliest
    )
    start = min(
        start,
        pd.Timestamp(receipt["end"]).date() - datetime.timedelta(days=overlap_days),
    )
    return max(start, earliest), latest


def _latest_date(source, records):
    now = pd.Timestamp.now(tz="Asia/Shanghai")
    bound = now.date() if now.hour >= 18 else (now - pd.Timedelta(days=1)).date()
    liquid = [
        r
        for r in records
        if r.instrument_code == "SHFE_RB"
        and r.first_trade_date <= bound <= r.expiry_date
    ]
    if not liquid:
        # A single-instrument update still probes a liquid market for publication.
        liquid = [
            r
            for r in source.fetch_contract_catalogue_result().contracts
            if r.instrument_code == "SHFE_RB"
            and r.first_trade_date <= bound <= r.expiry_date
        ]
    probe = min(liquid, key=lambda r: abs((r.expiry_date - bound).days - 120))
    recent = source.client.fut_daily(
        ts_code=probe.external_contract_code,
        start_date=(bound - datetime.timedelta(days=14)).strftime("%Y%m%d"),
        end_date=bound.strftime("%Y%m%d"),
    )
    if recent.empty:
        raise ValueError("近期交易日探针没有报价，请显式指定 end_date")
    return pd.to_datetime(recent.trade_date).max().date()


def rebuild_tushare_prices(
    instrument_code,
    data=arg_not_supplied,
    end_date=None,
    backup=None,
    repair_start=None,
):
    """Append verified calendar nodes; build both price series before any write."""
    from sysinit.futures.build_roll_calendars import (
        _create_approx_calendar_from_earliest_contract,
        adjust_to_price_series,
    )
    from sysinit.futures.multipleprices_from_db_prices_and_csv_calendars_to_db import (
        process_multiple_prices_single_instrument,
    )

    with (
        dataBlob(log_name="rebuild_tushare_prices")
        if data is arg_not_supplied
        else nullcontext(data)
    ) as data:
        prices = diagPrices(data)
        cutoff = (
            pd.Timestamp(end_date or datetime.date.today())
            + pd.Timedelta(days=1)
            - pd.Timedelta(nanoseconds=1)
        )
        episodes_file = (
            Path(data.parquet_root_directory).parent / "episode_boundaries.csv"
        )
        if not episodes_file.exists():
            episodes_file = Path(
                resolve_path_and_filename_for_package(
                    "data.futures.csvconfig", "instrument_price_episodes.csv"
                )
            )
        episodes_file = Path(
            data.config.get_element_or_default(
                "tushare_episode_boundaries_path", episodes_file
            )
        )
        episode_start, episode_calendar = None, None
        if episodes_file.exists():
            episodes = (
                pd.read_csv(episodes_file)
                .query("Instrument == @instrument_code")
                .sort_values("Start")
            )
            if len(episodes):
                modes = episodes.get(
                    "UpdateMode", pd.Series("closed", index=episodes.index)
                ).fillna("closed")
                if (
                    not modes.isin(["closed", "live", "manual"]).all()
                    or modes.iloc[:-1].eq("live").any()
                ):
                    raise ValueError("仅最后一个独立历史段可以标记live")
                if modes.iloc[-1] != "live":
                    if (
                        prices.get_multiple_prices(instrument_code).empty
                        or prices.get_adjusted_prices(instrument_code).empty
                    ):
                        return dict(
                            status="failed",
                            rolls_added=0,
                            reason="缺少已审核独立段的multiple或adjusted价格；请成套导入审核版本",
                        )
                    return dict(
                        status="retained_manual_calendar"
                        if modes.iloc[-1] == "manual"
                        else "retained_reviewed_episodes",
                        rolls_added=0,
                        reason="已保留审核历史和人工合约链；更新边界或换月前需要复核",
                    )
                last_episode = episodes.iloc[-1]
                if pd.isna(last_episode.get("Calendar")):
                    raise ValueError("live历史段需要指定已审核Calendar")
                episode_start = pd.Timestamp(last_episode.Start).normalize()
                episode_calendar = Path(last_episode.Calendar)
                if not episode_calendar.is_absolute():
                    episode_calendar = episodes_file.parent / episode_calendar
        calendars = csvRollCalendarData(
            str(episode_calendar.parent)
            if episode_calendar
            else data.config.get_element_or_default(
                "tushare_roll_calendar_path", arg_not_supplied
            )
        )
        calendar_key = episode_calendar.stem if episode_calendar else instrument_code
        old_calendar = calendars.get_roll_calendar(calendar_key)
        if old_calendar.empty:
            raise ValueError("需要先审核并保存该品种换月日历")
        old_multiple_full = prices.get_multiple_prices(instrument_code)
        old_adjusted_full = prices.get_adjusted_prices(instrument_code)
        old_multiple = (
            old_multiple_full.loc[episode_start:].copy()
            if episode_start is not None
            else old_multiple_full.copy()
        )
        for name in ["PRICE_CONTRACT", "FORWARD_CONTRACT", "CARRY_CONTRACT"]:
            old_multiple[name] = (
                old_multiple[name].astype(str).str.replace(r"\.0$", "", regex=True)
            )
        old_adjusted = (
            old_adjusted_full.loc[episode_start:]
            if episode_start is not None
            else old_adjusted_full
        )
        if episode_start is not None and (
            old_multiple.empty or old_multiple.index.min().normalize() != episode_start
        ):
            raise ValueError("live历史段Start必须匹配已审核价格起点")
        raw = prices.db_futures_contract_price_data.get_merged_prices_for_instrument(
            instrument_code
        ).final_prices()
        raw = type(raw)(
            {
                key: value.loc[:cutoff]
                for key, value in raw.items()
                if len(value.loc[:cutoff])
            }
        )
        parameters = csvRollParametersData(
            datapath=data.config.get_element_or_default(
                "tushare_roll_parameters_path", arg_not_supplied
            )
        ).get_roll_parameters(instrument_code)
        candidate = old_calendar.copy()
        anchor = str(old_calendar.next_contract.iloc[-1])
        contract = contractWithRollParametersAndPrices(
            contractDateWithRollParameters(contractDate(anchor), parameters), raw
        )
        contract.update_expiry_with_offset_from_parameters()
        log = StringIO()
        if (
            contract.desired_roll_date <= cutoff
            and anchor < raw.last_contract_date_str()
        ):
            with redirect_stdout(log):
                approximate = _create_approx_calendar_from_earliest_contract(contract)
                planned = approximate.loc[approximate.index <= cutoff]
                following = approximate.loc[approximate.index > cutoff].head(1)
                tail = adjust_to_price_series(
                    pd.concat([old_calendar.iloc[[-1]], planned, following]), raw
                )
            tail = tail.loc[
                (tail.index > old_calendar.index[-1])
                & (tail.index <= cutoff)
                & tail.current_contract.astype(str).isin(
                    planned.current_contract.astype(str)
                )
            ]
            for actual_date, row in tail.iterrows():
                expected = planned.index[
                    planned.current_contract.astype(str).eq(str(row.current_contract))
                ][0]
                if abs((actual_date.normalize() - expected.normalize()).days) > 14:
                    raise ValueError("换月重叠报价距离计划日期超过14天")
            if len(tail):
                if str(tail.current_contract.iloc[0]) != anchor:
                    raise ValueError("换月合约链不连续")
                candidate = pd.concat([old_calendar, tail])
        candidate = rollCalendar(candidate)
        if not np.array_equal(
            candidate.next_contract.iloc[:-1].astype(str).values,
            candidate.current_contract.iloc[1:].astype(str).values,
        ):
            raise ValueError("换月合约链不连续")
        with redirect_stdout(log):
            if not candidate.check_is_valid(raw):
                raise ValueError("换月日历缺少真实重叠报价")
            multiple = process_multiple_prices_single_instrument(
                instrument_code,
                adjust_calendar_to_prices=False,
                roll_calendar=candidate,
                roll_parameters=parameters,
                ADD_TO_DB=False,
                ADD_TO_CSV=False,
                data=data,
            ).loc[:cutoff]
        if episode_start is not None:
            multiple = multiple.loc[episode_start:]
        if multiple.empty:
            raise ValueError("原生构建器未生成连续价格")
        # The native calendar builder starts after its first roll. Preserve the
        # reviewed initial holding period, which can start before that first roll.
        prefix = old_multiple.loc[old_multiple.index < multiple.index.min()].copy()
        if old_multiple.empty:
            first_roll = candidate.iloc[0]
            index = raw[str(first_roll.current_contract)].loc[: first_roll.name].index
            prefix = pd.DataFrame(index=index[index < multiple.index.min()])
            for name, field in [
                ("PRICE", "current_contract"),
                ("FORWARD", "next_contract"),
                ("CARRY", "carry_contract"),
            ]:
                month = str(first_roll[field])
                prefix[name] = (
                    raw[month].reindex(prefix.index) if month in raw else np.nan
                )
                prefix[name + "_CONTRACT"] = month
            prefix = prefix.reindex(columns=multiple.columns)
        if len(prefix) and repair_start is not None:
            for price_name in ["PRICE", "FORWARD", "CARRY"]:
                selected = prefix.index >= pd.Timestamp(repair_start)
                for month in prefix.loc[selected, price_name + "_CONTRACT"].unique():
                    dates = prefix.index[
                        selected & prefix[price_name + "_CONTRACT"].eq(month)
                    ]
                    prefix.loc[dates, price_name] = (
                        raw[str(month)].reindex(dates) if str(month) in raw else np.nan
                    )
        if len(prefix):
            multiple = futuresMultiplePrices(pd.concat([prefix, multiple]))
        for name in ["PRICE_CONTRACT", "FORWARD_CONTRACT", "CARRY_CONTRACT"]:
            multiple[name] = (
                multiple[name].astype(str).str.replace(r"\.0$", "", regex=True)
            )
        adjusted = futuresAdjustedPrices.stitch_multiple_prices(
            multiple, forward_fill=False
        )
        if (
            not multiple.index.is_unique
            or not multiple.index.is_monotonic_increasing
            or multiple.PRICE.isna().any()
        ):
            raise ValueError("连续价格包含重复日期、无序日期或缺失PRICE")
        pd.testing.assert_frame_equal(
            pd.DataFrame(candidate.iloc[: len(old_calendar)]),
            pd.DataFrame(old_calendar),
            check_dtype=False,
        )
        preserved = (
            old_multiple.index
            if repair_start is None
            else old_multiple.index[old_multiple.index < pd.Timestamp(repair_start)]
        )
        if len(old_multiple) and old_multiple.index.max() > cutoff:
            raise ValueError("截止日早于已有连续价格末日，请使用覆盖末日的修复区间")
        if not preserved.isin(multiple.index).all():
            raise ValueError("重建会丢失已有历史日期")
        pd.testing.assert_frame_equal(
            pd.DataFrame(multiple.reindex(preserved)),
            pd.DataFrame(old_multiple.reindex(preserved)),
            check_dtype=False,
            check_names=False,
            check_exact=False,
            rtol=1e-10,
            atol=1e-8,
        )
        np.testing.assert_allclose(
            adjusted.reindex(preserved).diff(),
            old_adjusted.reindex(preserved).diff(),
            atol=1e-7,
            equal_nan=True,
        )
        if episode_start is not None:
            multiple = futuresMultiplePrices(
                pd.concat(
                    [
                        old_multiple_full.loc[old_multiple_full.index < episode_start],
                        multiple,
                    ]
                )
            )
            adjusted = futuresAdjustedPrices(
                pd.concat(
                    [
                        old_adjusted_full.loc[old_adjusted_full.index < episode_start],
                        adjusted,
                    ]
                )
            )
        backup = (
            Path(backup)
            if backup
            else Path(data.parquet_root_directory).parent
            / "tushare_updates"
            / pd.Timestamp.now().strftime("%Y%m%dT%H%M%S%f")
        )
        backup.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(old_multiple_full).to_parquet(
            backup / (instrument_code + "_multiple_before.parquet")
        )
        pd.DataFrame(old_adjusted_full).to_parquet(
            backup / (instrument_code + "_adjusted_before.parquet")
        )
        old_calendar.to_csv(backup / (instrument_code + "_calendar_before.csv"))
        (backup / (instrument_code + "_stitch.txt")).write_text(log.getvalue())
        try:
            prices.db_futures_multiple_prices_data.add_multiple_prices(
                instrument_code,
                futuresMultiplePrices(multiple),
                ignore_duplication=True,
            )
            prices.db_futures_adjusted_prices_data.add_adjusted_prices(
                instrument_code,
                futuresAdjustedPrices(adjusted),
                ignore_duplication=True,
            )
            calendars.add_roll_calendar(
                calendar_key, candidate, ignore_duplication=True
            )
        except Exception:
            prices.db_futures_multiple_prices_data.add_multiple_prices(
                instrument_code, old_multiple_full, ignore_duplication=True
            )
            prices.db_futures_adjusted_prices_data.add_adjusted_prices(
                instrument_code, old_adjusted_full, ignore_duplication=True
            )
            calendars.add_roll_calendar(
                calendar_key, old_calendar, ignore_duplication=True
            )
            raise
        return dict(
            rolls_added=len(candidate) - len(old_calendar),
            last_price=multiple.index.max(),
        )


def _save_contract_backup(run, contract, old, store, metadata):
    prefix = contract.key.replace("/", "_")
    pd.DataFrame(old).to_parquet(run / (prefix + "_day_before.parquet"))
    merged = store.get_merged_prices_for_contract_object(contract)
    pd.DataFrame(merged).to_parquet(run / (prefix + "_merged_before.parquet"))
    _write_json(
        run / (prefix + "_metadata_before.json"),
        None if metadata is None else metadata.as_dict(),
    )
    return merged


def _validate_prices(frame, first, last):
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("报价日期重复或无序")
    if len(frame) and (
        frame.index.min().date() < first or frame.index.max().date() > last
    ):
        raise ValueError("报价超出请求日期")
    if len(frame) and (not np.isfinite(frame.FINAL).all() or frame.FINAL.le(0).any()):
        raise ValueError("收盘价非有限值或非正值")


def _revision_rows(old, new, code, contract, first, last):
    if len(old) == 0:
        return []
    historical = old[(old.index.date >= first) & (old.index.date <= last)]
    if historical.empty:
        return []
    dates = historical.index.union(new.index[new.index <= old.index.max()])
    left, right = historical.reindex(dates), new.reindex(
        index=dates, columns=historical.columns
    )
    changed = ~np.isclose(
        left.to_numpy(), right.to_numpy(), rtol=1e-10, atol=1e-12, equal_nan=True
    )
    return [
        dict(
            instrument=code,
            contract=contract,
            date=str(dates[i]),
            field=left.columns[j],
            old=left.iloc[i, j],
            new=right.iloc[i, j],
        )
        for i, j in zip(*np.where(changed))
    ]


def _price_digest(frame):
    canonical = pd.DataFrame(frame).reindex(columns=sorted(frame.columns)).astype(float)
    canonical.index = pd.DatetimeIndex(frame.index).astype("datetime64[ns]")
    return hashlib.sha256(
        pd.util.hash_pandas_object(canonical, index=True).values.tobytes()
    ).hexdigest()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2))
    temporary.replace(path)


def _get_tushare_price_cleaning_config(data):
    return get_config_for_price_filtering(data)._replace(ignore_future_prices=False)


def _upsert_contract_record(contract_data, desired, backup=None):
    existing = None
    if contract_data.is_contract_in_data(desired):
        existing = contract_data.get_contract_from_db(desired)
        if (
            existing.expiry_date == desired.expiry_date
            and existing.currently_sampling == desired.currently_sampling
        ):
            return existing
    if backup is not None:
        filename = desired.key.replace("/", "_") + "_metadata_before.json"
        _write_json(
            Path(backup) / filename, None if existing is None else existing.as_dict()
        )
    contract_data.add_contract_data(desired, ignore_duplication=True)
    return desired


def validate_tushare_csv_configuration(source):
    instruments = set(source.configured_instrument_codes())
    for store, column in [
        (csvFuturesInstrumentData(), "Pointsize"),
        (csvSpreadCostData(), "SpreadCost"),
    ]:
        frame = pd.read_csv(store.config_file)
        counts = frame.Instrument.value_counts().reindex(
            list(instruments), fill_value=0
        )
        values = pd.to_numeric(
            frame.loc[frame.Instrument.isin(instruments), column], errors="coerce"
        )
        if not counts.eq(1).all() or values.isna().any() or values.le(0).any():
            raise ValueError("品种配置必须唯一，且 " + column + " 必须为正数")


def safe_tushare_error_text(error):
    from sysdata.config.private_config import get_private_config_as_dict

    text = str(error)
    try:
        private_token = get_private_config_as_dict().get("tushare_token")
    except Exception:
        private_token = None
    for token in [os.environ.get("TUSHARE_TOKEN"), private_token]:
        if token:
            text = text.replace(str(token), "<redacted>")
    return text


def update_tushare_cnhusd(
    data,
    as_of=None,
    full_backfill_start=datetime.date(2010, 8, 23),
    overlap_days=7,
):
    currency = dataCurrency(data)
    old = currency.get_fx_prices("CNHUSD")
    first = (
        max(
            full_backfill_start,
            old.index.max().date() - datetime.timedelta(days=overlap_days),
        )
        if len(old)
        else full_backfill_start
    )
    data.add_class_object(tushareFxPricesData)
    last = as_of or datetime.date.today()
    new = data.tushare_fx_prices.get_fx_prices(
        "CNHUSD", start_date=first, end_date=last
    )
    if _revision_rows(
        old.to_frame("FX"), new.to_frame("FX"), "CNHUSD", "", first, last
    ):
        raise RuntimeError("Tushare changed stored CNHUSD history; review the overlap")
    added = currency.update_fx_prices_and_return_rows_added(
        "CNHUSD", new, check_for_spike=True
    )
    if added is SPIKE_IN_DATA:
        raise ValueError("CNHUSD price spike check failed")
    return int(added)


def main(arguments=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", dest="instrument_code")
    parser.add_argument("--start", dest="start_date")
    parser.add_argument("--end", dest="end_date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-fx", action="store_true")
    parsed = vars(parser.parse_args(arguments))
    parsed["update_fx"] = not parsed.pop("skip_fx")
    result = update_tushare_futures(**parsed)
    print(result.to_string())
    return int(result.status.isin(["failed", "retained_previous"]).any())


if __name__ == "__main__":
    raise SystemExit(main())
