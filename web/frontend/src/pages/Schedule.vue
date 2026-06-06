<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('schedule.title')" :subtitle="t('schedule.subtitle')">
      <template #extra>
        <n-button type="primary" @click="openCreate">{{ t('schedule.newTask') }}</n-button>
      </template>
    </n-page-header>

    <n-alert type="info" :show-icon="false">
      {{ t('schedule.info') }}
    </n-alert>

    <n-spin :show="loading">
      <n-card>
        <n-data-table
          :columns="columns"
          :data="schedules"
          :pagination="{ pageSize: 20 }"
          :bordered="false"
          size="small"
        />
        <n-empty v-if="!schedules.length" :description="t('schedule.empty')" />
      </n-card>
    </n-spin>

    <!-- Create / Edit modal -->
    <n-modal v-model:show="showEdit" preset="card" :title="editingId ? t('schedule.editTitle') : t('schedule.createTitle')" style="width: 560px">
      <n-form label-placement="left" label-width="100">
        <n-form-item :label="t('schedule.fields.name')">
          <n-input v-model:value="form.name" :placeholder="t('schedule.fields.namePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('common.ticker')">
          <n-input v-model:value="form.ticker" :placeholder="t('schedule.fields.tickerPlaceholder')" :disabled="!!editingId" />
        </n-form-item>
        <n-form-item :label="t('schedule.fields.assetType')">
          <n-radio-group v-model:value="form.asset_type" :disabled="!!editingId">
            <n-radio value="stock">{{ t('common.stock') }}</n-radio>
            <n-radio value="crypto">{{ t('common.crypto') }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item :label="t('schedule.fields.triggerType')">
          <n-radio-group v-model:value="form.schedule_type">
            <n-radio value="interval">{{ t('schedule.triggerTypes.interval') }}</n-radio>
            <n-radio value="daily">{{ t('schedule.triggerTypes.daily') }}</n-radio>
            <n-radio value="weekly">{{ t('schedule.triggerTypes.weekly') }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="form.schedule_type === 'interval'" :label="t('schedule.fields.intervalMinutes')">
          <n-input-number v-model:value="form.interval_minutes" :min="5" :step="5" :placeholder="t('schedule.fields.intervalPlaceholder')" />
        </n-form-item>
        <n-form-item v-if="form.schedule_type !== 'interval'" :label="t('schedule.fields.timeOfDay')">
          <n-time-picker v-model:formatted-value="form.time_of_day" format="HH:mm" value-format="HH:mm" placeholder="HH:mm" />
        </n-form-item>
        <n-form-item v-if="form.schedule_type === 'weekly'" :label="t('schedule.fields.dayOfWeek')">
          <n-select
            v-model:value="form.day_of_week"
            :options="dowOptions"
            :placeholder="t('schedule.fields.dowPlaceholder')"
          />
        </n-form-item>
        <n-form-item :label="t('schedule.fields.enableAnalysts')">
          <n-checkbox-group v-model:value="form.analysts">
            <n-space>
              <n-checkbox value="market">{{ t('holdings.schedule.analystMarket') }}</n-checkbox>
              <n-checkbox value="news">{{ t('holdings.schedule.analystNews') }}</n-checkbox>
              <n-checkbox value="fundamentals">{{ t('holdings.schedule.analystFundamentals') }}</n-checkbox>
              <n-checkbox value="social">{{ t('holdings.schedule.analystSocial') }}</n-checkbox>
              <n-checkbox value="cn_social">{{ t('holdings.schedule.analystCnSocial') }}</n-checkbox>
              <n-checkbox value="event">{{ t('holdings.schedule.analystEvent') }}</n-checkbox>
            </n-space>
          </n-checkbox-group>
        </n-form-item>
        <n-form-item :label="t('schedule.fields.debateRounds')">
          <n-space>
            <n-input-number v-model:value="form.max_debate_rounds" :min="1" :max="3" />
            <n-text depth="3">{{ t('schedule.fields.researchDebate') }}</n-text>
            <n-input-number v-model:value="form.max_risk_discuss_rounds" :min="1" :max="3" />
            <n-text depth="3">{{ t('schedule.fields.riskDebate') }}</n-text>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('schedule.fields.autoTrade')">
          <n-space vertical :size="4" style="width: 100%">
            <n-switch v-model:value="form.auto_trade" />
            <n-text depth="3" style="font-size: 12px">{{ t('schedule.fields.autoTradeHint') }}</n-text>
          </n-space>
        </n-form-item>
        <n-form-item v-if="form.auto_trade" :label="t('schedule.fields.autoTradeCashPct')">
          <n-input-number
            v-model:value="form.auto_trade_cash_pct"
            :min="1"
            :max="100"
            :step="5"
          >
            <template #suffix>%</template>
          </n-input-number>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showEdit = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, h } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useMessage, useDialog, NButton, NSpace, NTag, NText } from 'naive-ui'
