<script setup lang="ts">
import { ArrowUpRight, Play } from '@lucide/vue'
import { computed } from 'vue'
import type { StrategyLibraryItem } from '../../../types/library'
import { catalogItemState, formatEvaluationWindow } from '../catalogModel'

const props = defineProps<{
  item: StrategyLibraryItem
  displayRank: number
  running: boolean
  runDisabled: boolean
  selectedForCompare: boolean
  compareDisabled: boolean
  detailHref: string
}>()

const emit = defineEmits<{
  run: [name: string]
  toggleCompare: [name: string]
  openDetail: [name: string]
}>()

const title = computed(() => props.item.title || props.item.name)
const description = computed(() => props.item.summary || '未提供 LLM 策略描述')
const positive = computed(() => typeof props.item.cum_return === 'number' && props.item.cum_return >= 0)
const rankLabel = computed(() => `当前排序第 ${props.displayRank} 名`)
const itemState = computed(() => catalogItemState(props.item))
const latestAttemptFailed = computed(() => {
  const status = String(props.item.latest_attempt_status || '').trim().toLowerCase()
  return itemState.value === 'ready'
    && (status === 'failed' || status === 'config_failed' || status === 'error' || status.includes('failed'))
})
const latestAttemptTitle = computed(() => {
  if (!latestAttemptFailed.value) return ''
  const reason = String(props.item.latest_attempt_error || '').replace(/\s+/g, ' ').trim()
  return reason
    ? `最近一次回测失败，当前继续显示上次有效结果：${reason}`
    : '最近一次回测失败，当前继续显示上次有效结果。'
})
const statusLabel = computed(() => {
  if (props.running) return '回测中'
  const label = String(props.item.status_label || '').trim()
  if (itemState.value === 'stale') return label && label !== '已完成' ? label : '结果已过期'
  if (itemState.value === 'failed') return label && label !== '已完成' ? label : '回测失败'
  if (itemState.value === 'ready') return label && label !== '待回测' ? label : '已完成'
  if (label) return label
  return '待回测'
})
const statusTitle = computed(() => {
  const itemError = String(props.item.error || '').replace(/\s+/g, ' ').trim()
  if (!props.running && itemState.value === 'failed' && itemError) {
    return `${statusLabel.value}：${itemError}`
  }
  const windowText = formatEvaluationWindow(props.item)
  const currentResult = windowText ? `${statusLabel.value} · 评估区间 ${windowText}` : statusLabel.value
  return latestAttemptTitle.value ? `${currentResult}；${latestAttemptTitle.value}` : currentResult
})
const statusClass = computed(() => {
  if (props.running) return 'is-running'
  if (itemState.value === 'ready') return 'is-ready'
  if (itemState.value === 'stale') return 'is-stale'
  if (itemState.value === 'failed') return 'is-failed'
  return 'is-idle'
})
const tags = computed(() => [
  props.item.style_label,
  props.item.portfolio_label,
]
  .map(value => String(value || '').trim())
  .filter((value, index, values) => value && values.indexOf(value) === index)
  .slice(0, 3))
const sparkline = computed(() => {
  const values = (Array.isArray(props.item.sparkline) ? props.item.sparkline : [])
    .map(Number)
    .filter(Number.isFinite)
  if (values.length < 2) return null

  const min = Math.min(...values, 1)
  const max = Math.max(...values, 1)
  const range = max - min || 1
  const coordinates = values.map((value, index) => ({
    x: (index / (values.length - 1)) * 100,
    y: 31 - ((value - min) / range) * 26,
  }))
  const points = coordinates.map(point => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' ')
  const areaPoints = `0,34 ${points} 100,34`
  const baselineY = 31 - ((1 - min) / range) * 26
  const lastPoint = coordinates.at(-1)
  return {
    points,
    areaPoints,
    baselineY: baselineY.toFixed(2),
    lastX: lastPoint?.x.toFixed(2) || '100',
    lastY: lastPoint?.y.toFixed(2) || '31',
  }
})

function formatPercent(value?: number | null, showSign = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  const sign = showSign && value > 0 ? '+' : ''
  return `${sign}${(value * 100).toFixed(2)}%`
}

function formatDrawdown(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? `-${(Math.abs(value) * 100).toFixed(2)}%` : '-'
}

function formatDate(value?: string | null): string {
  const text = String(value || '').trim()
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  return text ? text.replace('T', ' ').slice(0, 10) : '尚未回测'
}

function formatCount(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? Math.round(value).toLocaleString('zh-CN') : '-'
}

