# Tushare 中国期货数据

Tushare 提供历史合约信息和日线。原生 `dataBlob` 管理连接，原生价格类负责存储和回测。

```text
Tushare 日线 → Parquet 单合约 Day / merged
Tushare 合约信息 → MongoDB 到期日与采样状态
单合约价格 + 审核后的换月日历 → multiple prices → 加法复权价格
连续价格 + 审核后的可交易时段 → dbFuturesSimData → System
```

## 配置一次

在项目虚拟环境安装数据源依赖：

```bash
python -m pip install -e '.[tushare]'
```

在 `private/private_config.yaml` 配置现有存储地址及 `tushare_token`。凭据也可通过环境变量 `TUSHARE_TOKEN` 提供，环境变量优先。

```yaml
parquet_store: /your/data/parquet
mongo_host: localhost
mongo_port: 27017
mongo_db: production
tushare_token: YOUR_TOKEN
```

品种代码和规格分段位于 `sysdata/tushare/config/futures_instruments.csv`。中文名称、乘数、货币和成本沿用 `data/futures/csvconfig` 的原生配置。中国合约使用 `CNH` 记账，人民币组合的汇率换算为 1。

## 更新数据

在项目 Python 环境运行：

```python
from sysproduction.update_tushare_futures import update_tushare_futures

update_tushare_futures(dry_run=True)            # 查看计划
result = update_tushare_futures()              # 更新全部已配置品种
print(result)
```

空存储自动下载历史，已有存储自动增量更新。截止日取上海时间已完成、近期螺纹钢探针已经发布的交易日；当天 18:00 前采用前一日作为探针上限。报告复现时显式固定 `end_date`。

```python
result = update_tushare_futures(
    instrument_code="SHFE_RB", end_date="2026-09-07"
)
```

自选存储或注入测试客户端时传入 `dataBlob(..., tushare_connection=...)`，再调用 `update_tushare_futures(data=blob)`。实际导入通过 `blob.tushare_futures_contract` 获取原生合约与到期日，通过 `blob.tushare_futures_contract_price` 读取日线，通过 `blob.tushare_fx_prices` 读取 CNHUSD；供应商目录记录用于核对外部代码和规格有效期。

命令行使用相同参数：

```bash
python -m sysproduction.update_tushare_futures --dry-run
python -m sysproduction.update_tushare_futures --instrument SHFE_RB --end 2026-09-07
```

更新会包含新增月份，以及停更期间已经到期的合约。每次保留七天重叠。已到期历史只有在请求起止范围完整、存储内容与收据一致时才跳过；缺失的 merged 序列从 Day 修复。请求限流、重试和供应方行数截断检查集中在客户端。

`dry_run` 读取目录、发布时间探针和现有存储，返回计划，不写入价格、合约元数据和更新收据。固定 `end_date` 后无需发布时间探针。

## 修复一个历史区间

普通更新检测到历史修订时，保留已有数据并在结果中标明失败。查看 `revisions.csv` 后，用起止日期明确指定修复范围：

```python
result = update_tushare_futures(
    instrument_code="CZCE_LR", start_date="2018-01-01", end_date="2026-09-07"
)
```

指定 `start_date` 表示接受该区间的新报价，包括供应方删除的旧行。修复前保存 Day、merged 和合约元数据恢复副本，逐字段记录修订。连续价格重建保留修复区间之前的价格与加法复权差分。

每次运行的目录位于 Parquet 目录旁的 `tushare_updates/<运行时间>/`：

| 文件 | 用途 |
|---|---|
| `summary.csv` | 每个品种的状态、新增行、换月节点和异常原因 |
| `contracts.csv` | 每张合约的实际请求起止日期与结果 |
| `revisions.csv` | 日期、字段、旧值、新值 |
| `*_before.parquet`、`*_before.json`、`*_calendar_before.csv` | 本次写入前的恢复副本 |
| `*_stitch.txt` | 原生拼接诊断 |

共享的 `request_coverage.jsonl` 逐张合约追加完整请求范围及已存价格摘要。中断后重新调用同一个更新入口即可继续。

## 从备份恢复

先停止该存储的更新进程并保存当前版本，填入需要恢复的运行目录和合约。以下代码恢复一张已有合约的 Day 和 merged：

```python
from pathlib import Path
import pandas as pd
from syscore.dateutils import DAILY_PRICE_FREQ
from sysdata.data_blob import dataBlob
from sysobjects.contracts import futuresContract
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysproduction.data.prices import diagPrices, updatePrices

run = Path("/your/data/tushare_updates/<运行时间>")
contract = futuresContract("SHFE_RB", "20270100")
prefix = contract.key.replace("/", "_")
day = futuresContractPrices(pd.read_parquet(run / f"{prefix}_day_before.parquet"))
merged = futuresContractPrices(pd.read_parquet(run / f"{prefix}_merged_before.parquet"))
with dataBlob() as blob:
    update = updatePrices(blob)
    update.overwrite_prices_at_frequency_for_contract(contract, day, frequency=DAILY_PRICE_FREQ)
    update.overwrite_merged_prices_for_contract(contract, merged)
    store = diagPrices(blob).db_futures_contract_price_data
    assert store.get_prices_at_frequency_for_contract_object(contract, frequency=DAILY_PRICE_FREQ).equals(day)
    assert store.get_merged_prices_for_contract_object(contract).equals(merged)
```

