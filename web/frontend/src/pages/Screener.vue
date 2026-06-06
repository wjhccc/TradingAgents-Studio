<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('screener.title')" :subtitle="t('screener.subtitle')" />

    <!-- Goal input -->
    <n-card>
      <n-input-group>
        <n-input
          v-model:value="goal"
          :placeholder="t('screener.goalPlaceholder')"
          @keyup.enter="startScreen"
          clearable
        />
        <n-input-number v-model:value="topN" :min="5" :max="500" :step="10" style="width: 120px"
          :placeholder="t('screener.topN')" />
        <n-button type="primary" :loading="running" @click="startScreen">
          {{ running ? t('screener.running') : t('screener.start') }}
        </n-button>
      </n-input-group>
      <n-space style="margin-top: 10px" :size="12" align="center">
        <n-text depth="3">{{ t('screener.topN') }}:</n-text>
        <n-button-group size="tiny">
          <n-button v-for="p in [20, 50, 100, 200]" :key="p"
            :type="topN === p ? 'primary' : 'default'" @click="topN = p">{{ p }}</n-button>
        </n-button-group>
        <n-checkbox v-model:checked="useLlm">{{ t('screener.useLlm') }}</n-checkbox>
        <n-text depth="3">{{ t('screener.momentumPeriod') }}:</n-text>
        <n-select v-model:value="momentumPeriod" :options="periodOptions" size="small" style="width: 150px" />
        <n-select v-model:value="momentumDirection" :options="directionOptions" size="small" style="width: 160px" />
        <n-button text size="small" @click="openHistory">{{ t('screener.history') }}</n-button>
      </n-space>

      <!-- Advanced filters -->
      <n-collapse style="margin-top: 12px">
        <n-collapse-item :title="t('screener.advanced')" name="adv">
          <n-space :size="16" wrap>
            <n-form-item :label="t('screener.peMax')" label-placement="top">
              <n-input-number v-model:value="filters.pe_max" :min="0" clearable style="width: 120px" />
            </n-form-item>
            <n-form-item :label="t('screener.pbMax')" label-placement="top">
              <n-input-number v-model:value="filters.pb_max" :min="0" clearable style="width: 120px" />
            </n-form-item>
            <n-form-item :label="t('screener.mcMin')" label-placement="top">
              <n-input-number v-model:value="filters.market_cap_min" :min="0" clearable style="width: 130px" />
            </n-form-item>
            <n-form-item :label="t('screener.mcMax')" label-placement="top">
              <n-input-number v-model:value="filters.market_cap_max" :min="0" clearable style="width: 130px" />
            </n-form-item>
            <n-form-item :label="t('screener.sector')" label-placement="top">
              <n-input v-model:value="filters.sector_query" clearable style="width: 160px" />
            </n-form-item>
          </n-space>
        </n-collapse-item>
      </n-collapse>
    </n-card>

    <!-- Degraded data source banner -->
    <n-alert v-if="degraded" type="warning" :title="t('screener.degradedTitle')" closable>
      {{ t('screener.degradedBody', { source: dataSource || '新浪' }) }}
    </n-alert>

    <!-- Strategy / progress -->
    <n-card v-if="strategy || progressMsg" size="small">
      <n-space align="center" :size="10" wrap>
        <n-text strong>{{ t('screener.strategy') }}:</n-text>
        <n-tag v-for="(l, i) in strategyLabels" :key="i" size="small" type="info" :bordered="false">{{ l }}</n-tag>
        <n-tag v-if="strategy && strategy.provenance" size="small" :bordered="false">
          {{ strategy.provenance.source }}
        </n-tag>
        <n-text v-if="matched != null" depth="3">· {{ t('screener.matched') }} {{ matched }}</n-text>
        <n-tag v-if="dataSource" size="small" :bordered="false">{{ t('screener.dataSource') }}: {{ dataSource }}</n-tag>
        <n-text v-if="progressMsg" depth="3">· {{ progressMsg }}</n-text>
        <n-spin v-if="running" :size="14" />
      </n-space>
    </n-card>

    <!-- Results -->
    <n-card :title="t('screener.candidates')">
      <template #header-extra>
        <n-space :size="10" align="center" v-if="checkedKeys.length">
          <n-text depth="3">{{ t('screener.selected', { n: checkedKeys.length }) }}</n-text>
          <n-button size="small" type="primary" @click="showSizing = true">
            {{ t('screener.addToPaper') }}
          </n-button>
          <n-button size="small" @click="batchAnalyze" :loading="analyzing">
            {{ t('screener.batchAnalyze') }}
          </n-button>
          <n-button size="small" type="warning" @click="batchAutoTrade" :loading="addingSchedule">
            {{ t('screener.toSchedule') }}
          </n-button>
        </n-space>
      </template>

      <n-data-table
        :columns="columns"
        :data="candidates"
        :row-key="(r: any) => r.ticker"
        :checked-row-keys="checkedKeys"
        @update:checked-row-keys="(k: any) => checkedKeys = k"
        :bordered="false"
        size="small"
      />
      <n-empty v-if="!candidates.length && !running" :description="t('screener.noData')" style="padding: 28px" />
    </n-card>

    <!-- Sizing modal -->
    <n-modal v-model:show="showSizing" preset="card" :title="t('screener.sizingTitle')" style="width: 460px">
      <n-space vertical :size="16">
        <n-radio-group v-model:value="sizing">
          <n-space vertical>
            <n-radio value="equal_cash">{{ t('screener.sizingEqualCash') }}</n-radio>
            <n-radio value="fixed_cash">{{ t('screener.sizingFixedCash') }}</n-radio>
            <n-radio value="fixed_shares">{{ t('screener.sizingFixedShares') }}</n-radio>
          </n-space>
        </n-radio-group>
        <n-form-item :label="sizingValueLabel" label-placement="top">
          <n-input-number v-model:value="sizingValue" :min="0" :step="sizing === 'equal_cash' ? 0.1 : 100"
            :max="sizing === 'equal_cash' ? 1 : undefined" style="width: 100%" />
        </n-form-item>
        <n-text depth="3" style="font-size: 12px">{{ sizingHint }}</n-text>
        <n-space justify="end">
          <n-button @click="showSizing = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="addingPaper" @click="confirmAddToPaper">
            {{ t('screener.confirmBuy') }}
          </n-button>
        </n-space>
      </n-space>
    </n-modal>

    <!-- History drawer -->
    <n-drawer v-model:show="showHistory" :width="420">
      <n-drawer-content :title="t('screener.history')" closable>
        <n-empty v-if="!historyItems.length" :description="t('screener.historyEmpty')" />
        <n-list v-else hoverable clickable>
          <n-list-item v-for="h in historyItems" :key="h.id" @click="loadHistory(h.id)">
            <n-thing :title="h.text || '(默认因子)'">
              <template #description>
                <n-space :size="6" align="center">
                  <n-tag size="tiny" :bordered="false" :type="h.status === 'complete' ? 'success' : 'default'">{{ h.status }}</n-tag>
                  <n-text depth="3" style="font-size: 12px">{{ h.created_at }}</n-text>
                </n-space>
              </template>
            </n-thing>
          </n-list-item>
        </n-list>
      </n-drawer-content>
    </n-drawer>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, h, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { NText, NTag, NTooltip, NSpace, useMessage } from 'naive-ui'
