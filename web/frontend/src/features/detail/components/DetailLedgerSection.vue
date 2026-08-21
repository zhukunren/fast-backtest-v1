<script setup lang="ts">
import { ChevronRight, ListChecks, ReceiptText, RefreshCw, TableProperties } from '@lucide/vue'
import PanelSummaryCard from '../../../components/PanelSummaryCard.vue'
import CollapsiblePanel from './CollapsiblePanel.vue'
import DetailDataTable from './DetailDataTable.vue'
import DetailSectionHeader from './DetailSectionHeader.vue'
import { useDetailPageContext } from '../detailPageContext'

const context = useDetailPageContext()
</script>

<template>
  <section
    id="details"
    class="detail-section"
    tabindex="-1"
    aria-label="成交账单"
  >
    <DetailSectionHeader
      index="04"
      title="成交账单"
      description="核对全周期逐笔成交、订单流水与资金变化。"
    >
      <template #icon><ReceiptText :size="19" /></template>
    </DetailSectionHeader>

    <section v-if="context.fullTradesLoading || !context.fullTradesLoaded" class="panel compact-empty-panel overview-holdings-loading">
      <span v-if="context.fullTradesLoading" class="strategy-data-spinner" aria-hidden="true"></span>
      <span>{{ context.fullDataError || (context.fullTradesLoading ? '正在载入全周期成交记录...' : '正在准备全周期成交记录...') }}</span>
      <button v-if="context.fullDataError" class="secondary-btn" type="button" @click="context.ensureFullDataForSection('details')">
        <RefreshCw :size="14" aria-hidden="true" />重试
      </button>
    </section>

    <section
      v-else-if="!context.executionRows.length && !context.orderRows.length"
      id="details-summary"
      class="panel compact-empty-panel empty-state detail-page-feedback detail-subsection-anchor"
      tabindex="-1"
    >
      当前策略没有可用的逐笔成交或订单记录。
    </section>

    <template v-else>
    <section
      id="details-summary"
      class="panel ledger-summary-panel detail-subsection-anchor"
      tabindex="-1"
    >
      <header class="panel-head">
        <div>
          <h2 class="panel-title"><ReceiptText :size="17" aria-hidden="true" />全周期成交概览</h2>
          <p class="panel-note">{{ context.ledgerDateRangeText }}</p>
        </div>
      </header>
      <div class="detail-metric-strip">
        <PanelSummaryCard
          v-for="card in context.ledgerSummaryCards"
          :key="card.label"
          :label="card.label"
          :value="card.value"
        />
      </div>
    </section>

    <section v-if="!context.isResearchMode" class="panel ledger-preview-panel">
      <header class="panel-head">
        <div>
          <h2 class="panel-title"><ListChecks :size="17" aria-hidden="true" />最近成交</h2>
          <p class="panel-note">{{ context.executionPreviewSummary }}</p>
        </div>
        <button class="secondary-btn ledger-preview-action" type="button" @click="context.openAllExecutionRecords">
          查看全部成交<ChevronRight :size="16" aria-hidden="true" />
        </button>
      </header>
      <DetailDataTable
        :columns="context.executionColumns"
        :rows="context.latestExecutionRows"
        :empty-text="context.fullTradesLoading ? '正在加载逐笔成交记录...' : '暂无逐笔成交记录。'"
        aria-label="最近五条逐笔成交"
        sticky-first-column
        compact
      />
    </section>

    <div v-if="context.isResearchMode" class="detail-drilldown-stack">
      <CollapsiblePanel
        id="details-executions"
        class="detail-subsection-anchor"
        tabindex="-1"
        title="逐笔成交"
        :summary="context.executionPanelSummary"
        :model-value="context.expandedPanels.executionRecords"
        @update:model-value="context.setPanelExpanded('executionRecords', $event)"
      >
        <div class="detail-subsection single-subsection">
          <div class="detail-subsection-head">
            <div class="detail-toolbar detail-toolbar-inline ledger-toolbar">
              <label class="ledger-search-control">
                <span class="sr-only">搜索逐笔成交</span>
                <input
                  v-model="context.executionSearch"
                  class="backtest-input ledger-search-input"
                  type="search"
                  placeholder="搜索日期、代码、方向或状态"
                  aria-label="搜索逐笔成交"
                />
              </label>
              <span class="panel-note">{{ context.executionPanelSummary }}</span>
              <button class="secondary-btn" type="button" title="导出当前筛选结果" :disabled="!context.canExportExecutionCsv" @click="context.exportExecutionRecordsCsv">
                <TableProperties :size="16" aria-hidden="true" />导出 CSV
              </button>
            </div>
          </div>
          <DetailDataTable
            :columns="context.executionColumns"
            :rows="context.filteredExecutionRows"
            :empty-text="context.executionRows.length ? '当前搜索没有匹配的逐笔成交记录。' : '暂无逐笔成交记录。'"
            :page-size="context.ledgerPageSize"
            aria-label="逐笔成交"
            sortable
            column-chooser
            sticky-first-column
            compact
          />
        </div>
      </CollapsiblePanel>

      <CollapsiblePanel
        id="details-orders"
        class="detail-subsection-anchor"
        tabindex="-1"
        title="订单流水"
        :summary="context.orderPanelSummary"
        :model-value="context.expandedPanels.orderRecords"
        @update:model-value="context.setPanelExpanded('orderRecords', $event)"
      >
        <div class="detail-subsection single-subsection">
          <div class="detail-subsection-head">
            <div class="detail-toolbar detail-toolbar-inline ledger-toolbar">
              <label class="ledger-search-control">
                <span class="sr-only">搜索订单流水</span>
                <input
                  v-model="context.orderSearch"
                  class="backtest-input ledger-search-input"
                  type="search"
                  placeholder="搜索日期、代码、方向或状态"
                  aria-label="搜索订单流水"
                />
              </label>
              <span class="panel-note">{{ context.orderPanelSummary }}</span>
              <button class="secondary-btn" type="button" title="导出当前筛选结果" :disabled="!context.canExportOrderCsv" @click="context.exportOrderRecordsCsv">
                <TableProperties :size="16" aria-hidden="true" />导出 CSV
              </button>
            </div>
          </div>
          <DetailDataTable
            :columns="context.orderColumns"
            :rows="context.filteredOrderRows"
            :empty-text="context.orderRows.length ? '当前搜索没有匹配的订单流水。' : '当前策略没有订单流水数据。'"
            :page-size="context.ledgerPageSize"
            aria-label="订单流水"
            sortable
            column-chooser
            sticky-first-column
            compact
          />
        </div>
      </CollapsiblePanel>
    </div>
    </template>
  </section>
</template>
