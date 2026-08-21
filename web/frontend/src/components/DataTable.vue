<script setup lang="ts">
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Settings2 } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import type { CSSProperties } from 'vue'
import type { TableRow, TableValue } from '../types/library'

interface DataTableColumn {
  key: string
  label: string
  align?: 'left' | 'center' | 'right'
  minWidth?: number | string
  sortable?: boolean
  defaultVisible?: boolean
}

type SortDirection = 'ascending' | 'descending'

type RowKeyResolver = string | ((row: TableRow, index: number) => string | number)

interface DataTableCellPresentation {
  badgeClass?: string
  valueClass?: string
}

const props = withDefaults(defineProps<{
  columns: DataTableColumn[]
  rows: TableRow[]
  emptyText?: string
  compact?: boolean
  ariaLabel?: string
  caption?: string
  rowKey?: RowKeyResolver
  pageSize?: number
  pageSizeOptions?: number[]
  stickyFirstColumn?: boolean
  sortable?: boolean
  columnChooser?: boolean
  cellPresentation?: (column: DataTableColumn, value: TableValue, row: TableRow) => DataTableCellPresentation | null
}>(), {
  emptyText: '暂无数据',
  compact: false,
  ariaLabel: '',
  caption: '',
  pageSize: 0,
  pageSizeOptions: () => [25, 50, 100],
  stickyFirstColumn: false,
  sortable: false,
  columnChooser: false,
})

const currentPage = ref(1)
const selectedPageSize = ref(normalizePageSize(props.pageSize))
const sortKey = ref('')
const sortDirection = ref<SortDirection>('ascending')
const visibleColumnKeys = ref<string[]>(defaultVisibleColumnKeys(props.columns))

const isPaginated = computed(() => normalizePageSize(props.pageSize) > 0)
const visibleCaption = computed(() => props.caption.trim())
const accessibleName = computed(() => (
  props.ariaLabel.trim() || visibleCaption.value || '数据表格'
))
const visibleColumns = computed(() => {
  if (!props.columnChooser) return props.columns
  const visible = new Set(visibleColumnKeys.value)
  return props.columns.filter(column => visible.has(column.key))
})
const sortedRows = computed(() => {
  const column = props.columns.find(item => item.key === sortKey.value)
  if (!column || !isColumnSortable(column)) return props.rows
  const direction = sortDirection.value === 'ascending' ? 1 : -1
  return props.rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const comparison = compareTableValues(left.row[column.key], right.row[column.key])
      return comparison === 0 ? left.index - right.index : comparison * direction
    })
    .map(item => item.row)
})
const normalizedPageSizeOptions = computed(() => {
  const options = props.pageSizeOptions
    .map(normalizePageSize)
    .filter(value => value > 0)
  const currentSize = selectedPageSize.value
  if (currentSize > 0) options.push(currentSize)
  return [...new Set(options)].sort((left, right) => left - right)
})
const pageCount = computed(() => {
  if (!isPaginated.value || selectedPageSize.value <= 0) return 1
  return Math.max(1, Math.ceil(props.rows.length / selectedPageSize.value))
})
const pageStartIndex = computed(() => {
  if (!isPaginated.value || selectedPageSize.value <= 0) return 0
  return (currentPage.value - 1) * selectedPageSize.value
})
const displayedRows = computed(() => {
  if (!isPaginated.value || selectedPageSize.value <= 0) return sortedRows.value
  return sortedRows.value.slice(
    pageStartIndex.value,
    pageStartIndex.value + selectedPageSize.value,
  )
})
const pageRangeText = computed(() => {
  if (!props.rows.length) return '共 0 条'
  const start = pageStartIndex.value + 1
  const end = pageStartIndex.value + displayedRows.value.length
  return `第 ${start}-${end} 条，共 ${props.rows.length} 条`
})

watch(() => props.pageSize, value => {
  selectedPageSize.value = normalizePageSize(value)
  currentPage.value = 1
})

watch(() => props.columns, columns => {
  const available = new Set(columns.map(column => column.key))
  const retained = visibleColumnKeys.value.filter(key => available.has(key))
  const additions = columns
    .filter(column => column.defaultVisible !== false && !retained.includes(column.key))
    .map(column => column.key)
  visibleColumnKeys.value = [...retained, ...additions]
  if (sortKey.value && !available.has(sortKey.value)) sortKey.value = ''
}, { deep: true })

watch([() => props.rows, () => props.rows.length], () => {
  currentPage.value = 1
})

watch(selectedPageSize, () => {
  currentPage.value = 1
})

