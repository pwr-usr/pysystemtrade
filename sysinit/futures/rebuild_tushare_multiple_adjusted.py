"""Rebuild continuous prices from local contracts and reviewed calendars."""

import argparse

from sysdata.data_blob import dataBlob
from sysdata.tushare.manifest import TushareInstrumentManifest
from sysproduction.update_tushare_futures import rebuild_tushare_prices


def stitchable_tushare_instruments():
    return sorted(
        m.instrument_code
        for m in TushareInstrumentManifest.from_csv().mappings
        if m.is_stitchable
    )


def rebuild_tushare_instrument(instrument_code, data=None):
    return rebuild_tushare_prices(
        instrument_code, **({"data": data} if data is not None else {})
    )


def rebuild_tushare_instruments(instrument_codes):
    failed = 0
    with dataBlob(log_name="rebuild_tushare_prices") as data:
        for code in instrument_codes:
            try:
                print(code, rebuild_tushare_instrument(code, data=data))
            except Exception as error:
                failed += 1
                print("FAILED", code, str(error))
    return int(failed > 0)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--instrument", nargs="+")
    parsed = parser.parse_args(arguments)
    available = stitchable_tushare_instruments()
    selected = available if parsed.all else parsed.instrument
    if set(selected) - set(available):
        parser.error("品种不具备连续价格配置")
    return rebuild_tushare_instruments(selected)


if __name__ == "__main__":
    raise SystemExit(main())
