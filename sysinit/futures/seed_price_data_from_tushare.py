"""Resumable full-history bootstrap for all mapped Tushare futures contracts.

Downloads every mapped contract's daily prices into the standard parquet
store (Day + merged), writes contract records with exact expiries into Mongo,
and backfills CNHUSD.  Resume state is the parquet checkpoints themselves:
a re-run skips contracts whose stored prices are already valid and complete.

Filters allow re-seeding one instrument or one contract, which is also the
repair path when Tushare corrects already-stored history.

Uses the standard dataBlob, so storage locations come from
``private/private_config.yaml`` (``parquet_store``, ``mongo_host`` etc.) and
the token from ``TUSHARE_TOKEN`` (or private-config ``tushare_token``).
"""

from __future__ import annotations

import argparse
import sys

from sysdata.data_blob import dataBlob
from sysdata.tushare.client import TushareClient
from sysdata.tushare.source import TushareFuturesPriceSource
from sysproduction.update_tushare_futures import (
    seed_tushare_futures,
    validate_tushare_csv_configuration,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instrument",
        help="Seed only this internal instrument code (e.g. SHFE_CU)",
    )
    parser.add_argument(
        "--contract",
        help="Seed only this contract date (YYYYMM or YYYYMM00); requires --instrument",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-fetch and overwrite complete contract checkpoints",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate the catalogue, print counts, then stop",
    )
    parser.add_argument("--skip-fx", action="store_true")
    parser.add_argument("--yes", action="store_true")
    arguments = parser.parse_args()
    if arguments.contract and not arguments.instrument:
        parser.error("--contract requires --instrument")
    return arguments


def main() -> int:
    arguments = parse_arguments()

    source = TushareFuturesPriceSource(TushareClient())
    records = source.fetch_contract_catalogue()
    validate_tushare_csv_configuration(source)

    if arguments.instrument:
        records = [
            record
            for record in records
            if record.instrument_code == arguments.instrument
        ]
    if arguments.contract:
        contract_date = (
            arguments.contract
            if len(arguments.contract) == 8
            else arguments.contract + "00"
        )
        records = [
            record for record in records if record.contract_date == contract_date
        ]
    if not records:
        print("No catalogue records match the requested filters")
        return 1

    vendor_contracts = len({record.external_contract_code for record in records})
    print(f"External vendor contracts: {vendor_contracts}")
    print(f"Internal contract records: {len(records)}")
    if arguments.dry_run:
        return 0

    if not arguments.yes:
        answer = input("Seed these contracts into the standard stores? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted")
            return 1

    data = dataBlob(log_name="seed_tushare_futures")
    result = seed_tushare_futures(
        data=data,
        source=source,
        records=records,
        resume=not arguments.no_resume,
        update_fx=not arguments.skip_fx,
    )

    print(result.summary())
    for contract_key in result.no_data[:20]:
        print(f"NO DATA {contract_key}")
    for failure in result.failures[:50]:
        print(
            f"FAILED {failure.instrument_code}/{failure.contract_date} "
            f"{failure.external_code}: {failure.reason}"
        )
    return 0 if result.okay else 1


if __name__ == "__main__":
    sys.exit(main())