watch(pageCount, count => {
  currentPage.value = Math.min(Math.max(1, currentPage.value), count)
})

function normalizePageSize(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 0
  return Math.max(1, Math.floor(value))
}

function defaultVisibleColumnKeys(columns: DataTableColumn[]): string[] {
  const preferred = columns.filter(column => column.defaultVisible !== false).map(column => column.key)
  return preferred.length ? preferred : columns.slice(0, 1).map(column => column.key)
}

function isColumnSortable(column: DataTableColumn): boolean {
  return column.sortable ?? props.sortable
}

function toggleSort(column: DataTableColumn) {
  if (!isColumnSortable(column)) return
  if (sortKey.value !== column.key) {
    sortKey.value = column.key
    sortDirection.value = 'ascending'
  } else if (sortDirection.value === 'ascending') {
    sortDirection.value = 'descending'
  } else {
    sortKey.value = ''
    sortDirection.value = 'ascending'
  }
  currentPage.value = 1
}

function columnSortState(column: DataTableColumn): SortDirection | 'none' | undefined {
  if (!isColumnSortable(column)) return undefined
  return sortKey.value === column.key ? sortDirection.value : 'none'
}

function toggleColumn(columnKey: string) {
  if (visibleColumnKeys.value.includes(columnKey)) {
    if (visibleColumnKeys.value.length <= 1) return
    visibleColumnKeys.value = visibleColumnKeys.value.filter(key => key !== columnKey)
    if (sortKey.value === columnKey) sortKey.value = ''
  } else {
    visibleColumnKeys.value = props.columns
      .map(column => column.key)
      .filter(key => key === columnKey || visibleColumnKeys.value.includes(key))
  }
}

function normalizeComparableNumber(value: TableValue): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const text = String(value ?? '').trim()
  if (!text || text === '-') return null
  const normalized = text
    .replaceAll(',', '')
    .replaceAll('%', '')
    .replace(/[￥$¥元天笔条只]/g, '')
    .trim()
  if (!/^[-+]?\d+(?:\.\d+)?$/.test(normalized)) return null
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

function compareTableValues(left: TableValue, right: TableValue): number {
  if (left == null || left === '') return right == null || right === '' ? 0 : 1
  if (right == null || right === '') return -1

  const leftNumber = normalizeComparableNumber(left)
  const rightNumber = normalizeComparableNumber(right)
  if (leftNumber != null && rightNumber != null) return leftNumber - rightNumber

  return String(left).localeCompare(String(right), 'zh-CN', {
    numeric: true,
    sensitivity: 'base',
  })
}

function shiftPage(step: number) {
  currentPage.value = Math.min(
    pageCount.value,
    Math.max(1, currentPage.value + step),
  )
}

function resolveRowKey(row: TableRow, pageRowIndex: number): string | number {
  const absoluteIndex = pageStartIndex.value + pageRowIndex
  if (typeof props.rowKey === 'function') {
    const resolved = props.rowKey(row, absoluteIndex)
    if (resolved !== '') return resolved
  } else if (props.rowKey) {
    const resolved = row[props.rowKey]
    if (resolved != null && resolved !== '') return String(resolved)
  }
  return absoluteIndex
}

function columnStyle(column: DataTableColumn): CSSProperties | undefined {
  if (typeof column.minWidth === 'number') {
    if (!Number.isFinite(column.minWidth) || column.minWidth <= 0) return undefined
    return { minWidth: `${column.minWidth}px` }
  }
  const minWidth = column.minWidth?.trim()
  return minWidth ? { minWidth } : undefined
}

function formatCell(value: TableValue): string {
  if (value == null || value === '') return '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function cellClass(column: DataTableColumn): string {
  if (column.align === 'center') return 'align-center'
  if (column.align === 'right') return 'align-right'
  return 'align-left'
}

function resolveCellPresentation(column: DataTableColumn, value: TableValue, row: TableRow): DataTableCellPresentation {
  return props.cellPresentation?.(column, value, row) ?? {}
}
</script>

