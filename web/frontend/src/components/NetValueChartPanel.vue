<script setup lang="ts">
import { ChartNoAxesCombined } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init } from '../lib/echarts'
import type { ECharts, EChartsOption, SeriesOption } from '../lib/echarts'
import type { NetValuePayload } from '../types/library'
import {
  clamp,
  computeSliceMetrics,
  formatDrawdown,
  formatPercent,
  normalizeRangeValue,
  resolveFixedAxisRange,
  resolveRangeIndices,
} from '../utils/chart'

interface SelectionState {
  startIndex: number
  endIndex: number
  yMin: number
  yMax: number
}

interface DragState {
  startX: number
  startY: number
  currentX: number
  currentY: number
}

interface IndexRange {
  startIndex: number
  endIndex: number
}

interface AxisScale {
  min: number
  max: number
  interval: number
}

type ChartVariant = 'default' | 'workbench'

const props = withDefaults(defineProps<{
  data: NetValuePayload | null
  focusDate: string
  title?: string
  note?: string
  showControls?: boolean
  showDataZoom?: boolean
  variant?: ChartVariant
  baselineValue?: number | null
}>(), {
  title: '净值走势图',
  showControls: true,
  showDataZoom: true,
  variant: 'default',
  baselineValue: null,
})

const emit = defineEmits<{
  focusDate: [date: string]
}>()

const chartWrapRef = ref<HTMLDivElement | null>(null)
const chartRef = ref<HTMLDivElement | null>(null)
const selectionBandStyle = ref<Record<string, string>>({
  left: '0px',
  top: '0px',
  width: '0px',
  height: '0px',
})
const selectionBoxStyle = ref<Record<string, string>>({
  left: '0px',
  top: '0px',
})
const selectionBoxPayload = ref<{
  dateRange: string
  valueRange: string
  returnRate: number
  maxDrawdown: number
} | null>(null)
const rangeValue = ref<'all' | 10 | 60>('all')
const isBandVisible = ref(false)
const isSelectionBoxVisible = ref(false)

let chart: ECharts | null = null
let dragState: DragState | null = null
let activeSelection: SelectionState | null = null
let lastFocusedIndex = -1
let workbenchVisibleRange: IndexRange | null = null

const chartData = computed(() => {
  const dates = Array.isArray(props.data?.dates) ? props.data?.dates?.map(String) : []
  const netValues = Array.isArray(props.data?.net_value) ? props.data.net_value.map(Number).filter(Number.isFinite) : []
  const benchmarkValues = Array.isArray(props.data?.benchmark_net_value) ? props.data.benchmark_net_value.map(Number) : []
  const excessValues = Array.isArray(props.data?.excess_net_value) ? props.data.excess_net_value.map(Number) : []
  const hasSameLength = dates.length > 0 && netValues.length === dates.length
  const safeDates = hasSameLength ? dates : []
  return {
    dates: safeDates,
    values: hasSameLength ? netValues : [],
    benchmarkValues: benchmarkValues.length === dates.length ? benchmarkValues : [],
    excessValues: excessValues.length === dates.length ? excessValues : [],
    benchmarkLabel: String(props.data?.benchmark_label || props.data?.benchmark_name || '创业板指 399006.SZ'),
  }
})

const hasChartData = computed(() => chartData.value.dates.length > 0)
const chartBaselineValue = computed(() => {
  const value = Number(props.baselineValue)
  return Number.isFinite(value) && value > 0 ? value : null
})
const drawdownValues = computed(() => buildDrawdownValues(chartData.value.values, chartBaselineValue.value))
const chartPalette = computed(() => props.variant === 'workbench'
  ? {
      strategy: '#dc2626',
      strategyArea: 'rgba(220, 38, 38, 0.06)',
      benchmark: '#2563eb',
      excess: '#64748b',
      drawdown: '#0f766e',
      drawdownArea: 'rgba(15, 118, 110, 0.16)',
    }
  : {
      strategy: '#2563eb',
      strategyArea: 'rgba(37, 99, 235, 0.08)',
      benchmark: '#64748b',
      excess: '#0f766e',
      drawdown: '#0f766e',
      drawdownArea: 'rgba(15, 118, 110, 0.16)',
    })
