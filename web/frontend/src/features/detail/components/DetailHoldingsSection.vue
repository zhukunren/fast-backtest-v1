<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import { CalendarDays, ChevronLeft, ChevronRight, RefreshCw, WalletCards } from '@lucide/vue'
import PanelSummaryCard from '../../../components/PanelSummaryCard.vue'
import CollapsiblePanel from './CollapsiblePanel.vue'
import DetailDataTable from './DetailDataTable.vue'
import DetailSectionHeader from './DetailSectionHeader.vue'
import { useDetailPageContext } from '../detailPageContext'

const HoldingsHeatmapPanel = defineAsyncComponent(() => import('../../../components/HoldingsHeatmapPanel.vue'))

const context = useDetailPageContext()
</script>

<template>
  <section
    id="holdings"
    class="detail-section"
    tabindex="-1"
    aria-label="历史持仓"
  >
    <DetailSectionHeader
      index="03"
      title="历史持仓"
      description="按交易日核对账户资产、持仓结构与当日成交。"
    >
      <template #icon><WalletCards :size="19" /></template>
    </DetailSectionHeader>

    <section v-if="context.fullHoldingsLoading || context.fullTradesLoading || !context.fullHoldingsLoaded" class="panel compact-empty-panel overview-holdings-loading">
      <span v-if="context.fullHoldingsLoading || context.fullTradesLoading" class="strategy-data-spinner" aria-hidden="true"></span>
      <span>{{ context.fullDataError || (context.fullHoldingsLoading || context.fullTradesLoading ? '正在载入完整历史持仓快照...' : '正在准备完整历史持仓快照...') }}</span>
      <button v-if="context.fullDataError" class="secondary-btn" type="button" @click="context.ensureFullDataForSection('holdings')">
        <RefreshCw :size="14" aria-hidden="true" />重试
      </button>
    </section>

    <section
      v-else-if="!context.holdingsDates.length"
      id="holdings-current"
      class="panel compact-empty-panel empty-state detail-page-feedback detail-subsection-anchor"
      tabindex="-1"
    >
      暂无可用的历史持仓快照。
    </section>

    <section
      v-else
      id="holdings-current"
      class="panel daily-holdings-panel detail-subsection-anchor"
      tabindex="-1"
    >
      <header class="panel-head holdings-panel-head">
        <div>
          <h2 class="panel-title"><WalletCards :size="17" aria-hidden="true" />历史账户快照</h2>
          <p class="panel-note">{{ context.holdingsStatusText }}</p>
        </div>
        <div class="detail-toolbar holdings-toolbar">
          <div v-if="context.showHoldingsViewToggle" class="range-chip-group" role="group" aria-label="持仓汇总方式">
            <button
              v-for="option in context.holdingsViewOptions"
              :key="option.value"
              class="toolbar-btn"
              type="button"
              :class="{ active: context.holdingsCurrentView === option.value }"
              :aria-pressed="context.holdingsCurrentView === option.value"
              @click="context.holdingsCurrentView = context.normalizeHoldingsCurrentView(option.value)"
            >
              {{ option.label }}
            </button>
          </div>
          <span
            class="detail-focus-date"
            :class="{ 'is-latest': context.focusDate && context.focusDate === context.latestHoldingDate }"
          >
            {{ context.focusDate === context.latestHoldingDate ? '最新持仓日' : '历史快照' }} · {{ context.focusDate }}
          </span>
          <select
            class="backtest-input detail-date-select"
            :value="context.focusDate"
            :disabled="!context.holdingsDates.length"
            aria-label="历史查看日期"
            @change="context.focusDate = ($event.target as HTMLSelectElement).value"
          >
            <option v-for="dateValue in context.holdingsDates" :key="dateValue" :value="dateValue">
              {{ dateValue }}
            </option>
          </select>
          <button
            v-if="context.latestHoldingDate && context.focusDate !== context.latestHoldingDate"
            class="secondary-btn holdings-latest-button"
            type="button"
            @click="context.focusDate = context.latestHoldingDate"
          >
            <CalendarDays :size="15" aria-hidden="true" />回到最新
          </button>
          <button
            class="secondary-btn icon-btn"
            type="button"
            :disabled="!context.canShiftBackward"
            aria-label="上一交易日"
            title="上一交易日"
            @click="context.shiftFocus(-1)"
          >
            <ChevronLeft :size="17" aria-hidden="true" />
          </button>
          <button
            class="secondary-btn icon-btn"
            type="button"
            :disabled="!context.canShiftForward"
            aria-label="下一交易日"
            title="下一交易日"
            @click="context.shiftFocus(1)"
          >
            <ChevronRight :size="17" aria-hidden="true" />
          </button>
        </div>
      </header>

      <div class="detail-subsection">
        <div class="detail-subsection-head">
          <h3>当日账户摘要</h3>
          <span class="panel-note">
            {{ context.holdingsDisplayMode === 'simple' ? '展示模式聚焦总资产、今日盈亏、持仓盈亏与今日交易。' : '研究模式展示完整账户净值、资产、现金、持仓与盈亏口径。' }}
          </span>
        </div>
        <div class="detail-metric-strip">
          <PanelSummaryCard
            v-for="card in context.holdingsSummaryCards"
            :key="card.label"
            :label="card.label"
            :value="card.value"
            :value-class="card.valueClass"
          />
        </div>
      </div>

      <div class="detail-subsection">
        <div class="detail-subsection-head">
          <h3>当日持仓</h3>
          <span class="panel-note">{{ context.holdingsCurrentNote }}</span>
        </div>
        <div v-if="context.showHoldingsExport" class="detail-toolbar detail-toolbar-inline detail-toolbar-subsection">
          <button class="secondary-btn" type="button" :disabled="!context.canExportFocusedHoldings" @click="context.exportFocusedHoldings('csv')">导出 CSV</button>
          <button class="secondary-btn" type="button" :disabled="!context.canExportFocusedHoldings" @click="context.exportFocusedHoldings('xls')">导出 Excel</button>
        </div>
        <DetailDataTable
          :columns="context.currentHoldingColumns"
          :rows="context.currentHoldingDisplayRows"
          :empty-text="context.focusDate ? '该交易日没有持仓，视为当日空仓。' : '当前没有可定位的交易日。'"
          aria-label="当日持仓"
          sortable
          column-chooser
          sticky-first-column
        />
      </div>

      <div class="detail-subsection">
        <div class="detail-subsection-head">
          <h3>当日交易记录</h3>
          <span class="panel-note">默认使用逐笔成交账单，已包含风控减仓、加仓与恢复记录。</span>
        </div>
        <div v-if="context.dailyTradeRows.length" class="detail-metric-strip">
          <PanelSummaryCard
            v-for="card in context.dailyTradeCards"
            :key="card.label"
            :label="card.label"
            :value="card.value"
          />
        </div>
        <DetailDataTable
          v-if="context.dailyTradeRows.length"
          :columns="context.dailyTradeColumns"
          :rows="context.dailyTradeRows"
          empty-text="当前焦点日没有逐笔成交记录。"
          aria-label="当日交易记录"
          sortable
          column-chooser
          sticky-first-column
        />
        <div v-else class="empty-state compact-empty-state detail-page-feedback">
          所选日期没有逐笔成交记录。
        </div>
      </div>

    </section>

    <div v-if="context.isResearchMode && context.holdingsDates.length" class="detail-drilldown-stack">
      <CollapsiblePanel
        id="holdings-history"
        class="detail-subsection-anchor"
        tabindex="-1"
        title="按日持仓历史表"
        :summary="context.holdingHistoryPanelSummary"
        :model-value="context.expandedPanels.holdingHistory"
        @update:model-value="context.handleHoldingHistoryPanelToggle"
      >
        <div v-if="context.showHoldingsHistory" class="detail-subsection single-subsection">
          <div class="detail-subsection-head compact-head">
            <span class="panel-note">{{ context.holdingsHistoryNote }}</span>
          </div>
          <div class="detail-toolbar detail-toolbar-inline detail-toolbar-subsection" role="group" aria-label="按日持仓筛选">
            <label>
              <span class="sr-only">搜索按日持仓</span>
              <input
                v-model="context.holdingsHistorySearchInput"
                class="backtest-input holdings-filter-input"
                type="search"
                placeholder="搜索日期 / 槽位 / 股票 / 方向"
              />
            </label>
            <label>
              <span class="sr-only">筛选持仓槽位</span>
              <select
                v-model="context.holdingsSlotFilter"
                class="backtest-input holdings-filter-select"
                :disabled="!context.holdingsSlotOptions.length"
              >
                <option value="all">全部槽位</option>
                <option v-for="slot in context.holdingsSlotOptions" :key="slot" :value="slot">{{ slot }}</option>
              </select>
            </label>
            <label>
              <span class="sr-only">筛选持仓方向</span>
              <select v-model="context.holdingsDirectionFilter" class="backtest-input holdings-filter-select">
                <option value="all">全部方向</option>
                <option value="buy">买入</option>
                <option value="sell">卖出</option>
                <option value="hold">持有中</option>
              </select>
            </label>
            <button class="secondary-btn" type="button" @click="context.resetHoldingsHistoryFilters">清空筛选</button>
          </div>
          <DetailDataTable
            :columns="context.holdingHistoryColumns"
            :rows="context.holdingHistoryDisplayRows"
            :empty-text="context.holdingHistoryEmptyText"
            :row-key="context.holdingHistoryRowKey"
            :page-size="50"
            aria-label="按日持仓历史"
            sortable
            column-chooser
            sticky-first-column
            compact
          />
        </div>
        <div v-else class="empty-state detail-page-feedback">
          切换到高级核对后可查看按日持仓历史表。
        </div>
      </CollapsiblePanel>

      <CollapsiblePanel
        id="holdings-heatmap"
        class="detail-subsection-anchor"
        tabindex="-1"
        title="持仓热力图"
        :summary="context.holdingsHeatmapSummary"
        :model-value="context.expandedPanels.holdingsHeatmap"
        @update:model-value="context.setPanelExpanded('holdingsHeatmap', $event)"
      >
        <HoldingsHeatmapPanel :holdings="context.flattenedHoldings" embedded />
      </CollapsiblePanel>
    </div>
  </section>
</template>
