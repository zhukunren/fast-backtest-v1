# JSHS V1

[English](README.md) | 中文

> **LLM 驱动的 A 股交易策略生产工厂**：用户提出研究目标，Agent 负责把目标变成确定性策略、执行真实回测、完成领域检查，并输出可审计的合格策略和证据。

## 怎么使用

1. 安装项目并下载 [完整 Panel 数据包](https://github.com/zhukunren/fast-backtest-v1/releases/tag/panel-v1.0.0)。
2. 在项目目录打开 Agent，直接用自然语言描述你的策略研究目标。
3. Agent 固定回测合同，生成或修改策略，反复回测，直到结果为 `Passed` 或明确说明为什么无法通过。
4. 查看策略源码、回测摘要和审计产物，决定是否进入你自己的研究流程。

例如：

> 请用 2021-2025 年 A 股日线 Panel，研究一个每日 TopN 动量策略；目标是控制最大回撤、提高胜率，并确保没有未来数据。请使用 broker 引擎，输出可审计的策略源码和回测证据。

Agent 会处理策略模板、模式和引擎选择、回测迭代、失败定位以及产物解释。你不需要先阅读项目内部实现；遇到具体问题，直接继续询问 Agent。

## 三种模式

| 模式 | 用途 |
| --- | --- |
| `curve` | 从全市场 Panel 信号到 TopN、组合执行和净值曲线，回答策略历史表现如何。 |
| `assess` | 从全市场 Panel 信号到交易段评估，快速分析信号质量、交易表现和风险。 |
| `single` | 单只股票信号与净值测试，快速定位策略逻辑和执行问题。 |

## 五个引擎

| 引擎 | 用途 |
| --- | --- |
| `legacy` | 向量化组合回测，适合标准策略研究。 |
| `slot` | 槽位现金管理，适合固定持仓槽位的策略。 |
| `slot_broker` | 槽位分配与现金/成交账本结合。 |
| `broker` | 订单、成交、现金、持仓和风险账本，适合严格交易语义研究。 |
| `fixed_rebalance` | 按目标权重定期再平衡，不需要信号策略。 |

Agent 会根据研究问题选择模式和引擎，并在同一任务中保持条件一致。比较策略时，必须使用相同的模式、引擎、数据和回测窗口。

## 你会得到什么

- 一个可运行、可复查的 A 股策略源码模块；
- `summary.json`、目标检查和静态检查结果；
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

访问 `http://127.0.0.1:5000`。当前 `curve`/`assess` 执行仍需要配置交易日历服务；默认使用 TuShare。Panel 数据和源码已分离，真实数据授权由使用者负责。

## 边界

公共仓库只包含 V1 完整源码，不包含论文证据、私有研究记录、凭据、运行缓存或 V2/V3 项目。Web 上传的 Python 代码属于可信管理员代码，不是安全沙箱。代码采用 MIT License，详见 `LICENSE` 和 `SECURITY.md`。
