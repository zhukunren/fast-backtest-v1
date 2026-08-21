<script setup lang="ts">
import { ArrowLeft, ArrowUpRight, RefreshCw, SlidersHorizontal, X } from '@lucide/vue'
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'
import { fetchEnhancedCompare, fetchLibraryCatalog, fetchLibraryNetValue } from '../../api/library'
import DataTable from '../../components/DataTable.vue'
import type { EnhancedCompareResponse, LibraryCatalogResponse, NetValuePayload, StrategyLibraryItem, TableRow } from '../../types/library'
import { analyzeEvaluationComparability, formatEvaluationWindow, type EvaluationComparability } from '../catalog/catalogModel'

const CompareChartsPanel = defineAsyncComponent(() => import('../../components/CompareChartsPanel.vue'))
const CompareReturnHeatmapPanel = defineAsyncComponent(() => import('../../components/CompareReturnHeatmapPanel.vue'))
const CorrelationMatrixPanel = defineAsyncComponent(() => import('../../components/CorrelationMatrixPanel.vue'))

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

interface StrategyData {
  name: string
  values: number[]
}

interface CompareHeatmapStrategy {
  name: string
  title: string
  data: NetValuePayload | null
}

const ENHANCED_COMPARE_COLUMNS = [
  { key: 'rank', label: '排名', align: 'right' as const },
  { key: 'strategy', label: '策略' },
  { key: 'score', label: '评分', align: 'right' as const },
  { key: 'grade', label: '评级', align: 'center' as const },
  { key: 'cum_return', label: '累计收益', align: 'right' as const },
  { key: 'ann_return', label: '年化收益', align: 'right' as const },
  { key: 'max_drawdown', label: '最大回撤', align: 'right' as const },
  { key: 'sharpe', label: 'Sharpe', align: 'right' as const },
  { key: 'calmar', label: 'Calmar', align: 'right' as const },
  { key: 'positive_month_ratio', label: '正收益月', align: 'right' as const },
]

const props = defineProps<{
  names: string[]
  returnTo?: string | null
}>()

const COMPARE_SELECTION_KEY = 'strategy-library:compare-selection'

const loading = ref(true)
const error = ref('')
const enhancedLoading = ref(false)
const enhancedError = ref('')
const catalog = ref<LibraryCatalogResponse | null>(null)
const curves = ref<CompareCurves | null>(null)
const correlationData = ref<{ strategies: StrategyData[]; dates: string[] } | null>(null)
const heatmapStrategies = ref<CompareHeatmapStrategy[]>([])
const enhancedCompare = ref<EnhancedCompareResponse | null>(null)
const comparedNames = ref<string[]>([])
const evaluationComparison = ref<EvaluationComparability>({
  status: 'unknown',
  label: '评估条件待确认',
  windowText: '',
  detail: '未获得足够的评估身份信息。',
})

let loadToken = 0
let enhancedRequestToken = 0
let pageController: AbortController | null = null
let enhancedController: AbortController | null = null

onBeforeUnmount(() => {
  loadToken += 1
  enhancedRequestToken += 1
  pageController?.abort()
  enhancedController?.abort()
})

watch(
  () => props.names.join(','),
  () => {
    void loadPage()
  },
  { immediate: true },
)

const selectedEntries = computed<StrategyLibraryItem[]>(() => {
  const byName = new Map((catalog.value?.items ?? []).map(item => [item.name, item]))
  return comparedNames.value
    .map(name => byName.get(name))
    .filter((item): item is StrategyLibraryItem => Boolean(item))
})
const selectedEvaluationWindow = computed(() => {
  const windows = selectedEntries.value.map(formatEvaluationWindow).filter(Boolean)
  return new Set(windows).size === 1 ? windows[0] : ''
})
const enhancedCompareRows = computed<TableRow[]>(() => {
  const rows = enhancedCompare.value?.rankings ?? enhancedCompare.value?.strategies ?? []
  return rows.map((item, index) => ({
    rank: index + 1,
    strategy: `${String(item.title || item.name)} (${item.name})`,
    score: formatNumber(item.score, 1),
    grade: item.grade || '-',
    cum_return: formatPercent(item.cum_return),
    ann_return: formatPercent(item.ann_return),
    max_drawdown: formatDrawdown(item.max_drawdown),
    sharpe: formatNumber(item.sharpe, 3),
    calmar: formatNumber(item.calmar, 3),
    positive_month_ratio: formatPercent(item.positive_month_ratio),
  }))
})
const catalogReturnHref = computed(() => resolveCatalogReturnHref())

