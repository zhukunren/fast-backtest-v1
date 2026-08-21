export interface FilterOption {
  label: string
  value: string
  count?: number
}

export interface EvaluationWindow {
  start_time?: string | null
  end_time?: string | null
  start?: string | null
  end?: string | null
}

export interface StrategyEvaluationIdentity extends Record<string, unknown> {
  id?: string | null
  context_id?: string | null
  evaluation_id?: string | null
  run_kind?: string | null
  is_default?: boolean | null
  effective_window?: EvaluationWindow | null
  configured_window?: EvaluationWindow | null
  data_cutoff?: string | null
  execution_config_hash?: string | null
  cost_model_hash?: string | null
}

export interface CatalogEvaluationSummary extends Record<string, unknown> {
  comparable?: boolean | null
  status?: string | null
  status_label?: string | null
  identity_id?: string | null
  evaluation_id?: string | null
  context_id?: string | null
  effective_window?: EvaluationWindow | null
  data_cutoff?: string | null
  execution_config_hash?: string | null
}

export interface CatalogOverview {
  total: number
  cached: number
  completed: number
  valid?: number
  failed?: number
  stale?: number
  idle?: number
  no_signal?: number
  waiting?: number
  positive: number
  high_risk?: number
  completion_rate?: number
  avg_return?: number | null
  latest_timestamp?: string | null
  best?: {
    name?: string
    title?: string
    cum_return?: number | null
    ann_return?: number | null
    sharpe?: number | null
    calmar?: number | null
    max_drawdown?: number | null
    timestamp?: string | null
  } | null
}

export interface StrategyLibraryItem {
  name: string
  title?: string
  summary?: string | null
  buy_logic?: string | null
  sell_logic?: string | null
  style_label?: string | null
  portfolio_label?: string | null
  family?: string | null
  family_label?: string | null
  strategy_description?: StrategyDescriptionManifest | null
  description_status?: string | null
  description_diagnostics?: string[]
  rank?: number | null
  status?: string
  status_label?: string
  start_time?: string | null
  end_time?: string | null
  timestamp?: string | null
  cum_return?: number | null
  ann_return?: number | null
  sharpe?: number | null
  calmar?: number | null
  max_drawdown?: number | null
  win_rate?: number | null
  trade_count?: number | null
  sparkline?: Array<number | null> | null
  error?: string | null
  strategy_version?: string | null
  version_status?: string | null
  version_status_label?: string | null
  version_hash?: string | null
  source_sha256?: string | null
  source_hash?: string | null
  source_size?: number | null
  config_hash?: string | null
  full_config_hash?: string | null
  run_source_hash?: string | null
  run_config_hash?: string | null
  run_id?: string | null
  git_commit?: string | null
  git_describe?: string | null
  git_dirty?: boolean | null
  cache_status?: string | null
  cache_reason?: string | null
  cache_generation_id?: string | null
  cache_artifact_count?: number | null
  cache_size_bytes?: number | null
  cache_actual_start_time?: string | null
  cache_actual_end_time?: string | null
  cache_date_source?: string | null
  latest_attempt_generation_id?: string | null
  latest_attempt_status?: string | null
  latest_attempt_error?: string | null
  latest_attempt_activated?: boolean | null
  latest_attempt_evaluation_id?: string | null
  latest_attempt_evaluation_context_id?: string | null
  latest_attempt_run_kind?: string | null
  evaluation_identity?: StrategyEvaluationIdentity | null
  evaluation_id?: string | null
  evaluation_context_id?: string | null
  evaluation_status?: string | null
  evaluation_status_label?: string | null
  evaluation_comparable?: boolean | null
  evaluation_window?: EvaluationWindow | null
  evaluation_window_label?: string | null
  style?: string | null
  portfolio?: string | null
}

export type StrategyDescriptionRule = string | {
  text?: string
  all?: StrategyDescriptionRule[]
  any?: StrategyDescriptionRule[]
  none?: StrategyDescriptionRule[]
}

