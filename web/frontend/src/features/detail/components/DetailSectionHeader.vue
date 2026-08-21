<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  description?: string
  context?: string
  index?: string
}>(), {
  description: '',
  context: '',
  index: '',
})
</script>

<template>
  <header class="detail-section-head">
    <div class="detail-section-heading">
      <span v-if="index" class="detail-section-index" aria-hidden="true">{{ index }}</span>
      <span class="detail-section-icon" aria-hidden="true">
        <slot name="icon" />
      </span>
      <div class="detail-section-copy">
        <h2>{{ title }}</h2>
        <p v-if="description" class="panel-note">{{ description }}</p>
      </div>
    </div>
    <div v-if="$slots.actions || context" class="detail-section-actions">
      <slot name="actions" />
      <span v-if="context" class="section-context-chip">{{ context }}</span>
    </div>
  </header>
</template>
