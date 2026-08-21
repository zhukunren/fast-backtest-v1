<script setup lang="ts">
import { FileCode2, FileUp, Pencil, RefreshCw, Search, Trash2, X } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { deleteStrategy, renameStrategy, uploadStrategy } from '../../api/admin'
import { fetchLibraryCatalog } from '../../api/library'
import type { StrategyLibraryItem } from '../../types/library'

const props = defineProps<{
  token: string
  busy: boolean
}>()

const emit = defineEmits<{
  feedback: [message: string]
  error: [message: string]
}>()

const loading = ref(true)
const uploading = ref(false)
const renamingName = ref('')
const deletingName = ref('')
const items = ref<StrategyLibraryItem[]>([])
const searchQuery = ref('')
const selectedFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const strategyName = ref('')
const overwrite = ref(false)
const renameTarget = ref<StrategyLibraryItem | null>(null)
const renameName = ref('')
const renameTitle = ref('')
const deleteTarget = ref<StrategyLibraryItem | null>(null)

const filteredItems = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return items.value
  return items.value.filter(item => [item.name, item.title, item.status_label, item.family_label]
    .map(value => String(value || '').toLowerCase())
    .join(' ')
    .includes(query))
})
const operationBusy = computed(() => (
  props.busy || uploading.value || Boolean(renamingName.value) || Boolean(deletingName.value)
))
const canUpload = computed(() => Boolean(selectedFile.value && strategyName.value.trim() && !operationBusy.value))
const canRename = computed(() => {
  const target = renameTarget.value
  const name = renameName.value.trim()
  const titleText = renameTitle.value.trim()
  if (!target || operationBusy.value || !/^[A-Za-z0-9_-]{1,80}$/.test(name)) return false
  if (!titleText || titleText.length > 160) return false
  return name !== target.name || titleText !== (target.title || target.name)
})

function normalizeStrategyName(value: string): string {
  return value
    .replace(/\.[^.]+$/, '')
    .replace(/[^A-Za-z0-9_-]+/g, '_')
    .replace(/^[_-]+|[_-]+$/g, '')
    .slice(0, 80)
}

function formatPercent(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

function formatDate(value?: string | null): string {
  const text = String(value || '').trim()
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  return text ? text.replace('T', ' ').slice(0, 10) : '-'
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason || '请求失败')
}

async function loadCatalog() {
  loading.value = true
  try {
    const catalog = await fetchLibraryCatalog()
    items.value = catalog.items ?? []
  } catch (reason) {
    emit('error', errorMessage(reason))
  } finally {
    loading.value = false
  }
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] || null
  if (selectedFile.value && !strategyName.value.trim()) {
    strategyName.value = normalizeStrategyName(selectedFile.value.name)
  }
}

function resetUploadForm() {
  selectedFile.value = null
  strategyName.value = ''
  overwrite.value = false
  if (fileInput.value) fileInput.value.value = ''
}

async function submitUpload() {
  if (!canUpload.value || !selectedFile.value) return
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', selectedFile.value)
    form.append('strategy_name', strategyName.value.trim())
    form.append('overwrite', overwrite.value ? '1' : '0')
    const response = await uploadStrategy(form, props.token)
    const installed = response.strategy
    const warningCount = installed?.warnings?.length || 0
    emit('feedback', `${installed?.name || strategyName.value} 已安装到服务器${warningCount ? `，${warningCount} 项静态提示` : ''}。`)
    resetUploadForm()
    await loadCatalog()
  } catch (reason) {
    emit('error', errorMessage(reason))
  } finally {
    uploading.value = false
  }
}

function openRenameDialog(item: StrategyLibraryItem) {
  if (operationBusy.value) return
  renameTarget.value = item
  renameName.value = item.name
  renameTitle.value = item.title || item.name
}

function closeRenameDialog() {
  if (renamingName.value) return
  renameTarget.value = null
  renameName.value = ''
  renameTitle.value = ''
}

async function confirmRename() {
  const target = renameTarget.value
  if (!target || !canRename.value) return
  const requestedName = renameName.value.trim()
  const requestedTitle = renameTitle.value.trim()
  renamingName.value = target.name
  try {
    const response = await renameStrategy(
      target.name,
      { name: requestedName, title: requestedTitle },
      props.token,
    )
    const renamed = response.strategy
    emit(
      'feedback',
      `${target.title || target.name} 已重命名为 ${renamed?.title || requestedTitle}（${renamed?.name || requestedName}）。`,
    )
    renameTarget.value = null
    renameName.value = ''
    renameTitle.value = ''
    await loadCatalog()
  } catch (reason) {
    emit('error', errorMessage(reason))
  } finally {
    renamingName.value = ''
  }
}

