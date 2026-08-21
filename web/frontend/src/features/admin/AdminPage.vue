<script setup lang="ts">
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Download,
  GitBranch,
  KeyRound,
  RefreshCw,
  RotateCw,
  ServerCog,
  ShieldCheck,
  Terminal,
  X,
  XCircle,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  AdminApiError,
  fetchAdminStatus,
  fetchMaintenanceStatus,
  startDataUpdate,
  startVersionUpdate,
  type MaintenanceProgress,
  type MaintenanceTaskResult,
  type RepositoryStatus,
} from '../../api/admin'
import { readStoredAdminToken, storeAdminToken } from '../../api/admin-auth'
import StrategyManagementPanel from './StrategyManagementPanel.vue'

const loading = ref(true)
const submitting = ref(false)
const pageError = ref('')
const actionMessage = ref('')
const authRequired = ref(false)
const adminToken = ref(readStoredAdminToken())
const tokenInput = ref('')
const repository = ref<RepositoryStatus | null>(null)
const maintenance = ref<MaintenanceProgress>({ running: false, results: [] })
const versionDialogOpen = ref(false)

const endDate = ref(formatLocalDate(new Date()))
const startDate = ref(formatLocalDate(offsetDate(new Date(), -7)))
const maxWorkers = ref(2)
const updateStock = ref(true)
const updateIndex = ref(true)
const updateEtf = ref(false)
const rebuildPanel = ref(true)
const dryRun = ref(false)
const symbolType = ref<'stock' | 'index' | 'etf'>('stock')
const codes = ref('')

let pollTimer: number | null = null
let statusRequestPending = false

const taskResults = computed(() => maintenance.value.results ?? [])
const taskPercent = computed(() => {
  const total = Number(maintenance.value.total || 0)
  const completed = Number(maintenance.value.completed || 0)
  return total ? Math.min(100, Math.max(0, completed / total * 100)) : 0
})
const taskTitle = computed(() => maintenance.value.task_kind === 'version_update' ? '版本更新' : '数据更新')
const taskStatusText = computed(() => {
  if (maintenance.value.running) {
    const completed = Number(maintenance.value.completed || 0)
    const total = Number(maintenance.value.total || 0)
    const current = String(maintenance.value.current || '').trim()
    return [current, total ? `${completed}/${total}` : '', formatDuration(maintenance.value.elapsed_sec)]
      .filter(Boolean)
      .join(' · ')
  }
  if (!taskResults.value.length) return '空闲'
  const failed = taskResults.value.filter(result => result.status === 'failed').length
  return failed ? `完成，${failed} 项失败` : '已完成'
})
const repositoryStateLabel = computed(() => {
  if (!repository.value?.available) return '不可用'
  if (repository.value.dirty) return '有本地改动'
  if (repository.value.diverged) return '历史已分叉'
  if (repository.value.update_available) return `可更新 ${repository.value.behind || 0} 个提交`
  if (repository.value.ahead) return `本地领先 ${repository.value.ahead} 个提交`
  return '已同步'
})
const repositoryStateClass = computed(() => {
  if (!repository.value?.available || repository.value.diverged) return 'is-danger'
  if (repository.value.dirty) return 'is-warning'
  if (repository.value.update_available) return 'is-update'
  return 'is-ready'
})
const canStartDataUpdate = computed(() => {
  if (maintenance.value.running || submitting.value || !startDate.value || !endDate.value) return false
  if (codes.value.trim()) return true
  return updateStock.value || updateIndex.value || updateEtf.value
})
const canStartVersionUpdate = computed(() => Boolean(
  repository.value?.can_update
  && !maintenance.value.running
  && !submitting.value,
))
const restartRequired = computed(() => taskResults.value.some(result => result.restart_required))

function offsetDate(value: Date, days: number): Date {
  const result = new Date(value)
  result.setDate(result.getDate() + days)
  return result
}

