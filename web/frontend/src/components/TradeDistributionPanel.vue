<script setup lang="ts">
import { Activity } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init } from '../lib/echarts'
import type { ECharts, EChartsOption } from '../lib/echarts'

interface Trade {
  stock_code: string
  stock_name: string
  entry_date: string
  exit_date: string
  holding_days: number
  return_rate: number
}

const props = withDefaults(defineProps<{
  trades: Trade[] | null
  title?: string
  note?: string
  showControls?: boolean
}>(), {
  title: '交易分布分析',
  note: '按不同维度统计交易次数和平均收益率',
  showControls: true,
})

const chartRef = ref<HTMLDivElement | null>(null)
const viewMode = ref<'month' | 'weekday' | 'holding_days'>('month')

let chart: ECharts | null = null

const processedData = computed(() => {
  if (!Array.isArray(props.trades) || !props.trades.length) return null

  const trades = props.trades.filter(t => t.entry_date && t.exit_date)
  if (!trades.length) return null

  return {
    byMonth: computeMonthDistribution(trades),
    byWeekday: computeWeekdayDistribution(trades),
    byHoldingDays: computeHoldingDaysDistribution(trades),
  }
})

const hasData = computed(() => processedData.value !== null)
const zeroTradeMonthLabels = computed(() => {
  if (viewMode.value !== 'month') return []
  return processedData.value?.byMonth
    .filter(item => item.count === 0)
    .map(item => item.month) || []
})
const chartAriaLabel = computed(() => {
  const modeLabels = {
    month: '月份',
    weekday: '星期',
    holding_days: '持仓天数',
  }
  const tradeCount = Array.isArray(props.trades) ? props.trades.length : 0
  return `${props.title}。当前按${modeLabels[viewMode.value]}统计${tradeCount}笔交易，柱形表示交易次数，折线表示平均收益率。`
})

function parseTradeDate(value: string): Date | null {
  const text = String(value || '').trim()
  if (!text) return null

  const match = text.match(/^(\d{4})(\d{2})(\d{2})(?:\D|$)/)
    || text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\D|$)/)
  if (!match) return null

  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null

  const date = new Date(year, month - 1, day)
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null
  return date
}

function formatMonthKey(value: string): string {
  const text = String(value || '').trim()
  const date = parseTradeDate(value)
  if (!date) {
    const monthMatch = text.match(/^(\d{4})(\d{2})(?:\d{2})?(?:\D|$)/)
      || text.match(/^(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?(?:\D|$)/)
    if (!monthMatch) return text.slice(0, 7)
    const year = Number(monthMatch[1])
    const month = Number(monthMatch[2])
    if (!Number.isFinite(year) || !Number.isFinite(month) || month < 1 || month > 12) return text.slice(0, 7)
    return `${year}-${`${month}`.padStart(2, '0')}`
  }
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  return `${year}-${month}`
}

function monthKeyToIndex(month: string): number | null {
  const match = String(month || '').trim().match(/^(\d{4})-(\d{2})$/)
  if (!match) return null
  const year = Number(match[1])
  const monthNumber = Number(match[2])
  if (!Number.isFinite(year) || !Number.isFinite(monthNumber) || monthNumber < 1 || monthNumber > 12) return null
  return year * 12 + monthNumber - 1
}

function monthIndexToKey(index: number): string {
  const year = Math.floor(index / 12)
  const month = `${(index % 12) + 1}`.padStart(2, '0')
  return `${year}-${month}`
}

function formatDistributionAxisLabel(value: string): string {
  if (viewMode.value !== 'month') return value
  const match = String(value || '').match(/^(\d{4})-(\d{2})$/)
  if (!match) return value
  const [, year, month] = match
  if (month === '01') return `${year}\n01`
  return ['04', '07', '10'].includes(month) ? month : ''
}

function computeMonthDistribution(trades: Trade[]) {
  const monthMap = new Map<string, { count: number; avgReturn: number; totalReturn: number }>()

  trades.forEach(t => {
    const month = formatMonthKey(t.entry_date)
    if (!month) return
    const current = monthMap.get(month) || { count: 0, avgReturn: 0, totalReturn: 0 }
    current.count++
    current.totalReturn += t.return_rate || 0
    monthMap.set(month, current)
  })

  const monthIndexes = Array.from(monthMap.keys())
    .map(monthKeyToIndex)
    .filter((value): value is number => value != null)
  if (!monthIndexes.length) return []

  const startIndex = Math.min(...monthIndexes)
  const endIndex = Math.max(...monthIndexes)
  const data = []
  for (let index = startIndex; index <= endIndex; index += 1) {
    const month = monthIndexToKey(index)
    const stats = monthMap.get(month) || { count: 0, avgReturn: 0, totalReturn: 0 }
    data.push({
      month,
      count: stats.count,
      avgReturn: stats.count > 0 ? stats.totalReturn / stats.count : 0,
    })
  }

  return data
}

function computeWeekdayDistribution(trades: Trade[]) {
  const weekdayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  const weekdayMap = new Map<number, { count: number; totalReturn: number }>()

  trades.forEach(t => {
    const date = parseTradeDate(t.entry_date)
    if (!date) return
    const weekday = date.getDay()
    const current = weekdayMap.get(weekday) || { count: 0, totalReturn: 0 }
    current.count++
    current.totalReturn += t.return_rate || 0
    weekdayMap.set(weekday, current)
  })

  return weekdayNames.map((name, index) => {
    const stats = weekdayMap.get(index) || { count: 0, totalReturn: 0 }
    return {
      weekday: name,
      count: stats.count,
      avgReturn: stats.count > 0 ? stats.totalReturn / stats.count : 0,
    }
  })
}

function computeHoldingDaysDistribution(trades: Trade[]) {
  const buckets = [
    { label: '1天', min: 1, max: 1 },
    { label: '2-3天', min: 2, max: 3 },
    { label: '4-7天', min: 4, max: 7 },
    { label: '8-15天', min: 8, max: 15 },
    { label: '16-30天', min: 16, max: 30 },
    { label: '31-60天', min: 31, max: 60 },
    { label: '60天+', min: 61, max: Infinity },
  ]

  const bucketMap = new Map<string, { count: number; totalReturn: number }>()

  trades.forEach(t => {
    const days = t.holding_days || 0
    const bucket = buckets.find(b => days >= b.min && days <= b.max)
    if (!bucket) return

    const current = bucketMap.get(bucket.label) || { count: 0, totalReturn: 0 }
    current.count++
    current.totalReturn += t.return_rate || 0
    bucketMap.set(bucket.label, current)
  })

  return buckets.map(b => {
    const stats = bucketMap.get(b.label) || { count: 0, totalReturn: 0 }
    return {
      label: b.label,
      count: stats.count,
      avgReturn: stats.count > 0 ? stats.totalReturn / stats.count : 0,
    }
  })
}

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
}

