import { describe, expect, it } from 'vitest'

import type { StrategyLibraryItem } from '../../types/library'
import {
  analyzeEvaluationComparability,
  catalogItemState,
  hasMeaningfulFilter,
  matchesCatalogStatus,
  sortCatalogItems,
} from './catalogModel'

function evaluatedItem(name: string, id: string, contextId: string): StrategyLibraryItem {
  return {
    name,
    status: 'completed',
    evaluation_identity: {
      id,
      context_id: contextId,
      effective_window: { start_time: '20240101', end_time: '20241231' },
      execution_config_hash: 'config-a',
    },
  }
}

describe('catalogModel', () => {
  it('ranks risk-adjusted metrics ahead of cumulative return by default', () => {
    const items: StrategyLibraryItem[] = [
      { name: 'high-return', cum_return: 0.8, sharpe: 0.4, max_drawdown: -0.5 },
      { name: 'stable', cum_return: 0.2, sharpe: 1.8, max_drawdown: -0.08 },
      { name: 'middle', cum_return: 0.35, sharpe: 1.1, max_drawdown: -0.2 },
    ]

    expect(sortCatalogItems(items, 'risk_adjusted').map(item => item.name)).toEqual([
      'stable',
      'middle',
      'high-return',
    ])
  })

  it('uses the shared evaluation context instead of strategy-specific evaluation ids', () => {
    const comparable = analyzeEvaluationComparability([
      evaluatedItem('alpha', 'alpha-run', 'protocol-2024'),
      evaluatedItem('beta', 'beta-run', 'protocol-2024'),
    ])
    const incompatible = analyzeEvaluationComparability([
      evaluatedItem('alpha', 'alpha-run', 'protocol-2024'),
      evaluatedItem('beta', 'beta-run', 'protocol-2025'),
    ])

    expect(comparable.status).toBe('comparable')
    expect(incompatible.status).toBe('incompatible')
  })

  it('hides facets with fewer than two meaningful values', () => {
    expect(hasMeaningfulFilter([
      { label: '全部', value: 'all' },
      { label: '策略库', value: 'library' },
    ])).toBe(false)
    expect(hasMeaningfulFilter([
      { label: '全部', value: 'all' },
      { label: '动量', value: 'momentum' },
      { label: '反转', value: 'reversal' },
    ])).toBe(true)
  })

  it('normalizes invalid cache generations as stale and matches stale facets', () => {
    const stale: StrategyLibraryItem = {
      name: 'stale-result',
      status: 'stale',
      cache_status: 'invalid',
    }
    const ready: StrategyLibraryItem = {
      name: 'ready-result',
      status: 'completed',
      cache_status: 'valid',
    }

    expect(catalogItemState(stale)).toBe('stale')
    expect(matchesCatalogStatus(stale, 'stale')).toBe(true)
    expect(matchesCatalogStatus(stale, 'invalid')).toBe(true)
    expect(catalogItemState(ready)).toBe('ready')
    expect(matchesCatalogStatus(ready, 'completed')).toBe(true)
  })
})