若本次更新同时修改元数据或连续价格，使用同一运行目录中的备份成套恢复：

- `*_metadata_before.json`：用 `futuresContract.create_from_dict` 和 `dataContracts.add_contract_data(..., ignore_duplication=True)` 恢复到期日、采样状态；`null` 表示更新前不存在该合约。
- `<品种>_calendar_before.csv`：用 `pd.read_csv(..., index_col=0, parse_dates=True)` 读取，再转为 `rollCalendar`，通过 `csvRollCalendarData.add_roll_calendar(..., ignore_duplication=True)` 恢复审核日历。独立活跃段的目标文件由审核表的 `Calendar` 指定。
- `<品种>_multiple_before.parquet` 和 `<品种>_adjusted_before.parquet`：分别转为 `futuresMultiplePrices`、`futuresAdjustedPrices`，交给 `updatePrices.add_multiple_prices` 和 `add_adjusted_prices`。复权备份是单列 DataFrame，先取 `.iloc[:, 0]`。

只恢复该次实际改动且有备份的项目。读取恢复后的对象，与备份逐项比较；检查日期唯一、Day／merged 一致、日历合约链和复权差分。价格摘要与收据不一致时，下一次更新会重新核验完整合约历史。

## 换月和研究时段

更新入口自动调用原生 multiple / adjusted 构建器。日历只追加已经到期的计划节点，真实重叠报价与计划日期最多相差 14 天。已审核日历行、历史起点和价格差分通过检查后才写入。拼接失败保留原连续价格，并返回 `retained_previous`。

首次增加新产品需要先配置并审核换月日历。`instrument_trading_windows.csv` 保存逐品种可交易时段；原始报价完整保存。多段历史各自预热，段间停止交易。

`instrument_price_episodes.csv` 保护已经审核的独立价格段。`UpdateMode` 为 `closed` 时保留连续价格并返回 `retained_reviewed_episodes`；例如晚籼稻、油菜籽的短历史。`manual` 保留人工合约链，返回 `retained_manual_calendar`，追加换月前需要人工复核。缺少 `UpdateMode` 的旧表按 `closed` 处理。

仅最后一个价格段可以标为 `live`。其 `Calendar` 指向该段的已审核日历，路径相对于审核表所在目录；日历采用原生 `DATE_TIME` 索引，仅保存有真实重叠报价的换月行，区间边界记在审核表中。更新器从该段 `Start` 起构建 multiple / adjusted，并完整保留更早各段。各段独立加法复权，段间不建立换月价差。新活跃时段或更早独立段的修复需要重新审核边界和日历。测试其他审核版本时，可在传入的 `dataBlob.config` 设置 `tushare_episode_boundaries_path`。

### 空数据库初始化独立价格段

普通更新先导入单合约历史。对于 `closed` / `manual` 品种，本地缺少 multiple 或 adjusted 时返回 `failed`，原因提示成套导入审核版本。初始化使用同一审核批次的两个 Parquet 文件、独立段表、交易时段表、日历和换月参数；已经存在单侧价格时先保存恢复副本。

以下代码向尚无连续价格的数据库导入一个已审核品种：

```python
from pathlib import Path
import pandas as pd
from sysdata.data_blob import dataBlob
from sysobjects.multiple_prices import futuresMultiplePrices
from sysobjects.adjusted_prices import futuresAdjustedPrices
from sysproduction.data.prices import diagPrices, updatePrices

reviewed = Path("/your/reviewed/parquet")
code = "CZCE_LR"
multiple = futuresMultiplePrices(pd.read_parquet(reviewed / "futures_multiple_prices" / f"{code}.parquet"))
adjusted = futuresAdjustedPrices(pd.read_parquet(reviewed / "futures_adjusted_prices" / f"{code}.parquet").iloc[:, 0])
assert len(multiple) > 0 and multiple.index.equals(adjusted.index)
with dataBlob() as blob:
    prices, update = diagPrices(blob), updatePrices(blob)
    assert prices.get_multiple_prices(code).empty and prices.get_adjusted_prices(code).empty
    update.add_multiple_prices(code, multiple)
    update.add_adjusted_prices(code, adjusted)
```

同时安装该批次的 `instrument_price_episodes.csv`、`instrument_trading_windows.csv`、对应 `Calendar` 文件及换月参数，保持相对路径有效。核对入库值与审核文件一致，再调用统一更新入口；已有完整价格对会返回相应的 `retained_*` 状态。缺少已审核价格包时，先完成独立段审理与构建。

只用本地数据重建：

```bash
python -m sysinit.futures.rebuild_tushare_multiple_adjusted --instrument SHFE_RB
```

旧 `seed_price_data_from_tushare` 命令直接转到统一更新入口，采用相同的 `--instrument / --start / --end / --dry-run` 参数。生产进程 `run_daily_tushare_price_updates` 也调用这个入口。

原生读取示例见 [数据检查](tushare_data_inspection.md)。供应方字段依据官方 [合约信息](https://tushare.pro/document/2?doc_id=135)和[期货日线](https://tushare.pro/document/2?doc_id=138)文档。