function openDeleteDialog(item: StrategyLibraryItem) {
  if (!operationBusy.value) deleteTarget.value = item
}

function closeDeleteDialog() {
  if (!deletingName.value) deleteTarget.value = null
}

async function confirmDelete() {
  const target = deleteTarget.value
  if (!target || operationBusy.value) return
  deletingName.value = target.name
  try {
    const response = await deleteStrategy(target.name, props.token)
    emit('feedback', `${target.title || target.name} 已从服务器策略库移除，归档 ${response.archive_id || '-'}。`)
    deleteTarget.value = null
    await loadCatalog()
  } catch (reason) {
    emit('error', errorMessage(reason))
  } finally {
    deletingName.value = ''
  }
}

onMounted(() => void loadCatalog())
</script>

<template>
  <section class="strategy-admin-panel" aria-labelledby="strategy-admin-title">
    <header class="strategy-admin-head">
      <div>
        <FileCode2 :size="19" aria-hidden="true" />
        <h2 id="strategy-admin-title">服务器策略</h2>
        <span>{{ items.length }} 个</span>
      </div>
      <button class="strategy-icon-button" type="button" title="刷新策略目录" aria-label="刷新策略目录" :disabled="loading" @click="loadCatalog">
        <RefreshCw :size="16" aria-hidden="true" />
      </button>
    </header>

    <form class="strategy-upload-form" @submit.prevent="submitUpload">
      <label class="strategy-upload-file">
        <span>策略文件</span>
        <input ref="fileInput" type="file" accept=".zip" :disabled="operationBusy" @change="onFileChange" />
      </label>
      <label>
        <span>策略名称</span>
        <input v-model="strategyName" type="text" maxlength="80" placeholder="strategy_name" :disabled="operationBusy" />
      </label>
      <label class="strategy-overwrite">
        <input v-model="overwrite" type="checkbox" :disabled="operationBusy" />
        <span>覆盖同名策略</span>
      </label>
      <button class="strategy-upload-button" type="submit" :disabled="!canUpload">
        <FileUp :size="16" aria-hidden="true" />
        {{ uploading ? '安装中' : '上传并安装' }}
      </button>
    </form>

    <div class="strategy-admin-toolbar">
      <label>
        <Search :size="15" aria-hidden="true" />
        <input v-model="searchQuery" type="search" placeholder="搜索服务器策略" />
      </label>
      <span>显示 {{ filteredItems.length }} / {{ items.length }}</span>
    </div>

    <div v-if="loading" class="strategy-admin-empty">正在读取服务器策略目录</div>
    <div v-else-if="!filteredItems.length" class="strategy-admin-empty">没有匹配的策略</div>
    <div v-else class="strategy-admin-table-wrap">
      <table class="strategy-admin-table">
        <thead>
          <tr>
            <th>策略</th>
            <th>状态</th>
            <th>累计收益</th>
            <th>最近回测</th>
            <th>缓存</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in filteredItems" :key="item.name">
            <td>
              <a :href="`/strategy/${encodeURIComponent(item.name)}`">{{ item.title || item.name }}</a>
              <code>{{ item.name }}</code>
            </td>
            <td>{{ item.status_label || item.status || '-' }}</td>
            <td :class="(item.cum_return ?? 0) >= 0 ? 'is-positive' : 'is-negative'">{{ formatPercent(item.cum_return) }}</td>
            <td>{{ formatDate(item.timestamp) }}</td>
            <td>{{ item.cache_status || '-' }}</td>
            <td>
              <div class="strategy-row-actions">
                <button class="is-rename" type="button" title="重命名策略" :aria-label="`重命名 ${item.title || item.name}`" :disabled="operationBusy" @click="openRenameDialog(item)">
                  <Pencil :size="15" aria-hidden="true" />
                </button>
                <button class="is-delete" type="button" title="删除策略" :aria-label="`删除 ${item.title || item.name}`" :disabled="operationBusy" @click="openDeleteDialog(item)">
                  <Trash2 :size="15" aria-hidden="true" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <Teleport to="body">
      <div v-if="renameTarget" class="strategy-dialog-backdrop" @mousedown.self="closeRenameDialog">
        <section class="strategy-admin-dialog strategy-rename-dialog" role="dialog" aria-modal="true" aria-labelledby="strategy-rename-title">
          <header>
            <div>
              <p>STRATEGY IDENTITY</p>
              <h2 id="strategy-rename-title">重命名服务器策略</h2>
            </div>
            <button type="button" title="关闭" aria-label="关闭" :disabled="operationBusy" @click="closeRenameDialog">
              <X :size="18" aria-hidden="true" />
            </button>
          </header>
          <form @submit.prevent="confirmRename">
            <div class="strategy-rename-fields">
              <label>
                <span>策略标识</span>
                <input v-model="renameName" type="text" maxlength="80" pattern="[A-Za-z0-9_-]{1,80}" autocomplete="off" :disabled="operationBusy" />
              </label>
              <label>
                <span>显示标题</span>
                <input v-model="renameTitle" type="text" maxlength="160" autocomplete="off" :disabled="operationBusy" />
              </label>
            </div>
            <footer>
              <button class="is-secondary" type="button" :disabled="operationBusy" @click="closeRenameDialog">取消</button>
              <button class="is-primary" type="submit" :disabled="!canRename">
                <Pencil :size="15" aria-hidden="true" />
                {{ renamingName ? '保存中' : '保存名称' }}
              </button>
            </footer>
          </form>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="deleteTarget" class="strategy-dialog-backdrop" @mousedown.self="closeDeleteDialog">
        <section class="strategy-admin-dialog strategy-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="strategy-delete-title">
          <header>
            <div>
              <p>RECOVERABLE ARCHIVE</p>
              <h2 id="strategy-delete-title">删除服务器策略</h2>
            </div>
            <button type="button" title="关闭" aria-label="关闭" :disabled="Boolean(deletingName)" @click="closeDeleteDialog">
              <X :size="18" aria-hidden="true" />
            </button>
          </header>
          <div class="strategy-delete-target">
            <strong>{{ deleteTarget.title || deleteTarget.name }}</strong>
            <code>{{ deleteTarget.name }}</code>
          </div>
          <footer>
            <button class="is-secondary" type="button" :disabled="Boolean(deletingName)" @click="closeDeleteDialog">取消</button>
            <button class="is-danger" type="button" :disabled="Boolean(deletingName)" @click="confirmDelete">
              <Trash2 :size="15" aria-hidden="true" />
              {{ deletingName ? '删除中' : '确认删除' }}
            </button>
          </footer>
        </section>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.strategy-admin-panel {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
}

