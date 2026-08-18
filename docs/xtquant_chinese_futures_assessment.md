# XTQuant suitability for Chinese futures execution

Static assessment dated 2026-08-09. Scope: Chinese futures on CFFEX, DCE,
CZCE, SHFE, INE, and GFEX, using XTData for market data and XTTrader for paper
or live execution in this repository.

> **Safety status:** no XTQuant process, MiniQMT/QMT terminal, paper account, CTP
> counter, or live account was connected or probed for this assessment. No order
> was submitted. A tightly constrained paper proof of concept is conditionally
> reasonable; live trading remains **NO-GO**.

## Executive verdict

XTQuant is a plausible Chinese-futures endpoint, but it is not a drop-in
replacement for this repository's Interactive Brokers integration and should not
be treated as a portable raw-CTP wrapper. The documented Python API talks to a
locally running QMT/MiniQMT client; the exact broker, counter, permissions, and
client edition determine what is actually available. The public API has the
right foundations—six domestic exchanges, concrete-contract data, live level-1
quotes, explicit futures open/close-today/close-yesterday operations, detailed
long/short positions, assets, orders, trades, callbacks, and cancellation—but
several properties required for unattended execution are either absent from the
public contract or still need runtime proof.

| Decision | Verdict | Reason |
|---|---|---|
| Static research and documentation | **GO** | The pinned local corpus is adequate to design a test plan and a narrow adapter. |
| Read-only XTData experiment | **Conditional GO** | Requires the correct edition/entitlements and a Windows QMT/MiniQMT environment; do not write XT daily bars into the canonical Tushare store. |
| Paper execution proof of concept | **Conditional GO** | Fixed-price outright orders only, one-way speculative positions, one lot, explicit paper-mode guard, and full restart/reconciliation tests. |
| Automated paper trading | **NO-GO today** | No adapter or runtime evidence yet; the repository defaults include IB-specific execution behavior. |
| Real-money trading | **NO-GO** | Offset routing, gross-position safety, order identity, recovery, session handling, cancel races, and account semantics are unproven. |

The most serious issue is not simple API coverage. Pysystemtrade represents a
futures position as one signed net integer and a broker order as a signed change
in that net position. Chinese futures execution needs gross long/short,
today/yesterday inventory, direction, hedge flag, and an explicit offset. An
adapter which merely turns `+1` into buy and `-1` into sell can open the wrong
position, be rejected, or hide gross exposure. This mismatch is a hard live
gate.

The recommended target is therefore a deliberately limited Windows-hosted paper
POC, not a broker swap. Tushare remains the source of truth for daily concrete
contract history and stitching; XTData supplies live level-1 data and, only if
validated, intraday bars; XTTrader supplies execution, current account state,
and current detailed positions.

## Evidence, terminology, and status labels

### Pinned vendor evidence

The review uses the locally archived XTQuant `250807.1.2` wheel manuals and
public documentation under [`docs/vendor/xtquant/250807.1.2/`](vendor/xtquant/250807.1.2/README.md).
The archive [manifest](vendor/xtquant/250807.1.2/manifest.json) records source
URLs and retrieval metadata, while [checksums](vendor/xtquant/250807.1.2/checksums.sha256)
pin the material used for this decision. In particular:

- [packaged XTData manual](vendor/xtquant/250807.1.2/package-docs/xtdata.md) and
  [official XTData page](https://dict.thinktrader.net/nativeApi/xtdata.html);
- [packaged XTTrader manual](vendor/xtquant/250807.1.2/package-docs/xttrader.md)
  and [official XTTrader page](https://dict.thinktrader.net/nativeApi/xttrader.html);
- [futures data dictionary](vendor/xtquant/250807.1.2/data/futures.md) and its
  [official page](https://dict.thinktrader.net/dictionary/future.html);
- [access and entitlement comparison](vendor/xtquant/250807.1.2/data/access-and-entitlements.md)
  and the [official data-dictionary entry point](https://dict.thinktrader.net/dictionary/);
- [paper-account guidance](vendor/xtquant/250807.1.2/data/simulation-account-setup.md)
  and the [official QMT interface-operation page](https://dict.thinktrader.net/innerApi/interface_operation.md);
- [release/download history](vendor/xtquant/250807.1.2/native-api/downloads.md)
  ([official release page](https://dict.thinktrader.net/nativeApi/download_xtquant.html))
  and [runtime overview](vendor/xtquant/250807.1.2/native-api/start-now.md)
  ([official quick start](https://dict.thinktrader.net/nativeApi/start_now.html)).
- [source-derived API/manual coverage](vendor/xtquant/250807.1.2/generated/api-doc-coverage.md)
  and the generated [XTTrader API inventory](vendor/xtquant/250807.1.2/generated/xttrader-api.md),
  which were produced by static parsing only and are not runtime evidence.

This is a point-in-time archive of publicly retrievable pages and the manuals
shipped in the pinned wheel, not a claim that private broker manuals, signed-in
pages, or counter-specific rules were available. The official release row,
package version, and publication dates are not internally consistent, so the
wheel version and SHA-256 in the manifest—not a page date—must identify the
tested build. The static [coverage report](vendor/xtquant/250807.1.2/generated/api-doc-coverage.md)
also finds numerous public wrapper symbols absent from the bundled manuals and
multiple source/manual signature differences; wrapper presence is not a runtime
support contract. Redistribution rights are also uncertain; see the archive's
[third-party notices](vendor/xtquant/250807.1.2/THIRD_PARTY_NOTICES.md).

### What “IB parity” means here

The comparison target is the IB adapter actually used by this repository, not
the entire IBKR product. The current factory installs ten IB-backed data classes
([`sysbrokers/broker_factory.py`](../sysbrokers/broker_factory.py)); the
production facade consumes their generic interfaces
([`sysproduction/data/broker.py`](../sysproduction/data/broker.py)). IBKR may
offer features not exposed here, and XTQuant may contain undocumented features;
neither is counted without evidence at the relevant seam.

### Status labels

- **Supported** — present in the pinned public/package API and sufficient for a
  static mapping. It still needs ordinary adapter tests.
- **Edition-dependent** — documented, but availability depends on QMT/MiniQMT
  edition, broker, counter, purchased market data, or account permission.
- **Undocumented** — no usable contract was found in the pinned corpus. This is
  not proof that no private build can do it; it must be treated as unavailable
  until the vendor confirms it in writing and it passes a test.
- **Missing** — a capability required by this repository is absent at the
  relevant layer, or the public documentation expressly rules it out.
- **Runtime-test-required** — documented behavior is too operationally or
  financially important to accept without a paper-environment test.

“Supported” describes documentation coverage, not production readiness.

## Architecture and the current broker seam

### What the repository expects

[`dataBroker`](../sysproduction/data/broker.py) is the production facade. Its
broker factory currently supplies these responsibilities:

1. futures contract prices, including historical bars and a live ticker;
2. futures contracts, exact expiry, tick size, contract chain, and trading hours;
3. instrument identity and broker-symbol mapping;
4. current contract positions;
5. execution submission, broker queries, matching, cancellation, cancellation
   confirmation, and limit-price modification;
6. account value and excess liquidity;
7. commission estimation/reporting;
8. broker/account/client identity;
9. spot FX prices and FX execution/balances.

The abstract interfaces are visible in
[`broker_futures_contract_price_data.py`](../sysbrokers/broker_futures_contract_price_data.py),
[`broker_futures_contract_data.py`](../sysbrokers/broker_futures_contract_data.py),
[`broker_execution_stack.py`](../sysbrokers/broker_execution_stack.py),
[`broker_contract_position_data.py`](../sysbrokers/broker_contract_position_data.py),
[`broker_capital_data.py`](../sysbrokers/broker_capital_data.py), and
[`broker_instrument_data.py`](../sysbrokers/broker_instrument_data.py). For a
single-RMB Chinese-futures deployment, broker FX execution can be explicitly
out of scope, but callers must not silently receive fabricated data. The
existing repository convention labels the instruments `CNH` and uses offshore
`CNHUSD` as a proxy for onshore CNY; that valuation convention does not prove
that an XT futures account reports CNH rather than CNY.

### The advertised extension point is incomplete

`broker_factory_func` suggests that a private factory can substitute another
broker class list. That is necessary but not sufficient. [`dataBlob`](../sysdata/data_blob.py):

- imports and types `connectionIB` directly;
- only knows how to construct `ib`, `mongo`, `arctic`, `parquet`, and `csv`
  class prefixes;
- aliases only the `ib` prefix to `broker`;
- allocates Mongo-backed IB client IDs; and
- closes only an IB connection.

Naming XT classes with an `ib` prefix to exploit that machinery would conceal
the wrong lifecycle and is not an acceptable design. A real implementation
needs a provider-aware connection/constructor/close path or another explicit,
tested injection mechanism.

There are further IB leaks. The interactive order tool imports and accepts
`connectionIB` and labels broker operations as IB
([`interactive_order_stack.py`](../sysproduction/interactive_order_stack.py)).
The price-multiplier diagnostic reaches through `.ib_data` and calls an IB-only
price-magnifier method not present on the generic contract interface
([`interactive_controls.py`](../sysproduction/interactive_controls.py)). These
paths must either become broker-neutral or be declared unavailable for XT.

### XTQuant's actual deployment boundary

The vendor describes XTData and XTTrader as interfaces to a running QMT or
MiniQMT client, not as a direct Python CTP session. `XtQuantTrader` is created
with the terminal's userdata path and a session ID; `connect()` is explicitly a
one-shot connection that does not reconnect automatically after a disconnect
([archived XTTrader manual](vendor/xtquant/250807.1.2/package-docs/xttrader.md)).
The official Linux guide covers XTData, not XTTrader
([archived Linux guide](vendor/xtquant/250807.1.2/native-api/linux-guide.md),
[official page](https://dict.thinktrader.net/nativeApi/Linux%E7%89%88xtquant%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B%E6%8C%87%E5%8D%97.html)).

The safe initial topology is therefore:

```text
Tushare daily concrete contracts ──> native Parquet/Mongo price pipeline
                                           │
                                           ├──> multiple/adjusted prices and rolls
                                           │
Windows host: QMT/MiniQMT <──> XTData ─────┤ live L1 / optional intraday
                    │                      │
                    └────── XTTrader <──> XT broker adapter <──> dataBroker
```

Co-locating the adapter with QMT/MiniQMT avoids inventing a network protocol in
the first POC. If the rest of production must remain on macOS/Linux, an
out-of-process broker service with authentication, durable request IDs,
heartbeat, replay, and fail-closed semantics becomes a separate architecture
project; the current in-process factory does not provide that boundary.

## Capability matrix

The impact column is intentionally conservative. “Runtime-test-required” means
the feature must be proven using the exact terminal, account, broker/counter,
and permissions intended for deployment. Unless a row names a narrower source,
XTData claims below come from the pinned [XTData manual](vendor/xtquant/250807.1.2/package-docs/xtdata.md),
XTTrader claims from the pinned [XTTrader manual](vendor/xtquant/250807.1.2/package-docs/xttrader.md),
exchange/symbol claims from the [futures dictionary](vendor/xtquant/250807.1.2/data/futures.md),
and commercial availability from the [entitlement comparison](vendor/xtquant/250807.1.2/data/access-and-entitlements.md).

| Area | Repository / current IB-adapter behavior | XTQuant finding | Status | Consequence for an XT adapter |
|---|---|---|---|---|
| Runtime and transport | IB uses a socket connection to Gateway/TWS through [`connectionIB`](../sysbrokers/IB/ib_connection.py). | XTTrader talks to a local QMT/MiniQMT userdata directory. Trading is not documented by the Linux XTData guide. | **Runtime-test-required** | Run trading on supported Windows and supervise the terminal; do not assume a headless Linux replacement. |
| Chinese exchange universe | The generic model is exchange-neutral; mappings are broker-specific. | The futures dictionary lists SHFE, DCE, CZCE, CFFEX, INE, and GFEX market codes. | **Supported; Edition-dependent** | Obtain written confirmation that the exact account has both data and trading access for every intended exchange. |
| Market-data entitlement | IB behavior depends on subscriptions but requests delayed data type as a fallback in [`ib_price_client.py`](../sysbrokers/IB/client/ib_price_client.py). | The vendor comparison shows futures data and direct futures trading vary by edition; subscription counts and depth also vary. | **Edition-dependent** | Research/direct-futures capability appears necessary. Probe bid, ask, and sizes for the complete whitelist before accepting an entitlement. |
| Concrete contract discovery | IB queries contract details and contract chains. | XTData exposes futures sectors/current codes, expired-contract downloads, and instrument details. | **Supported; Runtime-test-required** | Cross-check every returned code against the reviewed internal manifest; unknown or ambiguous codes fail closed. |
| Contract metadata | IB supplies exact expiry, tick size, multiplier/config, and trading hours. | `get_instrument_detail` documents product/exchange identity, expiry, tick, multiplier, long/short margin ratios, price limits, settlement, open interest, and trading flags. | **Supported; Runtime-test-required** | Useful for validation and live risk checks, but values, types, sentinel values, and refresh timing must be tested for futures. |
| CZCE identity | IB resolves broker contract details; internal dates are full `YYYYMM`. | XT symbols follow exchange case/format; release notes say standardized CZCE codes are edition-dependent. | **Edition-dependent; Runtime-test-required** | Never infer a four-digit year from a three-digit CZCE symbol alone. Require an unambiguous full-year field or reviewed mapping. |
| Historical OHLCV | IB normalizes bars to `OPEN/HIGH/LOW/FINAL/VOLUME` at a fixed set of frequencies and horizons. | XTData documents downloadable/gettable tick and K-line data over the needed daily/minute/hour family. | **Supported; Edition-dependent; Runtime-test-required** | Validate fields, units, duplicate timestamps, night-session bars, trading-day attribution, and bar boundaries. Do not make XT the daily source of truth. |
| Missing-bar behavior | Repository cleaning preserves genuine missing data subject to native cleaning policy. | `get_market_data` and `get_market_data_ex` default `fill_data=True`. | **Supported but unsafe default** | Always request `fill_data=False`; a forward-filled held-contract close can corrupt stitching and execution signals. |
| Live level-1 quote | Every repository order needs a ticker exposing bid, ask, bid size, ask size, refresh, and cancellation ([`tick_data.py`](../sysexecution/tick_data.py)). | XTData documents quote subscription callbacks and unsubscribe IDs; futures tick fields include level-1 data subject to entitlement. | **Supported; Edition-dependent; Runtime-test-required** | Prove update frequency, staleness detection, size units in lots, reconnect behavior, and unsubscribe cleanup. No valid L1 means no order. |
| Trading calendar and sessions | The broker interface returns dated intervals used to block closed-market trading and detect market close. | Pinned source restricts `get_trading_calendar` to `SH`/`SZ` and raises for futures markets. Futures must instead use `get_trading_dates` plus the newer trading-period functions; the bundled manual still declares the deleted `get_trading_time`. | **Runtime-test-required** | Build and validate futures day/night sessions from the source-present APIs, including breaks, holidays, and early closes. A calendar date is not enough. |
| Delayed quote fallback | The IB price client explicitly requests market-data type 3. | No equivalent delayed-futures fallback contract was found. | **Undocumented** | Treat missing/stale live data as unavailable and stop submission; never substitute a delayed quote silently. |
| Fixed-price limit order | IB maps the generic limit type and supports broker-side polling. | XTTrader's generalized, stock-named order methods accept futures account/type and fixed-price orders. | **Supported; Runtime-test-required** | This is the only order style recommended for the first paper POC. Validate price-tick rejection, limit bands, and one-lot units. |
| Market-style order | The current IB client supports market orders; repository defaults route market instructions to IB SNAP MKT. | XTTrader documents exchange-specific futures market styles only for CZCE, DCE, and CFFEX and says market-price types work only in live, not simulation. | **Missing in paper; Runtime-test-required in live** | Do not use market types in the POC. SHFE/INE/GFEX need a validated marketable-limit policy, not a guessed market flag. |
| Stop / stop-limit / trailing | The IB client maps the generic `stop_loss` type to an IB stop order. | No native stop, stop-limit, trailing-stop, or bracket order was found in the pinned manuals or public wrapper inventory. | **Missing from the pinned public API** | Do not expose stop support. A client-side stop would add market-data, restart, and gap risk and requires a separate design. |
| SNAP and Adaptive | The current IB adapter implements SNAP MKT/MID/PRIM and Adaptive ([`ib_orders_client.py`](../sysbrokers/IB/client/ib_orders_client.py)). | No equivalent semantics are documented. A recent release mentions smart algorithms, but not a proven Chinese-futures contract. | **Undocumented; Edition-dependent** | Override repository defaults. Do not map names to superficially similar vendor algorithms. |
| In-place limit modification | `algoOriginalBest` repeatedly changes an active limit and the IB adapter resubmits the same IB order object ([`algo_original_best.py`](../sysexecution/algos/algo_original_best.py), [`ib_orders.py`](../sysbrokers/IB/ib_orders.py)). | Submit and cancel are documented; no modify/replace callable appears in the pinned manuals or public wrapper inventory. | **Missing from the pinned public API** | Disable `algoOriginalBest` initially. Cancel-and-new is not equivalent unless identity, fill races, and residual quantity are designed and tested. |
| Outright cancel | IB sends cancel and separately confirms a done/non-filled status. | XTTrader documents synchronous/asynchronous cancellation, including counter `order_sysid`; sending a cancel is not confirmation of cancellation. | **Supported; Runtime-test-required** | Wait for terminal state or requery. Test full fill during cancel, partial-fill cancel, duplicate callbacks, and rejected cancels. |
| Order status and rejects | IB translates active/done states and broker logs. | XTTrader documents unreported/reported/partial/filled/cancelled/junk/unknown states and status messages/error callbacks. | **Supported; Runtime-test-required** | Define an exhaustive state machine. `junk`, rejected, expired, and cancelled must become terminal non-filled/partial states without looking “active forever.” |
| Partial fills and executions | IB aggregates cumulative signed fills, per-leg prices, fill time, and commissions. | `XtOrder` exposes traded volume/average price and `XtTrade` exposes trade ID, price, volume, time, commission, direction, offset, order ID, and counter ID. | **Supported; Runtime-test-required** | Deduplicate by durable execution identity, aggregate cumulative quantities, and verify callback/query ordering. |
| Native calendar spread / combo | IB constructs an atomic `BAG` with legs and the repository can aggregate leg fills ([`ib_contracts_client.py`](../sysbrokers/IB/client/ib_contracts_client.py), [`ib_translate_broker_order_objects.py`](../sysbrokers/IB/ib_translate_broker_order_objects.py)). | Futures-arbitrage order enums exist, but no documented general multi-leg order structure, atomicity guarantee, combo quote, or leg-fill contract was found. | **Undocumented** | Reject multi-leg broker orders and configure `Force_Outright`; do not infer combo support from an enum name. |
| Current order query | IB queries broker trades and matches them to persisted broker orders. | XTTrader documents current-day order queries; `None` can mean query failure or an empty list. | **Supported but ambiguous; Runtime-test-required** | Never interpret `None` as flat/empty. Require healthy account status plus a successful typed response. |
| Execution history and restart ID | The repository matches IB temp ID first and permanent ID second; even IB recovery is documented as generally limited to recent orders ([`production.md`](production.md#an-aside-what-happens-if-fills-happen-later)). | XT exposes request sequence, `order_id`, counter `order_sysid`, and `traded_id`. Its date-ranged `query_data` is a convenience wrapper around `export_data`: it exports a CSV, reads it with pandas, then deletes the file. The public contract does not establish futures schemas, retention, ordering, completeness, or replay semantics; no IB-like durable `permId`, caller idempotency key, or message replay is documented. | **Undocumented / Missing for unattended recovery** | Persist all IDs and raw state transitions locally, requery on startup, and prove crash-window recovery. Do not treat the CSV helper as an independent broker ledger. This is a hard live gate. |
| Account value / available funds | IB maps NetLiquidation and FullExcessLiquidity; `dataBroker` derives margin used as capital minus excess liquidity. | `XtAsset` documents `total_asset`, available `cash`, frozen cash and other balance fields. | **Supported; Runtime-test-required** | Candidate mapping is total asset→capital and available cash→excess liquidity, but reconcile against the terminal before relying on derived margin. |
| Margin and pre-trade preview | IB commission estimation uses `whatIfOrder`; the generic facade only consumes total/excess values. | Instrument details expose margin ratios and `XtPositionStatistics` documents used/frozen margin, but no per-order what-if margin/commission preview was found. | **Supported for current state; Undocumented for preview** | Reconcile reported current margin. Calculate a conservative local pre-trade estimate only after validating exchange/broker add-ons; do not present it as broker-confirmed buying power. |
| Commission | IB reports fill commissions and supports what-if commission estimation ([`ib_broker_commissions.py`](../sysbrokers/IB/ib_broker_commissions.py)). | The pinned package manual documents `XtTrade.commission`; detailed futures positions also expose used/frozen commission and instrument detail lists charge fields. No what-if commission API was found. | **Supported for reported commission; Runtime-test-required** | Validate timing, sign, units, currency, partial-fill aggregation, and statement reconciliation. Keep a reviewed local schedule as a conservative fallback, never silent zero. |
| Net current position | The generic interface returns one signed `contractPosition` per exact contract. | XT can return current positions. | **Supported** | A derived net can satisfy legacy reporting only after detailed safety checks; it must not be the adapter's sole position state. |
| Gross/today/yesterday position | The repository model has no direction, today/yesterday, or hedge flag. | `query_position_statistics` documents direction, hedge flag, total, yesterday, today, and closeable volume. | **Supported in XT; Missing in repository model; Runtime-test-required** | Keep a detailed native ledger and offset planner. Lock on simultaneous long+short or non-speculative inventory in the first implementation. |
| Futures offset | Generic `brokerOrder` stores signed quantity but no open/close/close-today/close-yesterday. | For ordinary `order_stock`/`order_stock_async` submission, one of six `FUTURE_*` `order_type` values encodes direction plus offset. Direction and offset are separate fields on returned orders/trades; hedge flag is reported by detailed positions, not accepted by the documented normal-order signature. | **Supported for encoded direction/offset; Missing in repository model; Runtime-test-required** | Split each generic delta into explicit native child orders using a fresh position snapshot and pending-order reservations. Confirm the broker's default hedge classification is speculative; the adapter cannot claim to request it through the documented signature. This is the primary hard gate. |
| Paper/live boundary | IB ports/accounts are external configuration; the generic broker identity has no environment mode. | XT docs distinguish simulation/live behavior, but the same style of trading call can target the subscribed account. | **Missing** | Add explicit `paper`/`live` configuration, verify account identity/type at startup, default to paper, and require a separate fail-closed live enablement. |
| Connection recovery | The current IB connection is simple and its in-memory controls are imperfect, but the adapter can requery recent broker orders. | `connect()` does not auto-reconnect; account and quote subscriptions must be restored by the caller. | **Runtime-test-required** | On disconnect: freeze submission, reconnect, resubscribe, requery, reconcile, then—and only then—return to ready. |
| FX | Current IB factory includes spot FX, balances, and FX orders. | No equivalent XT futures-account FX workflow is in scope. | **Missing but intentionally out of scope** | Use the existing Tushare `CNHUSD` proxy for valuation. Limit the POC to one RMB futures account, explicitly reconcile XT's currency semantics to the repository's `CNH` convention, and make unsupported FX methods fail. |

## The Chinese futures position and offset mismatch

### Why signed quantity is insufficient

The generic order object contains a contract and signed trade quantity, but no
direction, offset, today/yesterday bucket, or hedge/speculation flag
([`broker_orders.py`](../sysexecution/orders/broker_orders.py)). The generic
position object contains only `position: int` plus a contract
([`positions.py`](../sysobjects/production/positions.py)). IB positions are
therefore naturally collapsed to a signed net value in
[`ib_contract_position_data.py`](../sysbrokers/IB/ib_contract_position_data.py).

XTTrader's detailed futures position is richer: long/short direction,
today/yesterday quantities, closeable quantity, and hedge flag. Its order and
trade records also carry direction and offset. That is an advantage over the
current generic model, but only if the adapter preserves the information.

For example, suppose the account is long two lots opened today and three lots
from yesterday:

| Generic requested delta | Required native intent under a close-today-first policy |
|---:|---|
| `-4` | sell close-today 2, then sell close-yesterday 2 |
| `-5` | sell close-today 2, then sell close-yesterday 3 |
| `-7` | close all 5 long lots, then sell-open 2 short lots |

The exact accepted flags and priority can differ by exchange and counter, so
the policy must be exchange-aware and paper-tested. In particular, it is unsafe
to assume that a generic “close” flag will always select the intended inventory
bucket. A fresh detailed snapshot, including frozen/closeable quantities and
all pending native orders, is required immediately before planning.

### Required offset planner behavior

A viable first adapter must:

1. query detailed positions with `query_position_statistics`, not just the
   collapsed `XtPosition` view;
2. normalize by account, exact exchange contract, long/short direction,
   today/yesterday bucket, and hedge flag;
3. reserve quantities already targeted by active close orders;
4. for a buy delta, close eligible short inventory in the configured order and
   open long only for the remainder; perform the mirror operation for a sell;
5. split one generic broker order into one or more native XT orders while
   exposing one aggregate control object to the existing stack;
6. persist every child request sequence, order ID, `order_sysid`, execution ID,
   direction, and offset before treating the parent as recoverable;
7. aggregate partial fills without exceeding the generic parent quantity; and
8. fail closed if the snapshot changes while orders are being planned or if an
   external/manual order appears.

“Close first, then open” is acceptable only under the POC's explicit one-way
speculative-position policy. Supporting intentional lock/hedge positions would
require strategy intent to carry through the repository order model and is out
of scope.

### Gross exposure can disappear in net reconciliation

If an account holds long 3 and short 3 in the same contract, the repository's
signed net position is zero. Its existing break comparison can therefore see a
flat account even though gross exposure is six lots and margin is in use. This
is not merely a reporting defect: the next signed order cannot be routed safely
without knowing which side it is meant to close or extend.

Until the core position model is enriched, the first XT adapter must lock the
instrument and prohibit submission when any of these are true:

- both gross long and gross short are non-zero for one exact contract;
- a hedge flag other than the explicitly allowed speculative flag is present;
- today + yesterday does not reconcile to the detailed total;
- closeable/frozen inventory conflicts with active orders;
- the derived XT net disagrees with the repository net; or
- a position, order, or trade query is ambiguous or failed.

This detailed gross-state check must run at startup, before every order, after
every fill/cancel, after reconnect, and during scheduled reconciliation. Passing
the legacy net comparison alone is insufficient.

### Other CTP-shaped operational issues

The public XT API exposes CTP-like fields but does not document every
counter-level responsibility. Before live use, confirm whether QMT/MiniQMT
handles settlement-information confirmation, trading-day rollover, password or
authentication requirements, and order-reference uniqueness. Test how
“current-day” order/trade queries behave across the evening session, midnight,
and the next settlement boundary. A calendar date and a Chinese futures trading
day are not interchangeable.

## Tushare and XTData source split

The existing Tushare integration deliberately occupies the historical-data
role and leaves the broker seat free. It stores daily concrete-contract traded
close—not settlement—then builds reviewed roll calendars, multiple prices, and
additive Panama adjusted prices locally
([`tushare_chinese_futures.md`](tushare_chinese_futures.md)). Its reviewed
manifest handles all six exchanges, exchange-qualified internal identifiers,
renamed products, specification eras, catalogue-only families, and CZCE's
three-digit symbol problem
([`futures_instruments.csv`](../sysdata/tushare/config/futures_instruments.csv),
[`manifest.py`](../sysdata/tushare/manifest.py)).

Use the following ownership split:

| Data or operation | Source of truth | XT role |
|---|---|---|
| Daily concrete-contract OHLCV history | Tushare → native Parquet | None in normal operation. XT daily bars may be compared offline but must not overwrite the series. |
| `FINAL` daily price | Tushare traded close | Do not substitute XT settlement or a forward-filled close. |
| Historical exact contract identity | Reviewed Tushare manifest + stored contract metadata | Cross-check, never silently rewrite. |
| Point size, currency, roll policy, spread cost | Repository CSV configuration/reviewed policy | Validate against XT detail; discrepancies stop the instrument. |
| Historical roll calendars / multiple / adjusted prices | Native repository pipeline | None. Do not use XT “main” or provider-continuous contracts. |
| Live L1 bid/ask and sizes | XTData | Primary XT market-data responsibility. |
| Optional intraday bars | XTData after validation | Separate frequency data only; `fill_data=False`; validate night sessions and timestamps. |
| Current expiry, limits, tick, multiplier, margin ratios, trading status | XTData plus reviewed local policy | Runtime validation/risk input, not an automatic policy overwrite. |
| Orders, trades, current detailed positions, account assets | XTTrader | Primary execution responsibility. |
| CNHUSD valuation series | Existing Tushare FX path | No XT FX execution in the POC. |

The scheduler currently contains both generic broker price updates and the
Tushare update. An XT deployment must not allow two writers to update the same
daily contract series. Keep the Tushare update before the native
multiple/adjusted update, and disable or scope any broker daily-price writer for
Chinese contracts
([`control_config.yaml`](../syscontrol/control_config.yaml),
[`update_tushare_futures.py`](../sysproduction/update_tushare_futures.py)).

## Recommended constrained proof of concept

### Environment

- A dedicated, operator-visible Windows host or VM with the exact supported
  QMT/MiniQMT build and the pinned XTQuant wheel.
- A futures **paper** account whose terminal and API both report account type
  `FUTURE`; no live account configured in that environment.
- Written confirmation of edition, Python-trading permission, market-data
  depth/subscription limits, simulation support, direct-futures routing, all
  intended exchanges, and the actual counter connection.
- One adapter process/session ID, one account, one controlled clock/time zone,
  and durable logs. No remote broker service in phase one.
- An instrument allowlist and an independent terminal-side/operator kill switch.

### Deliberate POC restrictions

- Outright concrete contracts only; reject every multi-leg order.
- `Force_Outright` roll state only; reject `Force` spread rolls. The distinction
  is native repository behavior
  ([`roll_state.py`](../sysobjects/production/roll_state.py),
  [`roll_orders.py`](../sysexecution/stack_handler/roll_orders.py)).
- Fixed-price limit orders only. The vendor simulator does not support the
  documented market-price modes.
- Do not use the repository's default `market_algo` (`algoSnapMkt`) or
  `algoOriginalBest`; the former is IB-specific and the latter requires active
  limit modification
  ([`defaults.yaml`](../sysdata/config/defaults.yaml),
  [`algo_original_best.py`](../sysexecution/algos/algo_original_best.py)). Use
  explicit limit instructions through the existing non-blocking limit algo only
  after the adapter can monitor them safely
  ([`algo_limit_orders.py`](../sysexecution/algos/algo_limit_orders.py)).
- One lot maximum per native child and one-way speculative inventory only. The
  broker's default order classification must be confirmed as speculative,
  because the documented normal-order signature has no hedge-flag argument;
  reject any reported non-speculative inventory. No intentional long/short lock
  and no manual/external trades in the account.
- No stop, bracket, OCA, smart-algo, market, or cancel-replace emulation.
- Tushare remains the only daily-history writer. XT uses `fill_data=False`.
- Any stale L1 quote, ambiguous contract, failed query, disconnect, reconciliation
  break, unknown callback state, or unrecognized native position locks the
  instrument and stops submission.

### Suggested POC progression

1. **Read-only inventory:** enumerate the six exchanges, map current concrete
   contracts, compare exact expiry/tick/multiplier with reviewed configuration,
   and record all discrepancies without writing policy.
2. **Market-data soak:** subscribe/unsubscribe L1, detect staleness, validate lot
   sizes and timestamps through day and night sessions, then restart the terminal
   and adapter repeatedly.
3. **Account-state soak:** query account status/assets and detailed positions;
   prove that failure, empty, and flat are distinguishable.
4. **Non-marketable paper orders:** submit a one-lot fixed-price order away from
   the market, observe acknowledgement, query it, cancel it, and confirm the
   terminal state.
5. **Controlled paper fills:** use a one-lot marketable limit in an approved
   liquid contract; test open long/short, close today, and—after a trading-day
   boundary—close yesterday.
6. **Failure injection:** kill the Python process and disconnect/restart
   QMT/MiniQMT at every order state; prove deterministic requery and no duplicate
   submission.
7. **Exchange expansion:** repeat the full suite on one liquid contract for each
   intended exchange, including at least one CZCE three-digit identity case and
   every exchange-specific close rule the account will use.

The POC should initially be a broker test harness plus adapter contract tests,
not automated strategy execution. Integration into the stack handler comes
only after the native lifecycle is deterministic.

## Known gaps relative to the current IB adapter

### XT capability gaps or documentation gaps

The pinned XT documentation does not establish parity for:

- IB SNAP MKT/MID/PRIM and Adaptive order types;
- a native futures stop, stop-limit, trailing stop, bracket, or OCA order;
- in-place modification of an active limit order;
- a general atomic `BAG`/multi-leg order and combo quote with per-leg fills;
- delayed market-data fallback;
- broker-side what-if commission or per-order margin preview;
- an IB-like permanent order ID, caller idempotency key, or message replay; the
  date-ranged `query_data` helper performs export-to-CSV/read/delete rather than
  an independent broker query, and its futures schemas, retention, consistency,
  and suitability as a recovery ledger are not established;
- automatic reconnect/reconciliation; or
- a documented end-to-end Chinese-futures example covering offset planning,
  partial fills, cancel races, restart recovery, and settlement rollover.

The release page's smart-algorithm entry is not evidence of futures support.
Treat it as unavailable until the vendor documents supported futures exchanges,
parameters, lifecycle, recovery IDs, and paper behavior for the exact edition.

### Repository gaps exposed by XT/CTP semantics

These are not XTTrader deficiencies:

- `dataBlob` has an IB-specific constructor, alias, client-ID allocator, and
  close lifecycle.
- The broker order has no explicit native direction, offset, or child-order
  collection.
- Contract positions are signed net values and cannot represent gross
  long/short, today/yesterday, frozen quantity, or hedge flag.
- The generic completion logic primarily reasons about requested versus filled
  quantity; terminal reject/expired states need an adapter state mapping.
- There is no explicit paper/live mode or live-account interlock at the broker
  interface.
- Default execution configuration uses IB-specific SNAP behavior, while normal
  best execution assumes active limit modification.
- Interactive diagnostics and terminology still reach into IB-specific data.
- There is no broker-adapter conformance test suite or fake broker exercising
  disconnect, partial-fill, cancel, and restart behavior.

IB is not a perfect gold standard here. Its control objects are held in memory,
the connection setup is simple, and repository documentation says recovery can
fall back to manual entry when a recent order can no longer be found
([`IB.md`](IB.md#orders-data),
[`production.md`](production.md#an-aside-what-happens-if-fills-happen-later)).
The XT implementation should improve on those weaknesses rather than merely
replicate them.

## Future paper and live validation protocol

No part of this protocol has been run. Each result must capture the XTQuant
wheel hash, QMT/MiniQMT version, client edition, broker, counter, account type,
exchange, contract, trading day, and raw request/callback/query records.

### 1. Contract and market-data protocol

For every intended exchange:

- enumerate current and expired concrete contracts and reject continuous/main
  pseudo-contracts from execution;
- round-trip internal instrument + `YYYYMM00` to XT symbol and back;
- compare exchange, product, exact expiry, minimum tick, multiplier, price
  limits, margin ratios, settlement, and trading flag against terminal and
  reviewed local data;
- explicitly test CZCE case and three-digit/four-digit year handling;
- download and retrieve each required frequency with `fill_data=False`;
- validate `OPEN/HIGH/LOW/FINAL/VOLUME`, lot units, ordering, duplicates,
  missing rows, and timestamps;
- subscribe to bid/ask and sizes, prove stale/disconnected detection, then
  unsubscribe and verify resources are released; and
- observe the full daytime/nighttime session, breaks, midnight, Friday night,
  holiday boundary, and trading-day rollover.

Any silent fill, ambiguous identity, naive-calendar misattribution, or stale
quote accepted as current is a failure.

### 2. Account and position protocol

- Compare XT assets with the terminal before open, after an order freezes funds,
  after fill, after cancel, after mark-to-market, and after settlement.
- Determine whether `total_asset` and available `cash` safely map to repository
  capital/excess liquidity and whether derived margin equals the terminal.
- Create long and short positions separately; verify total, today, yesterday,
  closeable, frozen, direction, hedge flag, average price, and net derivation.
- Prove close-today and close-yesterday for long and short on every exchange
  whose counter rules differ.
- Force simultaneous long and short in a disposable paper account if supported;
  verify the adapter locks instead of reporting a safe zero net.
- Place an external/manual paper order and verify the adapter detects the
  unexpected state and freezes submission.

### 3. Order lifecycle protocol

Exercise at least these cases with one-lot fixed-price orders:

| Case | Required evidence |
|---|---|
| Accepted, unfilled, cancel | Request sequence → order ID/sysid → cancel acknowledgement → terminal cancelled state; no fill. |
| Immediate full fill | Exactly one cumulative parent fill and one position change despite callback/query duplication. |
| Partial fill then rest | Monotonic cumulative quantity, correct average price/time, no overfill. |
| Partial fill then cancel | Filled part retained, remainder terminal-cancelled, parent and position correct. |
| Fill races cancel | Late fill is captured and cancel response is not treated as proof of no further fill. |
| Invalid tick / outside limit / bad offset | Explicit reject reason, terminal non-filled state, no stuck active order. |
| Insufficient closeable quantity | Planner refuses or native reject is terminal; it must never open the opposite side accidentally. |
| Duplicate/out-of-order callback | Idempotent state and fill aggregation. |
| Query returns `None` | Classified as query failure, never empty orders/positions. |
| Trading-day rollover | IDs, queries, positions, and local journal remain matchable through the night/day boundary. |

Validate every documented order status, including unknown/junk and account
disconnect states, even if some require synthetic adapter tests.

### 4. Disconnect, crash, and recovery protocol

Inject failure before submit, after request sequence allocation, after broker
acceptance but before local database write, after partial fill, during cancel,
after full fill but before propagation, and during terminal reconnect.

On every restart the adapter must:

1. start in `NOT_READY` and reject new submissions;
2. reconnect explicitly, then restore account and quote subscriptions;
3. verify account status and a fresh clock/trading-day view;
4. query detailed positions, orders, and trades successfully;
5. merge callbacks and query results into a durable, idempotent local journal;
6. match every active persisted parent to all native children using proven IDs;
7. compare detailed gross state and legacy net state;
8. surface every orphan order/trade/position and lock affected instruments; and
9. transition to `READY` only when reconciliation is exact.

The critical crash window is “broker accepted, local parent not persisted.” If
`strategy_name`/`order_remark` cannot carry a unique recoverable client reference
and XT offers no idempotency key, unattended live submission is a permanent
NO-GO until the architecture supplies another durable handshake.

### 5. Paper soak and shadow protocol

After functional tests, run multiple complete Chinese trading weeks including
night sessions with automated submission disabled, continuously comparing:

- XT quotes against the terminal;
- XT detailed positions/orders/trades/assets against the terminal;
- derived net positions against repository positions;
- Tushare daily close/history against the separately observed XT daily data;
  and
- session-open/closed decisions against actual exchange availability.

Require zero unexplained position breaks, zero orphan orders/fills, zero
duplicate submissions, zero stale quotes accepted as current, and successful
planned terminal/process restarts. Paper fills are not proof of live queueing or
counter behavior; they only qualify the adapter for further review.

## Go/no-go gates

Gates are cumulative. A later gate cannot waive an earlier failure.

| Gate | Pass condition | Current status |
|---|---|---|
| G0 — evidence and entitlement | Pinned build; vendor/broker confirms exact edition, paper/live account separation, Python futures trading, data depth, all intended exchanges, counter, and support boundaries. | **NOT PASSED** — static public evidence only. |
| G1 — architecture | Provider-neutral connection lifecycle; explicit paper/live guard; allowlist/size/risk limits; no IB naming hack; raw durable journal; unsupported functions fail closed. | **NOT STARTED**. |
| G2 — read-only conformance | Contract identity, expiry, tick/multiplier, sessions, L1, bar transforms, `fill_data=False`, Tushare separation, and staleness tests pass for all intended exchanges. | **NOT STARTED**. |
| G3 — offset and gross safety | Detailed position ledger and child-order planner pass long/short, today/yesterday, frozen/pending, gross lock, external-order, and reconciliation tests. | **NOT STARTED; HARD LIVE GATE**. |
| G4 — order lifecycle | Limit submit/query/cancel, all terminal states, rejects, partial fills, cancel races, callback deduplication, and commission handling pass in paper. | **NOT STARTED; HARD LIVE GATE**. |
| G5 — recovery | Every injected crash/disconnect window recovers without duplicate, orphan, missed fill, or false flat/empty state; accepted-before-persist window is solved. | **NOT STARTED; HARD LIVE GATE**. |
| G6 — paper soak | Multi-week all-session paper/shadow run meets zero-break criteria and planned failover succeeds. | **NOT STARTED**. |
| G7 — live-readiness review | Independent code/operations review, broker statement reconciliation, explicit user authorization, separate live credentials/host, rollback and kill-switch drill. | **NO-GO; not authorized by this document**. |

## Final decision

XTQuant appears suitable enough to justify a narrow Chinese-futures **paper**
POC. It may ultimately be a better domain fit than IB for explicit Chinese
open/close-today/close-yesterday and detailed position inventory. It is not yet
suitable for unattended production in this repository: the generic net model
loses essential CTP semantics, the runtime is terminal/edition dependent, and
documented parity is missing for modification, recovery identity, native
spreads, stop/SNAP/Adaptive orders, what-if margin/commission, and automatic
reconnect/reconciliation.

No runtime probes were run. Until G0–G6 pass and G7 is separately authorized,
live trading remains **NO-GO**.
