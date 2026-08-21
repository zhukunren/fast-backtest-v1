<script setup lang="ts">
defineProps<{
  icon?: string
  title?: string
  description?: string
  actionText?: string
}>()

const emit = defineEmits<{
  action: []
}>()
</script>

<template>
  <div class="empty-state">
    <div class="empty-icon">{{ icon || '📭' }}</div>
    <h3 class="empty-title">{{ title || '暂无数据' }}</h3>
    <p class="empty-description">
      {{ description || '当前没有可显示的内容' }}
    </p>
    <button v-if="actionText" class="primary-btn" type="button" @click="emit('action')">
      {{ actionText }}
    </button>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 24px;
  text-align: center;
}

.empty-icon {
  font-size: 64px;
  margin-bottom: 16px;
  opacity: 0.6;
}

.empty-title {
  font-size: 20px;
  font-weight: 600;
  color: #0F172A;
  margin-bottom: 8px;
}

.empty-description {
  font-size: 15px;
  color: #64748B;
  line-height: 1.6;
  max-width: 400px;
  margin-bottom: 24px;
}

.primary-btn {
  padding: 12px 24px;
  border-radius: 999px;
  background: linear-gradient(180deg, #2563EB, #1D4ED8);
  color: #FFFFFF;
  border: none;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.primary-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
}
</style>
