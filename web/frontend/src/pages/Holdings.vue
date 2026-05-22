<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('holdings.title')" :subtitle="t('holdings.subtitle')">
      <template #extra>
        <n-space>
          <n-button @click="showSchedule = true" :disabled="!holdings.length">{{ t('holdings.addToSchedule') }}</n-button>
          <n-button @click="showImport = true">{{ t('holdings.importCsv') }}</n-button>
          <n-button type="primary" @click="openCreate">{{ t('holdings.addHolding') }}</n-button>
        </n-space>
      </template>
    </n-page-header>

    <!-- Summary -->
    <n-grid :cols="4" :x-gap="16" v-if="holdings.length">
      <n-gi>
        <n-card size="small">
          <n-statistic :label="t('holdings.stat.count')" :value="holdings.length" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small">
          <n-statistic :label="t('holdings.stat.marketValue')" :value="totalMarketValue" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small">
          <n-statistic
            :label="t('holdings.stat.cumulativePnl')"
            :value="totalPnl"
            :value-style="{ color: totalPnl >= 0 ? '#d03050' : '#18a058' }"
          />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small">
          <n-statistic
            :label="t('holdings.stat.avgPnlPct')"
            :value="avgPnlPct + '%'"
            :value-style="{ color: avgPnlPct >= 0 ? '#d03050' : '#18a058' }"
          />
        </n-card>
      </n-gi>
    </n-grid>

    <n-spin :show="loading">
      <n-card>
        <n-data-table
          :columns="columns"
          :data="rows"
          :pagination="{ pageSize: 20 }"
          :bordered="false"
          size="small"
        />
        <n-empty v-if="!holdings.length" :description="t('holdings.empty')" />
      </n-card>
    </n-spin>

    <!-- Add / Edit modal -->
    <n-modal v-model:show="showEdit" preset="card" :title="editingId ? t('holdings.editTitle') : t('holdings.addTitle')" style="width: 520px">
      <n-form label-placement="left" label-width="100">
        <n-form-item :label="t('holdings.fields.ticker')">
          <n-input v-model:value="editForm.ticker" :placeholder="t('holdings.fields.tickerPlaceholder')" :disabled="!!editingId" />
        </n-form-item>
        <n-form-item :label="t('holdings.fields.assetType')">
          <n-radio-group v-model:value="editForm.asset_type" :disabled="!!editingId">
            <n-radio value="stock">{{ t('common.stock') }}</n-radio>
            <n-radio value="crypto">{{ t('common.crypto') }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item :label="t('holdings.fields.shares')">
          <n-input-number v-model:value="editForm.shares" :min="0" :precision="2" />
        </n-form-item>
        <n-form-item :label="t('holdings.fields.costPrice')">
          <n-input-number v-model:value="editForm.cost_price" :min="0" :precision="2" />
        </n-form-item>
        <n-form-item :label="t('holdings.fields.openDate')">
          <n-date-picker v-model:formatted-value="editForm.open_date" type="date" value-format="yyyy-MM-dd" clearable />
        </n-form-item>
        <n-form-item :label="t('holdings.fields.notes')">
          <n-input v-model:value="editForm.notes" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showEdit = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="saving" @click="saveHolding">{{ t('common.save') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Bulk schedule from holdings modal -->
    <n-modal v-model:show="showSchedule" preset="card" :title="t('holdings.schedule.title')" style="width: 540px">
      <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
        {{ t('holdings.schedule.info') }}
      </n-alert>
      <n-form label-placement="left" label-width="100">
        <n-form-item :label="t('holdings.schedule.type')">
          <n-radio-group v-model:value="schedForm.schedule_type">
            <n-radio value="daily">{{ t('holdings.schedule.daily') }}</n-radio>
            <n-radio value="weekly">{{ t('holdings.schedule.weekly') }}</n-radio>
            <n-radio value="interval">{{ t('holdings.schedule.interval') }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="schedForm.schedule_type === 'interval'" :label="t('holdings.schedule.intervalMinutes')">
          <n-input-number v-model:value="schedForm.interval_minutes" :min="5" :step="5" />
        </n-form-item>
        <n-form-item v-else :label="t('holdings.schedule.timeOfDay')">
          <n-time-picker v-model:formatted-value="schedForm.time_of_day" format="HH:mm" value-format="HH:mm" />
        </n-form-item>
        <n-form-item v-if="schedForm.schedule_type === 'weekly'" :label="t('holdings.schedule.dayOfWeek')">
          <n-select
            v-model:value="schedForm.day_of_week"
            :options="dowOptions"
          />
        </n-form-item>
        <n-form-item :label="t('holdings.schedule.enableAnalysts')">
          <n-checkbox-group v-model:value="schedForm.analysts">
            <n-space>
              <n-checkbox value="market">{{ t('holdings.schedule.analystMarket') }}</n-checkbox>
              <n-checkbox value="news">{{ t('holdings.schedule.analystNews') }}</n-checkbox>
              <n-checkbox value="fundamentals">{{ t('holdings.schedule.analystFundamentals') }}</n-checkbox>
              <n-checkbox value="cn_social">{{ t('holdings.schedule.analystCnSocial') }}</n-checkbox>
              <n-checkbox value="event">{{ t('holdings.schedule.analystEvent') }}</n-checkbox>
            </n-space>
          </n-checkbox-group>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showSchedule = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="schedSubmitting" @click="bulkSchedule">{{ t('holdings.schedule.createBtn') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- K-line drawer -->
    <n-drawer v-model:show="showKLine" :width="900" placement="right">
      <n-drawer-content :title="t('holdings.klineTitle', { ticker: klineTicker })">
        <KLineChart v-if="showKLine" :ticker="klineTicker" />
      </n-drawer-content>
    </n-drawer>

    <!-- CSV import modal -->
    <n-modal v-model:show="showImport" preset="card" :title="t('holdings.csv.title')" style="width: 640px">
      <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
        {{ t('holdings.csv.info') }}
      </n-alert>
      <n-input
        v-model:value="importCsv"
        type="textarea"
        :rows="10"
        :placeholder="t('holdings.csv.placeholder')"
      />
      <template #footer>
        <n-space justify="end">
          <n-button @click="showImport = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="importing" :disabled="!importCsv.trim()" @click="doImport">{{ t('holdings.csv.submit') }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, h } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useMessage, useDialog, NButton, NSpace, NTag } from 'naive-ui'
import api from '../api'
import KLineChart from '../components/KLineChart.vue'

interface Holding {
  id: number
  ticker: string
  asset_type: string
  shares: number
  cost_price: number
  open_date: string | null
  notes: string | null
  latest_analysis: {
    id: string
    signal: string | null
    confidence: number | null
    trade_date: string
    created_at: string
  } | null
}

interface Quote {
  last_price: number | null
  prev_close: number | null
  market_value: number | null
  pnl_amount: number | null
  pnl_pct: number | null
}

interface HoldingRow extends Holding {
  quote?: Quote
}

const { t } = useI18n()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const holdings = ref<Holding[]>([])
const quotes = ref<Record<number, Quote>>({})

const showEdit = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const editForm = reactive({
  ticker: '',
  asset_type: 'stock',
  shares: 0,
  cost_price: 0,
  open_date: null as string | null,
  notes: '',
})

const showImport = ref(false)
const importCsv = ref('')
const importing = ref(false)

const showKLine = ref(false)
const klineTicker = ref('')

function openKLine(row: Holding) {
  klineTicker.value = row.ticker
  showKLine.value = true
}

const showSchedule = ref(false)
const schedSubmitting = ref(false)
const schedForm = reactive({
  schedule_type: 'daily' as 'interval' | 'daily' | 'weekly',
  interval_minutes: 60 as number | null,
  time_of_day: '09:30' as string | null,
  day_of_week: 0 as number | null,
  analysts: ['market', 'news', 'fundamentals'] as string[],
})
const dowOptions = computed(() => [
  { label: t('schedule.days.mon'), value: 0 },
  { label: t('schedule.days.tue'), value: 1 },
  { label: t('schedule.days.wed'), value: 2 },
  { label: t('schedule.days.thu'), value: 3 },
  { label: t('schedule.days.fri'), value: 4 },
  { label: t('schedule.days.sat'), value: 5 },
  { label: t('schedule.days.sun'), value: 6 },
])

async function bulkSchedule() {
  schedSubmitting.value = true
  try {
    const payload = {
      schedule_type: schedForm.schedule_type,
      interval_minutes: schedForm.schedule_type === 'interval' ? schedForm.interval_minutes : null,
      time_of_day: schedForm.schedule_type !== 'interval' ? schedForm.time_of_day : null,
      day_of_week: schedForm.schedule_type === 'weekly' ? schedForm.day_of_week : null,
      analysts: schedForm.analysts,
      max_debate_rounds: 1,
      max_risk_discuss_rounds: 1,
    }
    const { data } = await api.post('/api/schedules/bulk-from-holdings', payload)
    const skipped = data.skipped?.length || 0
    if (skipped) {
      message.warning(t('holdings.schedule.createdMixed', { created: data.created, skipped }))
    } else {
      message.success(t('holdings.schedule.createdNew', { n: data.created }))
    }
    showSchedule.value = false
  } catch (e: any) {
    message.error(t('holdings.schedule.createFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    schedSubmitting.value = false
  }
}

const rows = computed<HoldingRow[]>(() =>
  holdings.value.map(hd => ({ ...hd, quote: quotes.value[hd.id] })),
)

const totalMarketValue = computed(() => {
  let sum = 0
  for (const hd of holdings.value) {
    const q = quotes.value[hd.id]
    if (q?.market_value != null) sum += q.market_value
  }
  return sum.toFixed(2)
})

const totalPnl = computed(() => {
  let sum = 0
  for (const hd of holdings.value) {
    const q = quotes.value[hd.id]
    if (q?.pnl_amount != null) sum += q.pnl_amount
  }
  return Number(sum.toFixed(2))
})

const avgPnlPct = computed(() => {
  const pcts: number[] = []
  for (const hd of holdings.value) {
    const q = quotes.value[hd.id]
    if (q?.pnl_pct != null) pcts.push(q.pnl_pct)
  }
  if (!pcts.length) return 0
  return Number((pcts.reduce((a, b) => a + b, 0) / pcts.length).toFixed(2))
})

const columns = computed(() => [
  { title: t('holdings.cols.ticker'), key: 'ticker', width: 110 },
  {
    title: t('holdings.cols.sharesCost'),
    key: 'shares',
    width: 130,
    render(row: HoldingRow) {
      return `${row.shares} × ${row.cost_price}`
    },
  },
  {
    title: t('holdings.cols.last'),
    key: 'last_price',
    width: 90,
    render(row: HoldingRow) {
      const v = row.quote?.last_price
      return v != null ? v.toFixed(2) : '—'
    },
  },
  {
    title: t('holdings.cols.marketValue'),
    key: 'market_value',
    width: 110,
    render(row: HoldingRow) {
      const v = row.quote?.market_value
      return v != null ? v.toFixed(2) : '—'
    },
  },
  {
    title: t('holdings.cols.pnl'),
    key: 'pnl_amount',
    width: 110,
    render(row: HoldingRow) {
      const v = row.quote?.pnl_amount
      const pct = row.quote?.pnl_pct
      if (v == null) return '—'
      const color = v >= 0 ? '#d03050' : '#18a058'
      return h('span', { style: { color } }, `${v >= 0 ? '+' : ''}${v.toFixed(2)} (${pct?.toFixed(2)}%)`)
    },
  },
  {
    title: t('holdings.cols.latestSignal'),
    key: 'latest_analysis',
    width: 160,
    render(row: HoldingRow) {
      const a = row.latest_analysis
      if (!a) return h(NTag, { size: 'small', bordered: false }, () => '—')
      const type = a.signal === 'BUY' ? 'success' : a.signal === 'SELL' ? 'error' : 'warning'
      return h(NTag, {
        size: 'small',
        type,
        bordered: false,
        style: { cursor: 'pointer' },
        onClick: () => router.push(`/report/${a.id}`),
      }, () => `${a.signal || 'N/A'} · ${a.trade_date}`)
    },
  },
  { title: t('holdings.cols.openDate'), key: 'open_date', width: 110 },
  { title: t('holdings.cols.notes'), key: 'notes', ellipsis: { tooltip: true } },
  {
    title: t('holdings.cols.actions'),
    key: 'actions',
    width: 260,
    render(row: HoldingRow) {
      return h(NSpace, { size: 4 }, () => [
        h(NButton, { size: 'tiny', type: 'primary', onClick: () => analyzeHolding(row) }, () => t('holdings.btn.analyze')),
        h(NButton, { size: 'tiny', onClick: () => openKLine(row) }, () => t('holdings.btn.kline')),
        h(NButton, { size: 'tiny', onClick: () => openEdit(row) }, () => t('holdings.btn.edit')),
        h(NButton, { size: 'tiny', type: 'error', onClick: () => confirmDelete(row) }, () => t('holdings.btn.delete')),
      ])
    },
  },
])

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/holdings')
    holdings.value = data.items || []
    // Fire quote requests in parallel and patch into reactive quotes map.
    await Promise.all(
      holdings.value.map(async hd => {
        try {
          const { data: q } = await api.get(`/api/holdings/${hd.id}/quote`)
          quotes.value[hd.id] = q
        } catch {
          // best-effort — UI handles undefined quotes
        }
      }),
    )
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  editForm.ticker = ''
  editForm.asset_type = 'stock'
  editForm.shares = 0
  editForm.cost_price = 0
  editForm.open_date = null
  editForm.notes = ''
  showEdit.value = true
}

function openEdit(row: Holding) {
  editingId.value = row.id
  editForm.ticker = row.ticker
  editForm.asset_type = row.asset_type
  editForm.shares = row.shares
  editForm.cost_price = row.cost_price
  editForm.open_date = row.open_date
  editForm.notes = row.notes || ''
  showEdit.value = true
}

async function saveHolding() {
  if (!editForm.ticker || editForm.shares <= 0 || editForm.cost_price <= 0) {
    message.warning(t('holdings.saveValidation'))
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await api.put(`/api/holdings/${editingId.value}`, {
        shares: editForm.shares,
        cost_price: editForm.cost_price,
        open_date: editForm.open_date,
        notes: editForm.notes,
      })
      message.success(t('common.updated'))
    } else {
      await api.post('/api/holdings', { ...editForm })
      message.success(t('common.added'))
    }
    showEdit.value = false
    await load()
  } catch (e: any) {
    message.error(t('holdings.saveFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    saving.value = false
  }
}

function confirmDelete(row: Holding) {
  dialog.warning({
    title: t('holdings.confirmDeleteTitle'),
    content: t('holdings.confirmDeleteContent', { ticker: row.ticker }),
    positiveText: t('common.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      await api.delete(`/api/holdings/${row.id}`)
      message.success(t('common.deleted'))
      await load()
    },
  })
}

function analyzeHolding(row: Holding) {
  // Pre-fill the new-analysis form via query params.
  router.push({
    path: '/analyze',
    query: {
      ticker: row.ticker,
      asset_type: row.asset_type,
    },
  })
}

async function doImport() {
  importing.value = true
  try {
    const { data } = await api.post('/api/holdings/import', {
      csv_text: importCsv.value,
      asset_type: 'stock',
    })
    if (data.errors?.length) {
      message.warning(t('holdings.csv.partialMsg', { created: data.created, skipped: data.errors.length }))
    } else {
      message.success(t('holdings.csv.okMsg', { n: data.created }))
    }
    showImport.value = false
    importCsv.value = ''
    await load()
  } catch (e: any) {
    message.error(t('holdings.csv.failed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>