function normalizeNames(names: string[]): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const raw of names) {
    const name = String(raw || '').trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    out.push(name)
  }
  return out
}

async function loadPage() {
  pageController?.abort()
  enhancedController?.abort()
  enhancedRequestToken += 1
  const currentToken = ++loadToken
  const normalized = normalizeNames(props.names)
  loading.value = true
  error.value = ''
  curves.value = null
  correlationData.value = null
  heatmapStrategies.value = []
  enhancedCompare.value = null
  enhancedLoading.value = false
  enhancedError.value = ''
  comparedNames.value = []
  evaluationComparison.value = {
    status: 'unknown',
    label: '评估条件待确认',
    windowText: '',
    detail: '未获得足够的评估身份信息。',
  }

  if (normalized.length < 2) {
    error.value = '请至少选择 2 个策略后再进入对比页。'
    loading.value = false
    return
  }
  if (normalized.length > 4) {
    error.value = '最多同时对比 4 个策略。'
    loading.value = false
    return
  }

  const controller = new AbortController()
  pageController = controller
  try {
    const catalogPayload = await fetchLibraryCatalog(controller.signal)
    if (currentToken !== loadToken) return
    catalog.value = catalogPayload
    const knownNames = new Set(catalogPayload.items.map(item => item.name))
    const validNames = normalized.filter(name => knownNames.has(name))
    const unknownNames = normalized.filter(name => !knownNames.has(name))
    if (validNames.length < 2) {
      const suffix = unknownNames.length ? ` 未找到：${unknownNames.join('、')}。` : ''
      error.value = `至少需要 2 个存在的策略才能对比。${suffix}`
      return
    }

    const payloadResults = await Promise.allSettled(
      validNames.map(name => fetchLibraryNetValue(name, controller.signal)),
    )
    if (currentToken !== loadToken) return
    const successful: Array<{ name: string; payload: NetValuePayload }> = []
    const failedNames: string[] = [...unknownNames]
    payloadResults.forEach((result, index) => {
      if (result.status === 'fulfilled' && hasUsableNetValue(result.value)) {
        successful.push({ name: validNames[index], payload: result.value })
      } else {
        failedNames.push(validNames[index])
      }
    })
    if (successful.length < 2) {
      const suffix = failedNames.length ? ` 无有效净值：${failedNames.join('、')}。` : ''
      error.value = `至少需要 2 条有效净值曲线才能对比。${suffix}`
      return
    }

    const activeNames = successful.map(item => item.name)
    const payloads = successful.map(item => item.payload)
    comparedNames.value = activeNames
    const activeItems = activeNames
      .map(name => catalogPayload.items.find(item => item.name === name))
      .filter((item): item is StrategyLibraryItem => Boolean(item))
    evaluationComparison.value = analyzeEvaluationComparability(activeItems)
    curves.value = buildCompareCurves(activeNames, catalogPayload.items, payloads)
    correlationData.value = buildCorrelationData(activeNames, payloads)
    heatmapStrategies.value = buildCompareHeatmapStrategies(activeNames, catalogPayload.items, payloads, curves.value.dates)
    if (!curves.value.dates.length) {
      error.value = '所选策略暂无可对比的净值数据，请先完成回测或刷新缓存。'
      return
    }
    loading.value = false
    if (evaluationComparison.value.status === 'incompatible') {
      enhancedError.value = '评估窗口或运行配置不一致，增强评分已停用。请先统一回测条件。'
      enhancedLoading.value = false
    } else {
      void loadEnhancedCompare(activeNames, currentToken)
    }
  } catch (err) {
    if (currentToken !== loadToken) return
    if (isAbortError(err)) return
    error.value = err instanceof Error ? err.message : 'compare_load_failed'
  } finally {
    if (pageController === controller) pageController = null
    if (currentToken === loadToken) {
      loading.value = false
    }
  }
}

async function loadEnhancedCompare(names: string[], currentToken = loadToken) {
  enhancedController?.abort()
  const controller = new AbortController()
  enhancedController = controller
  const currentRequestToken = ++enhancedRequestToken
  enhancedLoading.value = true
  enhancedError.value = ''
  enhancedCompare.value = null
  try {
    const result = await fetchEnhancedCompare([...names], controller.signal)
    if (currentToken !== loadToken || currentRequestToken !== enhancedRequestToken) return
    enhancedCompare.value = result
  } catch (reason) {
    if (currentToken !== loadToken || currentRequestToken !== enhancedRequestToken || isAbortError(reason)) return
    enhancedError.value = reason instanceof Error ? reason.message : '增强评分加载失败'
  } finally {
    if (enhancedController === controller) enhancedController = null
    if (currentToken === loadToken && currentRequestToken === enhancedRequestToken) {
      enhancedLoading.value = false
    }
  }
}

