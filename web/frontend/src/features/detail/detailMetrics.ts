import type { NetValuePayload } from '../../types/library'

export const TRADING_DAYS_PER_YEAR = 250

export interface CurveMetricSnapshot {
  cum_return: number | null
  max_drawdown: number | null
  ann_return: number | null
  sharpe: number | null
  ann_volatility: number | null
  sortino: number | null
  information_ratio: number | null
}

export function sliceNetValuePayload(payload: NetValuePayload | null, startDate: string): NetValuePayload | null {
  if (!payload) return null
  const dates = Array.isArray(payload.dates) ? payload.dates.map(String) : []
  if (!dates.length || !startDate) return payload
  const startIndex = dates.findIndex(date => date >= startDate)
  if (startIndex <= 0) return payload
  return {
    ...payload,
    dates: dates.slice(startIndex),
    net_value: sliceNumberArray(payload.net_value, startIndex),
    benchmark_net_value: sliceNumberArray(payload.benchmark_net_value, startIndex),
    excess_net_value: sliceNumberArray(payload.excess_net_value, startIndex),
  }
}

export function trimNetValuePayloadEnd(payload: NetValuePayload | null, endDate: string): NetValuePayload | null {
  if (!payload || !endDate) return payload
  const dates = Array.isArray(payload.dates) ? payload.dates.map(String) : []
  if (!dates.length || dates[dates.length - 1] <= endDate) return payload

  let endIndex = dates.length - 1
  while (endIndex >= 0 && dates[endIndex] > endDate) endIndex -= 1
  if (endIndex < 0) return payload

  const endExclusive = endIndex + 1
  return {
    ...payload,
    dates: dates.slice(0, endExclusive),
    net_value: sliceNumberArrayEnd(payload.net_value, endExclusive),
    benchmark_net_value: sliceNumberArrayEnd(payload.benchmark_net_value, endExclusive),
    excess_net_value: sliceNumberArrayEnd(payload.excess_net_value, endExclusive),
  }
}

function sliceNumberArray(values: number[] | undefined, startIndex: number): number[] | undefined {
  if (!Array.isArray(values)) return values
  return values.slice(startIndex)
}

function sliceNumberArrayEnd(values: number[] | undefined, endExclusive: number): number[] | undefined {
  if (!Array.isArray(values)) return values
  return values.slice(0, endExclusive)
}

export function computeCurveMetrics(
  payload: NetValuePayload | null,
  initialValue: number | null = null,
): CurveMetricSnapshot {
  const values = Array.isArray(payload?.net_value)
    ? payload.net_value.map(Number).filter(value => Number.isFinite(value) && value > 0)
    : []
  if (values.length < 2) {
    return {
      cum_return: null,
      max_drawdown: null,
      ann_return: null,
      sharpe: null,
      ann_volatility: null,
      sortino: null,
      information_ratio: null,
    }
  }

  const first = values[0]
  const last = values[values.length - 1]
  const normalizedInitialValue = Number(initialValue)
  const hasInitialValue = Number.isFinite(normalizedInitialValue) && normalizedInitialValue > 0
  const baseline = hasInitialValue ? normalizedInitialValue : first
  const cumReturn = baseline > 0 ? (last / baseline) - 1 : null
  let peak = baseline
  let maxDrawdown = 0
  const dailyReturns: number[] = []
  let previous = baseline

  for (const current of values) {
    if (current > peak) peak = current
    if (peak > 0) maxDrawdown = Math.max(maxDrawdown, 1 - (current / peak))
    if (previous > 0) {
      const dailyReturn = (current / previous) - 1
      if (Number.isFinite(dailyReturn)) dailyReturns.push(dailyReturn)
    }
    previous = current
  }

  const periodCount = hasInitialValue ? values.length : values.length - 1
  const annualReturn = baseline > 0 && last > 0
    ? Math.pow(last / baseline, TRADING_DAYS_PER_YEAR / Math.max(1, periodCount)) - 1
    : null
  const sharpe = computeAnnualizedSharpe(dailyReturns)
  const annVolatility = computeAnnualizedVolatility(dailyReturns)
  const sortino = computeAnnualizedSortino(dailyReturns)
  const benchmarkValues = Array.isArray(payload?.benchmark_net_value)
    ? payload.benchmark_net_value.map(Number).filter(value => Number.isFinite(value) && value > 0)
    : []
  const informationRatio = benchmarkValues.length === values.length
    ? computeAnnualizedInformationRatio(
      dailyReturns,
      computeDailyReturns(benchmarkValues, hasInitialValue ? normalizedInitialValue : null),
    )
    : null

  return {
    cum_return: cumReturn,
    max_drawdown: maxDrawdown,
    ann_return: Number.isFinite(annualReturn) ? annualReturn : null,
    sharpe,
    ann_volatility: annVolatility,
    sortino,
    information_ratio: informationRatio,
  }
}

function computeAnnualizedSharpe(values: number[]): number | null {
  if (values.length < 2) return null
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance = values.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / values.length
  const std = Math.sqrt(variance)
  if (!Number.isFinite(std) || std <= 0) return null
  return (mean / std) * Math.sqrt(TRADING_DAYS_PER_YEAR)
}

function computeDailyReturns(values: number[], initialValue: number | null = null): number[] {
  const returns: number[] = []
  const normalizedInitialValue = Number(initialValue)
  const hasInitialValue = Number.isFinite(normalizedInitialValue) && normalizedInitialValue > 0
  let previous = hasInitialValue ? normalizedInitialValue : values[0]
  const startIndex = hasInitialValue ? 0 : 1
  for (let index = startIndex; index < values.length; index += 1) {
    if (previous > 0) {
      const value = (values[index] / previous) - 1
      if (Number.isFinite(value)) returns.push(value)
    }
    previous = values[index]
  }
  return returns
}

function computeAnnualizedVolatility(values: number[]): number | null {
  if (values.length < 2) return null
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance = values.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / values.length
  const volatility = Math.sqrt(variance) * Math.sqrt(TRADING_DAYS_PER_YEAR)
  return Number.isFinite(volatility) ? volatility : null
}

function computeAnnualizedSortino(values: number[]): number | null {
  if (values.length < 2) return null
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const downsideVariance = values.reduce((sum, value) => sum + (Math.min(0, value) ** 2), 0) / values.length
  const downsideDeviation = Math.sqrt(downsideVariance)
  if (!Number.isFinite(downsideDeviation) || downsideDeviation <= 0) return null
  return (mean / downsideDeviation) * Math.sqrt(TRADING_DAYS_PER_YEAR)
}

function computeAnnualizedInformationRatio(strategyReturns: number[], benchmarkReturns: number[]): number | null {
  const count = Math.min(strategyReturns.length, benchmarkReturns.length)
  if (count < 2) return null
  const activeReturns = strategyReturns.slice(0, count).map((value, index) => value - benchmarkReturns[index])
  return computeAnnualizedSharpe(activeReturns)
}
