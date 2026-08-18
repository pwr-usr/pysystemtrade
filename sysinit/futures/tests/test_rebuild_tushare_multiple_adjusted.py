"""Tests for the thin Tushare rebuild adapter."""

from __future__ import annotations

import pytest

import sysinit.futures.rebuild_tushare_multiple_adjusted as rebuild


CATALOG_ONLY = {"DCE_L_F", "DCE_PP_F", "DCE_V_F", "INE_SCTAS"}


def test_all_selection_is_exactly_the_stitchable_manifest_universe(
    monkeypatch,
):
    expected = rebuild.stitchable_tushare_instruments()
    selected = []
    monkeypatch.setattr(
        rebuild,
        "rebuild_tushare_instruments",
        lambda instrument_codes: selected.extend(instrument_codes) or 0,
    )

    assert rebuild.main(["--all"]) == 0

    assert selected == expected
    assert len(selected) == 95
    assert not CATALOG_ONLY.intersection(selected)


def test_rebuild_uses_saved_calendar_unchanged_and_native_builders(monkeypatch):
    saved_calendar = object()
    multiple_prices = object()
    calls = {}

    class FakeCalendarStore:
        def get_roll_calendar(self, instrument_code):
            assert instrument_code == "SHFE_RB"
            return saved_calendar

    def build_multiple(instrument_code, **kwargs):
        calls["multiple"] = (instrument_code, kwargs)
        return multiple_prices

    def build_adjusted(instrument_code, **kwargs):
        calls["adjusted"] = (instrument_code, kwargs)

    monkeypatch.setattr(rebuild, "csvRollCalendarData", FakeCalendarStore)
    monkeypatch.setattr(
        rebuild, "process_multiple_prices_single_instrument", build_multiple
    )
    monkeypatch.setattr(
        rebuild, "process_adjusted_prices_single_instrument", build_adjusted
    )

    rebuild.rebuild_tushare_instrument("SHFE_RB")

    assert calls["multiple"] == (
        "SHFE_RB",
        {
            "adjust_calendar_to_prices": False,
            "roll_calendar": saved_calendar,
            "ADD_TO_DB": True,
            "ADD_TO_CSV": False,
        },
    )
    assert calls["adjusted"] == (
        "SHFE_RB",
        {
            "multiple_prices": multiple_prices,
            "ADD_TO_DB": True,
            "ADD_TO_CSV": False,
        },
    )


def test_missing_saved_calendar_produces_nonzero_batch_result(monkeypatch, capsys):
    class MissingCalendarStore:
        def get_roll_calendar(self, instrument_code):
            raise Exception("Calendar for %s not found!" % instrument_code)

    monkeypatch.setattr(rebuild, "csvRollCalendarData", MissingCalendarStore)

    result = rebuild.rebuild_tushare_instruments(["SHFE_RB"])

    assert result == 1
    assert "FAILED SHFE_RB" in capsys.readouterr().out


def test_builder_failure_is_isolated_and_produces_nonzero_result(monkeypatch, capsys):
    attempted = []

    def rebuild_one(instrument_code):
        attempted.append(instrument_code)
        if instrument_code == "SHFE_RB":
            raise RuntimeError("bad prices")

    monkeypatch.setattr(rebuild, "rebuild_tushare_instrument", rebuild_one)

    result = rebuild.rebuild_tushare_instruments(["SHFE_RB", "DCE_JD"])

    assert result == 1
    assert attempted == ["SHFE_RB", "DCE_JD"]
    output = capsys.readouterr().out
    assert "FAILED SHFE_RB" in output
    assert "Rebuilt 1/2 instruments" in output


def test_cli_requires_one_selection_mode():
    with pytest.raises(SystemExit, match="2"):
        rebuild.main([])

    with pytest.raises(SystemExit, match="2"):
        rebuild.main(["--all", "--instrument", "SHFE_RB"])


def test_cli_rejects_catalog_only_instrument():
    with pytest.raises(SystemExit, match="2"):
        rebuild.main(["--instrument", "DCE_L_F"])