const chartNote = computed(() => {
  if (!hasChartData.value) return '当前策略还没有可展示的净值曲线。'
  const parts = ['黑底卡片会随十字光标移动并同步每日持仓焦点。']
  if (chartData.value.benchmarkValues.length) {
    parts.push(`基准：${chartData.value.benchmarkLabel}`)
  }
  if (chartData.value.excessValues.length) {
    parts.push('包含超额曲线')
  }
  return parts.join(' · ')
})
const displayChartNote = computed(() => props.note ?? chartNote.value)
const chartAriaLabel = computed(() => {
  const { dates, values } = chartData.value
  if (!dates.length || !values.length) return `${props.title}，暂无可用净值数据。`
  const firstValue = Number(values[0])
  const latestValue = Number(values[values.length - 1])
  const returnBaseline = chartBaselineValue.value ?? firstValue
  const periodReturn = returnBaseline > 0 && Number.isFinite(latestValue)
    ? (latestValue / returnBaseline) - 1
    : null
  const maximumDrawdown = drawdownValues.value.length
    ? Math.min(...drawdownValues.value.filter(Number.isFinite))
    : null
  return [
    props.title,
    `${dates[0]}至${dates[dates.length - 1]}，共${dates.length}个净值点`,
    `最新净值${Number.isFinite(latestValue) ? latestValue.toFixed(4) : '-'}`,
    `区间收益${periodReturn == null ? '-' : formatPercent(periodReturn)}`,
    `最大回撤${maximumDrawdown == null ? '-' : formatDrawdown(maximumDrawdown)}`,
  ].join('。')
})

watch(chartData, async () => {
  await nextTick()
  renderChart()
})

watch(chartBaselineValue, async () => {
  await nextTick()
  renderChart()
})

watch(
  () => props.variant,
  async () => {
    await nextTick()
    renderChart()
  },
)

watch(
  () => props.focusDate,
  value => {
    if (props.variant === 'workbench') return
    if (!value || !chartData.value.dates.length) return
    const index = chartData.value.dates.indexOf(value)
    if (index >= 0) {
      syncFocusToIndex(index)
    }
  },
)

onMounted(() => {
  renderChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  disposeChart()
})

function handleResize() {
  chart?.resize()
  if (activeSelection) {
    syncSelectionOverlay(activeSelection)
  }
}

function disposeChart() {
  if (!chart) return
  chart.off('datazoom')
  const zr = chart.getZr()
  zr.off('mousedown')
  zr.off('mousemove')
  zr.off('mouseup')
  zr.off('globalout')
  chart.dispose()
  chart = null
  workbenchVisibleRange = null
}

