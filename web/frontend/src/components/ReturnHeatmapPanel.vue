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

const props = defineProps<{
  data: NetValuePayload | null
  embedded?: boolean
  baselineValue?: number | null
}>()

const granularity = ref<ReturnHeatmapGranularity>(DEFAULT_RETURN_HEATMAP_GRANULARITY)
const metricMode = ref<ReturnHeatmapMetricMode>(DEFAULT_RETURN_HEATMAP_METRIC_MODE)

const hasExcessSeries = computed(() => hasReturnHeatmapExcessSeries(props.data))
const heatmapData = computed(() => buildReturnHeatmapData(
  props.data,
  granularity.value,
  metricMode.value,
  props.baselineValue ?? null,
))
const panelNote = computed(() => {
  return buildReturnHeatmapPanelNote({
    scope: 'detail',
    granularity: granularity.value,
    metricMode: metricMode.value,
    hasExcessSeries: hasExcessSeries.value,
  })
})

watch(hasExcessSeries, value => {
  if (!value && metricMode.value === 'excess') {
    metricMode.value = 'return'
  }
})
</script>

<template>
  <section class="panel" :class="{ 'embedded-panel': props.embedded }" :aria-label="props.embedded ? '收益热力图' : undefined">
    <header class="panel-head">
      <div>
        <h2 v-if="!props.embedded" class="panel-title">收益热力图</h2>
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
            :disabled="option.value === 'excess' && !hasExcessSeries"
            @click="metricMode = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
    </header>

    <div v-if="!heatmapData" class="empty-state detail-chart-empty">
      {{ metricMode === 'excess' ? '暂无可展示的超额热力图数据。' : '暂无可展示的收益热力图数据。' }}
    </div>
    <ReturnHeatmapChart v-else :data="heatmapData" :metric-mode="metricMode" />
  </section>
</template>
