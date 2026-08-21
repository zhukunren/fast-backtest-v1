import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./features/catalog/StrategyLibraryPage.vue', () => ({
  default: {
    name: 'StrategyLibraryPage',
    template: '<section data-page="catalog"><a data-link="detail" href="/strategy/alpha?returnTo=%2F">detail</a></section>',
  },
}))

vi.mock('./features/detail/StrategyDetailPage.vue', () => ({
  __esModule: true,
  default: {
    name: 'StrategyDetailPage',
    props: ['name'],
    template: `
      <section data-page="detail" :data-name="name">
        <a data-link="home" href="/">home</a>
        <a data-link="target" href="/" target="_blank">target</a>
        <a data-link="download" href="/" download>download</a>
        <a data-link="hash" href="/#section">hash</a>
        <a data-link="external" href="https://example.test/">external</a>
      </section>
    `,
  },
}))

vi.mock('./features/compare/StrategyComparePage.vue', () => ({
  __esModule: true,
  default: {
    name: 'StrategyComparePage',
    props: ['names', 'returnTo'],
    template: '<section data-page="compare" :data-names="names.join(\',\')" :data-return-to="returnTo || \'\'">compare</section>',
  },
}))

vi.mock('./features/admin/AdminPage.vue', () => ({
  __esModule: true,
  default: {
    name: 'AdminPage',
    template: '<section data-page="admin">admin</section>',
  },
}))

import App from './App.vue'

