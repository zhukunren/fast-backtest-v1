<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init } from '../lib/echarts'
import type { ECharts, EChartsOption } from '../lib/echarts'

interface StrategyData {
  name: string
  values: number[]
}

const props = defineProps<{
  strategies: StrategyData[]
  dates: string[]
}>()

const chartRef = ref<HTMLDivElement | null>(null)
const windowSize = ref<number>(20)

let chart: ECharts | null = null

const correlationMatrix = computed(() => {
  if (!props.strategies.length || props.strategies.length < 2) return null
  if (props.dates.length < 2 || props.strategies.some(strategy => strategy.values.length < 2)) return null

  const strategyCount = props.strategies.length
  const matrix: number[][] = []

  for (let i = 0; i < strategyCount; i++) {
    matrix[i] = []
    for (let j = 0; j < strategyCount; j++) {
      if (i === j) {
        matrix[i][j] = 1
      } else {
        matrix[i][j] = calculateCorrelation(
          props.strategies[i].values,
          props.strategies[j].values
        )
      }
    }
  }

  return {
    matrix,
    names: props.strategies.map(s => s.name),
  }
})

const rollingCorrelation = computed(() => {
  if (!props.strategies.length || props.strategies.length < 2 || !props.dates.length) return null

  const window = windowSize.value
  const dataLength = props.strategies[0].values.length

  if (dataLength < window) return null

  // Calculate rolling correlation for first two strategies
  const series: Array<{ date: string; correlation: number }> = []

  for (let i = window - 1; i < dataLength; i++) {
    const slice1 = props.strategies[0].values.slice(i - window + 1, i + 1)
    const slice2 = props.strategies[1].values.slice(i - window + 1, i + 1)
    const corr = calculateCorrelation(slice1, slice2)
    series.push({
      date: props.dates[i],
      correlation: corr,
    })
  }

  return {
    series,
    pair: `${props.strategies[0].name} vs ${props.strategies[1].name}`,
  }
})

function calculateCorrelation(x: number[], y: number[]): number {
  const n = Math.min(x.length, y.length)
  if (n < 2) return 0

  const validPairs: Array<[number, number]> = []
  for (let i = 0; i < n; i++) {
    if (Number.isFinite(x[i]) && Number.isFinite(y[i])) {
      validPairs.push([x[i], y[i]])
    }
  }

  if (validPairs.length < 2) return 0

  const meanX = validPairs.reduce((sum, pair) => sum + pair[0], 0) / validPairs.length
  const meanY = validPairs.reduce((sum, pair) => sum + pair[1], 0) / validPairs.length

  let numerator = 0
  let sumSqX = 0
  let sumSqY = 0

  for (const [xi, yi] of validPairs) {
    const dx = xi - meanX
    const dy = yi - meanY
    numerator += dx * dy
    sumSqX += dx * dx
    sumSqY += dy * dy
  }

  const denominator = Math.sqrt(sumSqX * sumSqY)
  return denominator === 0 ? 0 : numerator / denominator
}

const displayMode = computed<'matrix' | 'rolling'>(() => (
  props.strategies.length > 2 ? 'matrix' : 'rolling'
))
const hasData = computed(() => (
  displayMode.value === 'matrix' ? correlationMatrix.value !== null : rollingCorrelation.value !== null
))

watch([correlationMatrix, rollingCorrelation], async () => {
  await nextTick()
  renderChart()
})