export interface StrategyDescriptionManifest {
  schema_version: 2
  authoring: {
    method: 'llm_source_review'
    language?: string
  }
  source_file?: string | null
  source_sha256?: string | null
  manifest_file?: string | null
  source_status?: string | null
  source?: { file: string; sha256: string }
  profile: {
    title: string
    summary: string
    buy_logic: string
    sell_logic: string
    position_text: string
    style_label: string
  }
  runtime: Record<string, unknown>
  semantics?: Record<string, unknown>
  description?: Record<string, unknown>
  limits?: Record<string, unknown>
  buy: StrategyDescriptionRule[] | Record<string, unknown>
  sell: StrategyDescriptionRule[] | Record<string, unknown>
  timing?: StrategyDescriptionRule[] | Record<string, unknown>
  risk?: StrategyDescriptionRule[] | Record<string, unknown>
  ranking?: StrategyDescriptionRule[] | Record<string, unknown>
  universe?: StrategyDescriptionRule[] | Record<string, unknown>
  data?: StrategyDescriptionRule[] | Record<string, unknown>
  dependencies?: StrategyDescriptionRule[] | Record<string, unknown>
  indicators?: StrategyIndicatorDefinition[]
  investor_explanation?: StrategyInvestorExplanation | null
}

export interface StrategyInvestorExplanationItem {
  title?: string
  description: string
  example?: string
}

export interface StrategyInvestorExplanationGroup {
  title: string
  logic?: string
  items: StrategyInvestorExplanationItem[]
}

export interface StrategyInvestorExplanationSection {
  summary?: string
  groups: StrategyInvestorExplanationGroup[]
}

export interface StrategyInvestorExplanation {
  buy?: StrategyInvestorExplanationSection
  sell?: StrategyInvestorExplanationSection
}

export interface StrategyIndicatorDefinition {
  name: string
  purpose?: string
  formula: string | string[]
  variables?: string[]
  parameters?: string[]
  usage?: string[]
  notes?: string[]
}

export interface LibraryCatalogResponse {
  overview: CatalogOverview
  filters: {
    families: FilterOption[]
    statuses: FilterOption[]
    styles?: FilterOption[]
    portfolios?: FilterOption[]
    evaluation_windows?: FilterOption[]
  }
  evaluation?: CatalogEvaluationSummary | null
  items: StrategyLibraryItem[]
}

export interface LibraryDetailResponse extends Record<string, unknown> {
  name?: string
  title?: string
  status?: string
  status_label?: string
  timestamp?: string | null
  cum_return?: number | null
  max_drawdown?: number | null
  win_rate?: number | null
  trade_count?: number | null
  final_net_value?: number | null
  strategy_description?: StrategyDescriptionManifest | null
  catalog_entry?: Record<string, unknown>
  result?: Record<string, unknown>
  extra?: Record<string, unknown>
}

export interface LibraryMetricsResponse extends Record<string, unknown> {
  cum_return?: number | null
  max_drawdown?: number | null
  ann_return?: number | null
  sharpe?: number | null
  calmar?: number | null
  max_dd_days?: number | null
  max_dd_recovery_days?: number | null
  max_drawdown_start_date?: string | null
  max_drawdown_end_date?: string | null
  max_dd_recovery_date?: string | null
  win_rate?: number | null
  total_trades?: number | null
  avg_holding_days?: number | null
  median_holding_days?: number | null
  profit_loss_ratio?: number | null
  profit_factor?: number | null
  avg_trade_return?: number | null
  monthly_trend?: Array<{
    period?: string
    return?: number | null
  }>
}

export interface LibraryHealthItem {
  name: string
  status: string
  module?: string
  strategy_version?: string
  version_status?: string
  version_status_label?: string
  source_hash?: string
  config_hash?: string
  run_source_hash?: string
  run_config_hash?: string
  run_id?: string
  cache_status?: string
  cache_reason?: string
  cache_generation_id?: string
  cache_artifact_count?: number
  cache_size_bytes?: number
  issues?: string[]
  cache_actual_start_time?: string
  cache_actual_end_time?: string
  cache_date_source?: string
}

export interface LibraryHealthResponse {
  overview: {
    total: number
    ok: number
    warning: number
    error: number
    valid_cache: number
    invalid_cache: number
    failed_cache: number
    missing_cache: number
  }
  items: LibraryHealthItem[]
}

export interface LibraryAnalysisDiagnostic {
  code: string
  label: string
  severity?: string
  detail?: string
}

export interface LibraryAnalysisWarning {
  code: string
  label: string
}

export interface LibraryPeriodStat {
  period: string
  start_date?: string
  end_date?: string
  day_count?: number
  return?: number | null
  max_drawdown?: number | null
  positive_days?: number
  negative_days?: number
}

export interface ApiEnvelope<T> {
  ok: boolean
  data: T | null
  error?: {
    code: string
    message: string
    detail?: unknown
  } | null
  meta?: Record<string, unknown>
}

