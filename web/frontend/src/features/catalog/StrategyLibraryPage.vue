<script setup lang="ts">
import { ArrowUpRight, Play, Search, X } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchLibraryCatalog } from '../../api/library'
import type { LibraryCatalogResponse, StrategyLibraryItem } from '../../types/library'
import {
  analyzeEvaluationComparability,
  catalogItemState,
  deriveCatalogStatusCounts,
  hasEvaluationMetadata,
  hasMeaningfulFilter,
  matchesCatalogStatus,
  sortCatalogItems,
} from './catalogModel'
import CompareToolbar from './components/CompareToolbar.vue'
import StrategyCard from './components/StrategyCard.vue'
import { useCatalogBacktestPolling } from './useCatalogBacktestPolling'
import { useCatalogQueryState } from './useCatalogQueryState'
import { useCompareSelection } from './useCompareSelection'

const MAX_COMPARE_ITEMS = 4
const CATALOG_SCROLL_KEY = 'strategy-library:scroll-position'

const loading = ref(true)
const error = ref('')
const catalog = ref<LibraryCatalogResponse | null>(null)
const {
  searchQuery,
  familyFilter,
  statusFilter,
  styleFilter,
  portfolioFilter,
  evaluationWindowFilter,
  sortMode,
  normalizeLoadedFilters,
  resetFilters,
} = useCatalogQueryState()
const runDialogOpen = ref(false)
const runTargets = ref<StrategyLibraryItem[]>([])
const runStartDate = ref('')
const runEndDate = ref('')
const runDialogError = ref('')
const runStartInput = ref<HTMLInputElement | null>(null)
const runDialogPanel = ref<HTMLElement | null>(null)

let dialogReturnFocus: HTMLElement | null = null

const items = computed<StrategyLibraryItem[]>(() => catalog.value?.items ?? [])
const overview = computed(() => catalog.value?.overview)
const {
  selectedNames: selectedCompareNames,
  selectedItems: selectedCompareItems,
  prune: pruneCompareSelection,
  toggle: toggleCompare,
  remove: removeCompareItem,
  clear: clearCompareSelection,
} = useCompareSelection(items, MAX_COMPARE_ITEMS)
const {
  actionPending,
  progress: backtestProgress,
  message: backtestMessage,
  busy: backtestBusy,
  percent: backtestPercent,
  statusText: backtestStatusText,
  statusTitle: backtestStatusTitle,
  submit: submitBacktestRun,
  syncStatus: syncBacktestStatus,
} = useCatalogBacktestPolling({
  refreshCatalog: loadCatalog,
  resolveStrategyTitle: name => items.value.find(item => item.name === name)?.title || name,
})
const filteredItems = computed<StrategyLibraryItem[]>(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const rows = items.value.filter((item) => {
    if (familyFilter.value !== 'all' && item.family !== familyFilter.value) return false
    if (!matchesCatalogStatus(item, statusFilter.value)) return false
    if (styleFilter.value !== 'all' && item.style !== styleFilter.value && item.style_label !== styleFilter.value) return false
    if (portfolioFilter.value !== 'all' && item.portfolio !== portfolioFilter.value && item.portfolio_label !== portfolioFilter.value) return false
    if (
      evaluationWindowFilter.value !== 'all'
      && item.evaluation_window_label !== evaluationWindowFilter.value
    ) return false
    if (!query) return true
    return [item.name, item.title, item.summary, item.style_label, item.family_label, item.portfolio_label]
      .map(value => String(value || '').toLowerCase())
      .join(' ')
      .includes(query)
  })
  return sortCatalogItems(rows, sortMode.value)
})

const familyOptions = computed(() => catalog.value?.filters.families ?? [])
const statusOptions = computed(() => catalog.value?.filters.statuses ?? [])
const styleOptions = computed(() => catalog.value?.filters.styles ?? [])
const portfolioOptions = computed(() => catalog.value?.filters.portfolios ?? [])
const evaluationWindowOptions = computed(() => catalog.value?.filters.evaluation_windows ?? [])
const showFamilyFilter = computed(() => hasMeaningfulFilter(familyOptions.value))
const showStatusFilter = computed(() => hasMeaningfulFilter(statusOptions.value))
const showStyleFilter = computed(() => hasMeaningfulFilter(styleOptions.value))
const showPortfolioFilter = computed(() => hasMeaningfulFilter(portfolioOptions.value))
const showEvaluationWindowFilter = computed(() => hasMeaningfulFilter(evaluationWindowOptions.value))

