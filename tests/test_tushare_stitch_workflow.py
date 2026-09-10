"""Native stitching regression checks; optional local fixture is always copied."""

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from sysdata.config.configdata import Config
from sysdata.data_blob import dataBlob
from sysproduction.data.prices import diagPrices
from sysproduction.update_tushare_futures import rebuild_tushare_prices
import sysproduction.update_tushare_futures as workflow


@pytest.fixture(autouse=True)
def no_mongo_connections(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Stitch tests use temporary prices and no Mongo metadata")

    monkeypatch.setattr(dataBlob, "_add_mongo_class", fail)


@pytest.mark.parametrize("mode", ["closed", "manual"])
@pytest.mark.parametrize(
    "available", [(), ("multiple",), ("adjusted",), ("multiple", "adjusted")]
)
def test_reviewed_episodes_require_a_complete_local_price_pair(
    tmp_path, mode, available
):
    from sysobjects.multiple_prices import futuresMultiplePrices
    from sysobjects.adjusted_prices import futuresAdjustedPrices
    from sysproduction.data.prices import updatePrices

    pd.DataFrame(
        [dict(Instrument="TEST", Start="2018-01-01", End="2019-01-01", UpdateMode=mode)]
    ).to_csv(tmp_path / "episode_boundaries.csv", index=False)
    index = pd.to_datetime(["2018-01-01 23:00", "2018-01-02 23:00"])
    multiple = futuresMultiplePrices(
        pd.DataFrame(
            dict(
                PRICE=[100.0, 101.0],
                FORWARD=[102.0, 103.0],
                CARRY=[102.0, 103.0],
                PRICE_CONTRACT="20180300",
                FORWARD_CONTRACT="20180600",
                CARRY_CONTRACT="20180600",
            ),
            index=index,
        )
    )
    adjusted = futuresAdjustedPrices(pd.Series([100.0, 101.0], index=index))
    with dataBlob(parquet_store_path=str(tmp_path / "parquet")) as data:
        data.db_futures_contract = SimpleNamespace()
        update = updatePrices(data)
        if "multiple" in available:
            update.add_multiple_prices("TEST", multiple)
        if "adjusted" in available:
            update.add_adjusted_prices("TEST", adjusted)
        before = {p: p.read_bytes() for p in tmp_path.rglob("*.parquet")}
        result = rebuild_tushare_prices("TEST", data=data, end_date="2026-09-07")
    expected = (
        "retained_reviewed_episodes" if mode == "closed" else "retained_manual_calendar"
    )
    assert result["status"] == (expected if len(available) == 2 else "failed")
    if len(available) < 2:
        assert "成套导入审核版本" in result["reason"]
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.parquet")}
    assert not (tmp_path / "tushare_updates").exists()


def test_nonfinal_live_episode_requires_review(tmp_path):
    pd.DataFrame(
        [
            dict(
                Instrument="TEST",
                Start="2018-01-01",
                End="2019-01-01",
                UpdateMode="live",
            ),
            dict(
                Instrument="TEST",
                Start="2020-01-01",
                End="2021-01-01",
                UpdateMode="closed",
            ),
        ]
    ).to_csv(tmp_path / "episode_boundaries.csv", index=False)
    with dataBlob(parquet_store_path=str(tmp_path / "parquet")) as data:
        data.db_futures_contract = SimpleNamespace()
        with pytest.raises(ValueError, match="最后"):
            rebuild_tushare_prices("TEST", data=data)


@pytest.mark.skipif(
    not os.environ.get("TUSHARE_REVIEWED_PRICE_FIXTURE"),
    reason="optional local reviewed-price fixture; all writes use a temporary copy",
)
@pytest.mark.parametrize(
    "code,case",
    [
        (code, case)
        for code in ["SHFE_AU", "CZCE_PL"]
        for case in ["unchanged", "future_plan", "distant_overlap", "live_episode"]
    ]
    + [("DCE_B", "reviewed_live")],
)
def test_native_rebuild_preserves_reviewed_initial_holding_period(
    tmp_path, code, case, monkeypatch
):
    reviewed = Path(os.environ["TUSHARE_REVIEWED_PRICE_FIXTURE"])
    calendar_source = reviewed.parent / "reviewed_calendars" / (code + ".csv")
    parameter_source = reviewed.parent / "rollconfig.csv"
    if case == "reviewed_live":
        table = reviewed.parent / "episode_boundaries_v2.csv"
        if not table.exists():
            pytest.skip("optional reviewed live-episode fixture")
        episodes = pd.read_csv(table).query("Instrument == @code").copy()
        calendar_source = table.parent / episodes.Calendar.iloc[-1]
        episodes.loc[
            episodes.UpdateMode.eq("live"), "Calendar"
        ] = f"calendars/{code}.csv"
        episodes.to_csv(tmp_path / "episode_boundaries.csv", index=False)
        parameter_source = reviewed.parent / "rollconfig_v2.csv"
        reviewed = reviewed.parent / "parquet_v2"
    raw = Path(os.environ["TUSHARE_RAW_PRICE_FIXTURE"])
    parquet = tmp_path / "parquet"
    parquet.mkdir()
    # The native rebuild only reads raw contracts. Copy rather than linking to
    # make an accidental future raw write harmless to the fixture.
    destination = parquet / "futures_contract_prices"
    destination.mkdir()
    for source in (raw / "futures_contract_prices").glob(code + "#*.parquet"):
        shutil.copy2(source, destination / source.name)
    for collection in ["futures_multiple_prices", "futures_adjusted_prices"]:
        (parquet / collection).mkdir()
        shutil.copy2(
            reviewed / collection / (code + ".parquet"),
            parquet / collection / (code + ".parquet"),
        )
    calendars = tmp_path / "calendars"
    calendars.mkdir()
    shutil.copy2(
        calendar_source,
        calendars / (code + ".csv"),
    )
    shutil.copy2(parameter_source, tmp_path / "rollconfig.csv")
    if case in ["future_plan", "distant_overlap"]:
        import sysinit.futures.build_roll_calendars as builder

        anchor = str(pd.read_csv(calendars / (code + ".csv")).next_contract.iloc[-1])
        approximate = pd.DataFrame(
            {
                "current_contract": [anchor, "20990100"],
                "next_contract": ["20990100", "20990200"],
                "carry_contract": ["20990100", "20990200"],
            },
            index=pd.to_datetime(["2026-07-01 23:00", "2026-10-01 23:00"]),
        )
        snapped = approximate.iloc[[1 if case == "future_plan" else 0]].copy()
        snapped.index = pd.to_datetime(["2026-09-07 23:00"])
        monkeypatch.setattr(
            builder,
            "_create_approx_calendar_from_earliest_contract",
            lambda contract: approximate,
        )
        monkeypatch.setattr(
            builder, "adjust_to_price_series", lambda calendar, raw: snapped
        )
        monkeypatch.setattr(
            workflow,
            "contractWithRollParametersAndPrices",
            lambda *args: SimpleNamespace(
                desired_roll_date=pd.Timestamp("2026-07-01"),
                update_expiry_with_offset_from_parameters=lambda: None,
            ),
        )
    with dataBlob(parquet_store_path=str(parquet)) as data:
        data.db_futures_contract = SimpleNamespace()
        data._config = Config(
            {
                "tushare_roll_calendar_path": str(calendars),
                "tushare_roll_parameters_path": str(tmp_path),
            }
        )
        prices = diagPrices(data)
        old = prices.get_multiple_prices(code)
        old_adjusted = prices.get_adjusted_prices(code)
        if case == "live_episode":
            from sysobjects.multiple_prices import futuresMultiplePrices
            from sysobjects.adjusted_prices import futuresAdjustedPrices
            from sysobjects.futures_per_contract_prices import futuresContractPrices
            from sysobjects.contracts import futuresContract
            from sysproduction.data.prices import updatePrices

            # A closed fragment deliberately has no forward quote at its end.
            # A global Panama stitch across this boundary would fail.
            earlier = old.iloc[:2].copy()
            earlier.index = old.index[:2] - pd.Timedelta(days=30)
            earlier["PRICE_CONTRACT"] = "19900100"
            earlier["FORWARD"] = np.nan
            earlier_adjusted = pd.Series([7.0, 8.0], index=earlier.index)
            pd.DataFrame(
                [
                    dict(
                        Instrument=code,
                        Start=earlier.index.min(),
                        End=earlier.index.max(),
                        UpdateMode="closed",
                        Calendar="",
                    ),
                    dict(
                        Instrument=code,
                        Start=old.index.min(),
                        End=old.index.max(),
                        UpdateMode="live",
                        Calendar=f"calendars/{code}.csv",
                    ),
                ]
            ).to_csv(tmp_path / "episode_boundaries.csv", index=False)
            update = updatePrices(data)
            old = futuresMultiplePrices(pd.concat([earlier, old]))
            old_adjusted = futuresAdjustedPrices(
                pd.concat([earlier_adjusted, old_adjusted])
            )
            update.add_multiple_prices(code, old)
            update.add_adjusted_prices(code, old_adjusted)
        if case in ["live_episode", "reviewed_live"]:
            from sysobjects.contracts import futuresContract
            from sysobjects.futures_per_contract_prices import futuresContractPrices
            from sysproduction.data.prices import updatePrices

            update = updatePrices(data)
            current = futuresContract(code, str(old.PRICE_CONTRACT.iloc[-1]))
            raw_store = prices.db_futures_contract_price_data
            current_prices = raw_store.get_merged_prices_for_contract_object(current)
            last = current_prices.iloc[[-1]].copy()
            last.index = pd.to_datetime(["2026-09-08 23:00"])
            last[["OPEN", "HIGH", "LOW", "FINAL"]] += 1.0
            update.overwrite_merged_prices_for_contract(
                current, futuresContractPrices(pd.concat([current_prices, last]))
            )
        if case == "distant_overlap":
            with pytest.raises(ValueError, match="14"):
                rebuild_tushare_prices(code, data=data, end_date="2026-09-07")
            pd.testing.assert_frame_equal(
                pd.DataFrame(prices.get_multiple_prices(code)), pd.DataFrame(old)
            )
            return
        result = rebuild_tushare_prices(
            code,
            data=data,
            end_date="2026-09-08"
            if case in ["live_episode", "reviewed_live"]
            else "2026-09-07",
        )
        after = prices.get_multiple_prices(code)
        after_adjusted = prices.get_adjusted_prices(code)
    assert result["rolls_added"] == 0
    if case in ["live_episode", "reviewed_live"]:
        assert after.index.max() == pd.Timestamp("2026-09-08 23:00")
    pd.testing.assert_frame_equal(
        pd.DataFrame(after.reindex(old.index)), pd.DataFrame(old), check_dtype=False
    )
    np.testing.assert_allclose(
        after_adjusted.reindex(old.index).diff(),
        old_adjusted.diff(),
        equal_nan=True,
        atol=1e-7,
    )
