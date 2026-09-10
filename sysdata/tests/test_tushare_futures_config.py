"""Invariants of the reviewed Tushare configuration files.

These tests pin the *shape* of the reviewed universe (manifest ↔ instrument
config ↔ spreads ↔ roll config ↔ roll calendars) and the audited point sizes.
They deliberately avoid freezing values that legitimately evolve with review
work (which instruments are stitched, individual spreads, carry offsets).
"""

from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "sysdata" / "tushare" / "config" / "futures_instruments.csv"
INSTRUMENT_CONFIG_PATH = (
    REPO_ROOT / "data" / "futures" / "csvconfig" / "instrumentconfig.csv"
)
SPREAD_CONFIG_PATH = REPO_ROOT / "data" / "futures" / "csvconfig" / "spreadcosts.csv"
ROLL_CONFIG_PATH = REPO_ROOT / "data" / "futures" / "csvconfig" / "rollconfig.csv"
ROLL_CALENDAR_PATH = REPO_ROOT / "data" / "futures" / "roll_calendars_csv"

MANIFEST_COLUMNS = [
    "Instrument",
    "Exchange",
    "FutCode",
    "ValidFrom",
    "ValidTo",
    "MinTick",
    "Predecessor",
    "StitchMode",
]
EXCHANGE_COUNTS = {
    "CFFEX": 8,
    "CZCE": 33,
    "DCE": 27,
    "GFEX": 5,
    "INE": 6,
    "SHFE": 20,
}
CATALOG_ONLY = {"DCE_L_F", "DCE_PP_F", "DCE_V_F", "INE_SCTAS"}
PREDECESSORS = {
    "CZCE_MA": "CZCE_ME",
    "CZCE_OI": "CZCE_RO",
    "CZCE_PM": "CZCE_WT",
    "CZCE_RI": "CZCE_ER",
    "CZCE_WH": "CZCE_WS",
    "CZCE_ZC": "CZCE_TC",
    "DCE_FB": "DCE_FB_OLD",
}
LOCKED_POINT_SIZE_SPEC = {
    "CFFEX": "IC=200 IF=300 IH=300 IM=200 T=10000 TF=10000 TL=10000 TS=20000",
    "DCE": (
        "A=10 B=10 BB=500 BZ=30 C=10 CS=10 EB=5 EG=10 FB_OLD=500 FB=10 "
        "I=100 J=100 JD=10 JM=60 L=5 LG=90 LH=16 L_F=5 M=10 P=10 PG=20 "
        "PP=5 PP_F=5 RR=10 V=5 V_F=5 Y=10"
    ),
    "CZCE": (
        "AP=10 CF=5 CJ=5 CY=5 ER=10 FG=20 JR=20 LR=20 MA=10 ME=50 OI=10 "
        "PF=5 PK=5 PL=20 PM=50 PR=15 PX=5 RI=20 RM=10 RO=5 RS=10 SA=20 "
        "SF=5 SH=30 SM=5 SR=10 TA=5 TC=200 UR=20 WH=20 WS=10 WT=10 ZC=100"
    ),
    "SHFE": (
        "AD=10 AG=15 AL=5 AO=20 AU=1000 BR=5 BU=10 CU=5 FU=10 HC=10 NI=1 "
        "OP=40 PB=5 RB=10 RU=10 SN=1 SP=10 SS=5 WR=10 ZN=5"
    ),
    "INE": "BC=5 EC=50 LU=10 NR=10 SC=1000 SCTAS=1000",
    "GFEX": "LC=1 PD=1000 PS=3 PT=1000 SI=5",
}
EXPECTED_POINT_SIZES = {
    f"{exchange}_{item.split('=')[0]}": int(item.split("=")[1])
    for exchange, specification in LOCKED_POINT_SIZE_SPEC.items()
    for item in specification.split()
}
MIN_SPREAD_TICKS = 0.25
MAX_SPREAD_TICKS = 3.0


@pytest.fixture(scope="module")
def manifest() -> pd.DataFrame:
    return pd.read_csv(
        MANIFEST_PATH,
        dtype={"ValidFrom": "string", "ValidTo": "string"},
        keep_default_na=False,
    )


@pytest.fixture(scope="module")
def tushare_instruments(manifest: pd.DataFrame) -> pd.DataFrame:
    instruments = pd.read_csv(INSTRUMENT_CONFIG_PATH)
    return instruments[instruments["Instrument"].isin(manifest["Instrument"])].copy()


@pytest.fixture(scope="module")
def tushare_spreads(manifest: pd.DataFrame) -> pd.DataFrame:
    spreads = pd.read_csv(SPREAD_CONFIG_PATH)
    return spreads[spreads["Instrument"].isin(manifest["Instrument"])].copy()


def test_manifest_covers_reviewed_universe_without_identity_collisions(manifest):
    assert list(manifest.columns) == MANIFEST_COLUMNS
    assert len(manifest) == 99
    assert manifest["Instrument"].is_unique
    assert manifest.groupby("Exchange").size().to_dict() == EXCHANGE_COUNTS

    source_families = manifest[["Exchange", "FutCode"]]
    assert len(source_families.drop_duplicates()) == 98
    duplicate_families = source_families[
        source_families.duplicated(keep=False)
    ].drop_duplicates()
    assert duplicate_families.to_dict("records") == [
        {"Exchange": "DCE", "FutCode": "FB"}
    ]

    predecessors = set(manifest["Predecessor"]) - {""}
    assert predecessors <= set(manifest["Instrument"])
    assert (
        manifest.loc[manifest["Predecessor"].ne("")]
        .set_index("Instrument")["Predecessor"]
        .to_dict()
        == PREDECESSORS
    )