function requestBacktest() {
  emit('run', props.item.name)
}

function requestCompare() {
  emit('toggleCompare', props.item.name)
}

function recordDetailNavigation() {
  emit('openDetail', props.item.name)
}

const cumulativeReturnText = computed(() => formatPercent(props.item.cum_return, true))
const maxDrawdownText = computed(() => formatDrawdown(props.item.max_drawdown))
const winRateText = computed(() => formatPercent(props.item.win_rate))
const tradeCountText = computed(() => formatCount(props.item.trade_count))
</script>

<template>
  <article class="strategy-record" role="row" :class="{ 'is-active': running, 'is-selected': selectedForCompare }">
    <div class="record-primary" role="cell">
      <div class="record-rank" role="note" :title="rankLabel" :aria-label="rankLabel">
        {{ String(displayRank).padStart(2, '0') }}
      </div>
      <div class="record-identity">
        <div class="record-title-line">
          <a :href="detailHref" @click="recordDetailNavigation">{{ title }}</a>
          <span class="library-status" :class="statusClass" :title="statusTitle">
            <span class="status-dot" aria-hidden="true"></span>
            {{ statusLabel }}
          </span>
          <span
            v-if="latestAttemptFailed && !running"
            class="record-attempt-warning"
            :title="latestAttemptTitle"
          >
            最近运行失败
          </span>
          <label
            class="record-compare-option"
            :class="{ 'is-selected': selectedForCompare }"
            :title="selectedForCompare ? `取消对比 ${title}` : `加入对比 ${title}`"
          >
            <input
              type="checkbox"
              :checked="selectedForCompare"
              :disabled="compareDisabled && !selectedForCompare"
              :aria-label="selectedForCompare ? `取消对比 ${title}` : `加入对比 ${title}`"
              @change="requestCompare"
            />
            <span>对比</span>
          </label>
        </div>
        <p>{{ description }}</p>
        <div class="record-tags" aria-label="策略属性">
          <code>{{ item.name }}</code>
          <span v-for="tag in tags" :key="tag">{{ tag }}</span>
        </div>
      </div>
    </div>

    <div class="record-curve" role="cell">
      <span class="record-label">净值走势</span>
      <span v-if="!sparkline" class="sparkline-empty">暂无净值</span>
      <svg v-else viewBox="0 0 100 36" preserveAspectRatio="none" role="img" :aria-label="`${title}净值走势`">
        <line x1="0" :y1="sparkline.baselineY" x2="100" :y2="sparkline.baselineY" />
        <polygon :points="sparkline.areaPoints" :class="positive ? 'is-positive' : 'is-negative'" />
        <polyline :points="sparkline.points" :class="positive ? 'is-positive' : 'is-negative'" />
        <circle :cx="sparkline.lastX" :cy="sparkline.lastY" r="1.8" :class="positive ? 'is-positive' : 'is-negative'" />
      </svg>
    </div>

    <div class="record-metric record-return" role="cell">
      <span class="record-label">累计收益</span>
      <strong :class="positive ? 'metric-positive' : 'metric-negative'" :title="cumulativeReturnText">
        {{ cumulativeReturnText }}
      </strong>
    </div>

    <div class="record-metric record-drawdown" role="cell">
      <span class="record-label">最大回撤</span>
      <strong class="metric-negative" :title="maxDrawdownText">{{ maxDrawdownText }}</strong>
    </div>

    <div class="record-metric record-win" role="cell">
      <span class="record-label">胜率</span>
      <strong :title="winRateText">{{ winRateText }}</strong>
    </div>

    <div class="record-metric record-trades" role="cell">
      <span class="record-label">交易数</span>
      <strong :title="tradeCountText">{{ tradeCountText }}</strong>
    </div>

    <div class="record-meta" role="cell">
      <span class="record-label">最近回测</span>
      <time>{{ formatDate(item.timestamp) }}</time>
      <small v-if="formatEvaluationWindow(item)">{{ formatEvaluationWindow(item) }}</small>
    </div>

    <div class="record-actions" role="cell">
      <button
        class="library-run-button"
        type="button"
        :disabled="runDisabled"
        :aria-label="`回测 ${title}`"
        @click="requestBacktest"
      >
        <Play :size="12" fill="currentColor" aria-hidden="true" />
        {{ running ? '运行中' : '回测' }}
      </button>
      <a class="library-result-link" :href="detailHref" @click="recordDetailNavigation">
        详情 <ArrowUpRight :size="13" aria-hidden="true" />
      </a>
    </div>
  </article>
</template>
