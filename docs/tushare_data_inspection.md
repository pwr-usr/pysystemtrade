# 中国期货数据检查

数据更新操作见 [Tushare 数据入口](tushare_chinese_futures.md)。下面的例子直接使用原生对象。

## 读取本地回测数据

```python
from sysdata.sim.db_futures_sim_data import dbFuturesSimData

data = dbFuturesSimData()
multiple = data.get_multiple_prices("SHFE_RB")
adjusted = data.get_backadjusted_futures_price("SHFE_RB")
print(multiple.tail())
print(adjusted.tail())
```

`PRICE` 是未复权当前合约价格，`FORWARD` 是下一张持有合约，`CARRY` 是计算期限结构的参照合约。对应的 `*_CONTRACT` 列保存合约月份。加法复权保持非换月日价格差，适合观察趋势、计算价格差波动率及现金盈亏。名义金额使用未复权价格和合约乘数。

应用审核后的流动性时段：

```python
data = dbFuturesSimData(
    trading_windows="data/futures/csvconfig/instrument_trading_windows.csv"
)
print(data.get_trading_windows("DCE_FB_OLD"))
```

时段表列为 `Instrument, Start, End, Reason, ReviewDate`，起止日均包含。一个品种可有多段有效历史；空起止行表示尚无可用时段。研究中每段独立建立波动率与规则预热，段间保持零仓位。全历史审核属于事后研究口径。

## 查看单合约原始日线

```python
from sysdata.data_blob import dataBlob
from sysproduction.data.prices import diagPrices
from sysobjects.contracts import futuresContract
from syscore.dateutils import DAILY_PRICE_FREQ

with dataBlob() as blob:
    store = diagPrices(blob).db_futures_contract_price_data
    contract = futuresContract("SHFE_RB", "20270100")
    daily = store.get_prices_at_frequency_for_contract_object(
        contract, frequency=DAILY_PRICE_FREQ
    )
    merged = store.get_merged_prices_for_contract_object(contract)
    print(daily.tail())
    print("Day / merged 相同：", daily.equals(merged))
```

日线字段为 `OPEN, HIGH, LOW, FINAL, VOLUME`，时间归一到交易日 23:00。零成交量是流动性审阅的证据；原始存储保留这些观察。

## 直接读取 Tushare 数据源

```python
from sysdata.data_blob import dataBlob
from sysdata.tushare.source import tushareFuturesContractData, tushareFuturesContractPriceData
from sysobjects.contracts import futuresContract
from syscore.dateutils import DAILY_PRICE_FREQ

with dataBlob(class_list=[tushareFuturesContractData, tushareFuturesContractPriceData]) as blob:
    contracts = blob.tushare_futures_contract
    print(contracts.get_all_contract_objects_for_instrument_code("SHFE_RB"))
    daily = blob.tushare_futures_contract_price.get_prices_at_frequency_for_contract_object(
        futuresContract("SHFE_RB", "20270100"), frequency=DAILY_PRICE_FREQ,
        start_date="2026-09-01", end_date="2026-09-07"
    )
    print(daily)
```

`tushareFuturesContractData`、`tushareFuturesContractPriceData`、`tushareFxPricesData` 分别继承原生合约、价格、汇率接口。它们通过 `dataBlob.tushare_connection` 共用目录和客户端；读取供应方数据不会写入本地库。导入统一通过 `update_tushare_futures` 完成。

## 解读异常

| 观察 | 检查方式 |
|---|---|
| PRICE 有值，CARRY 缺失 | 核对 carry 合约代码及同日真实报价；报告缺失行数与日期 |
| 长期零成交、重复报价 | 逐合约检查成交量和实际价格变化，审阅可交易时段 |
| 换月附近名义敞口跳升 | 对照当天 PRICE_CONTRACT、前日 FORWARD、持仓手数与资金 |
| 初期波动率接近零 | 先检查数据和预热，再采用明确的品种价格点波动率下限 |
| `retained_previous` | 查看拼接诊断，原连续价格已保留 |
| `retained_reviewed_episodes` | 独立历史段受审核表保护，新增段需要审核 |
| `retained_manual_calendar` | 人工合约链完整保留，追加换月需要复核 |
| `failed` 且提示缺少独立段价格 | 本地 multiple / adjusted 尚不完整，按同一审核批次成套初始化 |

“缺失 N 行”只统计特定输入没有报价的观察行数。它不等于日历天数，也不证明存在可补取的成交。缺失原因和可计算时段需要单列。