import api from '../api'

const { t } = useI18n()
const router = useRouter()
const message = useMessage()

const goal = ref('')
const topN = ref(20)
const useLlm = ref(true)
const momentumPeriod = ref('today')
const momentumDirection = ref('up')
const filters = reactive<Record<string, any>>({
  pe_max: null, pb_max: null, market_cap_min: null, market_cap_max: null, sector_query: null,
})

const periodOptions = computed(() => [
  { label: t('screener.periodToday'), value: 'today' },
  { label: t('screener.period5d'), value: '5d' },
  { label: t('screener.period20d'), value: '20d' },
  { label: t('screener.period60d'), value: '60d' },
  { label: t('screener.periodYtd'), value: 'ytd' },
])
const directionOptions = computed(() => [
  { label: t('screener.directionUp'), value: 'up' },
  { label: t('screener.directionDown'), value: 'down' },
])

const running = ref(false)
const runId = ref('')
const strategy = ref<any>(null)
const matched = ref<number | null>(null)
const dataSource = ref('')
const degraded = ref(false)
const progressMsg = ref('')
const candidates = ref<any[]>([])
const checkedKeys = ref<(string | number)[]>([])

const strategyLabels = computed<string[]>(() => strategy.value?.provenance?.labels || strategy.value?.labels || [])

