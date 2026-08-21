<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init } from '../lib/echarts'
import type { ECharts, EChartsOption } from '../lib/echarts'

interface DailyHoldingSummaryPoint {
  trade_date: string
  position_ratio: number
}

type DailyRatioPoint = [string, number]
type TradingWeekHeatmapPoint = [number, number, number, string]

const props = defineProps<{
  holdings: DailyHoldingSummaryPoint[] | null
  embedded?: boolean
}>()

const chartRef = ref<HTMLDivElement | null>(null)
const viewMode = ref<'calendar' | 'timeline'>('calendar')

let chart: ECharts | null = null

const processedData = computed(() => {
  if (!Array.isArray(props.holdings) || !props.holdings.length) return null

  const data = props.holdings
    .map(item => {
      const date = parseDateText(item.trade_date)
      const ratio = Number(item.position_ratio)
      if (!date || !Number.isFinite(ratio)) return null
      return [formatDateKey(date), Number((ratio * 100).toFixed(2))] as DailyRatioPoint
    })
    .filter((item): item is DailyRatioPoint => Array.isArray(item))
    .sort((a, b) => a[0].localeCompare(b[0]))

  if (!data.length) return null

  const tradingDayData = data.filter(([date]) => !isWeekendDate(date))
  if (!tradingDayData.length) return null

  const dates = tradingDayData.map(d => d[0])
  const weekKeys: string[] = []
  const weekIndexMap = new Map<string, number>()
  const monthAxisLabels: string[] = []
  const yearAxisLabels: string[] = []
  const yearBoundaryWeeks: string[] = []

  let previousMonth = ''
  let previousYear = ''
  for (const [dateText] of tradingDayData) {
    const weekKey = resolveTradingWeekKey(dateText)
    if (!weekKey || weekIndexMap.has(weekKey)) continue
    weekIndexMap.set(weekKey, weekKeys.length)
    weekKeys.push(weekKey)

    const weekStartDate = parseDateText(weekKey)
    const monthKey = weekStartDate ? `${weekStartDate.getFullYear()}-${String(weekStartDate.getMonth() + 1).padStart(2, '0')}` : ''
    const yearKey = weekStartDate ? String(weekStartDate.getFullYear()) : ''

    if (yearKey && previousYear && yearKey !== previousYear) {
      yearBoundaryWeeks.push(weekKey)
    }

    monthAxisLabels.push(monthKey && monthKey !== previousMonth ? `${Number(monthKey.slice(5, 7))}月` : '')
    yearAxisLabels.push(yearKey && yearKey !== previousYear ? yearKey : '')

    previousMonth = monthKey || previousMonth
    previousYear = yearKey || previousYear
  }

  const heatmapData: TradingWeekHeatmapPoint[] = tradingDayData
    .map(([dateText, value]) => {
      const weekKey = resolveTradingWeekKey(dateText)
      const weekIndex = weekKey ? weekIndexMap.get(weekKey) : undefined
      const weekdayIndex = resolveTradingWeekdayIndex(dateText)
      if (weekIndex == null || weekdayIndex == null) return null
      return [weekIndex, weekdayIndex, value, dateText]
    })
    .filter((item): item is TradingWeekHeatmapPoint => Array.isArray(item))

  const maxRatioPercent = Math.max(100, ...tradingDayData.map(([, value]) => value))

  return {
    dates,
    timelineData: tradingDayData,
    heatmapData,
    weekKeys,
    monthAxisLabels,
    yearAxisLabels,
    yearBoundaryWeeks,
    maxRatioPercent,
  }
})

const hasData = computed(() => processedData.value !== null)

watch(processedData, async () => {
  await nextTick()
  renderChart()
})

watch(viewMode, () => {
  renderChart()
})

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
  if (chart && hasData.value) {
    chart.setOption(viewMode.value === 'calendar' ? buildCalendarOption() : buildTimelineOption(), true)
  }
}

function disposeChart() {
  chart?.dispose()
  chart = null
}