export interface LibraryMarketStateStat {
  state: string
  label: string
  day_count?: number
  return?: number | null
  benchmark_return?: number | null
  excess_return?: number | null
  ann_return?: number | null
  max_drawdown?: number | null
  positive_day_ratio?: number | null
  avg_daily_return?: number | null
  volatility?: number | null
}

export interface LibraryMarketStateResponse {
  name: string
  benchmark_label?: string
  source?: string
  method?: Record<string, unknown>
  states?: {
    trend?: LibraryMarketStateStat[]
    volatility?: LibraryMarketStateStat[]
  }
  observations?: Array<Record<string, unknown>>
}

export interface LibraryScoreComponentMap {
  return_quality?: number
  risk_control?: number
  stability?: number
  trade_quality?: number
  sample_quality?: number
  regime_robustness?: number
}

export interface LibraryAnalysisResponse {
  name: string
  score?: {
    value?: number | null
    grade?: string
    components?: LibraryScoreComponentMap
    weights?: LibraryScoreComponentMap
  }
  sample_quality?: {
    start_date?: string
    end_date?: string
    date_count?: number
    daily_return_count?: number
    trade_count?: number
    trade_pnl_sample_count?: number
    holding_date_count?: number
    holding_coverage?: number | null
    warnings?: LibraryAnalysisWarning[]
  }
  return_profile?: {
    cum_return?: number | null
    ann_return?: number | null
    max_drawdown?: number | null
    sharpe?: number | null
    calmar?: number | null
    positive_day_ratio?: number | null
    positive_month_ratio?: number | null
  }
  trade_profile?: Record<string, number | null | undefined>
  period_stats?: {
    yearly?: LibraryPeriodStat[]
    quarterly?: LibraryPeriodStat[]
    monthly?: LibraryPeriodStat[]
  }
  diagnostics?: LibraryAnalysisDiagnostic[]
  market_state?: LibraryMarketStateResponse | null
}

export interface LibraryReviewStockRow extends Record<string, unknown> {
  code?: string
  name?: string
  holding_days?: number | null
  first_date?: string
  last_date?: string
  max_holding_value?: number | null
  latest_holding_value?: number | null
  latest_unrealized_pnl?: number | null
  realized_pnl?: number | null
  realized_return_sum?: number | null
  total_pnl?: number | null
  trade_count?: number | null
  avg_position_ratio?: number | null
}

export interface LibraryReviewResponse {
  name: string
  overview?: Record<string, number | null | undefined>
  stocks?: LibraryReviewStockRow[]
}

export interface EnhancedCompareStrategy extends Record<string, unknown> {
  name: string
  title?: string
  score?: number | null
  grade?: string
  cum_return?: number | null
  ann_return?: number | null
  max_drawdown?: number | null
  sharpe?: number | null
  calmar?: number | null
  positive_month_ratio?: number | null
  win_rate?: number | null
  trade_count?: number | null
}

export interface EnhancedCompareResponse {
  names: string[]
  strategies: EnhancedCompareStrategy[]
  rankings?: EnhancedCompareStrategy[]
  correlation_matrix?: {
    names: string[]
    values: Array<Array<number | null>>
  }
  equal_weight?: {
    dates: string[]
    net_value: number[]
  }
  best?: Record<string, EnhancedCompareStrategy | null>
}

export interface LibraryReportResponse {
  content: string
}

export interface NetValuePayload {
  dates?: string[]
  net_value?: number[]
  benchmark_net_value?: number[]
  excess_net_value?: number[]
  benchmark_label?: string
  benchmark_name?: string
  benchmark_code?: string
}

export type TableValue = string | number | boolean | null | undefined
export type TableRow = Record<string, TableValue>

export interface TradesPayload {
  columns?: string[]
  records?: TableRow[]
  execution_columns?: string[]
  execution_records?: TableRow[]
  execution_meta?: Record<string, number>
  order_columns?: string[]
  order_records?: TableRow[]
  order_meta?: Record<string, number>
}

