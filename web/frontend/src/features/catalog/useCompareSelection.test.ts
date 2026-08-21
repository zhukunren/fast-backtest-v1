import { effectScope, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'

import type { StrategyLibraryItem } from '../../types/library'
import { COMPARE_SELECTION_KEY, useCompareSelection } from './useCompareSelection'

describe('useCompareSelection', () => {
  beforeEach(() => window.sessionStorage.clear())

  it('restores, prunes, limits, and persists the selected strategy names', async () => {
    window.sessionStorage.setItem(COMPARE_SELECTION_KEY, JSON.stringify(['alpha', 'missing', 'alpha']))
    const items = ref<StrategyLibraryItem[]>([
      { name: 'alpha' },
      { name: 'beta' },
      { name: 'gamma' },
    ])
    const scope = effectScope()
    const selection = scope.run(() => useCompareSelection(items, 2))!

    expect(selection.selectedNames.value).toEqual(['alpha', 'missing'])
    selection.prune()
    selection.toggle('beta')
    selection.toggle('gamma')
    await nextTick()

    expect(selection.selectedNames.value).toEqual(['alpha', 'beta'])
    expect(selection.selectedItems.value.map(item => item.name)).toEqual(['alpha', 'beta'])
    expect(JSON.parse(window.sessionStorage.getItem(COMPARE_SELECTION_KEY) || '[]')).toEqual(['alpha', 'beta'])

    selection.remove('alpha')
    selection.clear()
    await nextTick()
    expect(selection.selectedNames.value).toEqual([])
    scope.stop()
  })
})
