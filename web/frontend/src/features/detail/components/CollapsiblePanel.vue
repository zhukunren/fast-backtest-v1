<script setup lang="ts">
import { ChevronDown } from '@lucide/vue'
import { ref, useId, watch } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  summary?: string
  modelValue: boolean
}>(), {
  summary: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const componentId = useId()
const contentId = `collapsible-panel-${componentId}`
const triggerId = `collapsible-trigger-${componentId}`
const hasOpened = ref(props.modelValue)

watch(() => props.modelValue, value => {
  if (value) hasOpened.value = true
})

function togglePanel() {
  emit('update:modelValue', !props.modelValue)
}
</script>

<template>
  <section
    class="panel collapsible-panel collapsible-card"
    :class="{ 'is-open': props.modelValue }"
  >
    <h3 class="collapsible-panel-heading">
      <button
        :id="triggerId"
        class="collapsible-panel-trigger"
        type="button"
        :aria-expanded="props.modelValue"
        :aria-controls="contentId"
        :title="props.modelValue ? `收起${title}` : `展开${title}`"
        @click="togglePanel"
      >
        <span class="collapsible-panel-title-wrap">
          <span class="collapsible-panel-title">{{ title }}</span>
          <span v-if="summary" class="collapsible-panel-summary">{{ summary }}</span>
        </span>
        <span class="collapsible-panel-state" aria-hidden="true">
          <ChevronDown :size="16" />
        </span>
      </button>
    </h3>
    <div
      v-show="props.modelValue"
      :id="contentId"
      class="collapsible-panel-body"
      role="region"
      :aria-labelledby="triggerId"
    >
      <slot v-if="hasOpened" />
    </div>
  </section>
</template>