function buildOption(): EChartsOption {
  const hasBenchmark = chartData.value.benchmarkValues.length === chartData.value.dates.length
  const hasExcess = chartData.value.excessValues.length === chartData.value.dates.length
  const isWorkbench = props.variant === 'workbench'
  const palette = chartPalette.value
  const benchmarkSeriesName = isWorkbench
    ? chartData.value.benchmarkLabel.replace(/\s+\d{6}\.(?:SZ|SH)$/i, '')
    : chartData.value.benchmarkLabel
  const legendData = [
    '策略净值',
    ...(hasBenchmark ? [benchmarkSeriesName] : []),
    ...(hasExcess ? ['超额曲线'] : []),
  ]
  const series: SeriesOption[] = [
    {
      id: 'net-value-series',
      name: '策略净值',
      type: 'line',
      data: chartData.value.values,
      showSymbol: false,
      lineStyle: { width: 2, color: palette.strategy },
      itemStyle: { color: palette.strategy },
      areaStyle: { color: palette.strategyArea },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: 'rgba(132, 148, 168, 0.5)', type: 'dashed' },
        label: { color: '#8494a8', formatter: '1.00' },
        data: [{ yAxis: 1 }],
      },
    },
  ]
  if (hasBenchmark) {
    series.push({
      id: 'benchmark-series',
      name: benchmarkSeriesName,
      type: 'line',
      data: chartData.value.benchmarkValues,
      showSymbol: false,
      lineStyle: { width: 1.7, color: palette.benchmark },
      itemStyle: { color: palette.benchmark },
    })
  }
  if (hasExcess) {
    series.push({
      id: 'excess-series',
      name: '超额曲线',
      type: 'line',
      data: chartData.value.excessValues,
      showSymbol: false,
      lineStyle: { width: 1.9, color: palette.excess },
      itemStyle: { color: palette.excess },
    })
  }
  if (isWorkbench) {
    series.push({
      id: 'drawdown-risk-series',
      name: '回撤',
      type: 'line',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: drawdownValues.value,
      showSymbol: false,
      lineStyle: { width: 1.35, color: palette.drawdown },
      itemStyle: { color: palette.drawdown },
      areaStyle: { color: palette.drawdownArea },
      z: 2,
    })
  }

  const primaryXAxis = {
    type: 'category' as const,
    gridIndex: 0,
    data: chartData.value.dates,
    boundaryGap: false,
    axisLabel: {
      show: !isWorkbench,
      color: '#8494a8',
      rotate: isWorkbench ? 0 : 28,
      hideOverlap: true,
    },
    axisTick: { show: !isWorkbench },
    axisLine: { lineStyle: { color: '#d9e2ec' } },
  }
  const workbenchTimeAxis = {
    type: 'category' as const,
    gridIndex: 1,
    data: chartData.value.dates,
    boundaryGap: false,
    axisLabel: {
      color: '#8494a8',
      fontSize: 10,
      width: 64,
      overflow: 'truncate' as const,
      hideOverlap: true,
      showMinLabel: true,
      showMaxLabel: true,
    },
    axisTick: { show: false },
    axisLine: { lineStyle: { color: '#d9e2ec' } },
  }
  const zoomAxisIndex = isWorkbench ? [0, 1] : 0

  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    axisPointer: isWorkbench ? { link: [{ xAxisIndex: [0, 1] }] } : undefined,
    legend: (hasBenchmark || hasExcess)
      ? {
          data: legendData,
          top: 2,
          left: 56,
          itemWidth: 18,
          itemHeight: 2,
          textStyle: { color: '#586a80', fontSize: 12 },
        }
      : undefined,
    tooltip: {
      trigger: 'axis',
      triggerOn: 'mousemove|click|mousewheel',
      alwaysShowContent: !isWorkbench,
      backgroundColor: 'rgba(15, 23, 42, 0.88)',
      borderColor: 'rgba(148, 163, 184, 0.24)',
      borderWidth: 1,
      textStyle: { color: '#e2e8f0' },
      axisPointer: { type: 'cross', snap: true, crossStyle: { color: 'rgba(148, 163, 184, 0.45)' } },
      formatter: isWorkbench ? formatWorkbenchTooltip : undefined,
      valueFormatter: (value: unknown) => {
        const num = Number(value)
        if (!Number.isFinite(num)) return '-'
        return num.toFixed(4)
      },
      position: (point, _params, _dom, _rect, size) => {
        const [x, y] = point as number[]
        const [boxWidth, boxHeight] = size.contentSize as number[]
        const [viewWidth, viewHeight] = size.viewSize as number[]
        return [
          clamp(x + 18, 12, Math.max(12, viewWidth - boxWidth - 12)),
          clamp(y - boxHeight - 18, 12, Math.max(12, viewHeight - boxHeight - 12)),
        ]
      },
    },
    grid: isWorkbench
      ? [
          {
            left: 56,
            right: 36,
            top: 36,
            bottom: props.showDataZoom ? 104 : 68,
          },
          {
            left: 56,
            right: 36,
            height: 36,
            bottom: props.showDataZoom ? 54 : 22,
          },
        ]
      : {
          left: 56,
          right: 24,
          top: (hasBenchmark || hasExcess) ? 56 : 28,
          bottom: props.showDataZoom ? 48 : 22,
        },
    xAxis: isWorkbench ? [primaryXAxis, workbenchTimeAxis] : primaryXAxis,
    yAxis: buildChartYAxis(workbenchVisibleRange),
    dataZoom: props.showDataZoom
      ? [
          { id: 'detail-zoom-inside', type: 'inside', xAxisIndex: zoomAxisIndex },
          {
            id: 'detail-zoom-slider',
            type: 'slider',
            xAxisIndex: zoomAxisIndex,
            height: 12,
            bottom: isWorkbench ? 16 : 14,
            showDetail: false,
            borderColor: '#d9e2ec',
            fillerColor: 'rgba(37, 99, 235, 0.14)',
            backgroundColor: 'rgba(217, 226, 236, 0.28)',
            textStyle: { color: '#8494a8' },
          },
        ]
      : undefined,
    series,
  }
}

function buildDrawdownValues(values: number[], initialValue: number | null = null): number[] {
  let peak = initialValue != null && initialValue > 0 ? initialValue : Number.NEGATIVE_INFINITY
  return values.map(rawValue => {
    const value = Number(rawValue)
    if (!Number.isFinite(value)) return 0
    peak = Math.max(peak, value)
    return peak > 0 ? Math.min(0, (value / peak) - 1) : 0
  })
}