.strategy-admin-head,
.strategy-admin-head > div,
.strategy-admin-toolbar,
.strategy-admin-toolbar label,
.strategy-admin-dialog header,
.strategy-admin-dialog footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.strategy-admin-head {
  min-height: 54px;
  padding: 13px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-muted);
}

.strategy-admin-head h2,
.strategy-admin-dialog h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 730;
  letter-spacing: 0;
}

.strategy-admin-head span,
.strategy-admin-toolbar > span {
  color: var(--ink-soft);
  font-size: 12px;
}

.strategy-icon-button,
.strategy-admin-table button,
.strategy-admin-dialog header button {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink-soft);
  cursor: pointer;
}

.strategy-upload-form {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--line);
}

.strategy-upload-form label {
  display: grid;
  min-width: 0;
  gap: 6px;
}

.strategy-upload-form label > span {
  color: var(--ink-soft);
  font-size: 12px;
  font-weight: 680;
}

.strategy-upload-form input[type="text"],
.strategy-upload-form input[type="date"],
.strategy-upload-form input[type="file"],
.strategy-admin-toolbar input {
  width: 100%;
  min-width: 0;
  height: 38px;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
  padding: 0 10px;
  font: inherit;
  font-size: 13px;
  letter-spacing: 0;
}

.strategy-upload-form input[type="file"] {
  padding: 6px 8px;
}

.strategy-upload-form input:focus,
.strategy-admin-toolbar input:focus {
  border-color: var(--accent);
  outline: 3px solid var(--accent-soft);
}

.strategy-overwrite {
  display: flex !important;
  align-items: center;
  align-self: end;
  min-height: 38px;
  grid-auto-flow: column;
  justify-content: start;
}

.strategy-overwrite input {
  width: 16px;
  height: 16px;
}