function retryEnhancedCompare() {
  if (
    enhancedLoading.value
    || comparedNames.value.length < 2
    || evaluationComparison.value.status === 'incompatible'
  ) return
  void loadEnhancedCompare(comparedNames.value)
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === 'AbortError'
}

function resolveCatalogReturnHref(): string {
  const rawTarget = props.returnTo
  if (!rawTarget) return '/'
  try {
    const target = new URL(rawTarget, window.location.origin)
    if (target.origin !== window.location.origin || target.pathname !== '/') return '/'
    return `${target.pathname}${target.search}${target.hash}`
  } catch {
    return '/'
  }
}

function buildComparePath(names: string[]): string {
  const params = new URLSearchParams({ names: names.join(',') })
  if (catalogReturnHref.value !== '/') params.set('returnTo', catalogReturnHref.value)
  return `/compare?${params.toString()}`
}

function buildStrategyDetailHref(name: string): string {
  const params = new URLSearchParams({ returnTo: buildComparePath(comparedNames.value) })
  return `/strategy/${encodeURIComponent(name)}?${params.toString()}`
}

function persistCompareSelection(names: string[]) {
  try {
    window.sessionStorage.setItem(COMPARE_SELECTION_KEY, JSON.stringify(names))
  } catch {
    // Navigation remains available when session storage is restricted.
  }
}

