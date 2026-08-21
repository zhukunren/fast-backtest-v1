import { computed, onBeforeUnmount, ref } from 'vue'
import { fetchBacktestStatus, startBacktestRun, type BacktestRunPayload } from '../../api/backtest'
import type { BacktestProgress, BacktestRunResult } from '../../types/library'

interface UseCatalogBacktestPollingOptions {
  refreshCatalog: () => Promise<boolean>
  resolveStrategyTitle: (name: string) => string
}

interface SubmitBacktestResult {
  accepted: boolean
  error?: string
}

function normalizeMessage(value: unknown): string {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function truncateMessage(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value
  return `${value.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`
}

function isFailedResult(result: BacktestRunResult): boolean {
  const status = String(result.status || '').trim().toLowerCase()
  return status.includes('failed') || status === 'error' || Boolean(normalizeMessage(result.error))
}

export function useCatalogBacktestPolling(options: UseCatalogBacktestPollingOptions) {
  const actionPending = ref(false)
  const progress = ref<BacktestProgress>({ running: false })
  const message = ref('')
  const messageTitle = ref('')

  let pollTimer: number | null = null
  let statusRequestPending = false
  let trackingActive = false
  let trackedTaskId = ''
  const handledResultKeys = new Set<string>()

  const busy = computed(() => progress.value.running || actionPending.value)
  const percent = computed(() => {
    const total = Number(progress.value.total || 0)
    const completed = Number(progress.value.completed || 0)
    if (!total) return 0
    return Math.min(100, Math.max(0, (completed / total) * 100))
  })
  const statusText = computed(() => {
    if (!progress.value.running) return message.value
    const total = Number(progress.value.total || 0)
    const completed = Number(progress.value.completed || 0)
    const current = String(progress.value.current || '').trim()
    const elapsed = Number(progress.value.elapsed_sec || 0)
    const parts = [`${completed}/${total}`]
    if (current) parts.push(options.resolveStrategyTitle(current) || current)
    if (elapsed > 0) parts.push(`${elapsed.toFixed(0)} 秒`)
    return parts.join(' · ')
  })
  const statusTitle = computed(() => progress.value.running
    ? statusText.value
    : messageTitle.value || statusText.value)

  function setMessage(text: string, title = text) {
    message.value = text
    messageTitle.value = title
  }

  function completionMessage(value: BacktestProgress): { text: string; title: string } {
    if (value.cancelled) {
      const completed = Number(value.completed || 0)
      const total = Number(value.total || 0)
      const text = total > 0
        ? `回测已取消，已完成 ${completed}/${total} 个策略。`
        : '回测已取消。'
      return { text, title: text }
    }

    const failedResults = (value.results ?? []).filter(isFailedResult)
    if (!failedResults.length) {
      const text = '回测完成，结果已更新。'
      return { text, title: text }
    }

    const firstFailure = failedResults.find(result => normalizeMessage(result.error)) || failedResults[0]
    const resultName = normalizeMessage(firstFailure?.name || firstFailure?.module)
    const title = options.resolveStrategyTitle(resultName)
    const strategyLabel = title && resultName && title !== resultName
      ? `${title}（${resultName}）`
      : title || resultName || '未知策略'
    const reason = normalizeMessage(firstFailure?.error) || '未返回错误详情'
    const prefix = `回测完成，${failedResults.length} 个策略失败：`
    return {
      text: `${prefix}${truncateMessage(strategyLabel, 36)}：${truncateMessage(reason, 88)}`,
      title: `${prefix}${strategyLabel}：${reason}`,
    }
  }

  function resultKey(result: BacktestRunResult, index: number): string {
    return [
      index,
      String(result.name || result.module || '').trim(),
      String(result.status || '').trim().toLowerCase(),
      String(result.duration_sec ?? ''),
      String(result.error || '').trim(),
    ].join('|')
  }

  function collectNewResults(nextProgress: BacktestProgress): BacktestRunResult[] {
    const taskId = String(nextProgress.task_id || '').trim()
    if (taskId && trackedTaskId && taskId !== trackedTaskId) {
      trackedTaskId = taskId
      handledResultKeys.clear()
    } else if (taskId && !trackedTaskId) {
      trackedTaskId = taskId
    }

    const fresh: BacktestRunResult[] = []
    for (const [index, result] of (nextProgress.results || []).entries()) {
      const key = resultKey(result, index)
      if (handledResultKeys.has(key)) continue
      handledResultKeys.add(key)
      fresh.push(result)
    }
    return fresh
  }

  function beginTracking(initial?: BacktestProgress | null) {
    trackingActive = true
    trackedTaskId = String(initial?.task_id || '').trim()
    handledResultKeys.clear()
    if (initial) progress.value = initial
  }

  async function syncStatus() {
    if (statusRequestPending) return
    statusRequestPending = true
    const wasRunning = progress.value.running
    try {
      const nextProgress = await fetchBacktestStatus()
      const shouldTrackResults = Boolean(nextProgress.running || wasRunning || trackingActive)
      const freshResults = shouldTrackResults ? collectNewResults(nextProgress) : []
      progress.value = nextProgress
      if (nextProgress.running) {
        trackingActive = true
        ensurePolling()
        // Keep polling lightweight while workers are publishing generations;
        // one summary refresh after the task settles is enough and avoids a
        // full catalog scan for every completed strategy.
        return
      }
      if (!trackingActive && !wasRunning) return

      trackingActive = false
      stopPolling()
      const completed = completionMessage(nextProgress)
      setMessage(completed.text, completed.title)
      if (freshResults.length || wasRunning) await options.refreshCatalog()
    } catch {
      if (trackingActive || wasRunning) setMessage('回测状态同步失败，正在重试。')
    } finally {
      statusRequestPending = false
    }
  }

  function ensurePolling() {
    if (pollTimer != null) return
    pollTimer = window.setInterval(() => void syncStatus(), 2000)
  }

  function stopPolling() {
    if (pollTimer == null) return
    window.clearInterval(pollTimer)
    pollTimer = null
  }

  async function submit(payload: BacktestRunPayload): Promise<SubmitBacktestResult> {
    if (busy.value) return { accepted: false, error: '已有回测任务正在运行。' }
    actionPending.value = true
    try {
      const response = await startBacktestRun(payload)
      if (response.ok) {
        const initial = response.data?.progress || {
          running: true,
          total: payload.names.length,
          completed: 0,
        }
        beginTracking(initial)
        setMessage(`已提交 ${payload.names.length} 个策略。`)
        ensurePolling()
        await syncStatus()
        return { accepted: true }
      }
      if (response.status === 409 && response.data?.progress) {
        beginTracking(response.data.progress)
        ensurePolling()
        await syncStatus()
        return { accepted: true }
      }
      return {
        accepted: false,
        error: response.data?.error || '回测启动失败，请稍后重试。',
      }
    } catch (reason) {
      return {
        accepted: false,
        error: reason instanceof Error ? reason.message : '回测启动失败，请稍后重试。',
      }
    } finally {
      actionPending.value = false
    }
  }

  onBeforeUnmount(stopPolling)

  return {
    actionPending,
    progress,
    message,
    messageTitle,
    busy,
    percent,
    statusText,
    statusTitle,
    submit,
    syncStatus,
  }
}
