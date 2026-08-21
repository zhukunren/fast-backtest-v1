import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AdminPage from './AdminPage.vue'

const adminApi = vi.hoisted(() => {
  class AdminApiError extends Error {
    readonly status: number
    readonly data: Record<string, unknown> | null

    constructor(status: number, message: string, data: Record<string, unknown> | null = null) {
      super(message)
      this.status = status
      this.data = data
    }
  }

  return {
    AdminApiError,
    fetchAdminStatus: vi.fn(),
    fetchMaintenanceStatus: vi.fn(),
    startDataUpdate: vi.fn(),
    startVersionUpdate: vi.fn(),
  }
})

const authApi = vi.hoisted(() => ({
  readStoredAdminToken: vi.fn(() => ''),
  storeAdminToken: vi.fn(),
}))

vi.mock('../../api/admin', () => adminApi)
vi.mock('../../api/admin-auth', () => authApi)

const readyStatus = {
  repository: {
    available: true,
    can_update: true,
    branch: 'main',
    revision: '1234567890abcdef',
    subject: 'release candidate',
    remote_url: 'https://github.com/example/project.git',
    target_ref: 'origin/main',
    target_revision: 'fedcba0987654321',
    ahead: 0,
    behind: 2,
    update_available: true,
    diverged: false,
    dirty: false,
    dirty_count: 0,
  },
  maintenance: { running: false, results: [] },
}