export interface TradeSummaryPayload {
  artifact_type?: string | null
  schema_version?: number | string | null
  view: 'summary'
  columns: string[]
  execution_columns: string[]
  order_columns: string[]
  records_preview: TableRow[]
  execution_records_preview: TableRow[]
  order_records_preview: TableRow[]
  latest_signals: {
    missing_code: boolean
    buy: {
      date: string
      items: Array<{ code: string; name: string }>
    }
    sell: {
      date: string
      items: Array<{ code: string; name: string }>
    }
  }
  execution_meta?: Record<string, number>
  order_meta?: Record<string, number>
  diagnostics?: Record<string, unknown>
  summary: {
    record_count: number
    execution_record_count: number
    order_record_count: number
    record_date_range?: { first: string; last: string } | null
    execution_date_range?: { first: string; last: string } | null
    order_date_range?: { first: string; last: string } | null
    fills_ledger_present?: boolean
  }
}

export interface DailyHoldingRow extends Record<string, unknown> {
  slot?: string
  code?: string
  name?: string
  dir?: string
  close?: number | null
  shares?: number | null
  original_shares?: number | null
  shares_scaled?: boolean
  holding_value?: number | null
  cost_price?: number | null
  cost_value?: number | null
  unrealized_pnl?: number | null
  unrealized_pnl_pct?: number | null
  nv?: number | null
  utilization_rate?: number | null
  effective_position_ratio?: number | null
}

export type HoldingsDisplayMode = 'simple' | 'pro'
export type HoldingsCurrentView = 'detail' | 'stock' | 'slot'

export interface StockAggregatedHoldingRow extends Record<string, unknown> {
  code?: string
  name?: string
  slots_text?: string
  slot_count?: number | null
  close?: number | null
  cost_price?: number | null
  shares?: number | null
  original_shares?: number | null
  shares_scaled?: boolean
  cost_value?: number | null
  holding_value?: number | null
  unrealized_pnl?: number | null
  unrealized_pnl_pct?: number | null
  position_ratio?: number | null
}

export interface SlotStockItem extends Record<string, unknown> {
  code?: string
  name?: string
  unrealized_pnl?: number | null
}

export interface SlotAggregatedHoldingRow extends Record<string, unknown> {
  slot?: string
  stock_count?: number | null
  stocks_text?: string
  stock_items?: SlotStockItem[]
  shares?: number | null
  original_shares?: number | null
  shares_scaled?: boolean
  cost_value?: number | null
  holding_value?: number | null
  unrealized_pnl?: number | null
  unrealized_pnl_pct?: number | null
  nv?: number | null
  position_ratio?: number | null
}

export interface DailyHoldingHistoryRow extends DailyHoldingRow {
  trade_date?: string
}

export interface DailyHoldingsSummary extends Record<string, unknown> {
  net_value?: number | null
  asset?: number | null
  cash?: number | null
  position_value?: number | null
  cum_pnl?: number | null
  daily_pnl?: number | null
  realized_pnl?: number | null
  unrealized_pnl?: number | null
  daily_return?: number | null
  holding_count?: number | null
}

export interface DailyHoldingsPayload {
  dates: string[]
  holdings_by_date: Record<string, DailyHoldingRow[]>
  summary_by_date: Record<string, DailyHoldingsSummary>
}

export interface DailyHoldingsSummaryPayload {
  artifact_type?: string | null
  schema_version?: number | string | null
  view: 'summary'
  dates: string[]
  summary_by_date: Record<string, DailyHoldingsSummary>
  latest_date: string
  latest_summary?: DailyHoldingsSummary | null
  latest_holdings_preview: DailyHoldingRow[]
  summary: {
    date_count: number
    populated_date_count: number
    holding_row_count: number
    latest_holding_count: number
    latest_preview_truncated?: boolean
  }
}

export interface BacktestWindow {
  start_time: string
  end_time: string
}

export interface BacktestProgress {
  running: boolean
  task_id?: string
  total?: number
  completed?: number
  current?: string
  elapsed_sec?: number
  task_age_sec?: number
  updated_at?: string
  finished_at?: string
  cancelled?: boolean
  results?: BacktestRunResult[]
}

export interface BacktestRunResponse {
  status?: string
  error?: string
  progress?: BacktestProgress
}

export interface BacktestRunResult {
  name?: string
  module?: string
  status?: string
  duration_sec?: number | null
  error?: string | null
}

export interface WebLogFile {
  name: string
  size: number
  modified_time?: string
}

export interface WebLogEntry extends Record<string, unknown> {
  timestamp?: string
  level?: string
  tag?: string
  message?: string
}

export interface WebLogListResponse {
  log_dir?: string
  files: WebLogFile[]
  latest?: string
}

export interface WebLogTailResponse {
  file?: string
  lines?: number
  entries: WebLogEntry[]
}
