"""Daily Tushare contract, price and CNH FX update process."""

from syscontrol.run_process import processToRun
from sysdata.data_blob import dataBlob
from sysdata.tushare.client import TushareClient
from sysdata.tushare.source import TushareFuturesPriceSource
from sysproduction.update_tushare_futures import update_tushare_futures


class updateTushareFutures:
    def __init__(self, data: dataBlob, source: TushareFuturesPriceSource):
        self.data = data
        self.source = source

    def update_tushare_futures(self):
        result = update_tushare_futures(data=self.data, source=self.source)
        if not result.okay:
            raise RuntimeError(
                "Tushare daily update was partially successful: " + result.summary()
            )
        return result


def run_daily_tushare_price_updates():
    process_name = "run_daily_tushare_price_updates"
    process_data = dataBlob(log_name=process_name)
    update_data = dataBlob(log_name="update_tushare_futures")
    source = TushareFuturesPriceSource(TushareClient())
    updater = updateTushareFutures(update_data, source)
    process = processToRun(
        process_name,
        process_data,
        [("update_tushare_futures", updater)],
    )
    process.run_process()


if __name__ == "__main__":
    run_daily_tushare_price_updates()
