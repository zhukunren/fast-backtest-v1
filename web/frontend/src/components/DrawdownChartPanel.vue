<script setup lang="ts">
import { TrendingDown } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init } from '../lib/echarts'
import type { ECharts, EChartsOption, SeriesOption } from '../lib/echarts'
import type { NetValuePayload } from '../types/library'

interface DrawdownPoint {
  date: string
  drawdown: number
}

const props = withDefaults(defineProps<{
  data: NetValuePayload | null
  title?: string
  note?: string
  baselineValue?: number | null
}>(), {
  title: '回撤时间线',
  note: '按每日净值相对历史峰值计算；数值越低，代表回撤越深。',
  baselineValue: null,
})

const panelRef = ref<HTMLElement | null>(null)
const chartRef = ref<HTMLDivElement | null>(null)

let chart: ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let lastCompactMode: boolean | null = null

const drawdownPoints = computed<DrawdownPoint[]>(() => {
  const dates = Array.isArray(props.data?.dates) ? props.data.dates : []
  const values = Array.isArray(props.data?.net_value) ? props.data.net_value : []
  const pointCount = Math.min(dates.length, values.length)
  const points: DrawdownPoint[] = []
  const baselineValue = Number(props.baselineValue)
  let runningPeak = Number.isFinite(baselineValue) && baselineValue > 0
    ? baselineValue
    : Number.NEGATIVE_INFINITY

  for (let index = 0; index < pointCount; index += 1) {
    const date = String(dates[index] ?? '').trim()
    const value = Number(values[index])
    if (!date || !Number.isFinite(value) || value <= 0) continue

    runningPeak = Math.max(runningPeak, value)
    points.push({
      date,
      drawdown: ((value / runningPeak) - 1) * 100,
    })
  }

  return points
})

const deepestDrawdown = computed(() => {
  if (!drawdownPoints.value.length) return null

  let deepestIndex = 0
  for (let index = 1; index < drawdownPoints.value.length; index += 1) {
    if (drawdownPoints.value[index].drawdown < drawdownPoints.value[deepestIndex].drawdown) {
      deepestIndex = index
    }
  }

  return {
    ...drawdownPoints.value[deepestIndex],
    index: deepestIndex,
  }
})

const hasData = computed(() => drawdownPoints.value.length > 0)
const deepestDrawdownLabel = computed(() => formatPercent(deepestDrawdown.value?.drawdown ?? 0))
const chartAriaLabel = computed(() => {
  if (!drawdownPoints.value.length) return `${props.title}，暂无回撤数据。`
  const first = drawdownPoints.value[0]
  const last = drawdownPoints.value[drawdownPoints.value.length - 1]
  return `${props.title}。${first.date}至${last.date}，最深回撤${deepestDrawdownLabel.value}，发生于${deepestDrawdown.value?.date || '-'}。`
})

watch(drawdownPoints, async () => {
  await nextTick()
  renderChart()
})

onMounted(() => {
  renderChart()
  if (panelRef.value && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      resizeChart()
    })
    resizeObserver.observe(panelRef.value)
  } else {
    window.addEventListener('resize', resizeChart)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  window.removeEventListener('resize', resizeChart)
  disposeChart()
})

function formatPercent(value: number): string {
  const safeValue = Math.abs(value) < 0.005 ? 0 : value
  return `${safeValue.toFixed(2)}%`
}

function formatAxisDate(value: string, compact: boolean): string {
  const date = String(value || '')
  const dashedMatch = date.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
  if (dashedMatch) {
    return compact
      ? `${dashedMatch[2].padStart(2, '0')}-${dashedMatch[3].padStart(2, '0')}`
      : `${dashedMatch[1]}-${dashedMatch[2].padStart(2, '0')}`
  }

  const compactMatch = date.match(/^(\d{4})(\d{2})(\d{2})/)
  if (compactMatch) {
    return compact ? `${compactMatch[2]}-${compactMatch[3]}` : `${compactMatch[1]}-${compactMatch[2]}`
  }

  return date
}

function isCompactChart(): boolean {
  return (chartRef.value?.clientWidth || 0) <= 420
}

function deepestPointLabelPosition(): 'left' | 'right' | 'top' {
  const deepest = deepestDrawdown.value
  const pointCount = drawdownPoints.value.length
  if (!deepest || pointCount < 2) return 'top'
  if (deepest.index <= pointCount * 0.16) return 'right'
  if (deepest.index >= pointCount * 0.84) return 'left'
  return 'top'
}

