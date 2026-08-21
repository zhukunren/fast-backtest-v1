<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ReturnHeatmapChart from './ReturnHeatmapChart.vue'
import type { NetValuePayload } from '../types/library'
import {
  buildReturnHeatmapData,
  buildReturnHeatmapPanelNote,
  DEFAULT_RETURN_HEATMAP_GRANULARITY,
  DEFAULT_RETURN_HEATMAP_METRIC_MODE,
  hasReturnHeatmapExcessSeries,
  RETURN_HEATMAP_GRANULARITY_OPTIONS,
  RETURN_HEATMAP_METRIC_OPTIONS,
  type ReturnHeatmapGranularity,
  type ReturnHeatmapMetricMode,
} from '../utils/returnHeatmap'

interface CompareReturnHeatmapItem {
  name: string
  title: string
  data: NetValuePayload | null
}

const props = defineProps<{
  strategies: CompareReturnHeatmapItem[]
}>()

const granularity = ref<ReturnHeatmapGranularity>(DEFAULT_RETURN_HEATMAP_GRANULARITY)
const metricMode = ref<ReturnHeatmapMetricMode>(DEFAULT_RETURN_HEATMAP_METRIC_MODE)

const hasAnyExcessSeries = computed(() => props.strategies.some(item => hasReturnHeatmapExcessSeries(item.data)))
const strategyHeatmaps = computed(() => {
  return props.strategies.map(item => ({
    ...item,
    hasExcessSeries: hasReturnHeatmapExcessSeries(item.data),
    heatmapData: buildReturnHeatmapData(item.data, granularity.value, metricMode.value, 1),
  }))
})
const sharedMaxAbs = computed(() => {
  return Math.max(1, ...strategyHeatmaps.value.map(item => Number(item.heatmapData?.maxAbs ?? 0)))
})
const panelNote = computed(() => {
  return buildReturnHeatmapPanelNote({
    scope: 'compare',
    granularity: granularity.value,
    metricMode: metricMode.value,
    hasExcessSeries: hasAnyExcessSeries.value,
  })
})

watch(hasAnyExcessSeries, value => {
  if (!value && metricMode.value === 'excess') {
    metricMode.value = 'return'
  }
})
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <div>
        <h2 class="panel-title">收益热力图对比</h2>
        <p class="panel-note">{{ panelNote }}</p>
      </div>
      <div class="detail-toolbar">
        <div class="range-chip-group">
          <button
            v-for="option in RETURN_HEATMAP_GRANULARITY_OPTIONS"
            :key="option.value"
            class="toolbar-btn"
            type="button"
            :class="{ active: granularity === option.value }"
            @click="granularity = option.value"
          >
            {{ option.label }}
          </button>
        </div>
        <div class="range-chip-group">
          <button
            v-for="option in RETURN_HEATMAP_METRIC_OPTIONS"
            :key="option.value"
            class="toolbar-btn"
            type="button"
            :class="{ active: metricMode === option.value }"
            :disabled="option.value === 'excess' && !hasAnyExcessSeries"
            @click="metricMode = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
    </header>

    <div v-if="!strategyHeatmaps.length" class="empty-state detail-chart-empty">暂无可对比的收益热力图数据。</div>
    <div v-else class="compare-heatmap-grid">
      <article
        v-for="item in strategyHeatmaps"
        :key="item.name"
        class="compare-heatmap-card"
      >
        <header class="compare-heatmap-card-head">
          <div>
            <h3 class="compare-chart-title">{{ item.title }}</h3>
            <p class="compare-summary-note">{{ item.name }}</p>
          </div>
          <span v-if="metricMode === 'excess' && !item.hasExcessSeries" class="trade-tag state-warning">缺少超额曲线</span>
        </header>

        <div v-if="!item.heatmapData" class="empty-state detail-chart-empty compare-heatmap-empty">
          {{ metricMode === 'excess' ? '暂无可展示的超额分布。' : '暂无可展示的收益分布。' }}
        </div>
        <ReturnHeatmapChart
          v-else
          :data="item.heatmapData"
          :metric-mode="metricMode"
          :max-abs="sharedMaxAbs"
          :height="340"
        />
      </article>
    </div>
  </section>
</template>