<template>
  <div v-if="!rows.length" class="empty-state data-table-empty">{{ emptyText }}</div>
  <div v-else class="data-table-shell">
    <div v-if="columnChooser" class="data-table-settings">
      <details class="data-table-column-menu">
        <summary title="选择显示列" aria-label="选择显示列">
          <Settings2 :size="16" aria-hidden="true" />
        </summary>
        <fieldset>
          <legend>显示列</legend>
          <label v-for="column in columns" :key="column.key">
            <input
              type="checkbox"
              :checked="visibleColumnKeys.includes(column.key)"
              :disabled="visibleColumnKeys.length <= 1 && visibleColumnKeys.includes(column.key)"
              @change="toggleColumn(column.key)"
            />
            <span>{{ column.label }}</span>
          </label>
        </fieldset>
      </details>
    </div>
    <div
      class="data-table-wrap"
      :class="{ compact }"
      role="region"
      :aria-label="accessibleName"
      tabindex="0"
    >
      <table
        class="data-table"
        :class="{ 'is-sticky-first-column': stickyFirstColumn }"
      >
        <caption
          class="data-table-caption"
          :class="{ 'is-visually-hidden': !visibleCaption }"
        >
          {{ visibleCaption || accessibleName }}
        </caption>
        <thead>
          <tr>
            <th
              v-for="column in visibleColumns"
              :key="column.key"
              :class="cellClass(column)"
              :style="columnStyle(column)"
              scope="col"
              :aria-sort="columnSortState(column)"
            >
              <button
                v-if="isColumnSortable(column)"
                class="data-table-sort-button"
                type="button"
                :title="`按${column.label}排序`"
                @click="toggleSort(column)"
              >
                <span>{{ column.label }}</span>
                <ArrowUp v-if="sortKey === column.key && sortDirection === 'ascending'" :size="13" aria-hidden="true" />
                <ArrowDown v-else-if="sortKey === column.key" :size="13" aria-hidden="true" />
                <ArrowUpDown v-else :size="13" aria-hidden="true" />
              </button>
              <template v-else>{{ column.label }}</template>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, rowIndex) in displayedRows"
            :key="resolveRowKey(row, rowIndex)"
          >
            <td
              v-for="column in visibleColumns"
              :key="column.key"
              :class="[cellClass(column), resolveCellPresentation(column, row[column.key], row).valueClass || '']"
              :style="columnStyle(column)"
              :data-label="column.label"
            >
              <span
                v-if="resolveCellPresentation(column, row[column.key], row).badgeClass"
                class="trade-tag"
                :class="resolveCellPresentation(column, row[column.key], row).badgeClass"
              >
                {{ formatCell(row[column.key]) }}
              </span>
              <span v-else>{{ formatCell(row[column.key]) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <nav
      v-if="isPaginated"
      class="data-table-pagination"
      :aria-label="`${accessibleName}分页`"
    >
      <span class="data-table-page-status" aria-live="polite" aria-atomic="true">
        {{ pageRangeText }} · 第 {{ currentPage }}/{{ pageCount }} 页
      </span>
      <div class="data-table-page-actions">
        <label class="data-table-page-size">
          <span>每页</span>
          <select v-model.number="selectedPageSize" aria-label="每页显示行数">
            <option
              v-for="option in normalizedPageSizeOptions"
              :key="option"
              :value="option"
            >
              {{ option }}
            </option>
          </select>
          <span>条</span>
        </label>
        <button
          class="data-table-page-button"
          type="button"
          title="上一页"
          aria-label="上一页"
          :disabled="currentPage <= 1"
          @click="shiftPage(-1)"
        >
          <ChevronLeft :size="17" aria-hidden="true" />
        </button>
        <button
          class="data-table-page-button"
          type="button"
          title="下一页"
          aria-label="下一页"
          :disabled="currentPage >= pageCount"
          @click="shiftPage(1)"
        >
          <ChevronRight :size="17" aria-hidden="true" />
        </button>
      </div>
    </nav>
  </div>
</template>

<style scoped>
.data-table-shell {
  width: 100%;
  min-width: 0;
  max-width: 100%;
}

.data-table-settings {
  display: flex;
  min-height: 36px;
  align-items: center;
  justify-content: flex-end;
  padding: 0 0 8px;
}

.data-table-column-menu {
  position: relative;
}

.data-table-column-menu summary {
  display: inline-grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border: 1px solid var(--ui-border, #d0d5dd);
  border-radius: 6px;
  background: var(--ui-surface, #ffffff);
  color: var(--ui-text-muted, #667085);
  cursor: pointer;
  list-style: none;
}

.data-table-column-menu summary::-webkit-details-marker {
  display: none;
}

.data-table-column-menu summary:hover,
.data-table-column-menu summary:focus-visible {
  border-color: var(--ui-primary-border, #98b9e8);
  background: var(--ui-primary-soft, #eef5ff);
  color: var(--ui-primary, #1769db);
}

.data-table-column-menu summary:focus-visible {
  outline: 3px solid var(--ui-focus-outline, #86b7fe);
  outline-offset: 2px;
}

.data-table-column-menu fieldset {
  position: absolute;
  z-index: 20;
  top: calc(100% + 6px);
  right: 0;
  display: grid;
  min-width: 180px;
  max-height: 320px;
  gap: 2px;
  overflow: auto;
  margin: 0;
  border: 1px solid var(--ui-border, #d0d5dd);
  border-radius: 6px;
  background: var(--ui-surface, #ffffff);
  box-shadow: var(--ui-shadow-popover, 0 12px 28px rgba(15, 23, 42, 0.14));
  padding: 8px;
}

.data-table-column-menu legend {
  padding: 3px 7px 7px;
  color: var(--ui-text-muted, #667085);
  font-size: 11px;
  font-weight: 700;
}

.data-table-column-menu label {
  display: grid;
  min-height: 32px;
  align-items: center;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 7px;
  border-radius: 4px;
  padding: 4px 7px;
  color: var(--ui-text, #1f2937);
  font-size: 12px;
  cursor: pointer;
}

.data-table-column-menu label:hover {
  background: var(--ui-surface-subtle, #f7f8fa);
}

.data-table-sort-button {
  display: inline-flex;
  width: 100%;
  min-height: 28px;
  align-items: center;
  justify-content: inherit;
  gap: 5px;
  border: 0;
  background: transparent;
  padding: 0;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.data-table-sort-button svg {
  flex: 0 0 auto;
  opacity: 0.72;
}

.data-table-sort-button:focus-visible {
  border-radius: 3px;
  outline: 2px solid var(--ui-focus-outline, #86b7fe);
  outline-offset: 2px;
}

.data-table-wrap:focus-visible {
  outline: 3px solid var(--ui-focus-outline, #86b7fe);
  outline-offset: -3px;
}

.data-table-caption {
  caption-side: top;
  padding: 10px 14px;
  color: var(--ui-text, #1f2937);
  font-size: 13px;
  font-weight: 700;
  text-align: left;
}

.data-table-caption.is-visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

.data-table.is-sticky-first-column th:first-child,
.data-table.is-sticky-first-column td:first-child {
  position: sticky;
  left: 0;
  background-color: var(--ui-surface, #ffffff);
  box-shadow: 1px 0 0 var(--ui-border-subtle, #e5e7eb);
}

.data-table.is-sticky-first-column th:first-child {
  z-index: 4;
}

.data-table.is-sticky-first-column td:first-child {
  z-index: 1;
}

.data-table-pagination {
  display: flex;
  min-height: 48px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-top: 1px solid var(--ui-border-subtle, #e5e7eb);
  padding: 8px 0 0;
  color: var(--ui-text-muted, #667085);
  font-size: 12px;
}

.data-table-page-status {
  min-width: 0;
}

.data-table-page-actions,
.data-table-page-size {
  display: inline-flex;
  align-items: center;
}

.data-table-page-actions {
  flex: 0 0 auto;
  gap: 6px;
}

.data-table-page-size {
  gap: 5px;
  margin-right: 4px;
  white-space: nowrap;
}

.data-table-page-size select {
  min-width: 66px;
  min-height: 36px;
  border: 1px solid var(--ui-border, #d0d5dd);
  border-radius: 6px;
  background: var(--ui-surface, #ffffff);
  padding: 0 8px;
  color: var(--ui-text, #1f2937);
  font: inherit;
}

.data-table-page-button {
  display: inline-grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 1px solid var(--ui-border, #d0d5dd);
  border-radius: 6px;
  background: var(--ui-surface, #ffffff);
  padding: 0;
  color: var(--ui-text, #344054);
  cursor: pointer;
}

.data-table-page-button:hover:not(:disabled) {
  border-color: var(--ui-primary-border, #98b9e8);
  background: var(--ui-primary-soft, #eef5ff);
  color: var(--ui-primary, #1769db);
}

.data-table-page-button:focus-visible,
.data-table-page-size select:focus-visible {
  outline: 3px solid var(--ui-focus-outline, #86b7fe);
  outline-offset: 2px;
}

.data-table-page-button:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}

@media (max-width: 560px) {
  .data-table-pagination {
    align-items: flex-start;
    flex-direction: column;
  }

  .data-table-page-actions {
    width: 100%;
  }

  .data-table-page-size {
    margin-right: auto;
  }
}
</style>

<style>
/* Let the open column picker escape card clipping without changing closed panels. */
.panel:has(.data-table-column-menu[open]) {
  overflow: visible;
}
</style>
