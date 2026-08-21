<script setup lang="ts">
import type { TableRow } from '../../../types/library'
import DataTable from '../../../components/DataTable.vue'
import { detailTableCellPresentation } from '../tradingTablePresentation'

interface DetailDataTableColumn {
  key: string
  label: string
  align?: 'left' | 'center' | 'right'
  minWidth?: number | string
  sortable?: boolean
  defaultVisible?: boolean
}

defineOptions({ inheritAttrs: false })

defineProps<{
  columns: DetailDataTableColumn[]
  rows: TableRow[]
}>()
</script>

<template>
  <DataTable
    v-bind="$attrs"
    :columns="columns"
    :rows="rows"
    :cell-presentation="detailTableCellPresentation"
  />
</template>
