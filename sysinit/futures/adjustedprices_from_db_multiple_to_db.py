"""
We create adjusted prices using multiple prices stored in database

We then store those adjusted prices in database and/or csv

"""
from syscore.constants import arg_not_supplied
from sysdata.csv.csv_adjusted_prices import csvFuturesAdjustedPricesData

from sysobjects.adjusted_prices import futuresAdjustedPrices
from sysobjects.dict_of_named_futures_per_contract_prices import price_name

from sysproduction.data.prices import diagPrices


def _get_data_inputs(csv_adj_data_path, data=arg_not_supplied):
    prices = diagPrices() if data is arg_not_supplied else diagPrices(data)
    db_multiple_prices = prices.db_futures_multiple_prices_data
    db_adjusted_prices = prices.db_futures_adjusted_prices_data
    csv_adjusted_prices = csvFuturesAdjustedPricesData(csv_adj_data_path)

    return db_multiple_prices, db_adjusted_prices, csv_adjusted_prices


def process_adjusted_prices_all_instruments(
    csv_adj_data_path=arg_not_supplied,
    ADD_TO_DB=True,
    ADD_TO_CSV=False,
    data=arg_not_supplied,
):
    db_multiple_prices, _notused, _alsonotused = _get_data_inputs(
        csv_adj_data_path, data=data
    )
    instrument_list = db_multiple_prices.get_list_of_instruments()
    for instrument_code in instrument_list:
        print(instrument_code)
        process_adjusted_prices_single_instrument(
            instrument_code,
            csv_adj_data_path=csv_adj_data_path,
            ADD_TO_DB=ADD_TO_DB,
            ADD_TO_CSV=ADD_TO_CSV,
            data=data,
        )


def process_adjusted_prices_single_instrument(
    instrument_code,
    csv_adj_data_path=arg_not_supplied,
    multiple_prices=arg_not_supplied,
    ADD_TO_DB=True,
    ADD_TO_CSV=False,
    data=arg_not_supplied,
):
    (
        db_multiple_prices,
        db_adjusted_prices,
        csv_adjusted_prices,
    ) = _get_data_inputs(csv_adj_data_path, data=data)
    if multiple_prices is arg_not_supplied:
        multiple_prices = db_multiple_prices.get_multiple_prices(instrument_code)
    _check_multiple_prices_have_held_prices(instrument_code, multiple_prices)
    adjusted_prices = futuresAdjustedPrices.stitch_multiple_prices(
        multiple_prices, forward_fill=False
    )

    print(adjusted_prices)

    if ADD_TO_DB:
        db_adjusted_prices.add_adjusted_prices(
            instrument_code, adjusted_prices, ignore_duplication=True
        )
    if ADD_TO_CSV:
        csv_adjusted_prices.add_adjusted_prices(
            instrument_code, adjusted_prices, ignore_duplication=True
        )

    return adjusted_prices


def _check_multiple_prices_have_held_prices(instrument_code, multiple_prices) -> None:
    if multiple_prices.empty:
        raise ValueError(
            "Cannot stitch %s: multiple prices are empty" % instrument_code
        )

    missing_held_prices = multiple_prices[price_name].isna()
    if missing_held_prices.any():
        missing_dates = list(multiple_prices.index[missing_held_prices])
        dates_for_message = ", ".join(str(date) for date in missing_dates[:5])
        if len(missing_dates) > 5:
            dates_for_message = "%s, ..." % dates_for_message
        raise ValueError(
            "Cannot stitch %s: held PRICE is missing at %s"
            % (instrument_code, dates_for_message)
        )


if __name__ == "__main__":
    input("Will overwrite existing prices are you sure?! CTL-C to abort")
    # modify flags and datapath as required
    process_adjusted_prices_all_instruments(
        csv_adj_data_path=arg_not_supplied, ADD_TO_DB=True, ADD_TO_CSV=True
    )