const StrategyPanelStub = {
  name: 'StrategyManagementPanel',
  props: ['token', 'busy'],
  emits: ['feedback', 'error'],
  template: `
    <div data-strategy-panel :data-token="token" :data-busy="String(busy)">
      <button data-child-feedback @click="$emit('feedback', '策略已安装')">feedback</button>
      <button data-child-error @click="$emit('error', 'cross_site_request_rejected')">error</button>
    </div>
  `,
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function mountPage() {
  return mount(AdminPage, {
    attachTo: document.body,
    global: {
      stubs: {
        teleport: true,
        StrategyManagementPanel: StrategyPanelStub,
      },
    },
  })
}

function buttonByText(wrapper: ReturnType<typeof mountPage>, text: string) {
  const button = wrapper.findAll('button').find(candidate => candidate.text().includes(text))
  if (!button) throw new Error(`button not found: ${text}`)
  return button
}

beforeEach(() => {
  vi.clearAllMocks()
  authApi.readStoredAdminToken.mockReturnValue('stored-token')
  adminApi.fetchAdminStatus.mockResolvedValue(readyStatus)
  adminApi.fetchMaintenanceStatus.mockResolvedValue({ running: false, results: [] })
  adminApi.startDataUpdate.mockResolvedValue({
    status: 'accepted',
    progress: { running: true, task_kind: 'data_update', total: 3, completed: 0 },
  })
  adminApi.startVersionUpdate.mockResolvedValue({
    status: 'accepted',
    progress: { running: true, task_kind: 'version_update', total: 1, completed: 0 },
  })
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('AdminPage', () => {
  it('requests a token after 401 and reconnects with a trimmed persisted value', async () => {
    adminApi.fetchAdminStatus
      .mockRejectedValueOnce(new adminApi.AdminApiError(401, 'admin_auth_required'))
      .mockResolvedValueOnce(readyStatus)
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('#admin-auth-title').text()).toBe('管理令牌')
    expect(wrapper.find('.maintenance-summary').exists()).toBe(false)
    await wrapper.get('input[type="password"]').setValue('  new-secret  ')
    await wrapper.get('.maintenance-auth form').trigger('submit')
    await flushPromises()

    expect(authApi.storeAdminToken).toHaveBeenCalledWith('new-secret')
    expect(adminApi.fetchAdminStatus).toHaveBeenLastCalledWith('new-secret')
    expect(wrapper.find('.maintenance-auth').exists()).toBe(false)
    expect(wrapper.get('.maintenance-summary').text()).toContain('可更新 2 个提交')
    expect(wrapper.get('[data-strategy-panel]').attributes('data-token')).toBe('new-secret')
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
  })

  it('renders repository state and relays child feedback and security errors', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(adminApi.fetchAdminStatus).toHaveBeenCalledWith('stored-token')
    expect(wrapper.text()).toContain('1234567890ab')
    expect(wrapper.text()).toContain('origin/main')
    expect(wrapper.text()).toContain('0 / 2')
    expect(wrapper.get('[data-strategy-panel]').attributes('data-busy')).toBe('false')

    await wrapper.get('[data-child-feedback]').trigger('click')
    expect(wrapper.get('.maintenance-feedback.is-success').text()).toContain('策略已安装')
    await wrapper.get('[data-child-error]').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('请求来源未获授权')
    expect(wrapper.find('.maintenance-feedback.is-success').exists()).toBe(false)
  })

  it('validates dates and submits the complete data-update contract', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const dates = wrapper.findAll('.maintenance-form input[type="date"]')

    await dates[0].setValue('2025-12-31')
    await dates[1].setValue('2025-01-01')
    await wrapper.get('.maintenance-form').trigger('submit')
    expect(adminApi.startDataUpdate).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('起始日期不能晚于截止日期')

    await dates[0].setValue('2025-01-02')
    await dates[1].setValue('2025-06-30')
    await wrapper.get('.maintenance-form input[type="number"]').setValue('6')
    await wrapper.get('.maintenance-form select').setValue('etf')
    const checkboxes = wrapper.findAll('.maintenance-options input[type="checkbox"]')
    await checkboxes[2].setValue(true)
    await checkboxes[4].setValue(true)
    await wrapper.get('.maintenance-form textarea').setValue('510300.SH, 159915.SZ')
    await wrapper.get('.maintenance-form').trigger('submit')
    await flushPromises()

    expect(adminApi.startDataUpdate).toHaveBeenCalledWith({
      start_date: '20250102',
      end_date: '20250630',
      update_stock: true,
      update_index: true,
      update_etf: true,
      rebuild_panel: true,
      dry_run: true,
      max_workers: 6,
      symbol_type: 'etf',
      codes: '510300.SH, 159915.SZ',
    }, 'stored-token')
    expect(wrapper.get('.maintenance-feedback.is-success').text()).toContain('数据更新任务已提交')
    expect(wrapper.get('[data-strategy-panel]').attributes('data-busy')).toBe('true')
  })

  it('polls one request at a time and refreshes the completed task with result diagnostics', async () => {
    vi.useFakeTimers()
    const pendingPoll = deferred<{
      running: boolean
      task_kind: string
      total: number
      completed: number
      results: Array<Record<string, unknown>>
    }>()
    adminApi.fetchAdminStatus
      .mockResolvedValueOnce({
        ...readyStatus,
        maintenance: {
          running: true,
          task_kind: 'data_update',
          total: 4,
          completed: 1,
          current: '更新股票日线',
          elapsed_sec: 65,
          results: [],
        },
      })
      .mockResolvedValueOnce({
        ...readyStatus,
        maintenance: {
          running: false,
          task_kind: 'data_update',
          total: 4,
          completed: 4,
          results: [
            { task: 'stock', label: '股票', status: 'completed', duration_sec: 12, stdout_tail: 'done' },
            { task: 'index', label: '指数', status: 'skipped', duration_sec: 0 },
            { task: 'panel', label: 'Panel', status: 'failed', duration_sec: 2, stderr_tail: 'build failed', restart_required: true },
          ],
        },
      })
    adminApi.fetchMaintenanceStatus.mockReturnValueOnce(pendingPoll.promise)
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('25')
    expect(wrapper.text()).toContain('更新股票日线 · 1/4 · 1 分 5 秒')
    await vi.advanceTimersByTimeAsync(4000)
    expect(adminApi.fetchMaintenanceStatus).toHaveBeenCalledTimes(1)

    pendingPoll.resolve({
      running: false,
      task_kind: 'data_update',
      total: 4,
      completed: 4,
      results: [{ task: 'panel', status: 'failed' }],
    })
    await flushPromises()

    expect(adminApi.fetchAdminStatus).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.maintenance-feedback.is-success').text()).toContain('完成，但存在失败项')
    expect(wrapper.findAll('.maintenance-result')).toHaveLength(3)
    expect(wrapper.findAll('.maintenance-result .maintenance-badge').map(item => item.text())).toEqual(['完成', '跳过', '失败'])
    expect(wrapper.text()).toContain('done')
    expect(wrapper.text()).toContain('build failed')
    expect(wrapper.text()).toContain('新版本已写入，重启 Web 服务后生效')
  })

  it('supports cancel, Escape, successful version update, and mapped failures', async () => {
    vi.useFakeTimers()
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, '检查并更新').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('从 GitHub 更新')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

    await buttonByText(wrapper, '检查并更新').trigger('click')
    await buttonByText(wrapper, '确认更新').trigger('click')
    await flushPromises()
    expect(adminApi.startVersionUpdate).toHaveBeenCalledWith('stored-token')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.get('.maintenance-feedback.is-success').text()).toContain('GitHub 更新任务已提交')

    adminApi.fetchAdminStatus.mockResolvedValueOnce(readyStatus)
    await buttonByText(wrapper, '刷新状态').trigger('click')
    await flushPromises()
    adminApi.startVersionUpdate.mockRejectedValueOnce(new Error('maintenance_running'))
    await buttonByText(wrapper, '检查并更新').trigger('click')
    await buttonByText(wrapper, '确认更新').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('已有维护任务正在运行')
  })

  it.each([
    [{ available: false, can_update: false }, '不可用', 'is-danger'],
    [{ available: true, can_update: false, dirty: true }, '有本地改动', 'is-warning'],
    [{ available: true, can_update: false, diverged: true }, '历史已分叉', 'is-danger'],
    [{ available: true, can_update: false, ahead: 3 }, '本地领先 3 个提交', 'is-ready'],
    [{ available: true, can_update: false }, '已同步', 'is-ready'],
  ])('labels repository guard states %#', async (repository, label, expectedClass) => {
    adminApi.fetchAdminStatus.mockResolvedValueOnce({
      repository,
      maintenance: { running: false, results: [] },
    })
    const wrapper = mountPage()
    await flushPromises()

    const badge = wrapper.findAll('.maintenance-badge').find(item => item.text() === label)
    expect(badge?.classes()).toContain(expectedClass)
    expect(buttonByText(wrapper, '检查并更新').attributes('disabled')).toBeDefined()
  })

  it('shows non-auth request failures and removes polling and keyboard listeners on unmount', async () => {
    vi.useFakeTimers()
    const clearInterval = vi.spyOn(window, 'clearInterval')
    const removeEventListener = vi.spyOn(window, 'removeEventListener')
    adminApi.fetchAdminStatus.mockResolvedValueOnce({
      ...readyStatus,
      maintenance: { running: true, task_kind: 'version_update', results: [] },
    })
    const wrapper = mountPage()
    await flushPromises()

    adminApi.fetchMaintenanceStatus.mockRejectedValueOnce(new Error('sensitive_local_only'))
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('当前敏感接口仅允许本机访问')

    wrapper.unmount()
    expect(clearInterval).toHaveBeenCalled()
    expect(removeEventListener).toHaveBeenCalledWith('keydown', expect.any(Function))
  })
})
