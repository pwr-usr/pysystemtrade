"""Compatibility CLI delegates to the unified native rebuilding workflow."""

from contextlib import nullcontext

import pytest

import sysinit.futures.rebuild_tushare_multiple_adjusted as rebuild


def test_all_selects_exactly_the_stitchable_universe(monkeypatch):
    expected = rebuild.stitchable_tushare_instruments()
    selected = []
    monkeypatch.setattr(
        rebuild,
        "rebuild_tushare_instruments",
        lambda codes: selected.extend(codes) or 0,
    )
    assert rebuild.main(["--all"]) == 0
    assert selected == expected
    assert len(selected) == 95
    assert "INE_SCTAS" not in selected


def test_single_rebuild_delegates_data_injection(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rebuild,
        "rebuild_tushare_prices",
        lambda code, **kwargs: calls.append((code, kwargs)),
    )
    data = object()
    rebuild.rebuild_tushare_instrument("SHFE_RB", data=data)
    assert calls == [("SHFE_RB", {"data": data})]


def test_batch_isolates_failed_instrument(monkeypatch):
    attempted = []
    monkeypatch.setattr(rebuild, "dataBlob", lambda **kwargs: nullcontext(object()))

    def rebuild_one(code, data):
        attempted.append(code)
        if code == "SHFE_RB":
            raise ValueError("missing roll overlap")

    monkeypatch.setattr(rebuild, "rebuild_tushare_instrument", rebuild_one)
    assert rebuild.rebuild_tushare_instruments(["SHFE_RB", "DCE_JD"]) == 1
    assert attempted == ["SHFE_RB", "DCE_JD"]


@pytest.mark.parametrize(
    "arguments", [[], ["--all", "--instrument", "SHFE_RB"], ["--instrument", "DCE_L_F"]]
)
def test_invalid_selection_fails(arguments):
    with pytest.raises(SystemExit, match="2"):
        rebuild.main(arguments)
