# JSHS V1 完整公共版

[English](README.md) | 中文

## 项目定位

JSHS V1 是一个 **LLM 驱动的 A 股交易策略生产工厂**。用户提出研究目标或交易策略需求，系统将需求转化为固定的回测测量合同，由 LLM 迭代编写同一个确定性策略模块，再调用真实回测引擎执行并生成结构化证据。

生产流程严格区分“生成”和“合格”：

1. 固定日期窗口、数据身份、股票池、引擎、成本、组合参数和指标目标。
2. LLM 根据用户目标生成或修改策略源码。
3. 回测引擎执行策略，并读取 `summary.json` 等结构化结果。
4. 检查数据质量、点时、schema、因果性、信号归属、可交易性、成交、费用、持仓和净值不变量。
5. 只有在声明的回测合同下所有目标和检查都通过，才输出可审计的合格 A 股交易策略及其证据。

这里的“合格”表示通过已声明的历史回测合同，不代表未来盈利、实盘可用或投资建议。项目不会把回测结果包装成收益承诺。

JSHS V1 是一个面向 A 股日线数据的回测工作区，包含 Python 回测引擎、命令行入口、数据适配器、策略 API、批量工具、Web 策略复盘服务和 Vue 前端。

这是经过裁剪的公共源码版本，不包含真实行情数据、供应商返回结果、账号凭据、私有策略、论文研究证据，也不包含独立的 V2/V3 引擎。

## 快速开始

建议使用 Python 3.11 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item config\config.example.ini config\config.local.ini
```

运行本地价格 smoke。价格数据使用仓库内置的合成 fixture，但交易日历仍通过官方 TuShare 获取：

```powershell
$env:TUSHARE_TOKEN = "你的 TuShare token"
python tests\test_offline_smoke.py
```

不要把真实 token 写入 `config/config.local.ini`、源码、日志或 Git。推荐只在当前终端设置环境变量。

## 启动 Web 界面

先构建前端：

```powershell
Set-Location web\frontend
npm ci
npm run build
Set-Location ..\..
python -m web
```

浏览器访问 `http://127.0.0.1:5000`。Web 目录默认展示公共的 `public_offline_smoke` 示例策略。

远程部署前必须阅读 `docs/full/web.md` 和 `SECURITY.md`。Web 服务会执行可信管理员上传的 Python 策略代码，不是安全沙箱；不可信代码必须放在独立进程或容器中，并由部署层限制文件系统、网络、凭据和资源权限。

## 数据与策略

真实回测需要用户自行取得合法授权的行情数据。日线行情至少需要以下字段：

```text
trade_date, stock_code, open, high, low, close, volume
```

数据接入方式、字段含义和点时约束见：

- `docs/full/data_onboarding.md`
- `docs/strategy_field_dictionary.md`
- `strategies/API.md`

策略只负责因果特征、信号意图和候选排序；交易日可成交性、涨跌停、执行价格、费用、持仓、风险状态和净值由 V1 框架负责。

## 验证

在干净的公共候选目录中运行：

```powershell
python tools\audit_full_public_surface.py
python tools\smoke_full_public_repo.py
```

公共候选由完整开发工作区的已跟踪文件生成：

```powershell
python tools\export_full_repo.py --clean
```

`FULL_PUBLIC_RELEASE_MANIFEST.json` 会记录来源提交、文件路径、大小和 SHA-256。不要直接发布开发工作区根目录，也不要携带其 Git 历史、`output/`、本地数据或 `config/config.local.ini`。

## 范围与许可

本项目是回测和研究工程工具，不是投资顾问、数据供应商或实盘交易系统。公共版只维护 V1 完整工作区；论文材料、私有研究记录以及 `projects/engine_v2/`、`projects/engine_v3/` 不属于本仓库的公共源码面。

代码采用 MIT License，详见 `LICENSE`。使用者需要自行确认数据授权、点时正确性、交易成本和执行假设。
