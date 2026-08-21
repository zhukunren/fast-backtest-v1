<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import {
  Activity,
  BadgeCheck,
  ChartNoAxesCombined,
  CircleCheck,
  Info,
  ShieldCheck,
  TriangleAlert,
} from '@lucide/vue'
import CollapsiblePanel from './CollapsiblePanel.vue'
import DetailDataTable from './DetailDataTable.vue'
import DetailSectionHeader from './DetailSectionHeader.vue'
import { useDetailPageContext } from '../detailPageContext'

const DrawdownChartPanel = defineAsyncComponent(() => import('../../../components/DrawdownChartPanel.vue'))
const ReturnHeatmapPanel = defineAsyncComponent(() => import('../../../components/ReturnHeatmapPanel.vue'))
const TradeDistributionPanel = defineAsyncComponent(() => import('../../../components/TradeDistributionPanel.vue'))

const context = useDetailPageContext()
</script>

<template>
  <section
    id="performance"
    class="detail-section"
    tabindex="-1"
    aria-label="研究分析"
  >
    <DetailSectionHeader
      index="06"
      title="研究分析"
      description="从风险、交易分布、样本质量和市场适应性验证策略。"
    >
      <template #icon><ChartNoAxesCombined :size="19" /></template>
      <template #actions>
        <span class="section-context-chip">{{ context.activeRangeOption.label }} · {{ context.visibleDateRangeText }}</span>
      </template>
    </DetailSectionHeader>

    <section v-if="context.activeRange !== 'all'" class="panel performance-interval-panel performance-interval-wide">
      <header class="panel-head">
        <div>
          <h2 class="panel-title"><Activity :size="17" aria-hidden="true" />当前区间摘要</h2>
          <p class="panel-note">{{ context.visibleDateRangeText }}</p>
        </div>
        <span class="performance-interval-range">{{ context.activeRangeOption.label }}</span>
      </header>
      <div class="detail-metric-strip">
        <article v-for="item in context.performanceIntervalMetrics" :key="item.label">
          <span>{{ item.label }}</span>
          <strong :class="item.valueClass">{{ item.value }}</strong>
          <small>{{ item.note }}</small>
        </article>
      </div>
    </section>

    <div class="detail-primary-grid performance-research-grid">
      <section class="panel risk-card insight-panel overview-risk-panel">
        <header class="panel-head">
          <div>
            <h2 class="panel-title"><ShieldCheck :size="18" aria-hidden="true" />研究质量与风险</h2>
            <p class="panel-note">全周期样本质量、交易表现与风险标签。</p>
          </div>
        </header>

        <div v-if="context.analysis" class="research-panel-body">
          <div class="risk-score-row">
            <div>
              <strong :class="context.researchScoreClass">{{ context.formatPlainNumber(context.researchScore?.value, 1) }}</strong>
              <span>研究评分</span>
            </div>
            <div>
              <strong :class="context.researchScoreClass">{{ context.researchScore?.grade || '-' }}</strong>
              <span>研究评级</span>
            </div>
          </div>

          <dl class="risk-fact-grid">
            <div v-for="item in context.researchFactCards" :key="item.label">
              <dt>{{ item.label }}</dt>
              <dd>{{ item.value }}</dd>
            </div>
          </dl>

          <div class="risk-diagnostic-block">
            <h3>诊断</h3>
            <div class="risk-diagnostic-list">
              <div
                v-for="item in context.displayResearchDiagnostics"
                :key="item.code"
                :class="`diagnostic-${item.severity || 'info'}`"
              >
                <TriangleAlert v-if="item.severity === 'danger' || item.severity === 'warning'" :size="16" aria-hidden="true" />
                <Info v-else :size="16" aria-hidden="true" />
                <span>{{ item.label }}</span>
              </div>
              <div v-for="item in context.researchWarnings" :key="item.code" class="diagnostic-info">
                <Info :size="16" aria-hidden="true" />
                <span>{{ item.label }}</span>
              </div>
              <div v-if="!context.displayResearchDiagnostics.length && !context.researchWarnings.length" class="diagnostic-ok">
                <BadgeCheck :size="16" aria-hidden="true" />
                <span>暂无显著风险标签</span>
              </div>
              <div class="diagnostic-ok">
                <CircleCheck :size="16" aria-hidden="true" />
                <span>样本数量 {{ context.analysis.sample_quality?.date_count ? '可用' : '待补充' }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="empty-state compact-empty-state detail-page-feedback">
          暂无研究分析缓存。
        </div>
      </section>

      <section class="panel overview-regime-panel">
        <header class="panel-head">
          <div>
            <h2 class="panel-title"><ChartNoAxesCombined :size="17" aria-hidden="true" />市场适应性</h2>
            <p class="panel-note">全周期按市场状态拆分收益、超额与回撤。</p>
          </div>
        </header>
        <div v-if="context.marketStateBars.length" class="overview-regime-list">
          <article v-for="item in context.marketStateBars" :key="item.label" class="overview-regime-row">
            <div class="overview-regime-heading">
              <span><i :class="`is-${item.tone}`"></i>{{ item.label }}</span>
              <small>{{ item.dayCount }} 天 · {{ item.percentage.toFixed(1) }}%</small>
            </div>
            <dl class="overview-regime-metrics">
              <div>
                <dt>策略收益</dt>
                <dd :class="context.metricTrendClass(item.returnValue)">{{ item.returnText }}</dd>
              </div>
              <div>
                <dt>超额收益</dt>
                <dd>{{ item.excessReturnText }}</dd>
              </div>
              <div>
                <dt>最大回撤</dt>
                <dd class="metric-down">{{ item.maxDrawdownText }}</dd>
              </div>
            </dl>
            <div class="overview-regime-track" aria-hidden="true">
              <i :class="`is-${item.tone}`" :style="{ width: `${item.percentage}%` }"></i>
            </div>
          </article>
        </div>
        <div v-else class="empty-state compact-empty-state detail-page-feedback">暂无市场状态切片。</div>
      </section>
    </div>

    <div
      id="performance-distribution"
      class="detail-primary-grid performance-secondary-grid detail-subsection-anchor"
      tabindex="-1"
    >
      <template v-if="context.performanceChartsActivated && context.fullTradesLoaded">
        <DrawdownChartPanel
          :data="context.visibleNetValue"
          :baseline-value="context.activeRange === 'all' ? 1 : null"
        />

        <TradeDistributionPanel
          :trades="context.visibleTradeRecordsForDistribution"
          title="交易分布"
        />
      </template>
      <template v-else>
        <section v-for="index in 2" :key="index" class="panel overview-holdings-loading" aria-hidden="true">
          <span class="strategy-data-spinner"></span>
        </section>
      </template>
    </div>

    <div v-if="context.isResearchMode" class="detail-drilldown-stack">
      <CollapsiblePanel
        id="performance-risk"
        class="detail-subsection-anchor"
        tabindex="-1"
        title="高级风险指标"
        summary="卡玛、索提诺、信息比率、修复期与交易质量"
        :model-value="context.expandedPanels.advancedMetrics"
        @update:model-value="context.setPanelExpanded('advancedMetrics', $event)"
      >
        <div class="detail-metric-strip performance-advanced-metrics">
          <article v-for="item in context.performanceMetricCells" :key="item.label">
            <span>{{ item.label }}</span>
            <strong :class="item.valueClass">{{ item.value }}</strong>
            <small>{{ item.note }}</small>
          </article>
        </div>
      </CollapsiblePanel>
      <CollapsiblePanel
        title="评分拆解"
        :summary="context.scoreDetailsSummary"
        :model-value="context.expandedPanels.scoreDetails"
        @update:model-value="context.setPanelExpanded('scoreDetails', $event)"
      >
        <div class="detail-subsection single-subsection">
          <div class="detail-subsection-head">
            <span class="panel-note">评分由收益、回撤、稳定性、交易质量、样本质量与市场状态稳健性加权生成。</span>
          </div>
          <DetailDataTable
            :columns="context.scoreComponentColumns"
            :rows="context.scoreComponentRows"
            empty-text="暂无评分拆解。"
            aria-label="评分拆解"
            sortable
            sticky-first-column
            compact
          />
        </div>
      </CollapsiblePanel>

      <CollapsiblePanel
        id="performance-cycles"
        class="detail-subsection-anchor"
        tabindex="-1"
        title="收益热力图"
        :summary="context.returnHeatmapSummary"
        :model-value="context.expandedPanels.returnHeatmap"
        @update:model-value="context.setPanelExpanded('returnHeatmap', $event)"
      >
        <ReturnHeatmapPanel
          :data="context.visibleNetValue"
          :baseline-value="context.activeRange === 'all' ? 1 : null"
          embedded
        />
      </CollapsiblePanel>

      <CollapsiblePanel
        title="市场状态切片"
        :summary="context.marketStateSummary"
        :model-value="context.expandedPanels.marketState"
        @update:model-value="context.setPanelExpanded('marketState', $event)"
      >
        <div class="detail-subsection single-subsection">
          <div class="detail-subsection-head">
            <span class="panel-note">按基准 20 日趋势切分上涨市、震荡市、下跌市；基准缺失时使用策略净值代用。</span>
          </div>
          <DetailDataTable
            :columns="context.marketStateColumns"
            :rows="context.marketStateRows"
            empty-text="暂无市场状态切片。"
            aria-label="市场状态切片"
            sortable
            sticky-first-column
            compact
          />
        </div>
      </CollapsiblePanel>

      <CollapsiblePanel
        title="年度表现"
        :summary="context.yearlyPerformanceSummary"
        :model-value="context.expandedPanels.yearlyPerformance"
        @update:model-value="context.setPanelExpanded('yearlyPerformance', $event)"
      >
        <div class="detail-subsection single-subsection">
          <div class="detail-subsection-head">
            <span class="panel-note">按净值点所在年度汇总收益、回撤和涨跌日数。</span>
          </div>
          <DetailDataTable
            :columns="context.researchYearlyColumns"
            :rows="context.researchYearlyRows"
            empty-text="暂无可展示的年度表现。"
            aria-label="年度表现"
            sortable
            sticky-first-column
            compact
          />
        </div>
      </CollapsiblePanel>
    </div>
  </section>
</template>