async function mountAt(url: string) {
  window.history.replaceState({ test: true }, '', url)
  const wrapper = mount(App, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

async function triggerWithoutNavigation(
  element: ReturnType<ReturnType<typeof mount>['get']>,
  event: Record<string, unknown>,
) {
  document.addEventListener('click', clickEvent => clickEvent.preventDefault(), { once: true })
  await element.trigger('click', event)
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState({}, '', '/')
  document.title = ''
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('App routing shell', () => {
  it('renders the catalog as the safe fallback and marks its navigation item current', async () => {
    const wrapper = await mountAt('/unknown/path?ignored=1')

    expect(wrapper.find('[data-page="catalog"]').exists()).toBe(true)
    expect(document.title).toBe('策略库')
    expect(wrapper.get('.site-nav a[href="/"]').attributes('aria-current')).toBe('page')
    expect(wrapper.get('main').classes()).toContain('site-main-catalog')
  })

  it('decodes detail names, tolerates malformed escapes, and hides the shared header', async () => {
    const wrapper = await mountAt('/strategy/alpha%20momentum/')

    expect(wrapper.get('[data-page="detail"]').attributes('data-name')).toBe('alpha momentum')
    expect(wrapper.find('.site-header').exists()).toBe(false)
    expect(wrapper.get('main').classes()).toContain('site-main-detail')
    expect(document.title).toBe('策略回测结果')

    window.history.pushState({}, '', '/strategy/%E0%A4%A')
    window.dispatchEvent(new PopStateEvent('popstate'))
    await flushPromises()
    expect(wrapper.get('[data-page="detail"]').attributes('data-name')).toBe('%E0%A4%A')
  })

  it('opens catalog detail links without a document reload', async () => {
    const wrapper = await mountAt('/')
    const pushState = vi.spyOn(window.history, 'pushState')

    await wrapper.get('[data-link="detail"]').trigger('click', { button: 0 })
    await flushPromises()

    expect(pushState).toHaveBeenCalledWith({ test: true }, '', '/strategy/alpha?returnTo=%2F')
    expect(wrapper.get('[data-page="detail"]').attributes('data-name')).toBe('alpha')
    wrapper.unmount()
  })

  it('normalizes compare names and follows browser history changes', async () => {
    const wrapper = await mountAt('/compare?names=alpha,beta&names=beta,%20gamma%20&names=')

    expect(wrapper.get('[data-page="compare"]').attributes('data-names')).toBe('alpha,beta,gamma')
    expect(wrapper.get('[data-page="compare"]').attributes('data-return-to')).toBe('')
    expect(wrapper.get('main').classes()).toContain('site-main-compare')
    expect(document.title).toBe('策略对比')

    window.history.pushState({}, '', '/admin/')
    window.dispatchEvent(new PopStateEvent('popstate'))
    await flushPromises()
    expect(wrapper.find('[data-page="admin"]').exists()).toBe(true)
    expect(wrapper.get('a[href="/admin"]').attributes('aria-current')).toBe('page')
    expect(document.title).toBe('管理后台')
  })

  it('returns from detail to an allowed same-origin compare URL without reloading', async () => {
    const target = '/compare?names=alpha%2Cbeta&returnTo=%2F'
    const wrapper = await mountAt(`/strategy/alpha?returnTo=${encodeURIComponent(target)}`)
    const replaceState = vi.spyOn(window.history, 'replaceState')

    await wrapper.get('[data-link="home"]').trigger('click', { button: 0 })
    await flushPromises()

    expect(replaceState).toHaveBeenCalledWith({ test: true }, '', target)
    expect(window.location.pathname).toBe('/compare')
    expect(wrapper.get('[data-page="compare"]').attributes('data-names')).toBe('alpha,beta')
  })

  it('does not hijack modified, attributed, fragmented, or cross-origin links', async () => {
    const cases = [
      { selector: '[data-link="home"]', event: { button: 1 } },
      { selector: '[data-link="home"]', event: { button: 0, ctrlKey: true } },
      { selector: '[data-link="home"]', event: { button: 0, metaKey: true } },
      { selector: '[data-link="home"]', event: { button: 0, shiftKey: true } },
      { selector: '[data-link="home"]', event: { button: 0, altKey: true } },
      { selector: '[data-link="target"]', event: { button: 0 } },
      { selector: '[data-link="download"]', event: { button: 0 } },
      { selector: '[data-link="hash"]', event: { button: 0 } },
      { selector: '[data-link="external"]', event: { button: 0 } },
    ]

    for (const [index, testCase] of cases.entries()) {
      const wrapper = await mountAt(`/strategy/case-${index}?returnTo=%2Fcompare`)
      const replaceState = vi.spyOn(window.history, 'replaceState')
      await triggerWithoutNavigation(wrapper.get(testCase.selector), testCase.event)
      expect(replaceState).not.toHaveBeenCalled()
      wrapper.unmount()
      replaceState.mockRestore()
    }
  })

  it('rejects external and malformed return targets and removes global listeners on unmount', async () => {
    const addWindow = vi.spyOn(window, 'addEventListener')
    const removeWindow = vi.spyOn(window, 'removeEventListener')
    const addDocument = vi.spyOn(document, 'addEventListener')
    const removeDocument = vi.spyOn(document, 'removeEventListener')
    const wrapper = await mountAt('/strategy/alpha?returnTo=https%3A%2F%2Fexample.test%2Fcompare')
    const replaceState = vi.spyOn(window.history, 'replaceState')

    await triggerWithoutNavigation(wrapper.get('[data-link="home"]'), { button: 0 })
    expect(replaceState).not.toHaveBeenCalled()

    window.history.replaceState({}, '', '/strategy/alpha?returnTo=http%3A%2F%2F%5B')
    replaceState.mockClear()
    await triggerWithoutNavigation(wrapper.get('[data-link="home"]'), { button: 0 })
    expect(replaceState).not.toHaveBeenCalled()

    wrapper.unmount()
    expect(addWindow).toHaveBeenCalledWith('popstate', expect.any(Function))
    expect(removeWindow).toHaveBeenCalledWith('popstate', expect.any(Function))
    expect(addDocument).toHaveBeenCalledWith('click', expect.any(Function))
    expect(removeDocument).toHaveBeenCalledWith('click', expect.any(Function))
  })
})