function navigateWithinApp(target: string) {
  window.history.pushState(window.history.state, '', target)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function adjustCompareSelection() {
  persistCompareSelection(comparedNames.value)
  navigateWithinApp(catalogReturnHref.value)
}

function removeComparedStrategy(name: string) {
  const nextNames = comparedNames.value.filter(item => item !== name)
  persistCompareSelection(nextNames)
  if (nextNames.length < 2) {
    navigateWithinApp(catalogReturnHref.value)
    return
  }
  navigateWithinApp(buildComparePath(nextNames))
}

function hasUsableNetValue(payload: NetValuePayload): boolean {
  const dates = Array.isArray(payload?.dates) ? payload.dates : []
  const values = Array.isArray(payload?.net_value) ? payload.net_value : []
  if (dates.length < 2 || dates.length !== values.length) return false
  // The API/cache contract requires strictly positive net values. Accepting
  // zero or negative values here produces a curve that cannot be normalized
  // against its first point and leaves the comparison charts blank/NaN.
  return values.every(value => {
    const normalized = Number(value)
    return Number.isFinite(normalized) && normalized > 0
  })
}

function buildCompareCurves(names: string[], items: StrategyLibraryItem[], payloads: Array<NetValuePayload | null>): CompareCurves {
  const byName = new Map(items.map(item => [item.name, item]))
  const dateSets = payloads.map(payload => new Set(
    Array.isArray(payload?.dates) ? payload.dates.map(String) : [],
  ))
  const dates = dateSets.length
    ? Array.from(dateSets[0]).filter(date => dateSets.every(set => set.has(date))).sort((left, right) => left.localeCompare(right))
    : []
  const palette = ['#2563eb', '#0f766e', '#c2410c', '#7c3aed']
  const benchmarkLabels = new Set<string>()

  const netSeries: CompareSeries[] = []
  const excessSeries: CompareSeries[] = []
  names.forEach((name, index) => {
    const payload = payloads[index] || {}
    const entry = byName.get(name)
    const title = entry?.title || name
    const color = palette[index % palette.length]
    const dateList = Array.isArray(payload?.dates) ? payload.dates.map(String) : []
    const netList = Array.isArray(payload?.net_value) ? payload.net_value : []
    const excessList = Array.isArray(payload?.excess_net_value) ? payload.excess_net_value : []
    const netMap = new Map<string, number | null>()
    const excessMap = new Map<string, number | null>()
    dateList.forEach((date, dateIndex) => {
      const netValue = Number(netList[dateIndex])
      const excessValue = Number(excessList[dateIndex])
      netMap.set(date, Number.isFinite(netValue) ? netValue : null)
      excessMap.set(date, Number.isFinite(excessValue) ? excessValue : null)
    })

    if (payload?.benchmark_label) benchmarkLabels.add(String(payload.benchmark_label))
    const baseNet = dates.map(date => netMap.get(date)).find(value => typeof value === 'number' && Number.isFinite(value) && value > 0)
    const baseExcess = dates.map(date => excessMap.get(date)).find(value => typeof value === 'number' && Number.isFinite(value) && value > 0)
    netSeries.push({
      name: title,
      color,
      data: dates.map(date => {
        const value = netMap.get(date)
        return value != null && baseNet ? value / baseNet : null
      }),
    })
    excessSeries.push({
      name: title,
      color,
      data: dates.map(date => {
        const value = excessMap.get(date)
        return value != null && baseExcess ? value / baseExcess : null
      }),
    })
  })

  return {
    dates,
    netSeries,
    excessSeries,
    benchmarkLabel: Array.from(benchmarkLabels).join(' / ') || '创业板指 399006.SZ',
  }
}

function buildCorrelationData(names: string[], payloads: Array<NetValuePayload | null>): { strategies: StrategyData[]; dates: string[] } {
  const valueMaps = payloads.map((payload) => {
    const dateList = Array.isArray(payload?.dates) ? payload.dates.map(String) : []
    const netList = Array.isArray(payload?.net_value) ? payload.net_value : []
    const netMap = new Map<string, number>()

    dateList.forEach((date, dateIndex) => {
      const netValue = Number(netList[dateIndex])
      if (Number.isFinite(netValue)) {
        netMap.set(date, netValue)
      }
    })
    return netMap
  })

  const commonDates = valueMaps.length
    ? Array.from(valueMaps[0].keys()).filter(date => valueMaps.every(netMap => netMap.has(date)))
    : []
  commonDates.sort((left, right) => left.localeCompare(right))
  const returnDates = commonDates.slice(1)

  const strategies: StrategyData[] = names.map((name, index) => {
    const netMap = valueMaps[index] || new Map<string, number>()
    return {
      name,
      values: returnDates.map((date, dateIndex) => {
        const prevDate = commonDates[dateIndex]
        const prev = Number(netMap.get(prevDate))
        const cur = Number(netMap.get(date))
        return Number.isFinite(prev) && prev > 0 && Number.isFinite(cur) ? cur / prev - 1 : Number.NaN
      }),
    }
  })

  return { strategies, dates: returnDates }
}

function buildCompareHeatmapStrategies(
  names: string[],
  items: StrategyLibraryItem[],
  payloads: Array<NetValuePayload | null>,
  commonDates: string[],
): CompareHeatmapStrategy[] {
  const byName = new Map(items.map(item => [item.name, item]))
  return names.map((name, index) => {
    const entry = byName.get(name)
    const payload = payloads[index]
    const dateSet = new Set(commonDates)
    const dateList = Array.isArray(payload?.dates) ? payload.dates.map(String) : []
    const values = Array.isArray(payload?.net_value) ? payload.net_value : []
    const excess = Array.isArray(payload?.excess_net_value) ? payload.excess_net_value : []
    const selectedIndexes = dateList
      .map((date, dateIndex) => ({ date, dateIndex }))
      .filter(({ date }) => dateSet.has(date))
    return {
      name,
      title: entry?.title || name,
      data: payload
        ? {
            ...payload,
            dates: selectedIndexes.map(item => item.date),
            net_value: selectedIndexes.map(item => values[item.dateIndex]),
            excess_net_value: selectedIndexes.map(item => excess[item.dateIndex]),
          }
        : null,
    }
  })
}

function formatPercent(value?: number | null): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return `${(value * 100).toFixed(2)}%`
}

function formatDrawdown(value?: number | null): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return `-${(Math.abs(value) * 100).toFixed(2)}%`
}

function formatTradeCount(value?: number | null): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return String(Math.round(value))
}

function formatNumber(value?: number | null, digits = 2): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return value.toFixed(digits)
}
</script>

