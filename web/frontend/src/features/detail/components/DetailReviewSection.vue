<script setup lang="ts">
import { Activity, ListChecks, RefreshCw } from '@lucide/vue'
import PanelSummaryCard from '../../../components/PanelSummaryCard.vue'
import CollapsiblePanel from './CollapsiblePanel.vue'
import DetailDataTable from './DetailDataTable.vue'
import DetailSectionHeader from './DetailSectionHeader.vue'
import { useDetailPageContext } from '../detailPageContext'

const context = useDetailPageContext()
</script>

<template>
  <section
    id="review"
    class="detail-section"
    tabindex="-1"
    aria-label="交易复盘"
  >
    <DetailSectionHeader
      index="05"
      title="交易复盘"
      description="回看最近信号、持仓周期和主要个股贡献。"
    >
      <template #icon><ListChecks :size="19" /></template>
    </DetailSectionHeader>

    <div class="detail-primary-grid review-primary-grid">
      <section
        id="review-signals"
        class="panel recent-signals-panel detail-subsection-anchor"
        tabindex="-1"
      >
        <header class="panel-head">
          <div>
            <h2 class="panel-title"><Activity :size="17" aria-hidden="true" />最近信号</h2>
            <p class="panel-note">显示最近一次买入信号和卖出信号对应的股票列表。</p>
          </div>
        </header>

        <div v-if="context.latestSignalData.missingCode" class="empty-state detail-page-feedback">
          交易记录缺少股票代码列，无法汇总最近信号。
        </div>
        <div
          v-else-if="context.latestSignalData.buy.items.length || context.latestSignalData.sell.items.length"
          class="signal-grid"
        >
          <article
            v-for="card in context.latestSignalCards"
            :key="card.label"
            class="signal-card"
            :class="card.kind"
          >
            <div class="signal-card-head">
              <div>
                <div class="signal-card-label">{{ card.label }}</div>
                <div class="signal-card-date">{{ card.signal.date ? `最新日期：${card.signal.date}` : '暂无信号记录' }}</div>
              </div>
              <span class="status-pill">{{ card.signal.items.length }} 只</span>
            </div>
            <div class="signal-card-note">按最近一次{{ card.label }}日期聚合同一批股票。</div>
            <div v-if="card.signal.items.length" class="signal-pills">
              <span
                v-for="item in card.signal.items"
                :key="item.code"
                class="signal-pill"
              >
                {{ context.formatSignalItem(item) }}
              </span>
            </div>
            <div v-else class="signal-empty">{{ card.emptyText }}</div>
          </article>
        </div>
        <div v-else class="empty-state compact-empty-state detail-page-feedback">
          暂无买入或卖出信号记录。
        </div>
      </section>

      <section
        id="review-holdings"
        class="panel review-panel detail-subsection-anchor"
        tabindex="-1"
      >
        <header class="panel-head">
          <div>
            <h2 class="panel-title"><ListChecks :size="17" aria-hidden="true" />持仓交易复盘</h2>
            <p class="panel-note">聚合展示持仓周期、仓位变化与交易贡献。</p>
          </div>
        </header>
        <div v-if="context.reviewDataLoading" class="overview-holdings-loading" aria-live="polite">
          <span class="strategy-data-spinner" aria-hidden="true"></span>正在载入持仓交易复盘...
        </div>
        <div v-else-if="context.reviewDataError" class="empty-state detail-page-feedback">
          <span>{{ context.reviewDataError }}</span>
          <button class="secondary-btn" type="button" @click="context.loadReviewData(context.getLoadToken())">
            <RefreshCw :size="14" aria-hidden="true" />重试复盘数据
          </button>
        </div>
        <div v-else-if="context.review" class="research-panel-body">
          <div class="detail-metric-strip">
            <PanelSummaryCard
              v-for="card in context.reviewMetricCards"
              :key="card.label"
              :label="card.label"
              :value="card.value"
              :value-class="card.valueClass"
            />
          </div>
        </div>
        <div v-else class="empty-state compact-empty-state detail-page-feedback">
          暂无持仓交易复盘数据。
        </div>
      </section>
    </div>

    <div v-if="context.isResearchMode" class="detail-drilldown-stack">
      <CollapsiblePanel
        id="review-stocks"
        class="detail-subsection-anchor"
        tabindex="-1"
        title="股票复盘 Top 30"
        :summary="context.reviewStocksSummary"
        :model-value="context.expandedPanels.reviewStocks"
        @update:model-value="context.setPanelExpanded('reviewStocks', $event)"
      >
        <div class="detail-subsection single-subsection">
          <div class="detail-subsection-head compact-head">
            <span class="panel-note">按总盈亏绝对值排序，便于快速定位主要贡献与拖累。</span>
          </div>
          <DetailDataTable
            :columns="context.reviewStockColumns"
            :rows="context.reviewStockRows"
            empty-text="暂无可复盘的持仓股票。"
            aria-label="股票复盘前三十"
            sortable
            column-chooser
            sticky-first-column
            compact
          />
        </div>
      </CollapsiblePanel>
    </div>
  </section>
</template>
