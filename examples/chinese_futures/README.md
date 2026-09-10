# 中国期货研究

当前保留一份原生回测教程和五份专题报告。研究统一使用 **2026-09-07 的 v2 审核历史**，冻结日期用于复现已完成实验。

| 阅读顺序 | 内容 |
|---|---|
| [数据审理](chinese_futures_data_review.ipynb) | 95品种、108有效区间、换月报价、流动性和线性图；读取冻结输入并核对SHA256 |
| [单品种敞口限制](carry_breakout_portfolio_research.ipynb) | 1亿元、carry／breakout／50-50混合、六档限额，共18组合 |
| [加入40% carry是否值得](carry40_breakout_comparison.ipynb) | 同16%目标与预设风险网格；原生收益CAGR与回撤 |
| [动态退出与类别配置](carry_breakout_adaptive_research.ipynb) | 暂停与恢复、全市场／大类／细类比例、年度／冻结更新，共44组合 |
| [Carry亏损、平滑与季节性](carry_diagnostics_research.ipynb) | 六候选、单周期与幅度对照、95品种证据、1260观察账户及组合验证 |
| [完整回测教程](backtesting_tutorial/backtesting_with_chinese_futures.ipynb) | 对应原生回测手册，读取本地中国期货数据库；附独立config与system示例 |

## 数据与运行

日常维护见 [Tushare中文指南](../../docs/tushare_chinese_futures.md)，原生读取对象见
[数据检查示例](../../docs/tushare_data_inspection.md)。教程使用本地数据库，专题报告使用本地冻结目录：

```
private/research_runs/carry_breakout_native_2026-09-07/
  v2_inputs/                 # multiple、adjusted、成交量、区间和换月参数
  v2_input_manifest.csv      # 冻结文件SHA256
  v2_native_checks.csv       # 95品种原生报价与复权检查
  v2_*                       # 审理证据与发布记录
  portfolio_capital_100m/     # 18组敞口实验
  carry40_comparison/        # 40%carry与风险目标实验
  adaptive_shared/           # 动态报告生成的共同输入与观察账户
  adaptive_research/         # 44组组合结果
  carry_diagnostics/         # carry候选与诊断
```

原始行情、数据库、凭据、恢复备份和上述运行目录保存在本地，受 `.gitignore` 排除。
Git仓库保存报告代码、已展示输出、审核政策和固定参数。换电脑重算时，将冻结目录恢复到相同
相对路径，并配置原生数据库元数据。报告检查缺失输入和冻结摘要；重新下载产生的历史修订应作为新版本审理。

固定参数独立存放：

- [波动率下限](config/vol_floor_points.csv)：95品种的日价格点下限，保留已完成回测的数值。
- [研究类别](config/instrument_categories.csv)：中文名称、大类、细类和经济品种映射，规格时代分别评分。

首次运行顺序：

1. 完成18组敞口实验和40%carry报告，生成基准核对结果。
2. 动态报告运行至“年度暂停与类别比例”之前，生成 `adaptive_shared`。
3. Carry诊断报告运行至末尾组合比较之前，生成候选和已实施观察账户。
4. 动态报告运行后半段，保存44组结果及统计。
5. Carry诊断报告运行末尾组合比较与结论。

已有本次结果时，可直接阅读保存的输出。通过 PyCharm MCP 修改、执行和检查单元格；
工作流程见 [CONTRIBUTING](../../CONTRIBUTING.md#notebooks)。

逐品种图册完整保存在本地，报告内展示代表品种。组合图册入口为
`portfolio_capital_100m/instrument_atlas.html`，数据审理图册为 `current_data_review_atlas.html`。

## 口径

流动性区间和5%原始波动率筛选属于全历史事后审理，区间分别预热并在审核边界平仓。
2021年以来标为时间切分检验。CAGR、资金乘数、现金权益与累计收益百分点分别标明单位。
容量诊断保留成交量不足和未知报价证据，固定成本模型未随订单参与率增加冲击成本。

## 清理与恢复

2026-09-10移除了旧01—07系列、7月逐品种研究、Donchian实验室及专用helper和测试。
数据审理报告保留最终冻结版本；组合报告清理了旧数据与旧本金对照。
恢复副本位于本地 `private/research_cleanup/2026-09-10/research-before-cleanup.tar.gz`。
旧运行数据继续保留。波动率下限和类别映射已迁入独立CSV。

提交和推送的目标为 **pwr-usr/pysystemtrade**。永远不向原版 upstream 提PR。