const countText = computed(() => {
  if (loading.value) return '正在同步策略目录'
  const total = items.value.length
  const visible = filteredItems.value.length
  return visible === total ? `${total} 个策略` : `显示 ${visible} / ${total} 个策略`
})
const hasActiveFilters = computed(() => Boolean(
  searchQuery.value.trim()
  || familyFilter.value !== 'all'
  || statusFilter.value !== 'all'
  || styleFilter.value !== 'all'
  || portfolioFilter.value !== 'all'
  || evaluationWindowFilter.value !== 'all',
))
const latestUpdateText = computed(() => formatDate(overview.value?.latest_timestamp) || '暂无回测记录')
const statusCounts = computed(() => {
  const derived = deriveCatalogStatusCounts(items.value)
  return {
    valid: numberOr(overview.value?.valid, derived.valid),
    failed: numberOr(overview.value?.failed, derived.failed),
    stale: numberOr(overview.value?.stale, derived.stale),
    idle: numberOr(overview.value?.idle ?? overview.value?.waiting, derived.idle),
  }
})
const completionRateText = computed(() => {
  const total = Number(overview.value?.total ?? items.value.length)
  return formatPercent(total > 0 ? statusCounts.value.valid / total : null, 0)
})
const riskLeader = computed(() => sortCatalogItems(
  items.value.filter(item => catalogItemState(item) === 'ready'),
  'risk_adjusted',
)[0])
const riskLeaderMetric = computed(() => {
  const item = riskLeader.value
  if (!item) return '-'
  if (typeof item.sharpe === 'number') return `Sharpe ${item.sharpe.toFixed(2)}`
  if (typeof item.calmar === 'number') return `Calmar ${item.calmar.toFixed(2)}`
  if (typeof item.ann_return === 'number') return `年化 ${formatPercent(item.ann_return, 2, true)}`
  return `回撤 ${formatPercent(-Math.abs(numberOr(item.max_drawdown, 0)), 2)}`
})
const hasEvaluationSummary = computed(() => Boolean(
  catalog.value?.evaluation || items.value.some(hasEvaluationMetadata),
))
const catalogEvaluation = computed(() => analyzeEvaluationComparability(items.value, catalog.value?.evaluation))
const compareStatusText = computed(() => {
  const count = selectedCompareItems.value.length
  return count < 2
    ? `已选 ${count} / ${MAX_COMPARE_ITEMS} · 至少选择 2 个`
    : `已选 ${count} / ${MAX_COMPARE_ITEMS} · 可以开始对比`
})
const compareBacktestStatusText = computed(() => {
  if (actionPending.value) return '正在提交回测'
  if (backtestProgress.value.running) return backtestStatusText.value || '回测运行中'
  return '回测空闲'
})
const canOpenCompare = computed(() => selectedCompareItems.value.length >= 2)
const canRunSelected = computed(() => selectedCompareItems.value.length > 0 && !backtestBusy.value)
const canRunAll = computed(() => !loading.value && items.value.length > 0 && !backtestBusy.value)
const runDialogTitle = computed(() => {
  if (runTargets.value.length === 1) return '回测策略'
  if (runTargets.value.length === items.value.length) return `回测全部 ${runTargets.value.length} 个策略`
  return `回测已选 ${runTargets.value.length} 个策略`
})
const runDialogTargetText = computed(() => {
  if (runTargets.value.length !== 1) return `本次任务包含 ${runTargets.value.length} 个策略。`
  const item = runTargets.value[0]
  return item?.title || item?.name || ''
})

function currentCatalogPath(): string {
  if (window.location.pathname !== '/') return '/'
  return `${window.location.pathname}${window.location.search}`
}

function buildDetailHref(name: string): string {
  const params = new URLSearchParams({ returnTo: currentCatalogPath() })
  return `/strategy/${encodeURIComponent(name)}?${params.toString()}`
}

