<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('history.title')" :subtitle="t('history.subtitle')">
      <template #extra>
        <n-button @click="$router.push('/analyze')">{{ t('history.newAnalysis') }}</n-button>
      </template>
    </n-page-header>

    <!-- Filters -->
    <n-card size="small">
      <n-space>
        <n-input v-model:value="filters.ticker" :placeholder="t('history.tickerPlaceholder')" clearable style="width: 120px" @change="load" />
        <n-select v-model:value="filters.signal" :options="signalOptions" clearable :placeholder="t('history.signal')" style="width: 100px" @update:value="load" />
        <n-date-picker v-model:formatted-value="filters.dateFrom" type="date" :placeholder="t('history.dateFrom')" value-format="yyyy-MM-dd" clearable @update:formatted-value="load" />
        <n-date-picker v-model:formatted-value="filters.dateTo" type="date" :placeholder="t('history.dateTo')" value-format="yyyy-MM-dd" clearable @update:formatted-value="load" />
      </n-space>
    </n-card>

    <!-- Table -->
    <n-data-table :columns="columns" :data="items" :loading="loading" :pagination="pagination" remote @update:page="onPageChange" />
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, h } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { NButton, NTag, NSpace, useDialog } from 'naive-ui'
import api from '../api'

const { t } = useI18n()
const router = useRouter()
const dialog = useDialog()
const loading = ref(false)
const items = ref<any[]>([])

const filters = reactive({
  ticker: '',
  signal: null as string | null,
  dateFrom: null as string | null,
  dateTo: null as string | null,
})

const pagination = reactive({ page: 1, pageSize: 20, itemCount: 0 })

const signalOptions = [
  { label: 'BUY', value: 'BUY' },
  { label: 'HOLD', value: 'HOLD' },
  { label: 'SELL', value: 'SELL' },
]

const columns = computed(() => [
  { title: t('history.cols.ticker'), key: 'ticker', width: 100 },
  { title: t('history.cols.date'), key: 'trade_date', width: 120 },
  {
    title: t('history.cols.signal'), key: 'signal', width: 80,
    render: (row: any) => h(NTag, { type: signalType(row.signal), size: 'small' }, () => row.signal || row.status),
  },
  { title: t('history.cols.confidence'), key: 'confidence', width: 80, render: (row: any) => row.confidence ? `${row.confidence}%` : '-' },
  { title: t('history.cols.createdAt'), key: 'created_at', width: 180 },
  {
    title: t('history.cols.actions'), key: 'actions', width: 150,
    render: (row: any) => h(NSpace, { size: 'small' }, () => [
      h(NButton, { size: 'tiny', onClick: () => router.push(`/report/${row.id}`) }, () => t('history.detail')),
      h(NButton, { size: 'tiny', type: 'error', onClick: () => confirmDelete(row.id) }, () => t('history.delete')),
    ]),
  },
])

function signalType(signal: string) {
  if (signal === 'BUY') return 'success'
  if (signal === 'SELL') return 'error'
  return 'warning'
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/history', {
      params: {
        ticker: filters.ticker || undefined,
        signal: filters.signal || undefined,
        from: filters.dateFrom || undefined,
        to: filters.dateTo || undefined,
        page: pagination.page,
        size: pagination.pageSize,
      },
    })
    items.value = data.items
    pagination.itemCount = data.total
  } finally {
    loading.value = false
  }
}

function onPageChange(page: number) {
  pagination.page = page
  load()
}

function confirmDelete(id: string) {
  dialog.warning({
    title: t('history.confirmDeleteTitle'),
    content: t('history.confirmDeleteContent'),
    positiveText: t('history.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      await api.delete(`/api/reports/${id}`)
      load()
    },
  })
}

onMounted(load)
</script>
