# 外部验证就绪协议

Status: active
Last verified: 2026-07-26

本协议固定未来可信外部行情、全引擎配对、券商对账和资源测量报告的输入身份与状态语义。当前没有
新增可信外部数据，现阶段只能运行合成 dry-run，结果必须保持 `deferred_external_data` 或
`not_production_capital_certification`，不得显示为 pass 或资金生产认证。

## 报告合同

`core.external_validation.ExternalValidationReport` 使用严格、禁止额外字段的 schema，至少包含：

- 行情快照的 provider、snapshot ID、市场、日期范围、复权口径、时区、schema ID、SHA-256、
  字节数、行数和 `trusted_external` 标志。
- 候选 commit 与配置 SHA-256。
- `legacy`、`slot`、`slot_broker`、`broker`、`fixed_rebalance` 各一次的配对结果和资源测量。
- 订单、成交、费用、拒单、撤单、交易日历与价格口径的券商对账摘要。
- `completed_offline_engineering_evidence`、`deferred_external_data`、
  `not_production_capital_certification` 三种互斥状态之一。

缺 provider、hash、有效日期范围、候选 commit 或任一内置引擎时校验失败。合成或未受信输入不能
声明 `completed_offline_engineering_evidence`。

## 使用

生成 canonical JSON Schema：

```powershell
python tools/validate_external_validation_report.py --schema-out output/external-validation-schema-v1.json
```

校验报告：

```powershell
python tools/validate_external_validation_report.py output/external-validation-report.json
```

退出码 `0` 只表示报告结构和状态合同有效，不表示外部数据验证通过，也不构成资金生产认证。