function parseDateText(value: string): Date | null {
  const text = String(value || '').trim()
  if (!text) return null
  const normalized = /^\d{8}$/.test(text)
    ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
    : text.slice(0, 10)
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

function isWeekendDate(value: string): boolean {
  const date = parseDateText(value)
  if (!date) return false
  const day = date.getDay()
  return day === 0 || day === 6
}

function resolveTradingWeekKey(value: string): string {
  const date = parseDateText(value)
  if (!date) return ''
  const day = date.getDay() || 7
  date.setDate(date.getDate() - day + 1)
  return formatDateKey(date)
}

function resolveTradingWeekdayIndex(value: string): number | null {
  const date = parseDateText(value)
  if (!date) return null
  const day = date.getDay()
  if (day === 0 || day === 6) return null
  return day - 1
}

function formatTimelineAxisLabel(value: string, previousValue: string): string {
  const current = parseDateText(value)
  if (!current) return value
  const previous = parseDateText(previousValue)
  const currentYear = current.getFullYear()
  const currentMonth = String(current.getMonth() + 1).padStart(2, '0')
  const currentDay = String(current.getDate()).padStart(2, '0')
  if (!previous || previous.getFullYear() !== currentYear) {
    return `${currentYear}\n${currentMonth}-${currentDay}`
  }
  return `${currentMonth}-${currentDay}`
}

function formatDateKey(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function buildCalendarOption(): EChartsOption {
  if (!processedData.value) return {}

  const { heatmapData, weekKeys, monthAxisLabels, yearAxisLabels, yearBoundaryWeeks, maxRatioPercent } = processedData.value
  const isCompact = (chartRef.value?.clientWidth ?? 0) < 520
  const monthLabelByWeek = new Map(weekKeys.map((weekKey, index) => [weekKey, monthAxisLabels[index] || '']))
  const yearLabelByWeek = new Map(weekKeys.map((weekKey, index) => [weekKey, yearAxisLabels[index] || '']))
  const compactStart = weekKeys.length > 52
    ? Math.max(0, ((weekKeys.length - 52) / weekKeys.length) * 100)
    : 0

  return {
    animation: false,
    tooltip: {
      position: 'top',
      formatter: (params: any) => {
        const date = params.data[3]
        const value = params.data[2]
        return `${date}<br/>仓位: ${Number(value).toFixed(2)}%`
      },
    },
    visualMap: {
      min: 0,
      max: maxRatioPercent,
      dimension: 2,
      calculable: !isCompact,
      orient: 'horizontal',
      left: 'center',
      bottom: 20,
      inRange: {
        color: ['#e0f2fe', '#0ea5e9', '#0369a1', '#1e3a8a'],
      },
      text: ['高', '低'],
      textStyle: { color: '#586a80' },
    },
    grid: {
      left: isCompact ? 48 : 72,
      right: isCompact ? 12 : 24,
      top: isCompact ? 72 : 86,
      bottom: 86,
    },
    xAxis: [
      {
        type: 'category',
        data: weekKeys,
        boundaryGap: false,
        position: 'bottom',
        axisLabel: {
          color: '#64748b',
          interval: 0,
          fontSize: isCompact ? 10 : 12,
          formatter: (value: string) => {
            const label = monthLabelByWeek.get(value) || ''
            if (!isCompact || !label) return label
            const month = Number(label.replace(/\D/g, ''))
            return [1, 4, 7, 10].includes(month) ? label : ''
          },
        },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#d9e2ec' } },
        splitArea: { show: false },
      },
      {
        type: 'category',
        data: weekKeys,
        boundaryGap: false,
        position: 'top',
        axisLabel: {
          color: '#334155',
          interval: 0,
          fontWeight: 700,
          fontSize: isCompact ? 10 : 12,
          formatter: (value: string) => yearLabelByWeek.get(value) || '',
        },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#d9e2ec' } },
      },
    ],
    yAxis: {
      type: 'category',
      inverse: true,
      data: ['周一', '周二', '周三', '周四', '周五'],
      axisLabel: { color: '#8494a8', fontSize: isCompact ? 10 : 12 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#d9e2ec' } },
      splitArea: { show: true, areaStyle: { color: ['#ffffff', '#fbfdff'] } },
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: isCompact ? compactStart : 0, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        start: isCompact ? compactStart : 0,
        end: 100,
        showDetail: false,
        height: 12,
        bottom: 40,
        borderColor: '#d9e2ec',
        fillerColor: 'rgba(37, 99, 235, 0.14)',
        backgroundColor: 'rgba(217, 226, 236, 0.28)',
        textStyle: { color: '#8494a8' },
      },
    ],
    series: [
      {
        type: 'heatmap',
        data: heatmapData,
        progressive: 0,
        itemStyle: {
          borderWidth: 1,
          borderColor: '#f1f5f9',
        },
        markLine: yearBoundaryWeeks.length ? {
          silent: true,
          symbol: 'none',
          label: { show: false },
          lineStyle: {
            color: 'rgba(15, 23, 42, 0.22)',
            width: 1.5,
            type: 'solid',
          },
          data: yearBoundaryWeeks.map(weekKey => ({ xAxis: weekKey })),
        } : undefined,
      },
    ],
  }
}

function buildTimelineOption(): EChartsOption {
  if (!processedData.value) return {}

  const { dates, timelineData, maxRatioPercent } = processedData.value
  const values = timelineData.map(d => Number(d[1]))
  const isCompact = (chartRef.value?.clientWidth ?? 0) < 520

  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.88)',
      borderColor: 'rgba(148, 163, 184, 0.24)',
      textStyle: { color: '#e2e8f0' },
      formatter: (params: any) => {
        const item = params[0]
        return `${item.axisValue}<br/>仓位: ${item.value}%`
      },
    },
    grid: { left: isCompact ? 48 : 56, right: isCompact ? 12 : 24, top: 40, bottom: 48 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: {
        color: '#8494a8',
        rotate: isCompact ? 0 : 28,
        hideOverlap: true,
        fontSize: isCompact ? 10 : 12,
        formatter: (value: string, index: number) => formatTimelineAxisLabel(value, dates[index - 1] || ''),
      },
      axisLine: { lineStyle: { color: '#d9e2ec' } },
    },
    yAxis: {
      type: 'value',
      name: '仓位 (%)',
      nameTextStyle: { color: '#64748b' },
      min: 0,
      max: maxRatioPercent,
      axisLabel: { color: '#8494a8', formatter: '{value}%', fontSize: isCompact ? 10 : 12 },
      splitLine: { lineStyle: { color: '#edf2f8' } },
    },
    dataZoom: [
      { type: 'inside' },
      {
        type: 'slider',
        height: 12,
        bottom: 14,
        borderColor: '#d9e2ec',
        fillerColor: 'rgba(37, 99, 235, 0.14)',
        backgroundColor: 'rgba(217, 226, 236, 0.28)',
        textStyle: { color: '#8494a8' },
      },
    ],
    series: [
      {
        type: 'line',
        data: values,
        showSymbol: false,
        lineStyle: { width: 2, color: '#0ea5e9' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(14, 165, 233, 0.3)' },
              { offset: 1, color: 'rgba(14, 165, 233, 0.05)' },
            ],
          },
        },
      },
    ],
  }
}