watch(windowSize, () => {
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

function buildMatrixOption(): EChartsOption {
  if (!correlationMatrix.value) return {}

  const { matrix, names } = correlationMatrix.value
  const data: Array<[number, number, number]> = []

  for (let i = 0; i < names.length; i++) {
    for (let j = 0; j < names.length; j++) {
      data.push([j, i, matrix[i][j]])
    }
  }

  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    tooltip: {
      position: 'top',
      formatter: (params: any) => {
        const [x, y, value] = params.data
        return `${names[y]} vs ${names[x]}<br/>相关系数: ${value.toFixed(3)}`
      },
    },
    grid: {
      left: 120,
      right: 60,
      top: 60,
      bottom: 120,
    },
    xAxis: {
      type: 'category',
      data: names,
      splitArea: { show: true },
      axisLabel: { color: '#8494a8', rotate: 45, interval: 0 },
    },
    yAxis: {
      type: 'category',
      data: names,
      splitArea: { show: true },
      axisLabel: { color: '#8494a8' },
    },
    visualMap: {
      min: -1,
      max: 1,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 20,
      inRange: {
        color: ['#1e3a8a', '#e0f2fe', '#ffffff', '#fef3c7', '#dc2626'],
      },
      text: ['正相关', '负相关'],
      textStyle: { color: '#586a80' },
    },
    series: [
      {
        type: 'heatmap',
        data: data,
        label: {
          show: true,
          formatter: (params: any) => params.data[2].toFixed(2),
          color: '#334155',
        },
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowColor: 'rgba(0, 0, 0, 0.5)',
          },
        },
      },
    ],
  }
}

function buildRollingOption(): EChartsOption {
  if (!rollingCorrelation.value) return {}

  const { series, pair } = rollingCorrelation.value
  const dates = series.map(s => s.date)
  const values = series.map(s => s.correlation)

  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    title: {
      text: `滚动相关性 (${windowSize.value}日窗口)`,
      subtext: pair,
      left: 'center',
      textStyle: { color: '#334155', fontSize: 14 },
      subtextStyle: { color: '#64748b', fontSize: 12 },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.88)',
      borderColor: 'rgba(148, 163, 184, 0.24)',
      textStyle: { color: '#e2e8f0' },
      formatter: (params: any) => {
        const item = params[0]
        return `${item.axisValue}<br/>相关系数: ${item.value.toFixed(3)}`
      },
    },
    grid: { left: 56, right: 24, top: 80, bottom: 48 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { color: '#8494a8', rotate: 28 },
      axisLine: { lineStyle: { color: '#d9e2ec' } },
    },
    yAxis: {
      type: 'value',
      min: -1,
      max: 1,
      axisLabel: { color: '#8494a8', formatter: '{value}' },
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
        lineStyle: { width: 2, color: '#8b5cf6' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(139, 92, 246, 0.3)' },
              { offset: 1, color: 'rgba(139, 92, 246, 0.05)' },
            ],
          },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: 'rgba(132, 148, 168, 0.5)', type: 'dashed' },
          label: { color: '#8494a8' },
          data: [
            { yAxis: 0, label: { formatter: '0' } },
            { yAxis: 0.5, label: { formatter: '0.5' } },
            { yAxis: -0.5, label: { formatter: '-0.5' } },
          ],
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

  const option = displayMode.value === 'matrix'
    ? buildMatrixOption()
    : buildRollingOption()

  instance.setOption(option, true)
}
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <div>
        <h2 class="panel-title">相关性分析</h2>
        <p class="panel-note">
          {{ displayMode === 'matrix' ? '策略间相关系数矩阵' : '双策略滚动相关性曲线' }}
        </p>
      </div>
      <div v-if="displayMode === 'rolling'" class="detail-toolbar">
        <label class="window-size-label">
          窗口大小:
          <select v-model.number="windowSize" class="window-size-select">
            <option :value="10">10日</option>
            <option :value="20">20日</option>
            <option :value="60">60日</option>
            <option :value="120">120日</option>
          </select>
        </label>
      </div>
    </header>

    <div v-if="!hasData" class="empty-state detail-chart-empty">需要至少2个策略且有足够共同交易日进行相关性分析</div>
    <div v-else ref="chartRef" class="correlation-chart"></div>
  </section>
</template>

<style scoped>
.correlation-chart {
  width: 100%;
  height: 520px;
}

.window-size-label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #64748b;
  font-size: 14px;
}

.window-size-select {
  padding: 4px 8px;
  border: 1px solid #d9e2ec;
  border-radius: 4px;
  background: white;
  color: #334155;
  font-size: 14px;
  cursor: pointer;
}

.window-size-select:hover {
  border-color: #3b82f6;
}
</style>
