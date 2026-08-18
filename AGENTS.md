# Repository guide for agents

Read the relevant source of truth before changing code:

- `CONTRIBUTING.md`: coding style, tests, and contribution policy.
- `docs/data.md`: data abstractions, `dataBlob`, and storage hierarchy.
- `docs/backtesting.md` and `docs/production.md`: simulation and production architecture.
- `docs/tushare_chinese_futures.md`: Chinese-futures setup, storage, updates, and stitching policy.
- `docs/tushare_data_inspection.md`: runnable checks for every Tushare pipeline stage.
- `examples/chinese_futures/README.md`: notebook order and research conventions. Edit
  `examples/chinese_futures/make_notebooks.py`, then regenerate only the notebooks in scope.

Keep changes small and native to the repository:

- Reuse existing data objects and entry points; do not create parallel storage or frameworks.
- Historical initialization is concrete-contract `FINAL` prices + manual `rollconfig.csv`
  -> reviewed roll-calendar CSV -> multiple prices -> additive Panama adjusted prices.
- Build candidate calendars one instrument at a time in a temporary directory, inspect and
  validate them, then deliberately copy accepted CSVs into the repository.
- Daily updates and live rolls continue from the stored multiple-price series; calendar CSVs
  are initialization and recovery artifacts, not the live source of contract identity.
- Historical rebuilds must never fill a missing held-contract close across a roll boundary.
  The interactive live-roll command alone may offer an explicit, less-accurate recovery fill
  after strict construction fails. Additive adjusted levels support differences, not returns.
- Preserve unrelated dirty or untracked work. Run focused tests first, then broader tests
  in proportion to the change.