function renderChart() {
  if (!chartRef.value || !hasData.value) {
    disposeChart()
    return
  }

  const instance = chart ?? init(chartRef.value)
  chart = instance

  const option = viewMode.value === 'calendar'
    ? buildCalendarOption()
    : buildTimelineOption()

  instance.setOption(option, true)
}
</script>

<template>
  <section class="panel" :class="{ 'embedded-panel': props.embedded }" :aria-label="props.embedded ? '持仓热力图' : undefined">
    <header class="panel-head">
      <div>
        <h2 v-if="!props.embedded" class="panel-title">持仓热力图</h2>
        <p class="panel-note">
          {{ viewMode === 'calendar' ? '交易周视图仅展示周一至周五，并通过年份分隔线与顶轴年份标识区分跨年边界' : '时间线视图展示仓位变化趋势' }}
        </p>
      </div>
      <div class="detail-toolbar">
        <div class="range-chip-group">
          <button
            class="toolbar-btn"
            type="button"
            :class="{ active: viewMode === 'calendar' }"
            @click="viewMode = 'calendar'"
          >
            交易周
          </button>
          <button
            class="toolbar-btn"
            type="button"
            :class="{ active: viewMode === 'timeline' }"
            @click="viewMode = 'timeline'"
          >
            时间线
          </button>
        </div>
      </div>
    </header>

    <div v-if="!hasData" class="empty-state detail-chart-empty">暂无持仓数据</div>
    <div v-else ref="chartRef" class="heatmap-chart"></div>
  </section>
</template>

<style scoped>
.heatmap-chart {
  width: 100%;
  height: 520px;
}
</style>
