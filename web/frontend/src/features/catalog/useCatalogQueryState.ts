import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { LibraryCatalogResponse } from '../../types/library'
import { CATALOG_SORT_MODES, hasMeaningfulFilter, type CatalogSortMode } from './catalogModel'

const DEFAULT_FILTER = 'all'
export const DEFAULT_CATALOG_SORT: CatalogSortMode = 'risk_adjusted'
const VALID_SORT_MODES = new Set<string>(CATALOG_SORT_MODES)

interface CatalogQuerySnapshot {
  query: string
  family: string
  status: string
  style: string
  portfolio: string
  evaluationWindow: string
  sort: CatalogSortMode
}

function readCatalogState(): CatalogQuerySnapshot {
  const params = new URLSearchParams(window.location.search)
  const requestedSort = params.get('sort') || DEFAULT_CATALOG_SORT
  return {
    query: params.get('q') || '',
    family: params.get('family') || DEFAULT_FILTER,
    status: params.get('status') || DEFAULT_FILTER,
    style: params.get('style') || DEFAULT_FILTER,
    portfolio: params.get('portfolio') || DEFAULT_FILTER,
    evaluationWindow: params.get('evaluation_window') || DEFAULT_FILTER,
    sort: VALID_SORT_MODES.has(requestedSort) ? requestedSort as CatalogSortMode : DEFAULT_CATALOG_SORT,
  }
}

function setOptionalUrlParam(params: URLSearchParams, key: string, value: string, defaultValue: string) {
  if (!value || value === defaultValue) params.delete(key)
  else params.set(key, value)
}

export function useCatalogQueryState() {
  const initial = readCatalogState()
  const searchQuery = ref(initial.query)
  const familyFilter = ref(initial.family)
  const statusFilter = ref(initial.status)
  const styleFilter = ref(initial.style)
  const portfolioFilter = ref(initial.portfolio)
  const evaluationWindowFilter = ref(initial.evaluationWindow)
  const sortMode = ref<CatalogSortMode>(initial.sort)
  let syncingFromHistory = false

  function syncUrlState() {
    if (syncingFromHistory || window.location.pathname !== '/') return
    const url = new URL(window.location.href)
    setOptionalUrlParam(url.searchParams, 'q', searchQuery.value.trim(), '')
    setOptionalUrlParam(url.searchParams, 'family', familyFilter.value, DEFAULT_FILTER)
    setOptionalUrlParam(url.searchParams, 'status', statusFilter.value, DEFAULT_FILTER)
    setOptionalUrlParam(url.searchParams, 'style', styleFilter.value, DEFAULT_FILTER)
    setOptionalUrlParam(url.searchParams, 'portfolio', portfolioFilter.value, DEFAULT_FILTER)
    setOptionalUrlParam(url.searchParams, 'evaluation_window', evaluationWindowFilter.value, DEFAULT_FILTER)
    setOptionalUrlParam(url.searchParams, 'sort', sortMode.value, DEFAULT_CATALOG_SORT)
    const nextPath = `${url.pathname}${url.search}${url.hash}`
    const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`
    if (nextPath !== currentPath) window.history.replaceState(window.history.state, '', nextPath)
  }

  function applyStateFromHistory() {
    if (window.location.pathname !== '/') return
    const state = readCatalogState()
    syncingFromHistory = true
    searchQuery.value = state.query
    familyFilter.value = state.family
    statusFilter.value = state.status
    styleFilter.value = state.style
    portfolioFilter.value = state.portfolio
    evaluationWindowFilter.value = state.evaluationWindow
    sortMode.value = state.sort
    void nextTick(() => {
      syncingFromHistory = false
    })
  }

  function normalizeLoadedFilters(filters: LibraryCatalogResponse['filters']) {
    const normalize = (
      value: string,
      options: LibraryCatalogResponse['filters']['families'] | undefined,
    ) => {
      if (!hasMeaningfulFilter(options)) return DEFAULT_FILTER
      const values = new Set((options ?? []).map(option => option.value))
      return values.has(value) ? value : DEFAULT_FILTER
    }
    familyFilter.value = normalize(familyFilter.value, filters.families)
    statusFilter.value = normalize(statusFilter.value, filters.statuses)
    styleFilter.value = normalize(styleFilter.value, filters.styles)
    portfolioFilter.value = normalize(portfolioFilter.value, filters.portfolios)
    evaluationWindowFilter.value = normalize(evaluationWindowFilter.value, filters.evaluation_windows)
  }

  function resetFilters() {
    searchQuery.value = ''
    familyFilter.value = DEFAULT_FILTER
    statusFilter.value = DEFAULT_FILTER
    styleFilter.value = DEFAULT_FILTER
    portfolioFilter.value = DEFAULT_FILTER
    evaluationWindowFilter.value = DEFAULT_FILTER
  }

  watch(
    [searchQuery, familyFilter, statusFilter, styleFilter, portfolioFilter, evaluationWindowFilter, sortMode],
    syncUrlState,
  )
  onMounted(() => window.addEventListener('popstate', applyStateFromHistory))
  onBeforeUnmount(() => window.removeEventListener('popstate', applyStateFromHistory))

  return {
    searchQuery,
    familyFilter,
    statusFilter,
    styleFilter,
    portfolioFilter,
    evaluationWindowFilter,
    sortMode,
    normalizeLoadedFilters,
    resetFilters,
  }
}
