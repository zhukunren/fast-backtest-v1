<script setup lang="ts">
defineProps<{
  label: string
  value: string
  valueClass?: string
}>()
</script>

<template>
  <article class="summary-card">
    <span class="summary-label">{{ label }}</span>
    <span class="summary-value" :class="valueClass">{{ value }}</span>
  </article>
</template>