import api from '../api'

interface Schedule {
  id: number
  name: string | null
  ticker: string
  asset_type: string
  schedule_type: 'interval' | 'daily' | 'weekly'
  interval_minutes: number | null
  time_of_day: string | null
  day_of_week: number | null
  analysts: string  // JSON string from backend
  config_json: string
  status: 'active' | 'paused' | 'disabled'
  fail_count: number
  last_run_at: string | null
  last_analysis_id: string | null
  next_run_at: string
  from_holding: number
  auto_trade: number
  auto_trade_cash_fraction: number | null
}

const { t } = useI18n()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const schedules = ref<Schedule[]>([])
const showEdit = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)

const form = reactive({
  name: '' as string | null,
  ticker: '',
  asset_type: 'stock',
  schedule_type: 'daily' as 'interval' | 'daily' | 'weekly',
  interval_minutes: 60 as number | null,
  time_of_day: '09:30' as string | null,
  day_of_week: 0 as number | null,
  analysts: ['market', 'news', 'fundamentals'] as string[],
  max_debate_rounds: 1,
  max_risk_discuss_rounds: 1,
  auto_trade: false,
  auto_trade_cash_pct: 10,
})

const DOW_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

const dowOptions = computed(() =>
  DOW_KEYS.map((k, i) => ({ label: t(`schedule.days.${k}`), value: i })),
)

const statusLabel = computed<Record<string, { label: string; type: 'success' | 'warning' | 'error' | 'default' }>>(() => ({
  active: { label: t('schedule.status.active'), type: 'success' },
  paused: { label: t('schedule.status.paused'), type: 'warning' },
  disabled: { label: t('schedule.status.disabled'), type: 'error' },
}))

function describePattern(s: Schedule): string {
  if (s.schedule_type === 'interval') {
    return t('schedule.pattern.interval', { n: s.interval_minutes })
  }
  if (s.schedule_type === 'daily') {
    return t('schedule.pattern.daily', { time: s.time_of_day })
  }
  const dow = dowOptions.value.find(d => d.value === s.day_of_week)?.label || ''
  return t('schedule.pattern.weekly', { dow, time: s.time_of_day })
}

function formatDate(s: string | null): string {
  if (!s) return '—'
  return s.replace('T', ' ').slice(0, 16)
}

