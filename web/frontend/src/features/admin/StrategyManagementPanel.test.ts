import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import type { LibraryCatalogResponse } from '../../types/library'
import StrategyManagementPanel from './StrategyManagementPanel.vue'

const adminApi = vi.hoisted(() => ({
  deleteStrategy: vi.fn(),
  renameStrategy: vi.fn(),
  uploadStrategy: vi.fn(),
}))

const libraryApi = vi.hoisted(() => ({
  fetchLibraryCatalog: vi.fn(),
}))

vi.mock('../../api/admin', () => adminApi)
vi.mock('../../api/library', () => libraryApi)

const catalog: LibraryCatalogResponse = {
  overview: {
    total: 3,
    cached: 2,
    completed: 2,
    waiting: 1,
    positive: 1,
    completion_rate: 2 / 3,
    avg_return: 0.03,
  },
  filters: { families: [], statuses: [] },
  items: [
    {
      name: 'alpha_momentum',
      title: 'Alpha 动量',
      family: 'momentum',
      family_label: '动量',
      status: 'completed',
      status_label: '已完成',
      cum_return: 0.1234,
      timestamp: '20250701',
      cache_status: 'valid',
    },
    {
      name: 'beta/reversal',
      title: '',
      family: 'reversal',
      family_label: '反转',
      status: 'waiting',
      status_label: '',
      cum_return: -0.05,
      timestamp: '2025-07-02T08:30:00',
      cache_status: '',
    },
    {
      name: 'gamma',
      title: 'Gamma',
      family: '',
      family_label: '',
      status: 'completed',
      status_label: '已完成',
      cum_return: Number.NaN,
      timestamp: '',
    },
  ],
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

function mountPanel(props: { token?: string; busy?: boolean } = {}) {
  return mount(StrategyManagementPanel, {
    attachTo: document.body,
    props: {
      token: props.token ?? 'admin-secret',
      busy: props.busy ?? false,
    },
    global: { stubs: { teleport: true } },
  })
}

function chooseFile(wrapper: ReturnType<typeof mountPanel>, file: File) {
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', {
    configurable: true,
    value: [file],
  })
  return input.trigger('change')
}

beforeEach(() => {
  vi.clearAllMocks()
  libraryApi.fetchLibraryCatalog.mockResolvedValue(catalog)
  adminApi.uploadStrategy.mockResolvedValue({
    ok: true,
    strategy: {
      name: 'alpha',
      module: 'strategies.alpha',
      source_file: 'alpha.py',
      file_count: 1,
      size_bytes: 20,
      overwritten: false,
      warnings: [],
    },
  })
  adminApi.deleteStrategy.mockResolvedValue({ ok: true, archive_id: 'archive-001' })
  adminApi.renameStrategy.mockResolvedValue({
    ok: true,
    strategy: {
      old_name: 'alpha_momentum',
      name: 'alpha_reclaim',
      title: 'Alpha 修复',
      renamed: true,
      identifier_changed: true,
      title_changed: true,
      cache_preserved: true,
    },
  })
})

