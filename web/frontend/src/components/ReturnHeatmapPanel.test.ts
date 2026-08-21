import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ReturnHeatmapChart from './ReturnHeatmapChart.vue'
import ReturnHeatmapPanel from './ReturnHeatmapPanel.vue'

describe('ReturnHeatmapPanel', () => {
  it('shows a useful empty state and disables excess mode without excess data', () => {
    const wrapper = shallowMount(ReturnHeatmapPanel, {
      props: { data: null },
    })

    expect(wrapper.get('.detail-chart-empty').text()).toBe('暂无可展示的收益热力图数据。')
    expect(wrapper.get('.panel-note').text()).toContain('默认按月查看收益分布')
    const excessButton = wrapper.findAll('button').find(button => button.text() === '超额分布')
    expect(excessButton?.attributes('disabled')).toBeDefined()
    expect(wrapper.findComponent(ReturnHeatmapChart).exists()).toBe(false)
  })

  it('switches granularity and metric, then falls back when excess data disappears', async () => {
    const wrapper = shallowMount(ReturnHeatmapPanel, {
      props: {
        embedded: true,
        data: {
          dates: ['2025-01-02', '2025-01-31', '2025-02-28'],
          net_value: [1, 1.1, 1.21],
          excess_net_value: [1, 1.02, 1.03],
        },
      },
    })

    expect(wrapper.get('section').attributes('aria-label')).toBe('收益热力图')
    expect(wrapper.find('h2').exists()).toBe(false)
    expect(wrapper.findComponent(ReturnHeatmapChart).props('metricMode')).toBe('return')

    await wrapper.findAll('button').find(button => button.text() === '按周')!.trigger('click')
    expect(wrapper.get('.panel-note').text()).toContain('当前为按周查看收益分布')
    expect(wrapper.findComponent(ReturnHeatmapChart).props('data')?.xAxisLabels).toHaveLength(53)

    await wrapper.findAll('button').find(button => button.text() === '超额分布')!.trigger('click')
    expect(wrapper.findComponent(ReturnHeatmapChart).props('metricMode')).toBe('excess')
    expect(wrapper.get('.panel-note').text()).toContain('当前为按周查看超额分布')

    await wrapper.setProps({
      data: {
        dates: ['2025-01-02', '2025-01-31'],
        net_value: [1, 1.1],
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.findComponent(ReturnHeatmapChart).props('metricMode')).toBe('return')
    const excessButton = wrapper.findAll('button').find(button => button.text() === '超额分布')
    expect(excessButton?.attributes('disabled')).toBeDefined()
  })
})
