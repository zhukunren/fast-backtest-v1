import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import DataTable from './DataTable.vue'
import { detailTableCellPresentation } from '../features/detail/tradingTablePresentation'

const sortableColumns = [
  { key: 'name', label: '名称', sortable: true, minWidth: 120 },
  { key: 'return', label: '收益率', sortable: true, align: 'right' as const, minWidth: '90px' },
  { key: 'enabled', label: '启用', align: 'center' as const },
]

function bodyRows(wrapper: ReturnType<typeof mount>): string[][] {
  return wrapper.findAll('tbody tr').map(row => row.findAll('td').map(cell => cell.text()))
}

describe('DataTable', () => {
  it('renders an explicit empty state without exposing an empty table', () => {
    const wrapper = mount(DataTable, {
      props: {
        columns: sortableColumns,
        rows: [],
        emptyText: '没有成交记录',
      },
    })

    expect(wrapper.get('.data-table-empty').text()).toBe('没有成交记录')
    expect(wrapper.find('table').exists()).toBe(false)
  })

  it('sorts numeric and text values in three states while preserving equal-value order', async () => {
    const originalRows = [
      { name: '策略 10', return: '￥1,200元', enabled: true },
      { name: '策略 2', return: '20%', enabled: false },
      { name: '策略 1', return: '20%', enabled: null },
      { name: '策略 3', return: '-', enabled: '' },
    ]
    const wrapper = mount(DataTable, {
      props: {
        columns: sortableColumns,
        rows: originalRows,
        sortable: false,
        caption: '策略收益',
        stickyFirstColumn: true,
      },
    })

    expect(wrapper.get('[role="region"]').attributes('aria-label')).toBe('策略收益')
    expect(wrapper.get('caption').classes()).not.toContain('is-visually-hidden')
    expect(wrapper.get('table').classes()).toContain('is-sticky-first-column')
    expect(wrapper.get('th').attributes('style')).toContain('min-width: 120px')
    expect(wrapper.findAll('th')[1].classes()).toContain('align-right')
    expect(wrapper.findAll('th')[2].classes()).toContain('align-center')
    expect(bodyRows(wrapper)[0]).toEqual(['策略 10', '￥1,200元', '是'])
    expect(bodyRows(wrapper)[1]).toEqual(['策略 2', '20%', '否'])
    expect(bodyRows(wrapper)[2]).toEqual(['策略 1', '20%', '-'])

    const returnSort = wrapper.get('button[title="按收益率排序"]')
    await returnSort.trigger('click')
    expect(wrapper.findAll('th')[1].attributes('aria-sort')).toBe('ascending')
    expect(bodyRows(wrapper).map(row => row[0])).toEqual(['策略 3', '策略 2', '策略 1', '策略 10'])

    await returnSort.trigger('click')
    expect(wrapper.findAll('th')[1].attributes('aria-sort')).toBe('descending')
    expect(bodyRows(wrapper).map(row => row[0])).toEqual(['策略 10', '策略 2', '策略 1', '策略 3'])

    await returnSort.trigger('click')
    expect(wrapper.findAll('th')[1].attributes('aria-sort')).toBe('none')
    expect(bodyRows(wrapper).map(row => row[0])).toEqual(originalRows.map(row => row.name))

    await wrapper.get('button[title="按名称排序"]').trigger('click')
    expect(bodyRows(wrapper).map(row => row[0])).toEqual(['策略 1', '策略 2', '策略 3', '策略 10'])
  })

  it('paginates sorted data and resets the page when its inputs change', async () => {
    const keyResolver = vi.fn((row: Record<string, unknown>, index: number) => `${row.name}-${index}`)
    const wrapper = mount(DataTable, {
      props: {
        columns: sortableColumns,
        rows: [
          { name: 'D', return: 4, enabled: true },
          { name: 'C', return: 3, enabled: true },
          { name: 'B', return: 2, enabled: true },
          { name: 'A', return: 1, enabled: true },
        ],
        ariaLabel: '候选策略',
        pageSize: 2,
        pageSizeOptions: [3, 2, 2, 0, Number.NaN],
        rowKey: keyResolver,
      },
    })

    expect(wrapper.get('.data-table-page-status').text()).toBe('第 1-2 条，共 4 条 · 第 1/2 页')
    expect(wrapper.get('nav').attributes('aria-label')).toBe('候选策略分页')
    expect(wrapper.findAll('select option').map(option => option.text())).toEqual(['2', '3'])
    expect(wrapper.get('button[aria-label="上一页"]').attributes('disabled')).toBeDefined()

    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    expect(bodyRows(wrapper).map(row => row[0])).toEqual(['B', 'A'])
    expect(wrapper.get('.data-table-page-status').text()).toContain('第 3-4 条')
    expect(keyResolver).toHaveBeenCalledWith(expect.objectContaining({ name: 'B' }), 2)

    await wrapper.get('button[title="按收益率排序"]').trigger('click')
    expect(wrapper.get('.data-table-page-status').text()).toContain('第 1/2 页')
    expect(bodyRows(wrapper).map(row => row[0])).toEqual(['A', 'B'])

    await wrapper.get('select[aria-label="每页显示行数"]').setValue('3')
    expect(wrapper.get('.data-table-page-status').text()).toBe('第 1-3 条，共 4 条 · 第 1/2 页')

    await wrapper.setProps({ pageSize: 1.8 })
    expect(wrapper.get('.data-table-page-status').text()).toBe('第 1-1 条，共 4 条 · 第 1/4 页')
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await wrapper.setProps({ rows: [{ name: 'Only', return: 1, enabled: false }] })
    expect(wrapper.get('.data-table-page-status').text()).toBe('第 1-1 条，共 1 条 · 第 1/1 页')
  })

  it('lets users choose columns but never hide the final visible column', async () => {
    const columns = [
      { key: 'name', label: '名称', sortable: true },
      { key: 'return', label: '收益', defaultVisible: false },
      { key: 'status', label: '状态' },
    ]
    const wrapper = mount(DataTable, {
      props: {
        columns,
        rows: [{ name: 'Alpha', return: '5%', status: '已成交' }],
        columnChooser: true,
      },
    })

    expect(wrapper.findAll('th').map(header => header.text())).toEqual(['名称', '状态'])
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    expect((checkboxes[1].element as HTMLInputElement).checked).toBe(false)

    await checkboxes[1].setValue(true)
    expect(wrapper.findAll('th').map(header => header.text())).toEqual(['名称', '收益', '状态'])

    await wrapper.get('button[title="按名称排序"]').trigger('click')
    await wrapper.findAll('input[type="checkbox"]')[0].setValue(false)
    expect(wrapper.findAll('th').map(header => header.text())).toEqual(['收益', '状态'])

    await wrapper.findAll('input[type="checkbox"]')[1].setValue(false)
    expect(wrapper.findAll('th').map(header => header.text())).toEqual(['状态'])
    const remaining = wrapper.findAll('input[type="checkbox"]')[2]
    expect(remaining.attributes('disabled')).toBeDefined()
    await remaining.trigger('change')
    expect(wrapper.findAll('th').map(header => header.text())).toEqual(['状态'])

    await wrapper.setProps({
      columns: [
        { key: 'status', label: '状态' },
        { key: 'direction', label: '方向' },
      ],
      rows: [{ status: '已成交', direction: '买入' }],
    })
    expect(wrapper.findAll('th').map(header => header.text())).toEqual(['状态', '方向'])
  })

  it('maps trading values to semantic tags and profit/loss states', () => {
    const columns = [
      { key: 'direction', label: '方向' },
      { key: 'source', label: '来源' },
      { key: 'status', label: '状态' },
      { key: 'reason', label: '风控原因' },
      { key: 'priceType', label: '价格类型' },
      { key: 'risk', label: '净值风控' },
      { key: 'position', label: '目标仓位' },
      { key: 'pnl', label: '盈亏' },
    ]
    const rows = [
      { direction: '买入', source: '风控', status: '已成交', reason: '风险恢复', priceType: 'open', risk: 'WARNING', position: '50%', pnl: '+12.5%' },
      { direction: '卖出', source: '策略', status: '已拒绝', reason: '触发止损', priceType: 'close', risk: 'DANGER', position: '0%', pnl: '-1,200' },
      { direction: '观望', source: '其他', status: '已跳过', reason: '-', priceType: 'vwap', risk: 'LOCK', position: true, pnl: 0 },
      { direction: '', source: '', status: '已取消', reason: '', priceType: '', risk: 'NORMAL', position: false, pnl: Number.NaN },
      { direction: '-', source: '-', status: '未知', reason: '-', priceType: '-', risk: '-', position: '-', pnl: '-' },
    ]
    const wrapper = mount(DataTable, {
      props: {
        columns,
        rows,
        cellPresentation: detailTableCellPresentation,
      },
    })

    const classes = wrapper.findAll('.trade-tag').map(tag => tag.classes())
    expect(classes).toEqual(expect.arrayContaining([
      expect.arrayContaining(['buy']),
      expect.arrayContaining(['sell']),
      expect.arrayContaining(['risk']),
      expect.arrayContaining(['strategy']),
      expect.arrayContaining(['order-filled']),
      expect.arrayContaining(['order-rejected']),
      expect.arrayContaining(['order-skipped']),
      expect.arrayContaining(['order-cancelled']),
      expect.arrayContaining(['risk-recover']),
      expect.arrayContaining(['risk-trigger']),
      expect.arrayContaining(['price-open']),
      expect.arrayContaining(['price-close']),
      expect.arrayContaining(['state-warning']),
      expect.arrayContaining(['state-danger']),
      expect.arrayContaining(['state-lock']),
      expect.arrayContaining(['ratio']),
    ]))
    expect(wrapper.findAll('td.trade-profit')).toHaveLength(1)
    expect(wrapper.findAll('td.trade-loss')).toHaveLength(1)
    expect(wrapper.findAll('td.trade-neutral')).toHaveLength(1)
  })

  it('supports hidden captions, compact layout, string row keys, and invalid widths', () => {
    const wrapper = mount(DataTable, {
      props: {
        columns: [
          { key: 'id', label: 'ID', minWidth: Number.POSITIVE_INFINITY },
          { key: 'value', label: 'Value', minWidth: '  ' },
        ],
        rows: [{ id: 'row-1', value: 'x' }],
        rowKey: 'id',
        compact: true,
      },
    })

    expect(wrapper.get('[role="region"]').attributes('aria-label')).toBe('数据表格')
    expect(wrapper.get('caption').classes()).toContain('is-visually-hidden')
    expect(wrapper.get('.data-table-wrap').classes()).toContain('compact')
    expect(wrapper.findAll('th').every(header => header.attributes('style') === undefined)).toBe(true)
  })
})