describe('StrategyManagementPanel', () => {
  it('keeps loading visible, then renders formatted catalog rows and search results', async () => {
    const request = deferred<LibraryCatalogResponse>()
    libraryApi.fetchLibraryCatalog.mockReturnValueOnce(request.promise)
    const wrapper = mountPanel()

    expect(wrapper.get('.strategy-admin-empty').text()).toContain('正在读取')
    expect(wrapper.get('button[aria-label="刷新策略目录"]').attributes('disabled')).toBeDefined()

    request.resolve(catalog)
    await flushPromises()

    expect(wrapper.get('.strategy-admin-head').text()).toContain('3 个')
    expect(wrapper.findAll('tbody tr')).toHaveLength(3)
    expect(wrapper.findAll('tbody tr')[0].text()).toContain('+12.34%')
    expect(wrapper.findAll('tbody tr')[0].text()).toContain('2025-07-01')
    expect(wrapper.findAll('tbody tr')[1].text()).toContain('-5.00%')
    expect(wrapper.findAll('tbody tr')[1].text()).toContain('2025-07-02')
    expect(wrapper.findAll('tbody tr')[1].get('a').attributes('href')).toBe('/strategy/beta%2Freversal')
    expect(wrapper.findAll('tbody tr')[2].text()).toContain('-')

    await wrapper.get('input[type="search"]').setValue('反转')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.get('.strategy-admin-toolbar').text()).toContain('显示 1 / 3')

    await wrapper.get('input[type="search"]').setValue('missing')
    expect(wrapper.get('.strategy-admin-empty').text()).toBe('没有匹配的策略')
  })

  it('uploads a complete strategy zip with the strict installation fields', async () => {
    adminApi.uploadStrategy.mockResolvedValueOnce({
      ok: true,
      strategy: {
        name: 'Alpha',
        module: 'strategies.Alpha',
        source_file: 'strategy.py',
        file_count: 3,
        size_bytes: 420,
        overwritten: true,
        warnings: ['dynamic import', 'network access'],
      },
    })
    const wrapper = mountPanel()
    await flushPromises()

    const file = new File(['complete strategy package'], 'Alpha 策略!!.zip', { type: 'application/zip' })
    await chooseFile(wrapper, file)
    const textInputs = wrapper.findAll('input[type="text"]')
    expect((textInputs[0].element as HTMLInputElement).value).toBe('Alpha')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('.strategy-upload-form').trigger('submit')
    await flushPromises()

    expect(adminApi.uploadStrategy).toHaveBeenCalledTimes(1)
    const [form, token] = adminApi.uploadStrategy.mock.calls[0] as [FormData, string]
    expect(token).toBe('admin-secret')
    expect((form.get('file') as File).name).toBe('Alpha 策略!!.zip')
    expect(form.get('strategy_name')).toBe('Alpha')
    expect(form.get('title')).toBeNull()
    expect(form.get('summary')).toBeNull()
    expect(form.get('start_time')).toBeNull()
    expect(form.get('end_time')).toBeNull()
    expect(form.get('overwrite')).toBe('1')
    expect(wrapper.emitted('feedback')?.at(-1)).toEqual(['Alpha 已安装到服务器，2 项静态提示。'])
    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(2)
    expect((textInputs[0].element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false)
  })

  it('surfaces an upload failure without losing the selected zip and strategy name', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await chooseFile(wrapper, new File(['x'], 'retry.zip', { type: 'application/zip' }))
    adminApi.uploadStrategy.mockRejectedValueOnce(new Error('安装校验失败'))
    await wrapper.get('.strategy-upload-form').trigger('submit')
    await flushPromises()

    expect(wrapper.emitted('error')?.at(-1)).toEqual(['安装校验失败'])
    expect((wrapper.findAll('input[type="text"]')[0].element as HTMLInputElement).value).toBe('retry')
    expect((wrapper.get('input[type="file"]').element as HTMLInputElement).files?.[0]?.name).toBe('retry.zip')
    expect(wrapper.get('.strategy-upload-button').attributes('disabled')).toBeUndefined()
  })

  it('requires confirmation before delete, prevents duplicate actions, and refreshes after archival', async () => {
    const deletion = deferred<{ ok: boolean; archive_id: string }>()
    adminApi.deleteStrategy.mockReturnValueOnce(deletion.promise)
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.get('button[aria-label="删除 Alpha 动量"]').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('Alpha 动量')
    await wrapper.get('[role="dialog"] button[aria-label="关闭"]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

    await wrapper.get('button[aria-label="删除 Alpha 动量"]').trigger('click')
    await wrapper.findAll('[role="dialog"] footer button')[1].trigger('click')
    expect(adminApi.deleteStrategy).toHaveBeenCalledWith('alpha_momentum', 'admin-secret')
    expect(wrapper.findAll('[role="dialog"] button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
    await wrapper.get('[role="dialog"] button[aria-label="关闭"]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)

    deletion.resolve({ ok: true, archive_id: 'archive-001' })
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.emitted('feedback')?.at(-1)).toEqual(['Alpha 动量 已从服务器策略库移除，归档 archive-001。'])
    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(2)
  })

  it('renames a strategy through the identity dialog and refreshes the catalog', async () => {
    const renaming = deferred<{ ok: boolean; strategy: Record<string, unknown> }>()
    adminApi.renameStrategy.mockReturnValueOnce(renaming.promise)
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.get('button[aria-label="重命名 Alpha 动量"]').trigger('click')
    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.text()).toContain('重命名服务器策略')
    const fields = dialog.findAll('input')
    await fields[0].setValue('alpha_reclaim')
    await fields[1].setValue('Alpha 修复')
    await dialog.get('form').trigger('submit')

    expect(adminApi.renameStrategy).toHaveBeenCalledWith(
      'alpha_momentum',
      { name: 'alpha_reclaim', title: 'Alpha 修复' },
      'admin-secret',
    )
    await nextTick()
    expect(wrapper.get('[role="dialog"]').findAll('button').every(button => button.attributes('disabled') !== undefined)).toBe(true)

    renaming.resolve({
      ok: true,
      strategy: {
        old_name: 'alpha_momentum',
        name: 'alpha_reclaim',
        title: 'Alpha 修复',
      },
    })
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.emitted('feedback')?.at(-1)).toEqual(['Alpha 动量 已重命名为 Alpha 修复（alpha_reclaim）。'])
    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(2)
  })

  it('keeps the rename dialog open when the server rejects the mutation', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('button[aria-label="重命名 Alpha 动量"]').trigger('click')
    const dialog = wrapper.get('[role="dialog"]')
    const fields = dialog.findAll('input')
    await fields[0].setValue('alpha_reclaim')
    adminApi.renameStrategy.mockRejectedValueOnce(new Error('名称已存在'))
    await dialog.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.emitted('error')?.at(-1)).toEqual(['名称已存在'])
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    await nextTick()
    expect(wrapper.get('[role="dialog"]').findAll('button').every(button => button.attributes('disabled') === undefined)).toBe(true)
  })

  it('recovers from catalog and delete failures and honors an externally busy state', async () => {
    libraryApi.fetchLibraryCatalog
      .mockRejectedValueOnce('catalog unavailable')
      .mockResolvedValueOnce(catalog)
    const wrapper = mountPanel({ busy: true })
    await flushPromises()

    expect(wrapper.emitted('error')?.at(-1)).toEqual(['catalog unavailable'])
    expect(wrapper.get('.strategy-upload-button').attributes('disabled')).toBeDefined()
    await wrapper.get('button[aria-label="刷新策略目录"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('tbody tr')).toHaveLength(3)
    await wrapper.get('button[aria-label="删除 Alpha 动量"]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

    await wrapper.setProps({ busy: false })
    adminApi.deleteStrategy.mockRejectedValueOnce(new Error('归档失败'))
    await wrapper.get('button[aria-label="删除 Alpha 动量"]').trigger('click')
    await wrapper.findAll('[role="dialog"] footer button')[1].trigger('click')
    await flushPromises()

    expect(wrapper.emitted('error')?.at(-1)).toEqual(['归档失败'])
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.findAll('[role="dialog"] button').every(button => button.attributes('disabled') === undefined)).toBe(true)
  })
})
