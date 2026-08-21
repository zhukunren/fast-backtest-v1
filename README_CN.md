# JSHS V1

[English](README.md) | 中文

> **LLM 驱动的 A 股交易策略生产工厂**：用户提出研究目标，Agent 负责把目标变成确定性策略、执行真实回测、完成领域检查，并输出可审计的合格策略和证据。

你可以直接用自然语言生成策略并回测，不需要先学习 Python 或策略接口。告诉 Agent 你想捕捉的市场行为、持有规则、风险限制和指标目标，它会生成策略源码、运行回测并解释结果。

## 怎么使用

1. 安装项目并下载 [完整 Panel 数据包](https://github.com/zhukunren/fast-backtest-v1/releases/tag/panel-v1.0.0)。
2. 在项目目录打开 Agent，直接用自然语言描述你的策略研究目标。
3. Agent 固定回测合同，生成或修改策略，反复回测，直到结果为 `Passed` 或明确说明为什么无法通过。
4. 查看策略源码、回测摘要和审计产物，决定是否进入你自己的研究流程。

例如：

> 请用 2021-2025 年 A 股日线 Panel，研究一个每日 TopN 动量策略；确保没有未来数据，并满足：累计收益率不低于 300%、最大回撤不超过 30%、实际交易段胜率不低于 55%、最小组合规模为 2x2。请自行选择合适的运行方式，输出可审计的策略源码和回测证据。

Agent 会处理策略模板、模式和引擎选择、回测迭代、失败定位以及产物解释。你不需要先阅读项目内部实现；遇到具体问题，直接继续询问 Agent。

这些不是描述性文字，而是会进入回测目标检查的具体阈值。Agent 读取真实回测得到的实际指标：全部通过才报告 `Passed`；回测有效但指标未达标报告 `Unmet`；数据、因果性、schema 或运行合同失败报告 `Invalid`。日期、数据、模式、引擎和阈值不会在迭代中静默放宽。

## 三种研究方式

你不需要记住系统名称，直接告诉 Agent 你想看什么：

| 你想做什么 | 系统提供的方式 |
| --- | --- |
| 看一套策略整体赚不赚钱、回撤多大 | 完整组合回测（`curve`） |
| 比较买卖信号和每笔交易质量 | 交易质量评估（`assess`） |
| 检查某只股票的信号和执行逻辑 | 单只股票检查（`single`） |

## 五种组合方式

Agent 会自动选择合适的组合方式。你只需在意它代表的交易含义：

| 组合方式 | 适合的问题 |
| --- | --- |
| 常规组合回测（`legacy`） | 标准策略的整体历史表现。 |
| 分仓管理（`slot`） | 固定持仓数量、分配资金和轮换仓位。 |
| 分仓加交易记录（`slot_broker`） | 同时关注分仓规则和成交过程。 |
| 真实交易账本（`broker`） | 逐笔订单、成交、现金、持仓和风险变化。 |
| 目标比例再平衡（`fixed_rebalance`） | 不选股，按用户设定的资产比例定期调仓。 |

模式和组合方式只是 Agent 的执行选择，不是用户的学习门槛。比较策略时，Agent 会确保比较条件一致。

## 你会得到什么

- 一个可运行、可复查的 A 股策略源码模块；
- 回测报告、目标检查和静态检查结果；
- 数据身份、回测窗口、配置和引擎记录；
- 信号、成交、持仓、净值和交易段等结构化结果；
- 对数据质量、点时、schema、因果性、可交易性、费用和净值不变量的审计结果。

`Passed` 只表示策略通过声明的历史回测合同，不代表未来盈利、实盘可用或投资建议。项目不会把回测结果包装成收益承诺。

## 安装与运行

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item config\config.example.ini config\config.local.ini
```

Panel 下载后解压到 `data/panel_cache/`，先按 Release 页面和 manifest 校验 SHA-256：

```powershell
python tools\data_quality_audit.py --target panel-manifest --input data\panel_cache\stock_daily.parquet
```

启动 Web 工作区：

```powershell
Set-Location web\frontend
npm ci
npm run build
Set-Location ..\..
python -m web
```

访问 `http://127.0.0.1:5000`。当前完整组合回测和交易质量评估仍需要配置交易日历服务；默认使用 TuShare。Panel 数据和源码已分离，真实数据授权由使用者负责。

## 边界

公共仓库只包含 V1 完整源码，不包含论文证据、私有研究记录、凭据、运行缓存或 V2/V3 项目。Web 上传的 Python 代码属于可信管理员代码，不是安全沙箱。代码采用 MIT License，详见 `LICENSE` 和 `SECURITY.md`。
