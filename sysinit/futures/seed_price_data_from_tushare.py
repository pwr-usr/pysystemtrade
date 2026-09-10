"""Compatibility command: the unified updater automatically bootstraps empty stores."""

from sysproduction.update_tushare_futures import main


if __name__ == "__main__":
    raise SystemExit(main())
