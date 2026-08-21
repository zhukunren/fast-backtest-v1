<script setup lang="ts">
import { computed } from 'vue'
import { ChartNoAxesCombined, CircleDollarSign, ListChecks, ReceiptText } from '@lucide/vue'
import CollapsiblePanel from './CollapsiblePanel.vue'
import DetailSectionHeader from './DetailSectionHeader.vue'
import { useDetailPageContext } from '../detailPageContext'

const context = useDetailPageContext()

const rulesSummary = computed(() => {
  const sections = context.investorConditionSections
  const groupCount = sections.reduce((total, section) => total + section.groups.length, 0)
  const itemCount = sections.reduce(
    (total, section) => total + section.groups.reduce((sectionTotal, group) => sectionTotal + group.items.length, 0),
    0,
  )
  const indicatorText = context.investorIndicators.length ? ` · ${context.investorIndicators.length} 项指标` : ''
  return sections.length ? `${groupCount} 组 · ${itemCount} 项条件${indicatorText}` : '暂无可展示条件'
})

const ruleOverviewCards = computed(() => context.investorConditionSections.map(section => ({
  ...section,
  groupCount: section.groups.length,
  itemCount: section.groups.reduce((total, group) => total + group.items.length, 0),
})))
</script>

<template>
  <section
    id="rules"
    class="detail-section rules-section"
    tabindex="-1"
    aria-label="策略规则"
  >
    <DetailSectionHeader
      index="02"
      title="策略规则"
      description="理解策略在什么情况下入场、如何选择候选以及何时离场。"
    >
      <template #icon><ListChecks :size="19" /></template>
    </DetailSectionHeader>

    <div v-if="ruleOverviewCards.length" class="rules-overview-grid" aria-label="策略规则摘要">
      <article
        v-for="section in ruleOverviewCards"
        :key="section.key"
        class="rule-overview-card"
        :class="`is-${section.key}`"
      >
        <span class="rule-overview-icon" aria-hidden="true">
          <CircleDollarSign v-if="section.key === 'buy'" :size="19" />
          <ReceiptText v-else :size="19" />
        </span>
        <div class="rule-overview-copy">
          <h3>{{ section.label }}</h3>
          <p>{{ section.summary }}</p>
        </div>
        <dl class="rule-overview-stats">
          <div><dt>条件组</dt><dd>{{ section.groupCount }}</dd></div>
          <div><dt>规则项</dt><dd>{{ section.itemCount }}</dd></div>
        </dl>
      </article>
    </div>

    <CollapsiblePanel
      v-if="context.investorConditionSections.length"
      id="rules-conditions"
      class="detail-subsection-anchor rules-collapsible-panel"
      tabindex="-1"
      title="完整买入与卖出条件"
      :summary="rulesSummary"
      :model-value="context.expandedPanels.strategyRules"
      @update:model-value="context.setPanelExpanded('strategyRules', $event)"
    >
      <div class="strategy-rules-panel">
        <div class="investor-condition-grid">
          <article
            v-for="section in context.investorConditionSections"
            :key="section.key"
            class="investor-condition-section"
            :class="`is-${section.key}`"
            :aria-labelledby="`investor-${section.key}-title`"
          >
            <header class="investor-condition-head">
              <span class="investor-condition-icon" aria-hidden="true">
                <CircleDollarSign v-if="section.key === 'buy'" :size="18" />
                <ReceiptText v-else :size="18" />
              </span>
              <div>
                <h3 :id="`investor-${section.key}-title`">{{ section.label }}</h3>
                <p>{{ section.summary }}</p>
              </div>
            </header>

            <div class="investor-condition-groups">
              <section v-for="group in section.groups" :key="`${section.key}-${group.title}`" class="investor-condition-group">
                <header>
                  <h4>{{ group.title }}</h4>
                  <span v-if="group.logic">{{ group.logic }}</span>
                </header>
                <ul>
                  <li
                    v-for="(item, index) in group.items"
                    :key="`${group.title}-${index}-${item.title || item.description}`"
                    class="investor-condition-item"
                  >
                    <strong v-if="item.title">{{ item.title }}</strong>
                    <p>{{ item.description }}</p>
                    <small v-if="item.example">{{ item.example }}</small>
                  </li>
                </ul>
              </section>
            </div>
          </article>
        </div>

        <section
          v-if="context.investorIndicators.length"
          class="strategy-indicator-section"
          aria-labelledby="strategy-indicators-title"
        >
          <header class="strategy-indicator-head">
            <span class="strategy-indicator-icon" aria-hidden="true">
              <ChartNoAxesCombined :size="18" />
            </span>
            <div>
              <h3 id="strategy-indicators-title">指标说明</h3>
              <p>这些指标用于判断市场环境、个股强弱和交易风险。</p>
            </div>
            <span class="strategy-indicator-count">{{ context.investorIndicators.length }} 项</span>
          </header>

          <div class="strategy-indicator-grid">
            <article v-for="indicator in context.investorIndicators" :key="indicator.name" class="strategy-indicator-card">
              <header>
                <h4>{{ indicator.name }}</h4>
                <p v-if="indicator.purpose">{{ indicator.purpose }}</p>
              </header>

              <dl class="strategy-indicator-facts">
                <div v-if="indicator.formula.length">
                  <dt>计算方式</dt>
                  <dd>
                    <code v-for="line in indicator.formula" :key="line">{{ line }}</code>
                  </dd>
                </div>
                <div v-if="indicator.variables.length">
                  <dt>变量口径</dt>
                  <dd><span v-for="line in indicator.variables" :key="line">{{ line }}</span></dd>
                </div>
                <div v-if="indicator.parameters.length">
                  <dt>参数范围</dt>
                  <dd><span v-for="line in indicator.parameters" :key="line">{{ line }}</span></dd>
                </div>
              </dl>

              <div v-if="indicator.usage.length" class="strategy-indicator-list">
                <strong>在规则中的作用</strong>
                <ul><li v-for="line in indicator.usage" :key="line">{{ line }}</li></ul>
              </div>
              <div v-if="indicator.notes.length" class="strategy-indicator-list is-note">
                <strong>阅读提示</strong>
                <ul><li v-for="line in indicator.notes" :key="line">{{ line }}</li></ul>
              </div>
            </article>
          </div>
        </section>
      </div>
    </CollapsiblePanel>

    <div v-else class="panel compact-empty-panel empty-state detail-page-feedback">
      暂无可展示的策略条件。
    </div>
  </section>
</template>