function recordCatalogScroll() {
  if (window.location.pathname !== '/') return
  try {
    window.sessionStorage.setItem(CATALOG_SCROLL_KEY, JSON.stringify({
      path: currentCatalogPath(),
      scrollY: window.scrollY,
      timestamp: Date.now(),
    }))
  } catch {
    // Navigation should continue even when session storage is unavailable.
  }
}

async function restoreCatalogScroll() {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(CATALOG_SCROLL_KEY) || 'null') as {
      path?: unknown
      scrollY?: unknown
      timestamp?: unknown
    } | null
    if (!stored) return
    const timestamp = Number(stored.timestamp)
    const scrollY = Number(stored.scrollY)
    const isFresh = Number.isFinite(timestamp) && Date.now() - timestamp < 2 * 60 * 60 * 1000
    if (stored.path !== currentCatalogPath() || !Number.isFinite(scrollY) || scrollY < 0 || !isFresh) {
      window.sessionStorage.removeItem(CATALOG_SCROLL_KEY)
      return
    }
    await nextTick()
    await new Promise<void>(resolve => {
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()))
    })
    window.scrollTo({ top: scrollY, behavior: 'auto' })
    window.sessionStorage.removeItem(CATALOG_SCROLL_KEY)
  } catch {
    // Invalid or unavailable session state is safe to ignore.
  }
}