let ws: WebSocket | null = null

function cleanFilters(): Record<string, any> {
  const out: Record<string, any> = {}
  for (const [k, v] of Object.entries(filters)) {
    if (v !== null && v !== '' && v !== undefined) out[k] = v
  }
  out.momentum_period = momentumPeriod.value
  out.momentum_direction = momentumDirection.value
  return out
}

async function startScreen() {
  running.value = true
  strategy.value = null
  matched.value = null
  dataSource.value = ''
  degraded.value = false
  progressMsg.value = ''
  candidates.value = []
  checkedKeys.value = []
  try {
    const f = cleanFilters()
    const { data } = await api.post('/api/screen', {
      text: goal.value,
      filters: Object.keys(f).length ? f : null,
      top_n: topN.value,
      use_llm: useLlm.value,
    })
    runId.value = data.id
    connect(data.id)
  } catch (e: any) {
    running.value = false
    message.error(e?.response?.data?.detail || e?.message || t('common.failed'))
  }
}

function connect(id: string) {
  if (ws) { try { ws.close() } catch {} }
  const url = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/screen/${id}`
  ws = new WebSocket(url)
  ws.onmessage = (msg) => {
    const ev = JSON.parse(msg.data)
    if (ev.strategy) strategy.value = ev.strategy
    if (ev.matched != null) matched.value = ev.matched
    if (ev.data_source) dataSource.value = ev.data_source
    if (ev.coverage === 'partial') degraded.value = true
    if (ev.type === 'warning') { message.warning(ev.content); return }
    if (ev.content) progressMsg.value = ev.content
    if (ev.type === 'screen_complete') {
      candidates.value = ev.candidates || []
      running.value = false
      progressMsg.value = ''
      ws?.close()
    } else if (ev.type === 'error') {
      running.value = false
      message.error(ev.content || t('common.failed'))
      ws?.close()
    }
  }
  ws.onerror = () => { running.value = false }
}

// --- table columns ---

function fmt(v: any, digits = 2): string {
  return v == null ? '-' : Number(v).toFixed(digits)
}

const columns = computed<any[]>(() => [
  { type: 'selection' },
  { title: t('screener.columns.rank'), key: 'rank', width: 60 },
  {
    title: t('screener.columns.name'), key: 'name', width: 150,
    render: (r: any) => h(NSpace, { size: 4, align: 'center' }, () => [
      h(NText, { strong: true }, () => r.name || r.ticker),
      h(NText, { depth: 3, style: 'font-size:12px' }, () => r.ticker),
    ]),
  },
  { title: t('screener.columns.price'), key: 'price', width: 80, render: (r: any) => fmt(r.metrics?.price) },
  {
    title: t('screener.columns.changePct'), key: 'change_pct', width: 80,
    render: (r: any) => {
      const v = r.metrics?.change_pct
      if (v == null) return '-'
      return h(NText, { type: v >= 0 ? 'error' : 'success' }, () => (v >= 0 ? '+' : '') + fmt(v) + '%')
    },
  },
  { title: t('screener.columns.pe'), key: 'pe', width: 70, render: (r: any) => fmt(r.metrics?.pe) },
  { title: t('screener.columns.pb'), key: 'pb', width: 70, render: (r: any) => fmt(r.metrics?.pb) },
  { title: t('screener.columns.marketCap'), key: 'mc', width: 90, render: (r: any) => fmt(r.metrics?.market_cap, 0) },
  { title: t('screener.columns.turnover'), key: 'turnover', width: 80, render: (r: any) => fmt(r.metrics?.turnover) },
  {
    title: t('screener.columns.score'), key: 'score', width: 90,
    render: (r: any) => h(NTooltip, null, {
      trigger: () => h(NTag, { size: 'small', type: 'info', bordered: false }, () => fmt(r.score)),
      default: () => {
        const fb = r.factor_breakdown || {}
        return `${t('screener.value')}: ${fmt(fb.value)} · ${t('screener.momentum')}: ${fmt(fb.momentum)} · ${t('screener.capitalFlow')}: ${fmt(fb.capital_flow)}`
      },
    }),
  },
  {
    title: t('screener.columns.reason'), key: 'reason',
    render: (r: any) => h(NSpace, { size: 4, vertical: true }, () => [
      h(NSpace, { size: 4, align: 'center' }, () => [
        h(NTag, { size: 'tiny', bordered: false, type: r.reason_source === 'llm' ? 'success' : 'default' },
          () => r.reason_source === 'llm' ? t('screener.reasonSourceLlm') : t('screener.reasonSourceRule')),
        h(NText, { style: 'font-size:13px' }, () => r.reason || '-'),
      ]),
      r.risk ? h(NText, { depth: 3, style: 'font-size:12px' }, () => `⚠ ${r.risk}`) : null,
    ]),
  },
])

// --- add to paper ---

const showSizing = ref(false)
const sizing = ref<'equal_cash' | 'fixed_cash' | 'fixed_shares'>('equal_cash')
const sizingValue = ref(0.5)
const addingPaper = ref(false)

const sizingValueLabel = computed(() => {
  if (sizing.value === 'equal_cash') return t('screener.sizingEqualCash')
  if (sizing.value === 'fixed_cash') return t('screener.sizingFixedCash')
  return t('screener.sizingFixedShares')
})
const sizingHint = computed(() => {
  if (sizing.value === 'equal_cash') return t('screener.sizingEqualCashHint')
  if (sizing.value === 'fixed_cash') return t('screener.sizingFixedCashHint')
  return t('screener.sizingFixedSharesHint')
})

// Reset the value field to a sensible default whenever the mode changes.
watch(sizing, (m) => {
  sizingValue.value = m === 'equal_cash' ? 0.5 : m === 'fixed_cash' ? 10000 : 100
})

async function confirmAddToPaper() {
  if (!checkedKeys.value.length) { message.warning(t('screener.emptySelect')); return }
  addingPaper.value = true
  try {
    const { data } = await api.post(`/api/screen/${runId.value}/to-paper`, {
      tickers: checkedKeys.value,
      sizing: sizing.value,
      value: sizingValue.value,
    })
    showSizing.value = false
    message.success(t('screener.toPaperDone', { filled: data.filled, total: data.total }))
    const failed = (data.results || []).filter((r: any) => !r.filled)
    for (const f of failed) message.warning(`${f.ticker}: ${f.reason}`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || t('common.failed'))
  } finally {
    addingPaper.value = false
  }
}

// --- batch analyze ---

const analyzing = ref(false)
async function batchAnalyze() {
  if (!checkedKeys.value.length) { message.warning(t('screener.emptySelect')); return }
  analyzing.value = true
  try {
    const { data } = await api.post(`/api/screen/${runId.value}/to-analyze`, {
      tickers: checkedKeys.value,
    })
    message.success(t('screener.toAnalyzeDone', { n: data.total }))
    if (data.started?.length === 1) router.push(`/progress/${data.started[0].analysis_id}`)
    else router.push('/history')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || t('common.failed'))
  } finally {
    analyzing.value = false
  }
}

// --- batch auto-trade portfolio ---

const addingSchedule = ref(false)
async function batchAutoTrade() {
  if (!checkedKeys.value.length) { message.warning(t('screener.emptySelect')); return }
  addingSchedule.value = true
  try {
    const { data } = await api.post(`/api/screen/${runId.value}/to-schedule`, {
      tickers: checkedKeys.value,
    })
    message.success(t('screener.toScheduleDone', { created: data.created, skipped: data.skipped.length }))
    router.push('/schedule')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || t('common.failed'))
  } finally {
    addingSchedule.value = false
  }
}

// --- history ---
const showHistory = ref(false)
const historyItems = ref<any[]>([])

async function openHistory() {
  showHistory.value = true
  try {
    const { data } = await api.get('/api/screen')
    historyItems.value = data.items || []
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || t('common.failed'))
  }
}

async function loadHistory(id: string) {
  try {
    const { data } = await api.get(`/api/screen/${id}`)
    runId.value = data.id
    strategy.value = data.strategy
    candidates.value = data.candidates || []
    checkedKeys.value = []
    matched.value = null
    progressMsg.value = ''
    degraded.value = (data.strategy?.provenance && data.strategy.coverage === 'partial') || false
    showHistory.value = false
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || t('common.failed'))
  }
}
</script>
