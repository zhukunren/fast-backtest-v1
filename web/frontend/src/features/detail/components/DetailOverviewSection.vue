<script setup lang="ts">
import { Activity, ChevronRight, WalletCards } from '@lucide/vue'
import DetailSectionHeader from './DetailSectionHeader.vue'
import { useDetailPageContext } from '../detailPageContext'

const context = useDetailPageContext()
</script>

<template>
  <section
    id="overview"
    class="detail-section current-status-section"
    tabindex="-1"
    aria-label="执行概览"
  >
    <DetailSectionHeader
      index="01"
      title="执行概览"
      description="先确认当前账户状态、最新仓位和最近一次交易动作。"
    >
      <template #icon><Activity :size="19" /></template>
    </DetailSectionHeader>

    <section class="panel overview-current-panel">
      <header class="panel-head overview-current-head">
        <div>
          <h2 class="panel-title"><WalletCards :size="17" aria-hidden="true" />最新账户与仓位</h2>
          <p class="panel-note">状态日期 {{ context.latestHoldingDate || '-' }}</p>
        </div>
        <div class="overview-current-commands">
          <span class="overview-execution-state" :class="context.overviewExecutionState.tone">
            {{ context.overviewExecutionState.label }}
          </span>
          <button class="secondary-btn" type="button" @click="context.scrollToSection('holdings')">
            历史快照<ChevronRight :size="16" aria-hidden="true" />
          </button>
        </div>
      </header>

      <div v-if="context.supportingDataLoading" class="overview-holdings-loading">
        <span class="strategy-data-spinner" aria-hidden="true"></span>正在载入当前状态...
      </div>
      <div
        v-else
        class="overview-current-grid"
        :class="{ 'has-exposure': context.overviewExposureRows.length }"
      >
        <div class="overview-current-summary">
          <div class="overview-current-facts">
            <article v-for="fact in context.overviewExecutionFacts" :key="fact.label">
              <span>{{ fact.label }}</span>
              <strong :class="fact.valueClass">{{ fact.value }}</strong>
            </article>
          </div>

          <div class="overview-recent-actions">
            <header>
              <h3>最近动作</h3>
              <button type="button" @click="context.scrollToSection('review')">
                完整信号<ChevronRight :size="15" aria-hidden="true" />
              </button>
            </header>
            <div v-if="context.latestSignalData.missingCode" class="overview-execution-note">
              交易记录缺少股票代码，无法汇总最近信号。
            </div>
            <div v-else class="overview-action-grid">
              <article v-for="card in context.latestSignalCards" :key="card.label" :class="card.kind">
                <span>{{ card.label }}</span>
                <strong>{{ card.signal.date || '暂无' }}</strong>
                <small>{{ card.signal.items.length ? `${card.signal.items.length} 只股票` : card.emptyText }}</small>
              </article>
            </div>
          </div>
        </div>

        <section v-if="context.overviewExposureRows.length" class="overview-current-exposure" aria-label="当前持仓权重前五">
          <header>
            <h3>持仓权重前 5</h3>
            <span>{{ context.overviewExposureRows.length }} 只</span>
          </header>
          <div class="overview-exposure-list">
            <article v-for="row in context.overviewExposureRows" :key="row.key">
              <div>
                <strong>{{ row.label }}</strong>
                <span>{{ context.formatPercent(row.weight) }} · {{ row.value }}</span>
              </div>
              <div class="overview-exposure-track" aria-hidden="true">
                <i :style="{ width: `${Math.min(100, row.weight * 100)}%` }"></i>
              </div>
            </article>
          </div>
        </section>
      </div>
    </section>
  </section>
</template>