function formatLocalDate(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function compactDate(value: string): string {
  return String(value || '').replaceAll('-', '')
}

function formatDuration(value?: number): string {
  const seconds = Number(value || 0)
  if (!seconds) return ''
  if (seconds < 60) return `${seconds.toFixed(0)} 秒`
  return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`
}

function shortRevision(value?: string): string {
  return String(value || '').slice(0, 12) || '-'
}

function friendlyError(reason: unknown): string {
  const message = reason instanceof Error ? reason.message : String(reason || '')
  const labels: Record<string, string> = {
    admin_auth_required: '管理令牌无效或未提供。',
    admin_local_only: '当前管理接口仅允许本机访问。',
    cross_site_request_rejected: '请求来源未获授权。',
    sensitive_auth_not_configured: 'Web 对外绑定前必须配置管理令牌。',
    sensitive_local_only: '当前敏感接口仅允许本机访问。',
    backtest_running: '回测正在运行，请等待回测结束。',
    maintenance_running: '已有维护任务正在运行。',
  }
  return labels[message] || message || '请求失败，请稍后重试。'
}

function handleApiError(reason: unknown) {
  if (reason instanceof AdminApiError && reason.status === 401) {
    authRequired.value = true
    pageError.value = ''
    stopPolling()
    return
  }
  pageError.value = friendlyError(reason)
}

function showChildFeedback(message: string) {
  pageError.value = ''
  actionMessage.value = message
}

function showChildError(message: string) {
  actionMessage.value = ''
  pageError.value = friendlyError(message)
}

async function loadStatus() {
  loading.value = true
  pageError.value = ''
  try {
    const status = await fetchAdminStatus(adminToken.value)
    repository.value = status.repository
    maintenance.value = status.maintenance
    authRequired.value = false
    if (status.maintenance.running) ensurePolling()
  } catch (reason) {
    handleApiError(reason)
  } finally {
    loading.value = false
  }
}

async function connectWithToken() {
  adminToken.value = tokenInput.value.trim()
  storeAdminToken(adminToken.value)
  await loadStatus()
  if (!authRequired.value) tokenInput.value = ''
}

async function syncTaskStatus() {
  if (statusRequestPending || authRequired.value) return
  statusRequestPending = true
  const wasRunning = maintenance.value.running
  try {
    maintenance.value = await fetchMaintenanceStatus(adminToken.value)
    if (maintenance.value.running) {
      ensurePolling()
    } else if (wasRunning) {
      stopPolling()
      actionMessage.value = taskResults.value.some(result => result.status === 'failed')
        ? `${taskTitle.value}完成，但存在失败项。`
        : `${taskTitle.value}已完成。`
      await loadStatus()
    }
  } catch (reason) {
    handleApiError(reason)
  } finally {
    statusRequestPending = false
  }
}

function ensurePolling() {
  if (pollTimer != null) return
  pollTimer = window.setInterval(() => void syncTaskStatus(), 2000)
}

function stopPolling() {
  if (pollTimer == null) return
  window.clearInterval(pollTimer)
  pollTimer = null
}

async function submitDataUpdate() {
  if (!canStartDataUpdate.value) return
  const start = compactDate(startDate.value)
  const end = compactDate(endDate.value)
  if (start > end) {
    pageError.value = '起始日期不能晚于截止日期。'
    return
  }
  submitting.value = true
  pageError.value = ''
  actionMessage.value = ''
  try {
    const response = await startDataUpdate({
      start_date: start,
      end_date: end,
      update_stock: updateStock.value,
      update_index: updateIndex.value,
      update_etf: updateEtf.value,
      rebuild_panel: rebuildPanel.value,
      dry_run: dryRun.value,
      max_workers: Number(maxWorkers.value),
      symbol_type: symbolType.value,
      codes: codes.value.trim(),
    }, adminToken.value)
    maintenance.value = response.progress || { running: true, task_kind: 'data_update' }
    actionMessage.value = '数据更新任务已提交。'
    ensurePolling()
  } catch (reason) {
    handleApiError(reason)
  } finally {
    submitting.value = false
  }
}

function openVersionDialog() {
  if (canStartVersionUpdate.value) versionDialogOpen.value = true
}

function closeVersionDialog() {
  if (!submitting.value) versionDialogOpen.value = false
}

async function confirmVersionUpdate() {
  if (!canStartVersionUpdate.value) return
  submitting.value = true
  pageError.value = ''
  actionMessage.value = ''
  try {
    const response = await startVersionUpdate(adminToken.value)
    maintenance.value = response.progress || { running: true, task_kind: 'version_update' }
    versionDialogOpen.value = false
    actionMessage.value = 'GitHub 更新任务已提交。'
    ensurePolling()
  } catch (reason) {
    versionDialogOpen.value = false
    handleApiError(reason)
  } finally {
    submitting.value = false
  }
}

function resultClass(result: MaintenanceTaskResult): string {
  if (result.status === 'completed') return 'is-ready'
  if (result.status === 'skipped') return 'is-warning'
  return 'is-danger'
}

function resultLabel(result: MaintenanceTaskResult): string {
  if (result.status === 'completed') return '完成'
  if (result.status === 'skipped') return '跳过'
  return '失败'
}

function resultOutput(result: MaintenanceTaskResult): string {
  return [result.stdout_tail, result.stderr_tail].map(value => String(value || '').trim()).filter(Boolean).join('\n')
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && versionDialogOpen.value) closeVersionDialog()
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
  void loadStatus()
})

onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <section class="maintenance-page">
    <header class="maintenance-header">
      <div>
        <p class="maintenance-kicker">SYSTEM / MAINTENANCE</p>
        <h1>系统维护</h1>
        <p>{{ taskStatusText }}</p>
      </div>
      <button class="maintenance-button is-secondary" type="button" :disabled="loading" @click="loadStatus">
        <RefreshCw :size="15" aria-hidden="true" />
        刷新状态
      </button>
    </header>

    <section v-if="authRequired" class="maintenance-auth" aria-labelledby="admin-auth-title">
      <KeyRound :size="20" aria-hidden="true" />
      <div>
        <h2 id="admin-auth-title">管理令牌</h2>
        <form @submit.prevent="connectWithToken">
          <input v-model="tokenInput" type="password" autocomplete="current-password" placeholder="WEB_ADMIN_TOKEN" />
          <button class="maintenance-button is-primary" type="submit">验证</button>
        </form>
      </div>
    </section>

    <template v-else>
      <section class="maintenance-summary" aria-label="维护状态概览">
        <div>
          <span>任务状态</span>
          <strong>{{ maintenance.running ? '运行中' : '空闲' }}</strong>
          <small>{{ maintenance.task_kind ? taskTitle : '暂无任务' }}</small>
        </div>
        <div>
          <span>当前版本</span>
          <strong class="is-mono">{{ shortRevision(repository?.revision) }}</strong>
          <small>{{ repository?.branch || '-' }}</small>
        </div>
        <div>
          <span>GitHub 目标</span>
          <strong>{{ repository?.target_ref || '-' }}</strong>
          <small>{{ repository?.remote_url || '-' }}</small>
        </div>
        <div>
          <span>版本状态</span>
          <strong :class="repositoryStateClass">{{ repositoryStateLabel }}</strong>
          <small>{{ repository?.subject || '-' }}</small>
        </div>
      </section>

      <div v-if="pageError" class="maintenance-feedback is-danger" role="alert">
        <XCircle :size="17" aria-hidden="true" />
        <span>{{ pageError }}</span>
      </div>
      <div v-else-if="actionMessage" class="maintenance-feedback is-success" aria-live="polite">
        <CheckCircle2 :size="17" aria-hidden="true" />
        <span>{{ actionMessage }}</span>
      </div>
      <div v-if="restartRequired" class="maintenance-feedback is-warning" aria-live="polite">
        <AlertTriangle :size="17" aria-hidden="true" />
        <span>新版本已写入，重启 Web 服务后生效。</span>
      </div>

      <div class="maintenance-grid">
        <section class="maintenance-panel" aria-labelledby="data-update-title">
          <header class="maintenance-panel-head">
            <div>
              <Database :size="19" aria-hidden="true" />
              <h2 id="data-update-title">更新数据</h2>
            </div>
            <span class="maintenance-badge" :class="maintenance.running && maintenance.task_kind === 'data_update' ? 'is-update' : 'is-ready'">
              {{ maintenance.running && maintenance.task_kind === 'data_update' ? '运行中' : '可用' }}
            </span>
          </header>

          <form class="maintenance-form" @submit.prevent="submitDataUpdate">
            <div class="maintenance-field-grid">
              <label class="maintenance-field">
                <span>起始日期</span>
                <input v-model="startDate" type="date" :disabled="maintenance.running" />
              </label>
              <label class="maintenance-field">
                <span>截止日期</span>
                <input v-model="endDate" type="date" :disabled="maintenance.running" />
              </label>
              <label class="maintenance-field">
                <span>并发数</span>
                <input v-model.number="maxWorkers" type="number" min="1" max="32" :disabled="maintenance.running" />
              </label>
              <label class="maintenance-field">
                <span>指定代码类型</span>
                <select v-model="symbolType" :disabled="maintenance.running">
                  <option value="stock">股票</option>
                  <option value="index">指数</option>
                  <option value="etf">ETF</option>
                </select>
              </label>
            </div>

            <div class="maintenance-options" aria-label="数据类型与选项">
              <label><input v-model="updateStock" type="checkbox" :disabled="maintenance.running || Boolean(codes.trim())" /> 股票</label>
              <label><input v-model="updateIndex" type="checkbox" :disabled="maintenance.running || Boolean(codes.trim())" /> 指数</label>
              <label><input v-model="updateEtf" type="checkbox" :disabled="maintenance.running || Boolean(codes.trim())" /> ETF</label>
              <label><input v-model="rebuildPanel" type="checkbox" :disabled="maintenance.running || dryRun" /> 重建 Panel</label>
              <label><input v-model="dryRun" type="checkbox" :disabled="maintenance.running" /> 试运行</label>
            </div>

            <label class="maintenance-field">
              <span>指定代码</span>
              <textarea v-model="codes" rows="3" :disabled="maintenance.running" placeholder="000001.SZ, 399006.SZ"></textarea>
            </label>

            <footer class="maintenance-panel-actions">
              <span>{{ codes.trim() ? `按 ${symbolType} 更新指定代码` : '按所选市场全量更新' }}</span>
              <button class="maintenance-button is-primary" type="submit" :disabled="!canStartDataUpdate">
                <RotateCw :size="15" aria-hidden="true" />
                {{ maintenance.running && maintenance.task_kind === 'data_update' ? '更新中' : '开始更新' }}
              </button>
            </footer>
          </form>
        </section>

        <section class="maintenance-panel" aria-labelledby="version-update-title">
          <header class="maintenance-panel-head">
            <div>
              <GitBranch :size="19" aria-hidden="true" />
              <h2 id="version-update-title">GitHub 版本</h2>
            </div>
            <span class="maintenance-badge" :class="repositoryStateClass">{{ repositoryStateLabel }}</span>
          </header>

          <div class="repository-details">
            <dl>
              <div><dt>当前分支</dt><dd>{{ repository?.branch || '-' }}</dd></div>
              <div><dt>当前提交</dt><dd class="is-mono">{{ shortRevision(repository?.revision) }}</dd></div>
              <div><dt>更新目标</dt><dd>{{ repository?.target_ref || '-' }}</dd></div>
              <div><dt>远端提交</dt><dd class="is-mono">{{ shortRevision(repository?.target_revision) }}</dd></div>
              <div><dt>领先 / 落后</dt><dd>{{ repository?.ahead ?? '-' }} / {{ repository?.behind ?? '-' }}</dd></div>
              <div><dt>工作区</dt><dd>{{ repository?.dirty ? `${repository.dirty_count || 0} 项改动` : '干净' }}</dd></div>
            </dl>

            <div class="repository-guard">
              <ShieldCheck :size="18" aria-hidden="true" />
              <span>{{ repository?.error || '快进更新检查已通过' }}</span>
            </div>
          </div>

          <footer class="maintenance-panel-actions version-actions">
            <span>{{ repository?.update_available ? '检测到新提交' : '执行时将重新检查远端' }}</span>
            <button class="maintenance-button is-primary" type="button" :disabled="!canStartVersionUpdate" @click="openVersionDialog">
              <Download :size="15" aria-hidden="true" />
              检查并更新
            </button>
          </footer>
        </section>
      </div>

      <StrategyManagementPanel
        :token="adminToken"
        :busy="maintenance.running || submitting"
        @feedback="showChildFeedback"
        @error="showChildError"
      />

      <section v-if="maintenance.running || taskResults.length" class="maintenance-results" aria-labelledby="maintenance-results-title">
        <header>
          <div>
            <ServerCog :size="19" aria-hidden="true" />
            <h2 id="maintenance-results-title">{{ taskTitle }}记录</h2>
          </div>
          <span>{{ taskStatusText }}</span>
        </header>
        <div v-if="maintenance.running" class="maintenance-progress" role="progressbar" aria-label="维护任务进度" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="Math.round(taskPercent)">
          <span :style="{ width: `${taskPercent}%` }"></span>
        </div>
        <div class="maintenance-result-list">
          <article v-for="(result, index) in taskResults" :key="`${result.task}-${index}`" class="maintenance-result">
            <div class="maintenance-result-head">
              <div>
                <Terminal :size="16" aria-hidden="true" />
                <strong>{{ result.label || result.task }}</strong>
                <small>{{ formatDuration(result.duration_sec) || '少于 1 秒' }}</small>
              </div>
              <span class="maintenance-badge" :class="resultClass(result)">{{ resultLabel(result) }}</span>
            </div>
            <pre v-if="resultOutput(result)">{{ resultOutput(result) }}</pre>
          </article>
        </div>
      </section>
    </template>

    <Teleport to="body">
      <div v-if="versionDialogOpen" class="maintenance-dialog-backdrop" @mousedown.self="closeVersionDialog">
        <section class="maintenance-dialog" role="dialog" aria-modal="true" aria-labelledby="version-dialog-title">
          <header>
            <div>
              <p>FAST-FORWARD ONLY</p>
              <h2 id="version-dialog-title">从 GitHub 更新</h2>
            </div>
            <button type="button" title="关闭" aria-label="关闭" :disabled="submitting" @click="closeVersionDialog">
              <X :size="18" aria-hidden="true" />
            </button>
          </header>
          <dl>
            <div><dt>当前</dt><dd class="is-mono">{{ shortRevision(repository?.revision) }}</dd></div>
            <div><dt>目标</dt><dd>{{ repository?.target_ref || '-' }}</dd></div>
          </dl>
          <footer>
            <button class="maintenance-button is-secondary" type="button" :disabled="submitting" @click="closeVersionDialog">取消</button>
            <button class="maintenance-button is-primary" type="button" :disabled="submitting" @click="confirmVersionUpdate">
              <Download :size="15" aria-hidden="true" />
              {{ submitting ? '提交中' : '确认更新' }}
            </button>
          </footer>
        </section>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.maintenance-page {
  display: grid;
  gap: 20px;
  color: var(--ink);
}

.maintenance-header,
.maintenance-panel-head,
.maintenance-panel-actions,
.maintenance-results > header,
.maintenance-result-head,
.maintenance-dialog > header,
.maintenance-dialog > footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.maintenance-header {
  align-items: flex-end;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line);
}

.maintenance-kicker,
.maintenance-dialog header p {
  margin: 0 0 5px;
  color: var(--accent);
  font-size: 11px;
  font-weight: 750;
}

.maintenance-header h1 {
  margin: 0;
  font-size: 30px;
  font-weight: 740;
  letter-spacing: 0;
}

.maintenance-header p:not(.maintenance-kicker) {
  margin: 5px 0 0;
  color: var(--ink-soft);
  font-size: 13px;
}

.maintenance-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.68);
}

.maintenance-summary > div {
  min-width: 0;
  padding: 14px 18px;
}

.maintenance-summary > div + div {
  border-left: 1px solid var(--line);
}

.maintenance-summary span,
.maintenance-summary small,
.maintenance-field > span,
.maintenance-panel-actions > span,
.maintenance-results > header > span,
.maintenance-result small {
  color: var(--ink-soft);
  font-size: 12px;
}

.maintenance-summary strong,
.maintenance-summary small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.maintenance-summary strong {
  margin-top: 4px;
  font-size: 17px;
  font-weight: 720;
}

.maintenance-summary small {
  margin-top: 2px;
}

.maintenance-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}

.maintenance-panel,
.maintenance-results,
.maintenance-auth {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
}

.maintenance-panel-head,
.maintenance-results > header {
  min-height: 54px;
  padding: 13px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-muted);
}

.maintenance-panel-head > div,
.maintenance-results > header > div,
.maintenance-result-head > div,
.maintenance-feedback,
.maintenance-auth {
  display: flex;
  align-items: center;
  gap: 8px;
}

.maintenance-panel h2,
.maintenance-results h2,
.maintenance-auth h2,
.maintenance-dialog h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 730;
  letter-spacing: 0;
}

.maintenance-form,
.repository-details {
  display: grid;
  gap: 15px;
  padding: 16px;
}

.maintenance-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.maintenance-field {
  display: grid;
  min-width: 0;
  gap: 6px;
}

.maintenance-field > span {
  font-weight: 680;
}

.maintenance-field input,
.maintenance-field select,
.maintenance-field textarea,
.maintenance-auth input {
  width: 100%;
  min-width: 0;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-size: 13px;
  letter-spacing: 0;
}

.maintenance-field input,
.maintenance-field select,
.maintenance-auth input {
  height: 38px;
  padding: 0 10px;
}

.maintenance-field textarea {
  resize: vertical;
  padding: 9px 10px;
}

.maintenance-field input:focus,
.maintenance-field select:focus,
.maintenance-field textarea:focus,
.maintenance-auth input:focus {
  border-color: var(--accent);
  outline: 3px solid var(--accent-soft);
}

.maintenance-options {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.maintenance-options label {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  color: var(--ink-soft);
  font-size: 12px;
  font-weight: 650;
}

.maintenance-options input {
  width: 15px;
  height: 15px;
  flex: 0 0 auto;
}

.maintenance-panel-actions {
  min-height: 56px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  background: var(--surface-muted);
}

.maintenance-form .maintenance-panel-actions {
  margin: 1px -16px -16px;
}

.maintenance-button {
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
  letter-spacing: 0;
  cursor: pointer;
}

.maintenance-button.is-primary {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #ffffff;
}

.maintenance-button.is-secondary {
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink);
}

.maintenance-button:hover:not(:disabled) {
  filter: brightness(0.94);
}

.maintenance-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.repository-details dl,
.maintenance-dialog dl {
  display: grid;
  gap: 0;
  margin: 0;
}

.repository-details dl > div,
.maintenance-dialog dl > div {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 14px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}

.repository-details dt,
.maintenance-dialog dt {
  color: var(--ink-soft);
  font-size: 12px;
  font-weight: 650;
}

.repository-details dd,
.maintenance-dialog dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 13px;
  font-weight: 680;
  text-align: right;
}

.repository-guard {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  border-left: 3px solid var(--accent);
  background: var(--accent-soft);
  color: var(--ink-soft);
  padding: 10px 12px;
  font-size: 12px;
  font-weight: 650;
}

.repository-guard svg {
  flex: 0 0 auto;
  color: var(--accent);
}

.maintenance-badge {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 720;
  white-space: nowrap;
}

.is-ready {
  color: var(--positive) !important;
}

.maintenance-badge.is-ready {
  background: #eaf7f0;
}

.is-update {
  color: var(--accent) !important;
}

.maintenance-badge.is-update {
  background: var(--accent-soft);
}

.is-warning {
  color: var(--warning) !important;
}

.maintenance-badge.is-warning {
  background: #fff7e8;
}

.is-danger {
  color: var(--negative) !important;
}

.maintenance-badge.is-danger {
  background: #fff0ee;
}

.is-mono {
  font-family: Consolas, "Cascadia Mono", monospace;
}

.maintenance-feedback {
  border: 1px solid;
  border-radius: 4px;
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 650;
}

.maintenance-feedback.is-success {
  border-color: #b7dfca;
  background: #eef9f3;
}

.maintenance-feedback.is-warning {
  border-color: #efd39e;
  background: #fff8eb;
}

.maintenance-feedback.is-danger {
  border-color: #efbbb5;
  background: #fff2f0;
}

.maintenance-progress {
  height: 7px;
  overflow: hidden;
  background: var(--line);
}

.maintenance-progress > span {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 180ms ease;
}

.maintenance-result-list {
  display: grid;
}

.maintenance-result {
  min-width: 0;
  padding: 13px 16px;
}

.maintenance-result + .maintenance-result {
  border-top: 1px solid var(--line);
}

.maintenance-result-head > div {
  min-width: 0;
}

.maintenance-result pre {
  max-height: 220px;
  margin: 11px 0 0;
  overflow: auto;
  border-radius: 4px;
  background: #151922;
  color: #e5e9f0;
  padding: 10px 12px;
  font: 12px/1.55 Consolas, "Cascadia Mono", monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.maintenance-auth {
  padding: 18px;
  color: var(--accent);
}

.maintenance-auth > div {
  display: grid;
  flex: 1;
  gap: 10px;
}

.maintenance-auth form {
  display: flex;
  gap: 8px;
}

.maintenance-auth input {
  max-width: 420px;
}

.maintenance-dialog-backdrop {
  position: fixed;
  z-index: 100;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgba(17, 20, 27, 0.46);
  padding: 20px;
}

.maintenance-dialog {
  width: min(460px, 100%);
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--surface);
  box-shadow: 0 24px 70px rgba(17, 20, 27, 0.22);
}

.maintenance-dialog > header,
.maintenance-dialog > footer,
.maintenance-dialog > dl {
  padding: 15px 17px;
}

.maintenance-dialog > header,
.maintenance-dialog > footer {
  border-bottom: 1px solid var(--line);
}

.maintenance-dialog > footer {
  justify-content: flex-end;
  border-top: 1px solid var(--line);
  border-bottom: 0;
  background: var(--surface-muted);
}

.maintenance-dialog header button {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink-soft);
  cursor: pointer;
}

@media (max-width: 920px) {
  .maintenance-summary,
  .maintenance-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .maintenance-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .maintenance-header,
  .maintenance-panel-actions,
  .maintenance-result-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .maintenance-summary,
  .maintenance-field-grid,
  .maintenance-options {
    grid-template-columns: 1fr;
  }

  .maintenance-summary > div + div {
    border-top: 1px solid var(--line);
    border-left: 0;
  }

  .maintenance-panel-actions .maintenance-button,
  .maintenance-header .maintenance-button {
    width: 100%;
  }

  .maintenance-auth,
  .maintenance-auth form {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