function openComparePage() {
  if (!canOpenCompare.value) return
  recordCatalogScroll()
  const params = new URLSearchParams({
    names: selectedCompareNames.value.join(','),
    returnTo: currentCatalogPath(),
  })
  window.history.pushState(window.history.state, '', `/compare?${params.toString()}`)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function numberOr(value: number | null | undefined, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function formatPercent(value?: number | null, digits = 2, showSign = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  const sign = showSign && value > 0 ? '+' : ''
  return `${sign}${(value * 100).toFixed(digits)}%`
}

function formatDate(value?: string | null): string {
  const text = String(value || '').trim()
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  return text ? text.replace('T', ' ').slice(0, 10) : ''
}

function normalizeDateInput(value: string | null | undefined): string {
  const text = String(value || '').trim()
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : ''
}

function compactDate(value: string): string {
  return normalizeDateInput(value).replaceAll('-', '')
}

async function loadCatalog(): Promise<boolean> {
  loading.value = true
  error.value = ''
  try {
    const nextCatalog = await fetchLibraryCatalog()
    catalog.value = nextCatalog
    normalizeLoadedFilters(nextCatalog.filters)
    pruneCompareSelection()
    return true
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
    return false
  } finally {
    loading.value = false
  }
}

function openBacktestDialog(target?: StrategyLibraryItem | StrategyLibraryItem[]) {
  if (backtestBusy.value) return
  const targets = Array.isArray(target) ? target.slice() : target ? [target] : items.value.slice()
  if (!targets.length) return
  runTargets.value = targets
  const item = targets.length === 1 ? targets[0] : undefined
  runStartDate.value = item
    ? normalizeDateInput(item.start_time)
    : ''
  runEndDate.value = item
    ? normalizeDateInput(item.end_time)
    : ''
  dialogReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  runDialogError.value = ''
  runDialogOpen.value = true
}

function closeBacktestDialog() {
  if (actionPending.value) return
  runDialogOpen.value = false
  runDialogError.value = ''
}

function handleDialogKeydown(event: KeyboardEvent) {
  if (!runDialogOpen.value) return
  if (event.key === 'Escape') {
    closeBacktestDialog()
    return
  }
  if (event.key !== 'Tab' || !runDialogPanel.value) return
  const focusable = Array.from(runDialogPanel.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
  )).filter(element => !element.hasAttribute('hidden'))
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable.at(-1)
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

async function submitBacktest() {
  if (backtestBusy.value || !runTargets.value.length) return
  const start = compactDate(runStartDate.value)
  const end = compactDate(runEndDate.value)
  if (start && end && start > end) {
    runDialogError.value = '起始日期不能晚于截止日期。'
    return
  }

  for (const item of runTargets.value) {
    const effectiveStart = start || compactDate(normalizeDateInput(item.start_time))
    const effectiveEnd = end || compactDate(normalizeDateInput(item.end_time))
    if (effectiveStart && effectiveEnd && effectiveStart > effectiveEnd) {
      runDialogError.value = `${item.title || item.name} 的回测区间无效。`
      return
    }
  }

  const names = runTargets.value.map(item => item.name)
  const dateOverrides: Record<string, { start_time?: string; end_time?: string }> = {}
  if (start || end) {
    for (const name of names) {
      dateOverrides[name] = {}
      if (start) dateOverrides[name].start_time = start
      if (end) dateOverrides[name].end_time = end
    }
  }

  runDialogError.value = ''
  const result = await submitBacktestRun({
    names,
    ...(Object.keys(dateOverrides).length ? { date_overrides: dateOverrides } : {}),
  })
  if (result.accepted) {
    runDialogOpen.value = false
  } else {
    runDialogError.value = result.error || '回测启动失败，请稍后重试。'
  }
}

watch(runDialogOpen, async (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
  if (open) {
    await nextTick()
    runStartInput.value?.focus()
  } else if (dialogReturnFocus?.isConnected) {
    await nextTick()
    dialogReturnFocus.focus()
    dialogReturnFocus = null
  }
})

onMounted(async () => {
  window.addEventListener('keydown', handleDialogKeydown)
  await Promise.all([loadCatalog(), syncBacktestStatus()])
  await restoreCatalogScroll()
})

onBeforeUnmount(() => {
  document.body.style.overflow = ''
  window.removeEventListener('keydown', handleDialogKeydown)
})
</script>

<template>
  <section class="strategy-index" :class="{ 'has-compare-selection': selectedCompareItems.length > 0 }">
    <header class="strategy-hero">
      <div>
        <p class="strategy-kicker">策略研究 / A股</p>
        <h1>策略库</h1>
        <p class="strategy-hero-meta">{{ countText }} · 最近回测 {{ latestUpdateText }}</p>
      </div>
      <button
        class="catalog-run-all"
        type="button"
        :disabled="loading || !items.length || backtestBusy"
        @click="openBacktestDialog()"
      >
        <Play :size="15" fill="currentColor" aria-hidden="true" />
        {{ backtestBusy ? '回测运行中' : '回测全部' }}
      </button>
    </header>

    <section class="strategy-overview" aria-label="策略库概览">
      <div class="overview-metric">
        <span>策略总数</span>
        <strong>{{ overview?.total ?? '-' }}</strong>
        <small>{{ statusCounts.idle }} 个待处理</small>
      </div>
      <div class="overview-metric">
        <span>可用结果</span>
        <strong>{{ statusCounts.valid }}</strong>
        <small>{{ completionRateText }} 覆盖</small>
      </div>
      <div class="overview-metric">
        <span>本次失败</span>
        <strong :class="statusCounts.failed ? 'metric-negative' : ''">{{ statusCounts.failed }}</strong>
        <small>{{ statusCounts.stale }} 个结果已过期</small>
      </div>
      <div class="overview-metric">
        <span>评估口径</span>
        <strong :class="catalogEvaluation.status === 'incompatible' ? 'metric-negative' : ''">
          {{ catalogEvaluation.status === 'comparable' ? '一致' : catalogEvaluation.status === 'incompatible' ? '不一致' : '待确认' }}
        </strong>
        <small>{{ catalogEvaluation.windowText || '等待评估身份' }}</small>
      </div>
      <div class="overview-leader">
        <span>风险调整优先</span>
        <a v-if="riskLeader" :href="buildDetailHref(riskLeader.name)" @click="recordCatalogScroll">
          <strong>{{ riskLeader.title || riskLeader.name }}</strong>
          <small :class="riskLeaderMetric.includes('-') ? 'metric-negative' : 'metric-positive'">
            {{ riskLeaderMetric }}
            <ArrowUpRight :size="14" aria-hidden="true" />
          </small>
        </a>
        <strong v-else>-</strong>
      </div>
    </section>

    <section
      v-if="hasEvaluationSummary"
      class="catalog-evaluation-notice"
      :class="`is-${catalogEvaluation.status}`"
      aria-live="polite"
    >
      <strong>{{ catalogEvaluation.label }}</strong>
      <span>{{ catalogEvaluation.detail }}</span>
      <span v-if="catalogEvaluation.windowText">评估区间：{{ catalogEvaluation.windowText }}</span>
    </section>

    <section v-if="backtestProgress.running || backtestMessage" class="catalog-run-status" aria-live="polite">
      <div class="catalog-run-status-copy">
        <span class="run-status-indicator" :class="{ 'is-running': backtestProgress.running }" aria-hidden="true"></span>
        <strong>{{ backtestProgress.running ? '正在回测' : '回测状态' }}</strong>
        <span :title="backtestStatusTitle || undefined">{{ backtestStatusText }}</span>
      </div>
      <div
        v-if="backtestProgress.running"
        class="catalog-run-progress"
        role="progressbar"
        aria-label="回测进度"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="Math.round(backtestPercent)"
      >
        <span :style="{ width: `${backtestPercent}%` }"></span>
      </div>
    </section>

    <section class="catalog-toolbar" aria-label="筛选与排序">
      <div class="catalog-filter-grid">
        <label class="strategy-search">
          <span>搜索</span>
          <div class="search-control">
            <Search :size="15" aria-hidden="true" />
            <input v-model="searchQuery" type="search" placeholder="名称、代码或策略风格" />
          </div>
        </label>
        <label v-if="showFamilyFilter">
          <span>来源</span>
          <select v-model="familyFilter">
            <option v-for="option in familyOptions" :key="option.value" :value="option.value">
              {{ option.label }}{{ typeof option.count === 'number' ? ` (${option.count})` : '' }}
            </option>
          </select>
        </label>
        <label v-if="showStatusFilter">
          <span>状态</span>
          <select v-model="statusFilter">
            <option v-for="option in statusOptions" :key="option.value" :value="option.value">
              {{ option.label }}{{ typeof option.count === 'number' ? ` (${option.count})` : '' }}
            </option>
          </select>
        </label>
        <label v-if="showStyleFilter">
          <span>风格</span>
          <select v-model="styleFilter">
            <option v-for="option in styleOptions" :key="option.value" :value="option.value">
              {{ option.label }}{{ typeof option.count === 'number' ? ` (${option.count})` : '' }}
            </option>
          </select>
        </label>
        <label v-if="showPortfolioFilter">
          <span>持仓</span>
          <select v-model="portfolioFilter">
            <option v-for="option in portfolioOptions" :key="option.value" :value="option.value">
              {{ option.label }}{{ typeof option.count === 'number' ? ` (${option.count})` : '' }}
            </option>
          </select>
        </label>
        <label v-if="showEvaluationWindowFilter">
          <span>评估区间</span>
          <select v-model="evaluationWindowFilter">
            <option v-for="option in evaluationWindowOptions" :key="option.value" :value="option.value">
              {{ option.label }}{{ typeof option.count === 'number' ? ` (${option.count})` : '' }}
            </option>
          </select>
        </label>
        <label>
          <span>排序</span>
          <select v-model="sortMode">
            <option value="risk_adjusted">风险调整优先</option>
            <option value="sharpe_desc">Sharpe 从高到低</option>
            <option value="calmar_desc">Calmar 从高到低</option>
            <option value="ann_return_desc">年化收益从高到低</option>
            <option value="return_desc">累计收益从高到低</option>
            <option value="drawdown_asc">最大回撤从低到高</option>
            <option value="win_desc">胜率从高到低</option>
            <option value="trades_desc">交易数从高到低</option>
            <option value="updated_desc">最近回测优先</option>
            <option value="name">策略名称</option>
          </select>
        </label>
      </div>
      <div class="catalog-toolbar-meta">
        <span>{{ countText }}</span>
        <button v-if="hasActiveFilters" type="button" @click="resetFilters">清除筛选</button>
      </div>
    </section>

    <CompareToolbar
      v-if="selectedCompareItems.length"
      :items="selectedCompareItems"
      :status-text="compareStatusText"
      :backtest-status-text="compareBacktestStatusText"
      :can-open-compare="canOpenCompare"
      :can-run-selected="canRunSelected"
      :can-run-all="canRunAll"
      @remove="removeCompareItem"
      @clear="clearCompareSelection"
      @open-compare="openComparePage"
      @run-selected="openBacktestDialog(selectedCompareItems)"
      @run-all="openBacktestDialog()"
    />

    <div v-if="loading" class="strategy-table-shell strategy-skeleton" aria-label="正在加载策略">
      <div v-for="index in 6" :key="index" class="skeleton-row"><span></span><span></span><span></span></div>
    </div>
    <div v-else-if="error" class="catalog-state catalog-state-error" role="alert">
      <strong>策略目录加载失败</strong>
      <p>{{ error }}</p>
      <button type="button" @click="loadCatalog">重新加载</button>
    </div>
    <div v-else-if="!items.length" class="catalog-state">
      <strong>策略库为空</strong>
      <p>当前没有可展示的策略记录。</p>
    </div>
    <div v-else-if="!filteredItems.length" class="catalog-state">
      <strong>没有匹配的策略</strong>
      <p>当前筛选条件未返回结果。</p>
      <button type="button" @click="resetFilters">清除筛选</button>
    </div>
    <section v-else class="strategy-table-shell" role="table" aria-label="策略回测结果">
      <div class="strategy-table-head" role="row">
        <span role="columnheader">策略 / 核心逻辑</span>
        <span role="columnheader">净值走势</span>
        <span role="columnheader">累计收益</span>
        <span role="columnheader">最大回撤</span>
        <span role="columnheader">胜率</span>
        <span role="columnheader">交易数</span>
        <span role="columnheader">最近回测</span>
        <span role="columnheader" class="sr-only">操作</span>
      </div>
      <div class="strategy-table-body" role="rowgroup">
        <StrategyCard
          v-for="(item, index) in filteredItems"
          :key="item.name"
          :item="item"
          :display-rank="index + 1"
          :running="backtestProgress.running && backtestProgress.current === item.name"
          :run-disabled="backtestBusy"
          :selected-for-compare="selectedCompareNames.includes(item.name)"
          :compare-disabled="selectedCompareNames.length >= MAX_COMPARE_ITEMS"
          :detail-href="buildDetailHref(item.name)"
          @run="openBacktestDialog(item)"
          @toggle-compare="toggleCompare"
          @open-detail="recordCatalogScroll"
        />
      </div>
    </section>

    <Teleport to="body">
      <div v-if="runDialogOpen" class="backtest-dialog-backdrop" @mousedown.self="closeBacktestDialog">
        <section
          ref="runDialogPanel"
          class="backtest-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="backtest-dialog-title"
          :aria-describedby="runDialogError ? 'backtest-dialog-error' : 'backtest-dialog-description'"
        >
          <header class="backtest-dialog-head">
            <div>
              <p>回测区间</p>
              <h2 id="backtest-dialog-title">{{ runDialogTitle }}</h2>
              <span id="backtest-dialog-description">{{ runDialogTargetText }}</span>
            </div>
            <button type="button" title="关闭" aria-label="关闭回测窗口" :disabled="actionPending" @click="closeBacktestDialog">
              <X :size="18" aria-hidden="true" />
            </button>
          </header>

          <div class="backtest-dialog-fields">
            <label>
              <span>起始日期</span>
              <input ref="runStartInput" v-model="runStartDate" type="date" :disabled="actionPending" />
            </label>
            <label>
              <span>截止日期</span>
              <input v-model="runEndDate" type="date" :disabled="actionPending" />
            </label>
          </div>
          <p v-if="runDialogError" id="backtest-dialog-error" class="backtest-dialog-error" role="alert">{{ runDialogError }}</p>

          <footer class="backtest-dialog-actions">
            <button class="dialog-secondary" type="button" :disabled="actionPending" @click="closeBacktestDialog">取消</button>
            <button class="dialog-primary" type="button" :disabled="actionPending" @click="submitBacktest">
              <Play :size="14" fill="currentColor" aria-hidden="true" />
              {{ actionPending ? '提交中...' : '开始回测' }}
            </button>
          </footer>
        </section>
      </div>
    </Teleport>
  </section>
</template>
