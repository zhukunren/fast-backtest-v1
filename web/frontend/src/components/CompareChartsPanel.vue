<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init } from '../lib/echarts'
import type { ECharts, EChartsOption } from '../lib/echarts'
import { normalizeRangeValue, resolveRangeIndices } from '../utils/chart'

interface CompareSeries {
  name: string
  data: Array<number | null>
  color: string
}

interface CompareCurves {
  dates: string[]
  netSeries: CompareSeries[]
  excessSeries: CompareSeries[]
  benchmarkLabel: string
}

const props = defineProps<{
  curves: CompareCurves | null
}>()

const rangeValue = ref<'all' | 10 | 60>('all')
const netChartRef = ref<HTMLDivElement | null>(null)
const excessChartRef = ref<HTMLDivElement | null>(null)
const compareDateRangeLabel = computed(() => {
  const dates = props.curves?.dates ?? []
  return dates.length ? `${dates[0]}至${dates[dates.length - 1]}` : '暂无日期'
})
const netChartAriaLabel = computed(() => `净值对比。${compareDateRangeLabel.value}，对比${props.curves?.netSeries.length ?? 0}个策略。`)
const excessChartAriaLabel = computed(() => `超额对比。${compareDateRangeLabel.value}，对比${props.curves?.excessSeries.length ?? 0}个策略。`)

let netChart: ECharts | null = null
let excessChart: ECharts | null = null

watch(
  () => props.curves,
  async () => {
    await nextTick()
    renderCharts()
  },
)

onMounted(() => {
  renderCharts()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  disposeCharts()
})

function handleResize() {
  netChart?.resize()
  excessChart?.resize()
}

function disposeCharts() {
  netChart?.dispose()
  excessChart?.dispose()
  netChart = null
  excessChart = null
}

function buildOption(dates: string[], series: CompareSeries[], zoomIds: { inside: string; slider: string }): EChartsOption {
  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    legend: {
      type: 'scroll',
      top: 0,
      itemWidth: 18,
      itemHeight: 2,
      textStyle: { color: '#586a80', fontSize: 12 },
    },
    tooltip: {
      trigger: 'axis',
      alwaysShowContent: true,
      backgroundColor: 'rgba(15, 23, 42, 0.88)',
      borderColor: 'rgba(148, 163, 184, 0.24)',
      textStyle: { color: '#e2e8f0' },
      axisPointer: { type: 'cross', snap: true, crossStyle: { color: 'rgba(148, 163, 184, 0.45)' } },
      valueFormatter: (value: unknown) => {
        const num = Number(value)
        return Number.isFinite(num) ? num.toFixed(4) : '-'
      },
    },
    grid: { left: 56, right: 24, top: 50, bottom: 48 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { color: '#8494a8', rotate: 28 },
      axisLine: { lineStyle: { color: '#d9e2ec' } },
    },
    yAxis: {
      type: 'value',
      min: (axis: { min: number }) => Math.min(0.8, Number(axis.min || 0.8)),
      axisLabel: { color: '#8494a8', formatter: (value: number) => Number(value).toFixed(2) },
      splitLine: { lineStyle: { color: '#edf2f8' } },
    },
    dataZoom: [
      { id: zoomIds.inside, type: 'inside' },
      {
        id: zoomIds.slider,
        type: 'slider',
        height: 12,
        bottom: 14,
        borderColor: '#d9e2ec',
        fillerColor: 'rgba(37, 99, 235, 0.14)',
        backgroundColor: 'rgba(217, 226, 236, 0.28)',
        textStyle: { color: '#8494a8' },
      },
    ],
    series: series.map(item => ({
      name: item.name,
      type: 'line',
      data: item.data,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 2, color: item.color },
      itemStyle: { color: item.color },
    })),
  }
}

function renderCharts() {
  if (!props.curves?.dates?.length || !netChartRef.value || !excessChartRef.value) {
    disposeCharts()
    return
  }

  const netInstance = netChart ?? init(netChartRef.value)
  const excessInstance = excessChart ?? init(excessChartRef.value)
  netChart = netInstance
  excessChart = excessInstance

  netInstance.setOption(buildOption(props.curves.dates, props.curves.netSeries, {
    inside: 'compare-net-inside',
    slider: 'compare-net-slider',
  }), true)

  excessInstance.setOption(buildOption(props.curves.dates, props.curves.excessSeries, {
    inside: 'compare-excess-inside',
    slider: 'compare-excess-slider',
  }), true)

  applyRange(rangeValue.value)
}

function applyRange(value: 'all' | 10 | 60) {
  rangeValue.value = normalizeRangeValue(value)
  if (!props.curves?.dates?.length) return
  const range = resolveRangeIndices(props.curves.dates, rangeValue.value)
  if (!range) return
  const startValue = props.curves.dates[range.startIndex]
  const endValue = props.curves.dates[range.endIndex]
  netChart?.setOption({
    dataZoom: [
      { id: 'compare-net-inside', startValue, endValue },
      { id: 'compare-net-slider', startValue, endValue },
    ],
  })
  excessChart?.setOption({
    dataZoom: [
      { id: 'compare-excess-inside', startValue, endValue },
      { id: 'compare-excess-slider', startValue, endValue },
    ],
  })
}
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <div>
        <h2 class="panel-title">曲线对比</h2>
        <p class="panel-note">基准指数：{{ curves?.benchmarkLabel || '-' }}。快捷区间会同时作用于净值和超额图。</p>
      </div>
      <div class="detail-toolbar">
        <div class="range-chip-group">
          <button class="toolbar-btn" type="button" :class="{ active: rangeValue === 10 }" @click="applyRange(10)">近10日</button>
          <button class="toolbar-btn" type="button" :class="{ active: rangeValue === 60 }" @click="applyRange(60)">近60日</button>
          <button class="toolbar-btn" type="button" :class="{ active: rangeValue === 'all' }" @click="applyRange('all')">全区间</button>
        </div>
      </div>
    </header>

    <div v-if="!curves?.dates?.length" class="empty-state detail-chart-empty">暂无可对比的净值数据。</div>
    <div v-else class="compare-chart-grid">
      <section class="compare-chart-card">
        <h3 class="compare-chart-title">净值对比</h3>
        <div ref="netChartRef" class="compare-chart" role="img" :aria-label="netChartAriaLabel"></div>
      </section>
      <section class="compare-chart-card">
        <h3 class="compare-chart-title">超额对比</h3>
        <div ref="excessChartRef" class="compare-chart" role="img" :aria-label="excessChartAriaLabel"></div>
      </section>
    </div>
  </section>
</template>
