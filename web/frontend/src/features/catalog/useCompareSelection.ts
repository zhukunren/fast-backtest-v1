import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import type { StrategyLibraryItem } from '../../types/library'

export const COMPARE_SELECTION_KEY = 'strategy-library:compare-selection'

function readStoredSelection(maxItems: number): string[] {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(COMPARE_SELECTION_KEY) || '[]')
    if (!Array.isArray(stored)) return []
    return stored
      .map(value => String(value || '').trim())
      .filter((value, index, values) => value && values.indexOf(value) === index)
      .slice(0, maxItems)
  } catch {
    return []
  }
}

export function useCompareSelection(
  items: MaybeRefOrGetter<StrategyLibraryItem[]>,
  maxItems = 4,
) {
  const selectedNames = ref<string[]>(readStoredSelection(maxItems))
  const selectedItems = computed<StrategyLibraryItem[]>(() => {
    const itemByName = new Map(toValue(items).map(item => [item.name, item]))
    return selectedNames.value
      .map(name => itemByName.get(name))
      .filter((item): item is StrategyLibraryItem => Boolean(item))
  })

  function persist() {
    try {
      window.sessionStorage.setItem(COMPARE_SELECTION_KEY, JSON.stringify(selectedNames.value))
    } catch {
      // Comparison remains usable when session storage is unavailable.
    }
  }

  function prune() {
    const knownNames = new Set(toValue(items).map(item => item.name))
    selectedNames.value = selectedNames.value.filter(name => knownNames.has(name)).slice(0, maxItems)
  }

  function toggle(name: string) {
    const normalized = String(name || '').trim()
    const selectedIndex = selectedNames.value.indexOf(normalized)
    if (selectedIndex >= 0) {
      selectedNames.value = selectedNames.value.filter(selectedName => selectedName !== normalized)
      return
    }
    if (
      !normalized
      || selectedNames.value.length >= maxItems
      || !toValue(items).some(item => item.name === normalized)
    ) return
    selectedNames.value = [...selectedNames.value, normalized]
  }

  function remove(name: string) {
    selectedNames.value = selectedNames.value.filter(selectedName => selectedName !== name)
  }

  function clear() {
    selectedNames.value = []
  }

  watch(selectedNames, persist, { deep: true })

  return {
    selectedNames,
    selectedItems,
    prune,
    toggle,
    remove,
    clear,
  }
}
