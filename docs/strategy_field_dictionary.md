# Strategy Field Dictionary

本页说明 AI 策略作者在 V1 日线策略中可以依赖的常用字段口径。普通 OHLCV 策略可直接参考
`strategies/templates/iterative_template.py`；需要额外行情列时使用 `tools/codex_strategy.py fields`，
只有需要可选框架能力时才查询 `capabilities --runtime v1` 或使用 scaffold。这些都不是普通策略
开始回测的前置步骤。

## 必备字段

| 字段 | 说明 | 口径 |
| --- | --- | --- |
| `trade_date` | 交易日 | 框架会标准化为 `datetime64[ns]`，所有 rolling/shift 前必须升序排列 |
| `stock_code` | 股票代码 | 形如 `300001.SZ` |
| `open` | 开盘价 | 元 |
| `high` | 最高价 | 元 |
| `low` | 最低价 | 元 |
| `close` | 收盘价 | 元 |
| `volume` | 成交量 | 股，历史数据如提供 `vol` 会在读取层归一为 `volume` |

## 常用可选字段

| 字段 | 说明 | 口径/注意事项 |
| --- | --- | --- |
| `stock_name` | 股票名称 | 用于 ST 名称识别和诊断展示 |
| `amount` | 成交额 | TuShare 日线口径，单位为千元 |
| `net_mf_amount` | 主力净流入 | TuShare moneyflow 口径，单位为万元 |
| `net_amount` | 项目内常用主力净额别名 | 单位为万元；通常等价于 `net_mf_amount` |
| `net_amount_rate` | 主力净额占成交额比例 | 百分点，例如 `3.5` 表示 3.5% |
| `pre_close` | 昨收价 | 元 |
| `pct_chg` | 当日涨跌幅 | 百分点，例如 `9.8` 表示 9.8% |

## 加载配置

字段目录和当前配置请求列可直接查询：

```powershell
python tools/codex_strategy.py fields
python tools/codex_strategy.py fields --id net_mf_amount --json
python tools/codex_strategy.py fields --configured
```

`fields --configured` 反映当前配置会请求的行情列，不保证底层数据源存在该列或每行都非空；策略仍应
按自身合同处理缺失值。需要查看某项可选框架能力是否能在标准 session 执行时使用：

```powershell
python tools/codex_strategy.py capabilities --runtime v1 --id <capability> --json
```

`required_fields` 是研究审计元数据，不控制行情加载。策略需要基础 OHLCV 之外的字段时，应在 `STRATEGY_META["price_columns"]` 中声明运行时加载列，并在 `required_fields` 同步记录真实依赖；也可通过 `MYSQL_DAILY_FIELDS` / `DAILY_FIELDS` 环境变量扩展全局读取字段。不要在策略模块里直接调用 `get_config()`。

## AI 策略作者规则

- `STRATEGY_SEMANTICS` 用于声明策略类型、持有周期、市场、时间级别和主要信号，只服务 AI 理解、策略分类和展示，不驱动买卖、排序、择时、成交或风控。
- `STRATEGY_DESCRIPTION` 用于统一解释入场、出场、排序、市场特征、风险和数据假设，只服务解释、比较、审计和策略进化，不驱动回测执行。
- `STRATEGY_LIMITS` 是可选研究备注，scaffold 默认留空。即使显式声明，超过其中预算也只生成
  warning；指标数、模型结构、代码体积和圈复杂度不构成 Codex 策略生产上限。
- 策略里可自由计算指标、因子、模型分数和中间列；各函数只约束返回格式和生效语义，框架只要求最终输出 `sort_spec` 引用列、`buy_cond`、`sell_cond`。
- 市场特征层使用 `market_rank_specs` 声明一组或多组横截面排名源列和输出列，使用 `market_aggregate_specs` 声明日级市场宽度/聚合列，使用 `market_feature_passthrough_cols` 声明额外透传列；框架基于每个 `feature_col` 分别生成对应的 `rank_col/pct_col/count_col` 工具列，并基于每个 `source_col` 按 `trade_date` 生成 `output_col`。标准 rule/factor 路径在一次完整 panel feature hook 中产生 source 列，再由 `compute_feature_batch()` 写入对齐数组；专家 panel profile 直接在 `compute_features(PanelView)` 返回的 `FeatureBatch` 中产生这些列。
- 上述所有市场 source/output/passthrough 必须使用策略派生列名；键、OHLCV、标准信号、
  `next_up_limit_ok` 和框架内部 `__...__` 列为保留列。需要按收盘价排名时先复制为 `mf_close_level`，
  避免市场特征回注覆盖引擎行情。
- `market_aggregate_specs` 支持 `mean/sum/median/count/min/max/std`。典型宽度写法是在 rule/factor profile 的完整 panel feature hook 或 panel profile 的 `compute_features(PanelView)` 中生成按 stock_code 分组的 `mf_is_up`、`mf_is_strong` 等 0/1 源列，再声明 `{"source_col": "mf_is_up", "output_col": "market_pos_ratio", "agg": "mean"}`。
- `signal_strength/entry_gate/score` 只是模板示例写法；AI 可以删除、替换或不用。模板只约束函数输出格式和回测语义，不约束指标和方法。
- `score` 可以直接由规则、因子、模型输出或横截面特征生成，不需要先生成 `signal_strength`。
- 不要手写 `direction/buy_signal_raw/sell_signal_raw/buy_signal/sell_signal/next_up_limit_ok`。rule/factor 公式返回与工作 frame 索引对齐的 `buy_cond/sell_cond`，batch helper 生成 `SignalBatch`；panel profile 直接返回与 sealed panel 等长的 raw bool 数组。涨跌停、停牌、无量和 next-bar 可成交性由框架处理。
- 标准 panel 公式必须保持输入 frame 的 `trade_date/stock_code` 行、顺序和原始 OHLCV。panel hook 不返回或替换完整行情 frame，只能返回与 sealed panel 等长的 `FeatureBatch` / `SignalBatch` 数组；键、OHLCV、框架列和已登记列不可覆盖，共享可写底层数组会被拒绝。
- 不要用未来数据：禁止 `shift(-1)`、`pct_change(-n)`、未来窗口或 t+1 价格参与 t 日信号。
- 使用金额类字段时先确认单位：`amount` 是千元，`net_mf_amount/net_amount` 是万元，比例字段是百分点。
