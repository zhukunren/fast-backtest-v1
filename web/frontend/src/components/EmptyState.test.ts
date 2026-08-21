import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EmptyState from './EmptyState.vue'

describe('EmptyState', () => {
  it('renders the default empty message without an action', () => {
    const wrapper = mount(EmptyState)

    expect(wrapper.get('.empty-title').text()).toBe('暂无数据')
    expect(wrapper.get('.empty-description').text()).toBe('当前没有可显示的内容')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('renders custom copy and emits the action command once', async () => {
    const wrapper = mount(EmptyState, {
      props: {
        icon: '!',
        title: '没有匹配结果',
        description: '请调整筛选条件。',
        actionText: '清除筛选',
      },
    })

    expect(wrapper.get('.empty-icon').text()).toBe('!')
    expect(wrapper.text()).toContain('没有匹配结果')
    await wrapper.get('button').trigger('click')

    expect(wrapper.emitted('action')).toHaveLength(1)
  })
})
