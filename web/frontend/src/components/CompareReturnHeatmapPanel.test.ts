import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'

import type { NetValuePayload } from '../types/library'
import ReturnHeatmapChart from './ReturnHeatmapChart.vue'
import CompareReturnHeatmapPanel from './CompareReturnHeatmapPanel.vue'

const ReturnHeatmapChartStub = defineComponent({
  name: 'ReturnHeatmapChart',
  props: ['data', 'metricMode', 'maxAbs', 'height'],
  template: '<div class="heatmap-stub" />',
})

function mountPanel(strategies: Array<{ name: string; title: string; data: NetValuePayload | null }>) {
  return mount(CompareReturnHeatmapPanel, {
    props: { strategies },
    global: {
      stubs: { ReturnHeatmapChart: ReturnHeatmapChartStub },
    },
  })
}

describe('CompareReturnHeatmapPanel', () => {
  it('renders empty state and disables excess mode without an excess series', () => {
    const wrapper = mountPanel([])
    expect(wrapper.get('.detail-chart-empty').text()).toContain('暂无可对比')
    expect(wrapper.findAll('button').at(-1)?.attributes('disabled')).toBeDefined()
  })

  it('shares scales, switches granularity, and marks strategies without excess data', async () => {
    const withExcess = {
      name: 'alpha',
      title: '策略 Alpha',
      data: {
        dates: ['2024-01-01', '2024-01-31', '2024-02-29'],
        net_value: [1, 1.1, 1.2],
        excess_net_value: [1, 1.05, 1.1],
      },
    }
    const withoutExcess = {
      name: 'beta',
      title: '策略 Beta',
      data: {
        dates: ['2024-01-01', '2024-01-31', '2024-02-29'],
        net_value: [1, 0.9, 1.1],
      },
    }
    const wrapper = mountPanel([withExcess, withoutExcess])
    await flushPromises()

    let charts = wrapper.findAllComponents(ReturnHeatmapChart)
    expect(charts).toHaveLength(2)
    expect(charts[1].props('maxAbs')).toBe(charts[0].props('maxAbs'))
    expect(wrapper.findAll('.compare-chart-title').map(item => item.text())).toEqual([
      '策略 Alpha',
      '策略 Beta',
    ])
    expect(wrapper.findAll('.heatmap-stub')[0].attributes()).toBeDefined()

    const buttons = wrapper.findAll('button')
    await buttons[0].trigger('click')
    expect(buttons[0].classes()).toContain('active')
    await buttons.at(-1)?.trigger('click')
    await flushPromises()

    expect(wrapper.get('.panel-note').text()).toContain('超额')
    expect(wrapper.get('.state-warning').text()).toContain('缺少超额曲线')
    charts = wrapper.findAllComponents(ReturnHeatmapChart)
    expect(charts).toHaveLength(1)
    expect(charts[0].props('metricMode')).toBe('excess')

    await wrapper.setProps({ strategies: [withoutExcess] })
    await flushPromises()
    expect(wrapper.findComponent(ReturnHeatmapChart).props('metricMode')).toBe('return')
    expect(wrapper.find('.state-warning').exists()).toBe(false)
  })
})
