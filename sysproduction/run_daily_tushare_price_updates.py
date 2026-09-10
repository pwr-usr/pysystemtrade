"""Daily Tushare contract, price and CNH FX update process."""

from syscontrol.run_process import processToRun
from sysdata.data_blob import dataBlob
from sysproduction.update_tushare_futures import update_tushare_futures


class updateTushareFutures:
    def __init__(self, data: dataBlob):
        self.data = data

    def update_tushare_futures(self):
        result = update_tushare_futures(data=self.data)
        if result.status.isin(["failed", "retained_previous"]).any():
            raise RuntimeError(
                "Tushare daily update needs review:\n" + result.to_string()
            )
        return result


def run_daily_tushare_price_updates():
    process_name = "run_daily_tushare_price_updates"
    process_data = dataBlob(log_name=process_name)
    update_data = dataBlob(log_name="update_tushare_futures")
    updater = updateTushareFutures(update_data)
    process = processToRun(
        process_name,
        process_data,
        [("update_tushare_futures", updater)],
    )
    process.run_process()


if __name__ == "__main__":
    run_daily_tushare_price_updates()