def test_fibreboard_specification_eras_are_explicit_and_non_overlapping(manifest):
    old = manifest.set_index("Instrument").loc["DCE_FB_OLD"]
    current = manifest.set_index("Instrument").loc["DCE_FB"]

    assert old["FutCode"] == current["FutCode"] == "FB"
    assert old["ValidFrom"] == ""
    assert old["ValidTo"] == "20191129"
    assert current["ValidFrom"] == "20191202"
    assert current["ValidTo"] == ""
    assert old["MinTick"] == pytest.approx(0.05)
    assert current["MinTick"] == pytest.approx(0.5)
    assert current["Predecessor"] == "DCE_FB_OLD"


def test_special_contracts_are_catalog_only(manifest):
    modes = manifest.set_index("Instrument")["StitchMode"]
    assert set(modes[modes.eq("catalog_only")].index) == CATALOG_ONLY
    assert set(modes.unique()) == {"stitch", "catalog_only"}


def test_public_instrument_config_has_exactly_one_reviewed_row_per_instrument(
    manifest, tushare_instruments
):
    assert len(tushare_instruments) == len(manifest) == 99
    assert tushare_instruments["Instrument"].is_unique
    assert set(tushare_instruments["Instrument"]) == set(manifest["Instrument"])
    assert tushare_instruments["Currency"].eq("CNH").all()
    assert tushare_instruments["Region"].eq("ASIA").all()
    assert tushare_instruments[["PerBlock", "Percentage", "PerTrade"]].eq(0).all().all()


def test_all_audited_point_sizes_match_the_locked_specification(tushare_instruments):
    point_sizes = (
        tushare_instruments.set_index("Instrument")["Pointsize"].astype(int).to_dict()
    )
    assert point_sizes == EXPECTED_POINT_SIZES


def test_spreads_exist_and_are_a_sane_number_of_ticks(manifest, tushare_spreads):
    assert len(tushare_spreads) == len(manifest) == 99
    assert tushare_spreads["Instrument"].is_unique

    reviewed = manifest[["Instrument", "MinTick"]].merge(
        tushare_spreads,
        on="Instrument",
        how="left",
        validate="one_to_one",
    )
    assert reviewed["SpreadCost"].notna().all()
    assert (reviewed["SpreadCost"] > 0).all()
    multiples = reviewed["SpreadCost"] / reviewed["MinTick"]
    out_of_band = reviewed.loc[
        (multiples < MIN_SPREAD_TICKS) | (multiples > MAX_SPREAD_TICKS), "Instrument"
    ]
    assert out_of_band.empty, out_of_band.tolist()


def test_roll_config_and_calendars_stay_in_sync_with_the_manifest(manifest):
    manifest_instruments = set(manifest["Instrument"])
    stitchable = set(manifest.loc[manifest["StitchMode"].eq("stitch"), "Instrument"])
    roll_config = pd.read_csv(ROLL_CONFIG_PATH)
    tushare_rolls = roll_config[
        roll_config["Instrument"].isin(manifest_instruments)
    ].copy()

    assert tushare_rolls["Instrument"].is_unique
    configured = set(tushare_rolls["Instrument"])
    assert configured == stitchable
    assert tushare_rolls["CarryOffset"].isin([-1, 1]).all()
    assert all(
        set(row.HoldRollCycle) <= set(row.PricedRollCycle)
        for row in tushare_rolls.itertuples()
    )

    calendar_files = {
        path.stem
        for path in ROLL_CALENDAR_PATH.glob("*.csv")
        if path.stem in manifest_instruments
    }
    assert calendar_files == configured

    for instrument_code in sorted(configured):
        calendar = pd.read_csv(
            ROLL_CALENDAR_PATH / f"{instrument_code}.csv",
            dtype={
                "current_contract": "string",
                "next_contract": "string",
                "carry_contract": "string",
            },
            parse_dates=["DATE_TIME"],
        )
        if calendar.empty:
            # A closed, single-contract episode has prices and no roll nodes.
            episode_file = ROLL_CONFIG_PATH.parent / "instrument_price_episodes.csv"
            episodes = (
                pd.read_csv(episode_file)
                .query("Instrument == @instrument_code")
                .sort_values("Start")
            )
            assert not episodes.empty
            assert episodes.UpdateMode.eq("closed").all()
            reviewed = episodes.dropna(subset=["Calendar"]).iloc[-1]
            assert reviewed.PriceRows > 0
            assert pd.Timestamp(reviewed.Start) <= pd.Timestamp(reviewed.End)
            episode_calendar = pd.read_csv(episode_file.parent / reviewed.Calendar)
            assert episode_calendar.empty
            assert list(episode_calendar.columns) == list(calendar.columns)
        assert calendar["DATE_TIME"].is_monotonic_increasing
        assert calendar["DATE_TIME"].is_unique
        assert (
            calendar[["current_contract", "next_contract", "carry_contract"]]
            .notna()
            .all()
            .all()
        )
        assert list(calendar["current_contract"].iloc[1:]) == list(
            calendar["next_contract"].iloc[:-1]
        )