function normalizeIndexRange(range: IndexRange | null): IndexRange {
  const lastIndex = Math.max(0, chartData.value.dates.length - 1)
  if (!range) return { startIndex: 0, endIndex: lastIndex }
  const startIndex = clamp(Math.round(range.startIndex), 0, lastIndex)
  const endIndex = clamp(Math.round(range.endIndex), startIndex, lastIndex)
  return { startIndex, endIndex }
}

function buildNiceStep(rawStep: number): number {
  if (!Number.isFinite(rawStep) || rawStep <= 0) return 0.01
  const exponent = Math.floor(Math.log10(rawStep))
  const magnitude = 10 ** exponent
  const fraction = rawStep / magnitude
  const niceFraction = fraction <= 1
    ? 1
    : fraction <= 2
      ? 2
      : fraction <= 2.5
        ? 2.5
        : fraction <= 5
          ? 5
          : 10
  return niceFraction * magnitude
}

function roundAxisValue(value: number): number {
  return Number(value.toPrecision(12))
}

function buildWorkbenchValueScale(range: IndexRange | null): AxisScale {
  const visibleRange = normalizeIndexRange(range)
  const seriesValues = [
    chartData.value.values,
    chartData.value.benchmarkValues,
    chartData.value.excessValues,
  ]
  let minimum = 1
  let maximum = 1
  for (const values of seriesValues) {
    for (let index = visibleRange.startIndex; index <= visibleRange.endIndex; index += 1) {
      const value = Number(values[index])
      if (!Number.isFinite(value)) continue
      minimum = Math.min(minimum, value)
      maximum = Math.max(maximum, value)
    }
  }

  const rawSpan = maximum - minimum
  const span = rawSpan > 0 ? rawSpan : 0.1
  const padding = Math.max(span * 0.08, 0.005)
  const paddedMinimum = minimum - padding
  const paddedMaximum = maximum + padding
  const interval = buildNiceStep((paddedMaximum - paddedMinimum) / 5)
  const axisMinimum = 1 + (Math.floor((paddedMinimum - 1) / interval) * interval)
  const axisMaximum = 1 + (Math.ceil((paddedMaximum - 1) / interval) * interval)

  return {
    min: roundAxisValue(axisMinimum),
    max: roundAxisValue(Math.max(axisMinimum + interval, axisMaximum)),
    interval: roundAxisValue(interval),
  }
}

function buildDrawdownScale(range: IndexRange | null): AxisScale {
  const visibleRange = normalizeIndexRange(range)
  let maximumDrawdown = 0
  for (let index = visibleRange.startIndex; index <= visibleRange.endIndex; index += 1) {
    const value = Number(drawdownValues.value[index])
    if (!Number.isFinite(value)) continue
    maximumDrawdown = Math.max(maximumDrawdown, Math.abs(Math.min(0, value)))
  }
  const paddedDrawdown = Math.max(maximumDrawdown * 1.08, 0.02)
  const interval = buildNiceStep(paddedDrawdown / 2.5)
  const minimum = -Math.max(interval * 2, Math.ceil(paddedDrawdown / interval) * interval)
  return {
    min: roundAxisValue(minimum),
    max: 0,
    interval: roundAxisValue(interval),
  }
}

function buildYAxisOption(value: 'all' | 10 | 60, range: IndexRange | null = null) {
  if (props.variant === 'workbench') return buildWorkbenchValueScale(range)
  const fixed = resolveFixedAxisRange(chartData.value.values, value)
  if (fixed) {
    return {
      min: fixed.min,
      max: fixed.max,
    }
  }
  return {
    min: (axis: { min: number }) => Math.min(0.8, Number(axis.min || 0.8)),
    max: null,
  }
}

function buildChartYAxis(range: IndexRange | null = null) {
  const primaryAxis = {
    type: 'value' as const,
    gridIndex: 0,
    ...(buildYAxisOption(rangeValue.value, range) as Record<string, unknown>),
    axisPointer: props.variant === 'workbench' ? { label: { show: false } } : undefined,
    axisLabel: {
      color: '#8494a8',
      formatter: (value: number) => Number(value).toFixed(2),
    },
    splitLine: { lineStyle: { color: '#edf2f8' } },
  }
  if (props.variant !== 'workbench') return primaryAxis

  return [
    primaryAxis,
    {
      type: 'value' as const,
      gridIndex: 1,
      ...buildDrawdownScale(range),
      name: '回撤',
      nameLocation: 'end' as const,
      nameRotate: 0,
      nameGap: -14,
      nameTextStyle: {
        color: '#8494a8',
        fontSize: 9,
        align: 'left' as const,
        backgroundColor: 'rgba(255, 255, 255, 0.9)',
        borderRadius: 2,
        padding: [1, 3],
      },
      axisLabel: {
        color: '#8494a8',
        fontSize: 9,
        formatter: (value: number) => `${Math.round(Number(value) * 100)}%`,
      },
      axisTick: { show: false },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#edf2f8', type: 'dashed' as const } },
    },
  ]
}

