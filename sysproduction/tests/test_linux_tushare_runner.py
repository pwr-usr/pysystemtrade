import stat
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LINUX_DIRECTORY = REPOSITORY_ROOT / "sysproduction" / "linux"
SCRIPTS_DIRECTORY = LINUX_DIRECTORY / "scripts"


def _scheduled_minute(crontab: str, script_name: str) -> int:
    matching_lines = [
        line
        for line in crontab.splitlines()
        if not line.lstrip().startswith("#") and f"$SCRIPT_PATH/{script_name} " in line
    ]
    assert len(matching_lines) == 1
    minute, hour = matching_lines[0].split()[:2]
    return int(hour) * 60 + int(minute)


def test_tushare_linux_runner_always_dispatches_and_is_executable():
    runner = SCRIPTS_DIRECTORY / "run_daily_tushare_price_updates"
    runner_text = runner.read_text(encoding="utf-8")

    assert runner_text.startswith("#!/bin/bash\n. ~/.profile\n")
    assert "TUSHARE_TOKEN" not in runner_text
    assert (
        "sysproduction.run_daily_tushare_price_updates."
        "run_daily_tushare_price_updates"
    ) in runner_text
    assert runner.stat().st_mode & stat.S_IXUSR


def test_tushare_cron_job_precedes_multiple_and_adjusted_update():
    crontab = (LINUX_DIRECTORY / "crontab").read_text(encoding="utf-8")

    tushare_time = _scheduled_minute(crontab, "run_daily_tushare_price_updates")
    multiple_adjusted_time = _scheduled_minute(
        crontab, "run_daily_update_multiple_adjusted_prices"
    )

    assert tushare_time < multiple_adjusted_time
