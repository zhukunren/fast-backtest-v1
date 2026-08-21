import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { StrategyLibraryItem } from '../../../types/library'
import CompareToolbar from './CompareToolbar.vue'
import StrategyCard from './StrategyCard.vue'

const failedItem: StrategyLibraryItem = {
  name: 'alpha',
  title: 'Alpha 动量',
  summary: '捕捉趋势延续。',
  style_label: '动量',
  portfolio_label: '双持仓',
  status: 'failed',
  error: '行情文件缺失',
  cum_return: -0.0312,
  max_drawdown: -0.0876,
  win_rate: 0.4567,
  trade_count: 12,
  timestamp: '20250701T120000',
}

describe('StrategyCard', () => {
  it('formats a failed strategy and forwards user commands', async () => {
    const wrapper = mount(StrategyCard, {
      props: {
        item: failedItem,
        displayRank: 2,
        running: false,
        runDisabled: false,
        selectedForCompare: false,
        compareDisabled: false,
        detailHref: '/strategy/alpha',
      },
    })

    expect(wrapper.get('.record-rank').text()).toBe('02')
    expect(wrapper.get('.library-status').text()).toContain('回测失败')
    expect(wrapper.get('.library-status').attributes('title')).toContain('行情文件缺失')
    expect(wrapper.get('.record-return strong').text()).toBe('-3.12%')
    expect(wrapper.get('.record-drawdown strong').text()).toBe('-8.76%')
    expect(wrapper.get('.sparkline-empty').text()).toBe('暂无净值')

    await wrapper.get('.library-run-button').trigger('click')
    await wrapper.get('input[type="checkbox"]').trigger('change')
    await wrapper.get('.record-title-line > a').trigger('click')

    expect(wrapper.emitted('run')).toEqual([['alpha']])
    expect(wrapper.emitted('toggleCompare')).toEqual([['alpha']])
    expect(wrapper.emitted('openDetail')).toEqual([['alpha']])
  })

  it('marks the active strategy and disables its run command', () => {
    const wrapper = mount(StrategyCard, {
      props: {
        item: { ...failedItem, sparkline: [1, 1.05, 1.02] },
        displayRank: 1,
        running: true,
        runDisabled: true,
        selectedForCompare: true,
        compareDisabled: true,
        detailHref: '/strategy/alpha',
      },
    })

    expect(wrapper.get('.strategy-record').classes()).toContain('is-active')
    expect(wrapper.get('.library-status').text()).toContain('回测中')
    expect(wrapper.get('.library-run-button').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.library-run-button').text()).toContain('运行中')
    expect(wrapper.find('svg[aria-label="Alpha 动量净值走势"]').exists()).toBe(true)
  })

  it('keeps a valid result visible while disclosing a failed latest attempt', () => {
    const wrapper = mount(StrategyCard, {
      props: {
        item: {
          ...failedItem,
          status: 'completed',
          status_label: '已完成',
          cache_status: 'valid',
          error: '',
          cum_return: 0.12,
          latest_attempt_status: 'failed',
          latest_attempt_error: 'Tushare 连接超时',
        },
        displayRank: 1,
        running: false,
        runDisabled: false,
        selectedForCompare: false,
        compareDisabled: false,
        detailHref: '/strategy/alpha',
      },
    })

    expect(wrapper.get('.library-status').classes()).toContain('is-ready')
    expect(wrapper.get('.library-status').text()).toContain('已完成')
    expect(wrapper.get('.record-return strong').text()).toBe('+12.00%')
    expect(wrapper.get('.record-attempt-warning').text()).toBe('最近运行失败')
    expect(wrapper.get('.record-attempt-warning').attributes('title')).toContain('继续显示上次有效结果')
    expect(wrapper.get('.record-attempt-warning').attributes('title')).toContain('Tushare 连接超时')
    expect(wrapper.get('.library-status').attributes('title')).toContain('最近一次回测失败')
  })
})

describe('CompareToolbar', () => {
  it('exposes selection commands with capability-based disabled states', async () => {
    const wrapper = mount(CompareToolbar, {
      props: {
        items: [failedItem],
        statusText: '已选 1 / 4 · 至少选择 2 个',
        backtestStatusText: '回测运行中',
        canOpenCompare: false,
        canRunSelected: false,
        canRunAll: false,
      },
    })

    const buttons = wrapper.findAll('button')
    expect(buttons.find(button => button.text() === '开始对比')?.attributes('disabled')).toBeDefined()
    expect(buttons.find(button => button.text() === '回测已选')?.attributes('disabled')).toBeDefined()
    expect(wrapper.get('.toolbar-inline-status .toolbar-status').classes()).toContain('busy')

    await wrapper.get('button[aria-label="移除 Alpha 动量"]').trigger('click')
    await buttons.find(button => button.text() === '清空')!.trigger('click')

    expect(wrapper.emitted('remove')).toEqual([['alpha']])
    expect(wrapper.emitted('clear')).toHaveLength(1)
  })
})