<template>
  <section class="detail-layout">
    <div class="detail-page-nav">
      <a class="secondary-btn" :href="catalogReturnHref"><ArrowLeft :size="16" aria-hidden="true" />返回策略列表</a>
    </div>

    <section v-if="loading" class="panel">
      <div class="loading-state detail-page-feedback">正在加载对比数据...</div>
    </section>

    <section v-else-if="error" class="panel">
      <div class="error-state detail-page-feedback">{{ error }}</div>
    </section>

    <template v-else>
      <section class="page-head compare-page-head">
        <div class="page-head-main">
          <p class="eyebrow">Strategy Compare</p>
          <h1>净值与超额对比</h1>
          <p class="intro">同一页面查看多个策略的净值曲线、超额曲线、收益热力图、相关性分析与核心绩效指标。</p>
        </div>
        <div class="page-side compare-head-side">
          <div class="page-count">已选 {{ selectedEntries.length }} 个策略</div>
          <div class="compare-chip-list">
            <span
              v-for="item in selectedEntries"
              :key="item.name"
              class="compare-chip"
            >
              <span>{{ item.title || item.name }}</span>
              <button
                type="button"
                :title="`移除 ${item.title || item.name}`"
                :aria-label="`移除 ${item.title || item.name}`"
                @click="removeComparedStrategy(item.name)"
              >
                <X :size="13" aria-hidden="true" />
              </button>
            </span>
          </div>
          <button class="secondary-btn" type="button" @click="adjustCompareSelection">
            <SlidersHorizontal :size="16" aria-hidden="true" />调整对比策略
          </button>
        </div>
      </section>

      <section
        class="compare-evaluation-notice"
        :class="`is-${evaluationComparison.status}`"
        aria-live="polite"
      >
        <strong>{{ evaluationComparison.label }}</strong>
        <span>{{ evaluationComparison.detail }}</span>
        <span v-if="evaluationComparison.windowText || selectedEvaluationWindow">
          共同评估区间：{{ evaluationComparison.windowText || selectedEvaluationWindow }}
        </span>
      </section>

      <div class="compare-summary-grid">
        <article
          v-for="item in selectedEntries"
          :key="item.name"
          class="compare-summary-card"
        >
          <h3 class="compare-summary-title">
            <a :href="buildStrategyDetailHref(item.name)">
              {{ item.title || item.name }}<ArrowUpRight :size="15" aria-hidden="true" />
            </a>
          </h3>
          <p class="compare-summary-note">{{ item.name }}</p>
          <p v-if="formatEvaluationWindow(item)" class="compare-summary-note">评估区间：{{ formatEvaluationWindow(item) }}</p>
          <div class="compare-summary-metrics">
            <div>
              <span class="metric-label">累计收益</span>
              <span class="compare-summary-value" :class="{ 'metric-up': (item.cum_return ?? 0) >= 0, 'metric-down': (item.cum_return ?? 0) < 0 }">
                {{ formatPercent(item.cum_return) }}
              </span>
            </div>
            <div>
              <span class="metric-label">最大回撤</span>
              <span class="compare-summary-value">{{ formatDrawdown(item.max_drawdown) }}</span>
            </div>
            <div>
              <span class="metric-label">交易笔数</span>
              <span class="compare-summary-value">{{ formatTradeCount(item.trade_count as number | null) }}</span>
            </div>
            <div>
              <span class="metric-label">胜率</span>
              <span class="compare-summary-value">{{ formatPercent(item.win_rate) }}</span>
            </div>
          </div>
        </article>
      </div>

      <section class="panel" :aria-busy="enhancedLoading">
        <header class="panel-head">
          <div>
            <h2 class="panel-title">增强对比评分</h2>
            <p class="panel-note">统一按收益、回撤、稳定性、交易质量、样本质量与市场状态稳健性排序。</p>
          </div>
          <button
            v-if="enhancedError && evaluationComparison.status !== 'incompatible'"
            class="secondary-btn"
            type="button"
            :disabled="enhancedLoading"
            @click="retryEnhancedCompare"
          >
            <RefreshCw :size="15" aria-hidden="true" />重试评分
          </button>
        </header>
        <div v-if="enhancedLoading" class="loading-state detail-page-feedback" aria-live="polite">
          正在计算增强评分，基础对比可继续查看...
        </div>
        <div v-else-if="enhancedError" class="error-state detail-page-feedback" role="alert">
          {{ enhancedError }}
        </div>
        <DataTable
          v-else
          :columns="ENHANCED_COMPARE_COLUMNS"
          :rows="enhancedCompareRows"
          empty-text="暂无增强对比数据。"
          aria-label="增强策略对比评分"
          sortable
          column-chooser
          sticky-first-column
          compact
        />
      </section>

      <CompareChartsPanel :curves="curves" />
      <CompareReturnHeatmapPanel :strategies="heatmapStrategies" />

      <CorrelationMatrixPanel
        v-if="correlationData"
        :strategies="correlationData.strategies"
        :dates="correlationData.dates"
      />
    </template>
  </section>
</template>