const columns = computed(() => [
  { title: t('schedule.cols.name'), key: 'name', width: 140, render(r: Schedule) { return r.name || r.ticker } },
  { title: t('schedule.cols.ticker'), key: 'ticker', width: 100 },
  {
    title: t('schedule.cols.trigger'),
    key: 'pattern',
    width: 160,
    render(r: Schedule) {
      const parts: any[] = [describePattern(r)]
      if (r.auto_trade) {
        parts.push(
          h(NTag, { size: 'tiny', type: 'warning', bordered: false, style: { marginLeft: '6px' } },
            () => t('schedule.autoTradeBadge')),
        )
      }
      return h(NSpace, { size: 2, align: 'center', wrapItem: false }, () => parts)
    },
  },
  {
    title: t('schedule.cols.analysts'),
    key: 'analysts',
    width: 200,
    render(r: Schedule) {
      let arr: string[] = []
      try { arr = JSON.parse(r.analysts) } catch { /* ignore */ }
      return arr.join(', ')
    },
  },
  {
    title: t('schedule.cols.status'),
    key: 'status',
    width: 110,
    render(r: Schedule) {
      const cfg = statusLabel.value[r.status] || { label: r.status, type: 'default' as const }
      const tag = h(NTag, { size: 'small', type: cfg.type, bordered: false }, () => cfg.label)
      if (r.fail_count > 0 && r.status === 'active') {
        return h(NSpace, { size: 4 }, () => [
          tag,
          h(NText, { depth: 3, style: { fontSize: '12px' } }, () => t('schedule.failCount', { n: r.fail_count })),
        ])
      }
      return tag
    },
  },
  { title: t('schedule.cols.nextRun'), key: 'next_run_at', width: 140, render(r: Schedule) { return formatDate(r.next_run_at) } },
  { title: t('schedule.cols.lastRun'), key: 'last_run_at', width: 140, render(r: Schedule) { return formatDate(r.last_run_at) } },
  {
    title: t('schedule.cols.actions'),
    key: 'actions',
    width: 240,
    render(r: Schedule) {
      const buttons: any[] = [
        h(NButton, { size: 'tiny', type: 'primary', onClick: () => triggerNow(r) }, () => t('schedule.btn.runNow')),
      ]
      if (r.last_analysis_id) {
        buttons.push(
          h(NButton, { size: 'tiny', onClick: () => router.push(`/report/${r.last_analysis_id}`) }, () => t('schedule.btn.viewLatest')),
        )
      }
      if (r.status === 'active') {
        buttons.push(h(NButton, { size: 'tiny', onClick: () => setStatus(r, 'paused') }, () => t('schedule.btn.pause')))
      } else if (r.status !== 'active') {
        buttons.push(h(NButton, { size: 'tiny', onClick: () => setStatus(r, 'active') }, () => t('schedule.btn.enable')))
      }
      buttons.push(h(NButton, { size: 'tiny', type: 'error', onClick: () => confirmDelete(r) }, () => t('schedule.btn.delete')))
      return h(NSpace, { size: 4 }, () => buttons)
    },
  },
])

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/schedules')
    schedules.value = data.items || []
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.name = ''
  form.ticker = ''
  form.asset_type = 'stock'
  form.schedule_type = 'daily'
  form.interval_minutes = 60
  form.time_of_day = '09:30'
  form.day_of_week = 0
  form.analysts = ['market', 'news', 'fundamentals']
  form.max_debate_rounds = 1
  form.max_risk_discuss_rounds = 1
  form.auto_trade = false
  form.auto_trade_cash_pct = 10
  showEdit.value = true
}

async function save() {
  if (!form.ticker) {
    message.warning(t('schedule.validation.ticker'))
    return
  }
  if (form.schedule_type === 'interval' && (!form.interval_minutes || form.interval_minutes < 5)) {
    message.warning(t('schedule.validation.intervalMin'))
    return
  }
  if (form.schedule_type !== 'interval' && !form.time_of_day) {
    message.warning(t('schedule.validation.timeOfDay'))
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name || null,
      ticker: form.ticker.trim().toUpperCase(),
      asset_type: form.asset_type,
      schedule_type: form.schedule_type,
      interval_minutes: form.schedule_type === 'interval' ? form.interval_minutes : null,
      time_of_day: form.schedule_type !== 'interval' ? form.time_of_day : null,
      day_of_week: form.schedule_type === 'weekly' ? form.day_of_week : null,
      analysts: form.analysts,
      max_debate_rounds: form.max_debate_rounds,
      max_risk_discuss_rounds: form.max_risk_discuss_rounds,
      auto_trade: form.auto_trade,
      auto_trade_cash_fraction: form.auto_trade ? form.auto_trade_cash_pct / 100 : null,
    }
    await api.post('/api/schedules', payload)
    message.success(t('schedule.msg.created'))
    showEdit.value = false
    await load()
  } catch (e: any) {
    message.error(t('schedule.msg.saveFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    saving.value = false
  }
}

async function triggerNow(row: Schedule) {
  try {
    const { data } = await api.post(`/api/schedules/${row.id}/trigger`)
    message.success(t('schedule.msg.started'))
    if (data.analysis_id) {
      router.push(`/progress/${data.analysis_id}`)
    }
  } catch (e: any) {
    message.error(t('schedule.msg.triggerFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  }
}

async function setStatus(row: Schedule, status: 'active' | 'paused') {
  try {
    await api.put(`/api/schedules/${row.id}`, { status })
    message.success(status === 'active' ? t('schedule.msg.enabled') : t('schedule.msg.paused'))
    await load()
  } catch (e: any) {
    message.error(t('schedule.msg.actionFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  }
}

function confirmDelete(row: Schedule) {
  dialog.warning({
    title: t('schedule.confirmDeleteTitle'),
    content: t('schedule.confirmDeleteContent', { name: row.name || row.ticker }),
    positiveText: t('common.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      await api.delete(`/api/schedules/${row.id}`)
      message.success(t('common.deleted'))
      await load()
    },
  })
}

onMounted(load)
</script>