function disposeChart() {
  chart?.dispose()
  chart = null
}

function buildOption(): EChartsOption {
  if (!processedData.value) return {}

  let xData: string[] = []
  let countData: number[] = []
  let returnData: number[] = []

  if (viewMode.value === 'month') {
    const data = processedData.value.byMonth
    xData = data.map(d => d.month)
    countData = data.map(d => d.count)
    returnData = data.map(d => d.avgReturn * 100)
  } else if (viewMode.value === 'weekday') {
    const data = processedData.value.byWeekday
    xData = data.map(d => d.weekday)
    countData = data.map(d => d.count)
    returnData = data.map(d => d.avgReturn * 100)
  } else {
    const data = processedData.value.byHoldingDays
    xData = data.map(d => d.label)
    countData = data.map(d => d.count)
    returnData = data.map(d => d.avgReturn * 100)
  }

  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.88)',
      borderColor: 'rgba(148, 163, 184, 0.24)',
      textStyle: { color: '#e2e8f0' },
      axisPointer: { type: 'shadow' },
    },
    legend: {
      top: 0,
      textStyle: { color: '#586a80' },
    },
    grid: { left: 56, right: 56, top: 50, bottom: 60 },
    xAxis: {
      type: 'category',
      data: xData,
      axisLabel: {
        color: '#8494a8',
        rotate: 0,
        interval: viewMode.value === 'month' ? 0 : 'auto',
        hideOverlap: true,
        formatter: formatDistributionAxisLabel,
      },
      axisLine: { lineStyle: { color: '#d9e2ec' } },
    },
    yAxis: [
      {
        type: 'value',
        name: '交易次数',
        position: 'left',
        axisLabel: { color: '#8494a8' },
        splitLine: { lineStyle: { color: '#edf2f8' } },
      },
      {
        type: 'value',
        name: '平均收益率 (%)',
        position: 'right',
        axisLabel: { color: '#8494a8', formatter: '{value}%' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '交易次数',
        type: 'bar',
        yAxisIndex: 0,
        data: countData,
        itemStyle: { color: '#3b82f6' },
        label: { show: false },
        barMaxWidth: 40,
      },
      {
        name: '平均收益率',
        type: 'line',
        yAxisIndex: 1,
        data: returnData,
        lineStyle: { width: 2, color: '#10b981' },
        itemStyle: { color: '#10b981' },
        symbol: 'circle',
        symbolSize: 6,
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
  instance.setOption(buildOption(), true)
}
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <div>
        <h2 class="panel-title"><Activity :size="17" aria-hidden="true" />{{ props.title }}</h2>
        <p v-if="props.note" class="panel-note">{{ props.note }}</p>
      </div>
      <div v-if="props.showControls" class="detail-toolbar">
        <div class="range-chip-group">
          <button
            class="toolbar-btn"
            type="button"
            :class="{ active: viewMode === 'month' }"
            :aria-pressed="viewMode === 'month'"
            @click="viewMode = 'month'"
          >
            按月份
          </button>
          <button
            class="toolbar-btn"
            type="button"
            :class="{ active: viewMode === 'weekday' }"
            :aria-pressed="viewMode === 'weekday'"
            @click="viewMode = 'weekday'"
          >
            按星期
          </button>
          <button
            class="toolbar-btn"
            type="button"
            :class="{ active: viewMode === 'holding_days' }"
            :aria-pressed="viewMode === 'holding_days'"
            @click="viewMode = 'holding_days'"
          >
            按持仓天数
          </button>
        </div>
      </div>
    </header>

    <div v-if="!hasData" class="empty-state detail-chart-empty">暂无交易数据</div>
    <template v-else>
      <div ref="chartRef" class="distribution-chart" role="img" :aria-label="chartAriaLabel"></div>
      <p v-if="zeroTradeMonthLabels.length" class="zero-month-note">
        0 交易月份：{{ zeroTradeMonthLabels.join('、') }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.distribution-chart {
  width: 100%;
  height: 420px;
}

.zero-month-note {
  margin: 10px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
}
</style>
