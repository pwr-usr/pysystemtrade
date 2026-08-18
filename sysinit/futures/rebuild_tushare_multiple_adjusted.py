"""Rebuild Tushare multiple and adjusted prices from reviewed calendars.

This is a small universe adapter around the native pysystemtrade builders. It
does not create calendars or change roll configuration.
"""

from __future__ import annotations

import argparse
import sys

from sysdata.csv.csv_roll_calendars import csvRollCalendarData
from sysdata.tushare.manifest import TushareInstrumentManifest
from sysinit.futures.adjustedprices_from_db_multiple_to_db import (
    process_adjusted_prices_single_instrument,
)
from sysinit.futures.multipleprices_from_db_prices_and_csv_calendars_to_db import (
    process_multiple_prices_single_instrument,
)


def stitchable_tushare_instruments() -> list[str]:
    manifest = TushareInstrumentManifest.from_csv()
    return sorted(
        mapping.instrument_code
        for mapping in manifest.mappings
        if mapping.is_stitchable
    )


def rebuild_tushare_instrument(instrument_code: str) -> None:
    roll_calendar = csvRollCalendarData().get_roll_calendar(instrument_code)
    multiple_prices = process_multiple_prices_single_instrument(
        instrument_code,
        adjust_calendar_to_prices=False,
        roll_calendar=roll_calendar,
        ADD_TO_DB=True,
        ADD_TO_CSV=False,
    )
    process_adjusted_prices_single_instrument(
        instrument_code,
        multiple_prices=multiple_prices,
        ADD_TO_DB=True,
        ADD_TO_CSV=False,
    )


def rebuild_tushare_instruments(instrument_codes: list[str]) -> int:
    failures: list[str] = []
    for position, instrument_code in enumerate(instrument_codes, start=1):
        print("[%d/%d] %s" % (position, len(instrument_codes), instrument_code))
        try:
            rebuild_tushare_instrument(instrument_code)
        except Exception as error:
            failures.append(instrument_code)
            print("FAILED %s: %r" % (instrument_code, error))

    print(
        "Rebuilt %d/%d instruments"
        % (len(instrument_codes) - len(failures), len(instrument_codes))
    )
    return 1 if failures else 0


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--all",
        action="store_true",
        help="Rebuild every stitchable instrument in the Tushare manifest",
    )
    selection.add_argument(
        "--instrument",
        nargs="+",
        metavar="CODE",
        help="Rebuild one or more stitchable Tushare instruments",
    )
    parsed = parser.parse_args(arguments)

    stitchable = stitchable_tushare_instruments()
    if parsed.all:
        instrument_codes = stitchable
    else:
        instrument_codes = list(parsed.instrument)
        not_stitchable = sorted(set(instrument_codes) - set(stitchable))
        if not_stitchable:
            parser.error(
                "not stitchable Tushare instruments: %s" % ", ".join(not_stitchable)
            )

    return rebuild_tushare_instruments(instrument_codes)


if __name__ == "__main__":
    sys.exit(main())