function buildOption(): EChartsOption {
  const points = drawdownPoints.value
  const deepest = deepestDrawdown.value
  const compact = isCompactChart()
  const deepestPointData = points.map((_point, index) => (
    index === deepest?.index ? deepest.drawdown : null
  ))
  const series: SeriesOption[] = [
    {
      id: 'drawdown-series',
      name: '回撤',
      type: 'line',
      data: points.map(point => point.drawdown),
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 2, color: '#dc2626' },
      itemStyle: { color: '#dc2626' },
      areaStyle: { color: 'rgba(220, 38, 38, 0.12)' },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { width: 1, color: '#cbd5e1' },
        label: { show: false },
        data: [{ yAxis: 0 }],
      },
      z: 2,
    },
    {
      id: 'deepest-drawdown-series',
      name: '最深回撤',
      type: 'line',
      data: deepestPointData,
      connectNulls: false,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: compact ? 8 : 10,
      silent: true,
      lineStyle: { width: 0, opacity: 0 },
      itemStyle: {
        color: '#b91c1c',
        borderColor: '#ffffff',
        borderWidth: 2,
        shadowBlur: 6,
        shadowColor: 'rgba(185, 28, 28, 0.28)',
      },
      label: {
        show: !compact,
        position: deepestPointLabelPosition(),
        distance: 10,
        color: '#991b1b',
        fontSize: 11,
        fontWeight: 600,
        backgroundColor: 'rgba(255, 255, 255, 0.94)',
        borderColor: '#fecaca',
        borderWidth: 1,
        borderRadius: 4,
        padding: [4, 6],
        formatter: `最深 ${deepestDrawdownLabel.value}`,
      },
      tooltip: { show: false },
      z: 5,
    },
  ]

  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(15, 23, 42, 0.9)',
      borderColor: 'rgba(148, 163, 184, 0.24)',
      borderWidth: 1,
      textStyle: { color: '#e2e8f0', fontSize: compact ? 11 : 12 },
      axisPointer: { type: 'line', lineStyle: { color: 'rgba(100, 116, 139, 0.5)' } },
      valueFormatter: value => formatPercent(Number(value)),
    },
    grid: {
      left: compact ? 48 : 58,
      right: compact ? 12 : 24,
      top: compact ? 22 : 30,
      bottom: compact ? 40 : 44,
    },
    xAxis: {
      type: 'category',
      data: points.map(point => point.date),
      boundaryGap: false,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#d9e2ec' } },
      axisLabel: {
        color: '#8494a8',
        fontSize: compact ? 10 : 11,
        hideOverlap: true,
        interval: 'auto',
        margin: 12,
        formatter: (value: string) => formatAxisDate(value, compact),
      },
    },
    yAxis: {
      type: 'value',
      max: 0,
      min: (axis: { min: number }) => {
        const minimum = Number(axis.min)
        if (!Number.isFinite(minimum) || minimum >= 0) return -1
        const padding = Math.max(0.5, Math.abs(minimum) * 0.12)
        return Math.floor((minimum - padding) * 10) / 10
      },
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: {
        color: '#8494a8',
        fontSize: compact ? 10 : 11,
        formatter: (value: number) => `${Number(value).toFixed(0)}%`,
      },
      splitLine: { lineStyle: { color: '#edf2f8', type: 'dashed' } },
    },
    series,
  }
}

function renderChart() {
  if (!chartRef.value || !hasData.value) {
    disposeChart()
    return
  }

  const instance = chart ?? init(chartRef.value)
  chart = instance
  lastCompactMode = isCompactChart()
  instance.setOption(buildOption(), true)
  instance.resize()
}

function resizeChart() {
  if (!chartRef.value || !hasData.value) return
  if (!chart) {
    renderChart()
    return
  }

  chart.resize()
  const compact = isCompactChart()
  if (compact !== lastCompactMode) {
    lastCompactMode = compact
    chart.setOption(buildOption(), true)
  }
}

function disposeChart() {
  chart?.dispose()
  chart = null
  lastCompactMode = null
}
</script>

<template>
  <section ref="panelRef" class="panel drawdown-panel">
    <header class="panel-head drawdown-panel-head">
      <div class="drawdown-heading">
        <h2 class="panel-title">
          <TrendingDown :size="17" aria-hidden="true" />
          {{ props.title }}
        </h2>
        <p v-if="props.note" class="panel-note">{{ props.note }}</p>
      </div>
      <div v-if="deepestDrawdown" class="drawdown-summary" aria-label="最深回撤">
        <span>最深回撤</span>
        <strong>{{ deepestDrawdownLabel }}</strong>
        <time :datetime="deepestDrawdown.date">{{ deepestDrawdown.date }}</time>
      </div>
    </header>

    <div v-if="!hasData" class="empty-state detail-chart-empty">暂无可计算的净值数据</div>
    <div v-else class="detail-chart-wrap drawdown-chart-wrap">
      <div ref="chartRef" class="detail-chart" role="img" :aria-label="chartAriaLabel"></div>
    </div>
  </section>
</template>

<style scoped>
.drawdown-panel-head {
  flex-wrap: wrap;
}

.drawdown-heading {
  min-width: 0;
  flex: 1 1 240px;
}

.drawdown-summary {
  display: grid;
  min-width: 118px;
  grid-template-columns: auto auto;
  align-items: baseline;
  justify-content: end;
  gap: 2px 8px;
  color: #64748b;
  text-align: right;
}

.drawdown-summary span,
.drawdown-summary time {
  font-size: 11px;
  line-height: 1.4;
}

.drawdown-summary strong {
  color: #b91c1c;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.3;
}

.drawdown-summary time {
  grid-column: 1 / -1;
  color: #8494a8;
}

.drawdown-chart-wrap {
  height: 320px;
  margin: 0;
  border: 0;
  border-radius: 0;
  background: #ffffff;
  cursor: default;
}

@media (max-width: 420px) {
  .drawdown-panel-head {
    align-items: flex-start;
    gap: 10px;
  }

  .drawdown-heading {
    flex-basis: 100%;
  }

  .drawdown-summary {
    width: 100%;
    min-width: 0;
    grid-template-columns: auto auto 1fr;
    justify-content: start;
    text-align: left;
  }

  .drawdown-summary time {
    grid-column: auto;
    justify-self: end;
  }

  .drawdown-chart-wrap {
    height: 280px;
  }
}
</style>
