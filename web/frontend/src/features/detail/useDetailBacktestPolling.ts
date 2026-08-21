import { onBeforeUnmount, ref, toValue, type MaybeRefOrGetter } from 'vue'
import { fetchBacktestStatus, startBacktestRun } from '../../api/backtest'
import type { BacktestProgress, BacktestRunResult } from '../../types/library'

interface UseDetailBacktestPollingOptions {
  name: MaybeRefOrGetter<string>
  loadPage: () => Promise<void>
  hasError: () => boolean
}

export function useDetailBacktestPolling(options: UseDetailBacktestPollingOptions) {
  const backtestActionPending = ref(false)
  const backtestActionMessage = ref('')

  let pollTimer: number | null = null
  let statusRequestPending = false
  let trackingActive = false
  let trackedTaskId = ''
  const handledResultKeys = new Set<string>()
  const loadedResultKeys = new Set<string>()

  function isFailedResult(result: BacktestRunResult): boolean {
    const status = String(result.status || '').trim().toLowerCase()
    return status === 'failed'
      || status === 'config_failed'
      || status.includes('failed')
      || Boolean(String(result.error || '').trim())
  }

  function isSuccessfulResult(result: BacktestRunResult): boolean {
    const status = String(result.status || '').trim().toLowerCase()
    return status === 'completed' || status === 'cached' || status === 'no_signal'
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

  function beginTracking(progress?: BacktestProgress | null) {
    trackingActive = true
    trackedTaskId = String(progress?.task_id || '').trim()
    handledResultKeys.clear()
    loadedResultKeys.clear()
  }

  function collectNewResults(progress: BacktestProgress): Array<{ result: BacktestRunResult; key: string }> {
    const taskId = String(progress.task_id || '').trim()
    if (taskId && trackedTaskId && taskId !== trackedTaskId) {
      trackedTaskId = taskId
      handledResultKeys.clear()
      loadedResultKeys.clear()
    } else if (taskId && !trackedTaskId) {
      trackedTaskId = taskId
    }

    const results = Array.isArray(progress.results) ? progress.results : []
    const fresh: Array<{ result: BacktestRunResult; key: string }> = []
    results.forEach((result, index) => {
      const key = resultKey(result, index)
      if (handledResultKeys.has(key)) return
      handledResultKeys.add(key)
      fresh.push({ result, key })
    })
    return fresh
  }

  function errorMessage(result: BacktestRunResult): string {
    const errorText = String(result.error || '').trim()
    if (errorText) return errorText
    return String(result.status || '').trim().toLowerCase() === 'config_failed'
      ? '策略配置校验失败。'
      : '回测执行失败，任务未生成可用结果。'
  }

  function setResultMessage(result: BacktestRunResult) {
    if (isFailedResult(result)) {
      backtestActionMessage.value = `回测失败：${errorMessage(result)}`
      return
    }

    if (!isSuccessfulResult(result)) {
      const status = String(result.status || '').trim() || '未知状态'
      backtestActionMessage.value = `回测任务已结束，但未确认成功：${status}`
      return
    }

    const noSignal = String(result.status || '').trim().toLowerCase() === 'no_signal'
    backtestActionMessage.value = noSignal
      ? (options.hasError()
        ? '回测完成，但未产生交易信号，因此没有可展示的新结果。'
        : '回测完成，当前区间未产生交易信号。')
      : (options.hasError()
        ? '回测已完成，但最新结果加载失败，请使用“重新加载”重试。'
        : '已加载最新回测结果。')
  }

  async function refreshResult(result: BacktestRunResult, key: string) {
    if (!isFailedResult(result) && !isSuccessfulResult(result)) return
    backtestActionMessage.value = '回测已完成，正在刷新结果。'
    await options.loadPage()
    if (!options.hasError()) loadedResultKeys.add(key)
    setResultMessage(result)
  }

  async function finish(progress: BacktestProgress) {
    stopPolling()
    trackingActive = false
    backtestActionPending.value = false

    if (progress.cancelled) {
      const reason = String((progress as BacktestProgress & { error?: string }).error || '').trim()
      backtestActionMessage.value = reason && reason !== 'cancelled'
        ? `回测已取消：${reason}`
        : '回测已取消，当前页面继续保留原结果。'
      return
    }

    const results = Array.isArray(progress.results) ? progress.results : []
    const currentName = toValue(options.name)
    const currentIndex = results.findIndex(result => String(result.name || '').trim() === currentName)
    const currentResult = currentIndex >= 0 ? results[currentIndex] : undefined
    if (!currentResult) {
      backtestActionMessage.value = results.length
        ? '运行中的回测任务已结束，但该任务不包含当前策略。'
        : '回测任务已结束，但未返回当前策略的执行结果。'
      return
    }

    const key = resultKey(currentResult, currentIndex)
    if (!loadedResultKeys.has(key)) {
      await refreshResult(currentResult, key)
      return
    }
    setResultMessage(currentResult)
  }

  async function pollStatus() {
    if (statusRequestPending) return
    statusRequestPending = true
    try {
      const progress = await fetchBacktestStatus()
      if (progress.running) {
        if (!trackingActive) beginTracking(progress)
        const freshResults = collectNewResults(progress)
        const completed = Number(progress.completed || 0)
        const total = Number(progress.total || 0)
        const current = String(progress.current || '').trim()
        backtestActionPending.value = true
        backtestActionMessage.value = current
          ? `回测中 ${completed}/${total}，当前 ${current}`
          : `回测中 ${completed}/${total}`
        const currentName = toValue(options.name)
        const currentResult = freshResults.find(({ result }) => String(result.name || '').trim() === currentName)
        if (currentResult) await refreshResult(currentResult.result, currentResult.key)
        return
      }
      if (!trackingActive) return
      collectNewResults(progress)
      await finish(progress)
    } catch {
      backtestActionMessage.value = '回测状态同步失败，正在重试。'
    } finally {
      statusRequestPending = false
    }
  }

  function ensurePolling() {
    if (pollTimer != null) return
    pollTimer = window.setInterval(() => {
      void pollStatus()
    }, 2000)
  }

  function stopPolling() {
    if (pollTimer == null) return
    window.clearInterval(pollTimer)
    pollTimer = null
  }

  function reset() {
    stopPolling()
    trackingActive = false
    trackedTaskId = ''
    handledResultKeys.clear()
    loadedResultKeys.clear()
    backtestActionPending.value = false
    backtestActionMessage.value = ''
  }

  async function rerunCurrentStrategy() {
    if (backtestActionPending.value) return
    backtestActionPending.value = true
    backtestActionMessage.value = ''
    let accepted = false
    try {
      const response = await startBacktestRun({ names: [toValue(options.name)] })
      if (response.ok) {
        accepted = true
        beginTracking(response.data?.progress)
        backtestActionMessage.value = '回测任务已提交，正在等待执行结果。'
        ensurePolling()
        await pollStatus()
        return
      }
      if (response.status === 409 && response.data?.progress) {
        accepted = true
        const progress = response.data.progress
        beginTracking(progress)
        const current = String(progress.current || '').trim()
        backtestActionMessage.value = current
          ? `已有回测任务在运行，正在跟踪：${current}`
          : '已有回测任务在运行，正在跟踪任务进度。'
        ensurePolling()
        await pollStatus()
        return
      }
      if (response.status === 409) {
        backtestActionMessage.value = '已有回测任务在运行，但未返回可跟踪的任务进度。'
        return
      }
      backtestActionMessage.value = response.data?.error
        ? `启动失败：${response.data.error}`
        : '启动回测失败，请稍后重试。'
    } catch (error) {
      backtestActionMessage.value = error instanceof Error
        ? `启动失败：${error.message}`
        : '启动回测失败，请稍后重试。'
    } finally {
      if (!accepted) backtestActionPending.value = false
    }
  }

  onBeforeUnmount(stopPolling)

  return {
    backtestActionPending,
    backtestActionMessage,
    rerunCurrentStrategy,
    reset,
  }
}
