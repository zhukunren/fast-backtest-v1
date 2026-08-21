<script setup lang="ts">
import { X } from '@lucide/vue'
import type { StrategyLibraryItem } from '../../../types/library'

defineProps<{
  items: StrategyLibraryItem[]
  statusText: string
  backtestStatusText: string
  canOpenCompare: boolean
  canRunSelected: boolean
  canRunAll: boolean
}>()

defineEmits<{
  remove: [name: string]
  clear: []
  openCompare: []
  runSelected: []
  runAll: []
}>()
</script>

<template>
  <section class="compare-toolbar">
    <div class="compare-toolbar-main">
      <p class="compare-toolbar-title">策略对比</p>
      <div class="compare-chip-list">
        <template v-if="items.length">
          <span
            v-for="item in items"
            :key="item.name"
            class="compare-chip"
          >
            <span>{{ item.title || item.name }}</span>
            <button
              type="button"
              :title="`移除 ${item.title || item.name}`"
              :aria-label="`移除 ${item.title || item.name}`"
              @click="$emit('remove', item.name)"
            >
              <X :size="13" aria-hidden="true" />
            </button>
          </span>
        </template>
        <span v-else class="compare-empty">当前还没有加入对比的策略。</span>
      </div>
    </div>

    <div class="compare-toolbar-side">
      <span class="toolbar-status" aria-live="polite">{{ statusText }}</span>
      <div class="toolbar-inline-status">
        <span class="toolbar-separator" aria-hidden="true"></span>
        <span class="toolbar-status" :class="{ busy: backtestStatusText !== '回测空闲' }">{{ backtestStatusText }}</span>
      </div>
      <div class="toolbar-actions">
        <button class="secondary-btn" type="button" :disabled="!canRunSelected" @click="$emit('runSelected')">回测已选</button>
        <button class="secondary-btn" type="button" :disabled="!canRunAll" @click="$emit('runAll')">回测全部</button>
        <button class="secondary-btn" type="button" :disabled="!items.length" @click="$emit('clear')">清空</button>
        <button class="primary-btn" type="button" :disabled="!canOpenCompare" @click="$emit('openCompare')">开始对比</button>
      </div>
    </div>
  </section>
</template>