.strategy-upload-button,
.strategy-admin-dialog footer button {
  display: inline-flex;
  min-height: 36px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  align-self: end;
  border-radius: 4px;
  padding: 7px 12px;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.strategy-upload-button {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.strategy-admin-toolbar {
  padding: 11px 16px;
  border-bottom: 1px solid var(--line);
}

.strategy-admin-toolbar label {
  justify-content: flex-start;
  width: min(360px, 100%);
  color: var(--ink-soft);
}

.strategy-admin-toolbar input {
  height: 34px;
}

.strategy-admin-table-wrap {
  overflow: auto;
}

.strategy-admin-table {
  width: 100%;
  min-width: 820px;
  border-collapse: collapse;
}

.strategy-admin-table th,
.strategy-admin-table td {
  padding: 11px 16px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.strategy-admin-table th {
  color: var(--ink-soft);
  background: var(--surface-muted);
  font-size: 12px;
  font-weight: 700;
}

.strategy-admin-table td {
  font-size: 13px;
}

.strategy-admin-table td:first-child a,
.strategy-admin-table td:first-child code {
  display: block;
}

.strategy-admin-table td:first-child a {
  color: var(--ink);
  font-weight: 700;
}

.strategy-admin-table td:first-child code {
  margin-top: 2px;
  color: var(--ink-soft);
  font-size: 11px;
}

.strategy-admin-table td:last-child,
.strategy-admin-table th:last-child {
  text-align: right;
}

.strategy-row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

.strategy-admin-table td:last-child button {
  margin: 0;
}

.strategy-admin-table td:last-child button.is-rename {
  color: var(--accent);
}

.strategy-admin-table td:last-child button.is-delete {
  color: var(--negative);
}

.is-positive {
  color: var(--positive);
  font-weight: 700;
}

.is-negative {
  color: var(--negative);
  font-weight: 700;
}

.strategy-admin-empty {
  display: grid;
  min-height: 110px;
  place-items: center;
  color: var(--ink-soft);
  font-size: 13px;
}

.strategy-dialog-backdrop {
  position: fixed;
  z-index: 110;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgba(17, 20, 27, 0.46);
  padding: 20px;
}

.strategy-admin-dialog {
  width: min(440px, 100%);
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--surface);
  box-shadow: 0 24px 70px rgba(17, 20, 27, 0.22);
}

.strategy-admin-dialog header,
.strategy-admin-dialog footer,
.strategy-delete-target {
  padding: 15px 17px;
}

.strategy-admin-dialog header {
  border-bottom: 1px solid var(--line);
}

.strategy-admin-dialog header p {
  margin: 0 0 5px;
  color: var(--accent);
  font-size: 11px;
  font-weight: 750;
}

.strategy-delete-dialog header p {
  color: var(--negative);
}

.strategy-rename-fields {
  display: grid;
  gap: 13px;
  padding: 16px 17px;
}

.strategy-rename-fields label {
  display: grid;
  gap: 6px;
}

.strategy-rename-fields label > span {
  color: var(--ink-soft);
  font-size: 12px;
  font-weight: 680;
}

.strategy-rename-fields input {
  width: 100%;
  min-width: 0;
  height: 38px;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
  padding: 0 10px;
  font: inherit;
  font-size: 13px;
  letter-spacing: 0;
}

.strategy-rename-fields input:focus {
  border-color: var(--accent);
  outline: 3px solid var(--accent-soft);
}

.strategy-delete-target strong,
.strategy-delete-target code {
  display: block;
}

.strategy-delete-target code {
  margin-top: 4px;
  color: var(--ink-soft);
}

.strategy-admin-dialog footer {
  justify-content: flex-end;
  border-top: 1px solid var(--line);
  background: var(--surface-muted);
}

.strategy-admin-dialog footer button {
  display: inline-flex;
  min-height: 36px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border-radius: 4px;
  padding: 7px 12px;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.strategy-admin-dialog footer .is-secondary {
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink);
}

.strategy-admin-dialog footer .is-primary {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
}

.strategy-admin-dialog footer .is-danger {
  border: 1px solid var(--negative);
  background: var(--negative);
  color: #fff;
}

@media (max-width: 860px) {
  .strategy-upload-form {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 600px) {
  .strategy-upload-form {
    grid-template-columns: 1fr;
  }

  .strategy-admin-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .strategy-admin-toolbar label {
    width: 100%;
  }
}
</style>
