"""Small opt-in live smoke test; normal CI never needs a Tushare token."""

import datetime
import os

import pytest

from sysdata.tushare import TushareClient, TushareFuturesPriceSource


RUN_LIVE_TESTS = os.environ.get("TUSHARE_LIVE_TESTS") == "1" and bool(
    os.environ.get("TUSHARE_TOKEN")
)
REPRESENTATIVE_INSTRUMENTS = (
    "CFFEX_IF",
    "DCE_M",
    "CZCE_CF",
    "SHFE_CU",
    "INE_SC",
    "GFEX_SI",
)


@pytest.mark.skipif(
    not RUN_LIVE_TESTS,
    reason="set TUSHARE_LIVE_TESTS=1 and TUSHARE_TOKEN for provider smoke tests",
)
def test_one_liquid_contract_per_exchange_and_cnhusd():
    source = TushareFuturesPriceSource(TushareClient())
    records = source.fetch_contract_catalogue()
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=45)

    for instrument_code in REPRESENTATIVE_INSTRUMENTS:
        active = [
            record
            for record in records
            if record.instrument_code == instrument_code
            and (record.price_start_date or record.first_trade_date) <= today
            and today <= (record.price_end_date or record.expiry_date)
        ]
        assert active, f"no active contract found for {instrument_code}"
        record = min(active, key=lambda item: item.expiry_date)
        prices = source.get_prices_at_frequency_for_contract(
            record,
            start_date=max(
                start_date,
                record.price_start_date or record.first_trade_date,
            ),
            end_date=today,
        )

        assert not prices.empty
        assert list(prices.columns) == ["OPEN", "HIGH", "LOW", "FINAL", "VOLUME"]
        assert prices.index.is_monotonic_increasing
        assert not prices.index.has_duplicates
        assert set(prices.index.hour) == {23}

    cnhusd = source.get_cnhusd_prices(start_date=start_date, end_date=today)
    assert not cnhusd.empty
    assert (cnhusd.dropna() > 0.0).all()
