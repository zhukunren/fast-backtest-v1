<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init } from '../lib/echarts'
import type { ECharts, EChartsOption } from '../lib/echarts'
import type { ReturnHeatmapData, ReturnHeatmapMetricMode } from '../utils/returnHeatmap'
import { formatReturnHeatmapPercent } from '../utils/returnHeatmap'

const props = withDefaults(defineProps<{
  data: ReturnHeatmapData | null
  metricMode: ReturnHeatmapMetricMode
  maxAbs?: number | null
  height?: number
}>(), {
  maxAbs: null,
  height: 460,
})

const chartRef = ref<HTMLDivElement | null>(null)
const chartAriaLabel = computed(() => {
  const metricLabel = props.metricMode === 'excess' ? '超额收益' : '策略收益'
  const pointCount = props.data?.seriesData.length ?? 0
  return `${metricLabel}热力图，共${pointCount}个时间区间；红色表示正收益，绿色表示负收益。`
})

let chart: ECharts | null = null

watch(
  () => [props.data, props.metricMode, props.maxAbs, props.height],
  async () => {
    await nextTick()
    renderChart()
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
  if (chart && props.data) {
    chart.setOption(buildOption(), true)
  }
}

function disposeChart() {
  chart?.dispose()
  chart = null
}

function buildOption(): EChartsOption {
  if (!props.data) return {}

  const isCompact = (chartRef.value?.clientWidth ?? 0) < 520
  const showDenseLabels = !isCompact && props.data.xAxisLabels.length <= 12
  const metricLabel = props.metricMode === 'excess' ? '超额' : '收益'
  const visualMaxAbs = Math.max(1, Number(props.maxAbs ?? props.data.maxAbs ?? 1))
  const visualRangeColors = [
    '#14532d',
    '#166534',
    '#15803d',
    '#4d7c0f',
    '#86efac',
    '#dcfce7',
    '#f8fafc',
    '#fee2e2',
    '#fecaca',
    '#fca5a5',
    '#ef4444',
    '#c2410c',
    '#991b1b',
  ]
  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    tooltip: {
      backgroundColor: 'rgba(15, 23, 42, 0.88)',
      borderColor: 'rgba(148, 163, 184, 0.24)',
      borderWidth: 1,
      textStyle: { color: '#e2e8f0' },
      formatter: (params: any) => {
        const data = Array.isArray(params?.data) ? params.data : []
        const value = Number(data[2] ?? 0)
        const displayText = String(data[3] || '')
        const startDate = String(data[4] || '')
        const endDate = String(data[5] || '')
        const periodLabel = String(data[6] || '')
        const yearLabel = String(data[7] || '')
        return [
          displayText || `${yearLabel} ${periodLabel}`.trim(),
          `${metricLabel}: ${formatReturnHeatmapPercent(Number.isFinite(value) ? value : 0)}`,
          `区间: ${startDate} ~ ${endDate}`,
        ].join('<br/>')
      },
    },
    grid: {
      left: isCompact ? 48 : 64,
      right: isCompact ? 12 : 24,
      top: 30,
      bottom: 82,
    },
    xAxis: {
      type: 'category',
      data: props.data.xAxisLabels,
      axisLabel: {
        color: '#8494a8',
        interval: isCompact
          ? (props.data.xAxisLabels.length > 12 ? 3 : 0)
          : (props.data.xAxisLabels.length > 20 ? 3 : 0),
        fontSize: isCompact ? 10 : 12,
      },
      axisLine: { lineStyle: { color: '#d9e2ec' } },
      splitArea: { show: true, areaStyle: { color: ['#ffffff', '#fbfdff'] } },
    },
    yAxis: {
      type: 'category',
      data: props.data.yearLabels,
      axisLabel: { color: '#64748b', fontSize: isCompact ? 10 : 12 },
      axisLine: { lineStyle: { color: '#d9e2ec' } },
      splitArea: { show: true, areaStyle: { color: ['#ffffff', '#fafcfe'] } },
    },
    visualMap: {
      dimension: 2,
      min: -visualMaxAbs,
      max: visualMaxAbs,
      calculable: false,
      orient: 'horizontal',
      splitNumber: 8,
      left: 'center',
      bottom: 18,
      text: ['盈利', '亏损'],
      textStyle: { color: '#586a80' },
      inRange: {
        color: visualRangeColors,
      },
    },
    series: [
      {
        name: metricLabel,
        type: 'heatmap',
        data: props.data.seriesData,
        label: {
          show: showDenseLabels,
          color: '#102238',
          fontSize: 11,
          formatter: (params: any) => {
            const value = Number(params?.data?.[2] ?? 0)
            return formatReturnHeatmapPercent(Number.isFinite(value) ? value : 0)
          },
        },
        emphasis: {
          itemStyle: {
            borderColor: '#0f172a',
            borderWidth: 1,
          },
        },
      },
    ],
  }
}

function renderChart() {
  if (!chartRef.value || !props.data) {
    disposeChart()
    return
  }

  const instance = chart ?? init(chartRef.value)
  chart = instance
  instance.setOption(buildOption(), true)
}
</script>

<template>
  <div ref="chartRef" class="return-heatmap-chart" role="img" :aria-label="chartAriaLabel" :style="{ height: `${height}px` }"></div>
</template>

<style scoped>
.return-heatmap-chart {
  width: 100%;
}
</style>