function escapeTooltipText(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function resolveTooltipDataIndex(rawParams: unknown): number {
  const params = Array.isArray(rawParams) ? rawParams : [rawParams]
  for (const param of params) {
    if (!param || typeof param !== 'object') continue
    const dataIndex = Number((param as { dataIndex?: unknown }).dataIndex)
    if (Number.isFinite(dataIndex)) {
      return clamp(Math.round(dataIndex), 0, Math.max(0, chartData.value.dates.length - 1))
    }
  }
  return clamp(lastFocusedIndex, 0, Math.max(0, chartData.value.dates.length - 1))
}

function formatWorkbenchTooltip(rawParams: unknown): string {
  const index = resolveTooltipDataIndex(rawParams)
  const palette = chartPalette.value
  const rows = [
    {
      label: '策略净值',
      value: chartData.value.values[index],
      color: palette.strategy,
      formatter: (value: number) => value.toFixed(4),
    },
    ...(chartData.value.benchmarkValues.length
      ? [{
          label: chartData.value.benchmarkLabel.replace(/\s+\d{6}\.(?:SZ|SH)$/i, ''),
          value: chartData.value.benchmarkValues[index],
          color: palette.benchmark,
          formatter: (value: number) => value.toFixed(4),
        }]
      : []),
    ...(chartData.value.excessValues.length
      ? [{
          label: '超额曲线',
          value: chartData.value.excessValues[index],
          color: palette.excess,
          formatter: (value: number) => value.toFixed(4),
        }]
      : []),
    {
      label: '回撤',
      value: drawdownValues.value[index],
      color: palette.drawdown,
      formatter: (value: number) => formatDrawdown(value),
    },
  ].filter(row => Number.isFinite(Number(row.value)))

  return [
    `<div style="margin-bottom:6px;font-weight:700">${escapeTooltipText(chartData.value.dates[index])}</div>`,
    ...rows.map(row => (
      `<div style="display:flex;min-width:190px;align-items:center;justify-content:space-between;gap:16px">`
      + `<span><i style="display:inline-block;width:8px;height:8px;margin-right:7px;border-radius:50%;background:${row.color}"></i>${escapeTooltipText(row.label)}</span>`
      + `<strong>${escapeTooltipText(row.formatter(Number(row.value)))}</strong>`
      + '</div>'
    )),
  ].join('')
}

function asEventRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function resolveZoomIndex(rawValue: unknown, rawPercent: unknown, fallback: number): number {
  const dates = chartData.value.dates
  const lastIndex = Math.max(0, dates.length - 1)
  if (rawValue != null) {
    const dateIndex = dates.indexOf(String(rawValue))
    if (dateIndex >= 0) return dateIndex
    if (typeof rawValue === 'number' && Number.isFinite(rawValue)) {
      return clamp(Math.round(rawValue), 0, lastIndex)
    }
  }
  const percent = Number(rawPercent)
  if (Number.isFinite(percent)) {
    return clamp(Math.round((clamp(percent, 0, 100) / 100) * lastIndex), 0, lastIndex)
  }
  return clamp(fallback, 0, lastIndex)
}

function resolveDataZoomRange(rawEvent: unknown): IndexRange | null {
  const event = asEventRecord(rawEvent)
  if (!event || !chartData.value.dates.length) return null
  const batch = Array.isArray(event.batch) ? event.batch.map(asEventRecord).find(Boolean) : null
  const payload = batch ?? event
  const currentRange = normalizeIndexRange(workbenchVisibleRange)
  const startIndex = resolveZoomIndex(payload.startValue, payload.start, currentRange.startIndex)
  const endIndex = resolveZoomIndex(payload.endValue, payload.end, currentRange.endIndex)
  return {
    startIndex: Math.min(startIndex, endIndex),
    endIndex: Math.max(startIndex, endIndex),
  }
}

function handleWorkbenchDataZoom(rawEvent: unknown) {
  if (!chart || props.variant !== 'workbench') return
  const nextRange = resolveDataZoomRange(rawEvent)
  if (!nextRange) return
  if (
    workbenchVisibleRange
    && workbenchVisibleRange.startIndex === nextRange.startIndex
    && workbenchVisibleRange.endIndex === nextRange.endIndex
  ) return
  workbenchVisibleRange = nextRange
  chart.setOption({ yAxis: buildChartYAxis(nextRange) })
}

function attachWorkbenchDataZoomHandler() {
  if (!chart) return
  chart.off('datazoom')
  if (props.variant === 'workbench') chart.on('datazoom', handleWorkbenchDataZoom)
}

function renderChart() {
  if (!chartRef.value || !hasChartData.value) {
    disposeChart()
    clearSelection()
    return
  }

  const instance = chart ?? init(chartRef.value)
  chart = instance
  const initialRange = resolveRangeIndices(chartData.value.dates, rangeValue.value)
  workbenchVisibleRange = props.variant === 'workbench' ? initialRange : null
  instance.setOption(buildOption(), true)
  attachSelectionHandlers()
  applyRange(rangeValue.value, props.variant !== 'workbench')
  attachWorkbenchDataZoomHandler()

  const preferredIndex = props.focusDate ? chartData.value.dates.indexOf(props.focusDate) : -1
  const preferredFocusIndex = preferredIndex >= 0 ? preferredIndex : chartData.value.dates.length - 1
  lastFocusedIndex = preferredFocusIndex
  if (props.variant === 'workbench') {
    chart.dispatchAction({ type: 'hideTip' })
  } else {
    syncFocusToIndex(preferredFocusIndex)
  }
}

function syncFocusToIndex(index: number) {
  if (!chart || !chartData.value.dates.length) return
  const safeIndex = clamp(index, 0, chartData.value.dates.length - 1)
  lastFocusedIndex = safeIndex
  chart.dispatchAction({
    type: 'showTip',
    seriesIndex: 0,
    dataIndex: safeIndex,
  })
}

function applyRange(value: 'all' | 10 | 60, showTooltip = true) {
  rangeValue.value = normalizeRangeValue(value)
  if (!chart || !chartData.value.dates.length) return

  const range = resolveRangeIndices(chartData.value.dates, rangeValue.value)
  if (!range) return
  if (props.variant === 'workbench') workbenchVisibleRange = range

  chart.setOption({
    yAxis: buildChartYAxis(range),
    dataZoom: props.showDataZoom
      ? [
          { id: 'detail-zoom-inside', startValue: chartData.value.dates[range.startIndex], endValue: chartData.value.dates[range.endIndex] },
          { id: 'detail-zoom-slider', startValue: chartData.value.dates[range.startIndex], endValue: chartData.value.dates[range.endIndex] },
        ]
      : [],
  })
  clearSelection()
  if (showTooltip) {
    syncFocusToIndex(lastFocusedIndex >= 0 ? lastFocusedIndex : chartData.value.dates.length - 1)
  } else {
    chart.dispatchAction({ type: 'hideTip' })
  }
}

function clearSelection() {
  activeSelection = null
  dragState = null
  isBandVisible.value = false
  isSelectionBoxVisible.value = false
  selectionBoxPayload.value = null
  if (chart) {
    chart.setOption({
      series: [{ id: 'net-value-series', markArea: { data: [] } }],
    })
  }
}

function updateSelectionBand(startX: number, startY: number, currentX: number, currentY: number) {
  const wrap = chartWrapRef.value
  if (!wrap) return
  const left = clamp(Math.min(startX, currentX), 0, wrap.clientWidth)
  const top = clamp(Math.min(startY, currentY), 0, wrap.clientHeight)
  const right = clamp(Math.max(startX, currentX), 0, wrap.clientWidth)
  const bottom = clamp(Math.max(startY, currentY), 0, wrap.clientHeight)
  selectionBandStyle.value = {
    left: `${left}px`,
    top: `${top}px`,
    width: `${Math.max(0, right - left)}px`,
    height: `${Math.max(0, bottom - top)}px`,
  }
  isBandVisible.value = true
}

function selectionPixels(selection: SelectionState) {
  if (!chart) return null
  const startDate = chartData.value.dates[selection.startIndex]
  const endDate = chartData.value.dates[selection.endIndex]
  const leftRaw = Number(chart.convertToPixel({ xAxisIndex: 0 }, startDate))
  const rightRaw = Number(chart.convertToPixel({ xAxisIndex: 0 }, endDate))
  const topRaw = Number(chart.convertToPixel({ yAxisIndex: 0 }, selection.yMax))
  const bottomRaw = Number(chart.convertToPixel({ yAxisIndex: 0 }, selection.yMin))
  if (![leftRaw, rightRaw, topRaw, bottomRaw].every(Number.isFinite)) return null
  const step = selection.endIndex > selection.startIndex
    ? Math.abs(rightRaw - leftRaw) / Math.max(1, selection.endIndex - selection.startIndex)
    : 16
  const xPadding = Math.max(8, step / 2)
  return {
    left: leftRaw - xPadding,
    right: rightRaw + xPadding,
    top: topRaw,
    bottom: bottomRaw,
  }
}

function syncSelectionOverlay(selection: SelectionState) {
  const pixels = selectionPixels(selection)
  if (!pixels) {
    isBandVisible.value = false
    return
  }
  updateSelectionBand(pixels.left, pixels.top, pixels.right, pixels.bottom)
}

function applySelection(selection: SelectionState) {
  const metrics = computeSliceMetrics(chartData.value.values, selection.startIndex, selection.endIndex)
  const pixels = selectionPixels(selection)
  if (!metrics || !pixels || !chartWrapRef.value) return

  activeSelection = selection
  chart?.setOption({
    series: [{
      id: 'net-value-series',
      markArea: {
        silent: true,
        itemStyle: { color: 'rgba(37, 99, 235, 0.10)' },
        data: [[
          { xAxis: chartData.value.dates[selection.startIndex], yAxis: selection.yMax },
          { xAxis: chartData.value.dates[selection.endIndex], yAxis: selection.yMin },
        ]],
      },
    }],
  })
  syncSelectionOverlay(selection)
  selectionBoxPayload.value = {
    dateRange: `${chartData.value.dates[selection.startIndex]} ~ ${chartData.value.dates[selection.endIndex]}`,
    valueRange: `${selection.yMin.toFixed(4)} ~ ${selection.yMax.toFixed(4)}`,
    returnRate: metrics.returnRate,
    maxDrawdown: metrics.maxDrawdown,
  }
  const wrap = chartWrapRef.value
  const margin = 12
  const boxLeft = clamp(pixels.right + 16, margin, Math.max(margin, wrap.clientWidth - 260))
  const boxTop = clamp(pixels.top - 16, margin, Math.max(margin, wrap.clientHeight - 132))
  selectionBoxStyle.value = {
    left: `${boxLeft}px`,
    top: `${boxTop}px`,
  }
  isSelectionBoxVisible.value = true
  syncFocusToIndex(selection.endIndex)
  emitFocusDate(selection.endIndex)
}

function emitFocusDate(index: number) {
  const safeIndex = clamp(index, 0, chartData.value.dates.length - 1)
  const nextDate = chartData.value.dates[safeIndex]
  if (nextDate && nextDate !== props.focusDate) {
    emit('focusDate', nextDate)
  }
}

function resolveDataIndex(rawValue: unknown): number {
  if (typeof rawValue === 'string') return chartData.value.dates.indexOf(rawValue)
  const num = Number(rawValue)
  return Number.isFinite(num) ? Math.round(num) : -1
}

function syncFocusToPixel(x: number) {
  if (!chart || !chartWrapRef.value || !chartData.value.dates.length) return
  const safeX = clamp(x, 0, chartWrapRef.value.clientWidth)
  const rawIndex = chart.convertFromPixel({ xAxisIndex: 0 }, safeX)
  const resolvedIndex = resolveDataIndex(rawIndex)
  const focusIndex = resolvedIndex >= 0 ? resolvedIndex : (lastFocusedIndex >= 0 ? lastFocusedIndex : chartData.value.dates.length - 1)
  lastFocusedIndex = focusIndex
  chart.dispatchAction({
    type: 'showTip',
    seriesIndex: 0,
    dataIndex: focusIndex,
  })
  emitFocusDate(focusIndex)
}

function attachSelectionHandlers() {
  if (!chart) return
  const zr = chart.getZr()
  zr.off('mousedown')
  zr.off('mousemove')
  zr.off('mouseup')
  zr.off('globalout')

  zr.on('mousedown', event => {
    const pointerEvent = event.event as MouseEvent | undefined
    if (pointerEvent && typeof pointerEvent.button === 'number' && pointerEvent.button !== 0) return
    if (!chart?.containPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY])) return
    if (activeSelection) {
      clearSelection()
      syncFocusToPixel(event.offsetX)
      return
    }
    dragState = {
      startX: event.offsetX,
      startY: event.offsetY,
      currentX: event.offsetX,
      currentY: event.offsetY,
    }
    syncFocusToPixel(event.offsetX)
    updateSelectionBand(event.offsetX, event.offsetY, event.offsetX, event.offsetY)
  })

  zr.on('mousemove', event => {
    if (chart?.containPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY])) {
      syncFocusToPixel(event.offsetX)
    }
    if (!dragState) return
    dragState.currentX = event.offsetX
    dragState.currentY = event.offsetY
    updateSelectionBand(dragState.startX, dragState.startY, dragState.currentX, dragState.currentY)
  })

  zr.on('mouseup', () => {
    if (!chart || !dragState) return
    const moved = Math.max(
      Math.abs(dragState.currentX - dragState.startX),
      Math.abs(dragState.currentY - dragState.startY),
    )
    if (moved < 6) {
      dragState = null
      isBandVisible.value = false
      return
    }

    const leftPixel = Math.min(dragState.startX, dragState.currentX)
    const rightPixel = Math.max(dragState.startX, dragState.currentX)
    const topPixel = Math.min(dragState.startY, dragState.currentY)
    const bottomPixel = Math.max(dragState.startY, dragState.currentY)
    let startIndex = resolveDataIndex(chart.convertFromPixel({ xAxisIndex: 0 }, leftPixel))
    let endIndex = resolveDataIndex(chart.convertFromPixel({ xAxisIndex: 0 }, rightPixel))
    dragState = null

    if (startIndex < 0 || endIndex < 0) return
    startIndex = clamp(startIndex, 0, chartData.value.dates.length - 1)
    endIndex = clamp(endIndex, 0, chartData.value.dates.length - 1)
    if (endIndex <= startIndex) return

    const yTopRaw = Number(chart.convertFromPixel({ yAxisIndex: 0 }, topPixel))
    const yBottomRaw = Number(chart.convertFromPixel({ yAxisIndex: 0 }, bottomPixel))
    if (!Number.isFinite(yTopRaw) || !Number.isFinite(yBottomRaw)) return

    applySelection({
      startIndex,
      endIndex,
      yMin: Math.min(yTopRaw, yBottomRaw),
      yMax: Math.max(yTopRaw, yBottomRaw),
    })
  })

  zr.on('globalout', () => {
    if (props.variant === 'workbench') {
      chart?.dispatchAction({ type: 'hideTip' })
      return
    }
    if (!chartData.value.dates.length) return
    const fallbackIndex = lastFocusedIndex >= 0 ? lastFocusedIndex : chartData.value.dates.length - 1
    syncFocusToIndex(fallbackIndex)
  })
}
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <div>
        <h2 class="panel-title"><ChartNoAxesCombined :size="17" aria-hidden="true" />{{ props.title }}</h2>
        <p v-if="displayChartNote" class="panel-note">{{ displayChartNote }}</p>
      </div>
      <div v-if="props.showControls" class="detail-toolbar">
        <div class="range-chip-group">
          <button class="toolbar-btn" type="button" :class="{ active: rangeValue === 10 }" @click="applyRange(10)">近10日</button>
          <button class="toolbar-btn" type="button" :class="{ active: rangeValue === 60 }" @click="applyRange(60)">近60日</button>
          <button class="toolbar-btn" type="button" :class="{ active: rangeValue === 'all' }" @click="applyRange('all')">全区间</button>
        </div>
        <button class="secondary-btn" type="button" :disabled="!selectionBoxPayload" @click="clearSelection">取消框选</button>
      </div>
    </header>

    <div v-if="!hasChartData" class="empty-state detail-chart-empty">暂无回测数据</div>
    <div v-else ref="chartWrapRef" class="detail-chart-wrap">
      <div ref="chartRef" class="detail-chart" role="img" :aria-label="chartAriaLabel"></div>
      <div v-if="isBandVisible" class="detail-selection-band" :style="selectionBandStyle"></div>
      <div v-if="isSelectionBoxVisible && selectionBoxPayload" class="detail-selection-box" :style="selectionBoxStyle">
        <p class="detail-selection-title">框选区间</p>
        <div class="detail-selection-row">
          <span>时间区间</span>
          <span>{{ selectionBoxPayload.dateRange }}</span>
        </div>
        <div class="detail-selection-row">
          <span>净值范围</span>
          <span>{{ selectionBoxPayload.valueRange }}</span>
        </div>
        <div class="detail-selection-row">
          <span>区间收益</span>
          <span>{{ formatPercent(selectionBoxPayload.returnRate) }}</span>
        </div>
        <div class="detail-selection-row">
          <span>最大回撤</span>
          <span>{{ formatDrawdown(selectionBoxPayload.maxDrawdown) }}</span>
        </div>
      </div>
    </div>
  </section>
</template>
