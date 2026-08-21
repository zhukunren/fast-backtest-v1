<script setup lang="ts">
import { Zap } from '@lucide/vue'
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref, watchEffect } from 'vue'
import StrategyLibraryPage from './features/catalog/StrategyLibraryPage.vue'

const StrategyDetailPage = defineAsyncComponent(() => import('./features/detail/StrategyDetailPage.vue'))
const StrategyComparePage = defineAsyncComponent(() => import('./features/compare/StrategyComparePage.vue'))
const AdminPage = defineAsyncComponent(() => import('./features/admin/AdminPage.vue'))

type AppRoute =
  | { kind: 'catalog' }
  | { kind: 'detail'; name: string }
  | { kind: 'compare'; names: string[]; returnTo: string | null }
  | { kind: 'admin' }

const route = ref<AppRoute>(resolveRoute(window.location.pathname, window.location.search))

watchEffect(() => {
  document.title = route.value.kind === 'detail'
    ? '策略回测结果'
    : route.value.kind === 'compare'
      ? '策略对比'
      : route.value.kind === 'admin'
        ? '管理后台'
        : '策略库'
})

function resolveRoute(pathname: string, search = ''): AppRoute {
  const match = pathname.match(/^\/strategy\/([^/]+)\/?$/)
  if (match?.[1]) {
    return {
      kind: 'detail',
      name: safeDecodeURIComponent(match[1]),
    }
  }
  if (/^\/compare\/?$/.test(pathname)) {
    const params = new URLSearchParams(search)
    const names = params.getAll('names')
      .flatMap(value => value.split(','))
      .map(value => value.trim())
      .filter((value, index, values) => value && values.indexOf(value) === index)
    return { kind: 'compare', names, returnTo: params.get('returnTo') }
  }
  if (/^\/admin\/?$/.test(pathname)) return { kind: 'admin' }
  return { kind: 'catalog' }
}

function safeDecodeURIComponent(value: string): string {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function syncRoute() {
  route.value = resolveRoute(window.location.pathname, window.location.search)
}

function resolveReturnTarget(): string | null {
  const rawTarget = new URLSearchParams(window.location.search).get('returnTo')
  if (!rawTarget) return null
  try {
    const target = new URL(rawTarget, window.location.origin)
    const normalizedPath = target.pathname.replace(/\/$/, '') || '/'
    if (target.origin !== window.location.origin || !['/', '/compare'].includes(normalizedPath)) return null
    return `${target.pathname}${target.search}${target.hash}`
  } catch {
    return null
  }
}

function handleInternalNavigation(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0) return
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const source = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href]') : null
  if (!source || source.target || source.hasAttribute('download')) return
  const href = new URL(source.href, window.location.href)
  if (href.origin !== window.location.origin) return

  if (/^\/strategy\/[^/]+\/?$/.test(href.pathname)) {
    event.preventDefault()
    window.history.pushState(window.history.state, '', `${href.pathname}${href.search}${href.hash}`)
    syncRoute()
    return
  }

  if (route.value.kind !== 'detail') return
  if (href.pathname !== '/' || href.search || href.hash) return
  const returnTarget = resolveReturnTarget()
  if (!returnTarget) return
  event.preventDefault()
  window.history.replaceState(window.history.state, '', returnTarget)
  syncRoute()
}

onMounted(() => {
  window.addEventListener('popstate', syncRoute)
  document.addEventListener('click', handleInternalNavigation)
})
onBeforeUnmount(() => {
  window.removeEventListener('popstate', syncRoute)
  document.removeEventListener('click', handleInternalNavigation)
})
</script>

<template>
  <div class="site-shell" :class="{ 'site-shell-detail': route.kind === 'detail' }">
    <header v-if="route.kind !== 'detail'" class="site-header">
      <a class="site-brand" href="/" aria-label="返回策略库首页">
        <span class="site-brand-mark" aria-hidden="true">
          <Zap :size="16" fill="currentColor" />
        </span>
        <span>急速回测</span>
      </a>
      <nav class="site-nav" aria-label="主导航">
        <a href="/" :aria-current="route.kind === 'catalog' ? 'page' : undefined">全部策略</a>
        <a href="/admin" :aria-current="route.kind === 'admin' ? 'page' : undefined">管理后台</a>
      </nav>
    </header>

    <main
      class="site-main"
      :class="{
        'site-main-catalog': route.kind === 'catalog',
        'site-main-detail': route.kind === 'detail',
        'site-main-compare': route.kind === 'compare',
        'site-main-admin': route.kind === 'admin',
      }"
    >
      <StrategyDetailPage v-if="route.kind === 'detail'" :name="route.name" />
      <StrategyComparePage
        v-else-if="route.kind === 'compare'"
        :names="route.names"
        :return-to="route.returnTo"
      />
      <AdminPage v-else-if="route.kind === 'admin'" />
      <StrategyLibraryPage v-else />
    </main>
  </div>
</template>
