<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('backtest.title')" :subtitle="t('backtest.subtitle')">
      <template #extra>
        <n-button type="primary" @click="showCreate = true">{{ t('backtest.newBtn') }}</n-button>
      </template>
    </n-page-header>

    <!-- Mechanism explainer — folds open so the page isn't dominated by text. -->
    <n-card size="small">
      <n-collapse arrow-placement="right" :default-expanded-names="['intro']">
        <n-collapse-item :title="t('backtest.intro.header')" name="intro">
          <n-space vertical :size="14">
            <n-text>
              <b>{{ t('backtest.intro.principleLabel') }}</b>{{ t('backtest.intro.principle') }}
            </n-text>
            <n-text>
              <b>{{ t('backtest.intro.whyLabel') }}</b>{{ t('backtest.intro.why') }}
              <b>{{ t('backtest.intro.whyEmph') }}</b>{{ t('backtest.intro.whySuffix') }}
            </n-text>
            <n-text>
              <b>{{ t('backtest.intro.howLabel') }}</b>{{ t('backtest.intro.how') }}
              <b>{{ t('backtest.intro.holdEmph') }}</b>{{ t('backtest.intro.howSuffix') }}
            </n-text>
            <n-grid :cols="3" :x-gap="12" :y-gap="0">
              <n-gi>
                <n-card size="small" embedded>
                  <n-tag type="success" size="small" :bordered="false">{{ t('backtest.intro.cards.liveBadge') }}</n-tag>
                  <n-text strong style="display: block; margin: 6px 0 4px">{{ t('backtest.intro.cards.liveTitle') }}</n-text>
                  <n-text depth="3" style="font-size: 12px">
                    {{ t('backtest.intro.cards.liveDesc') }}
                  </n-text>
                </n-card>
              </n-gi>
              <n-gi>
                <n-card size="small" embedded>
                  <n-tag type="warning" size="small" :bordered="false">{{ t('backtest.intro.cards.planBadge') }}</n-tag>
                  <n-text strong style="display: block; margin: 6px 0 4px">{{ t('backtest.intro.cards.planTitle') }}</n-text>
                  <n-text depth="3" style="font-size: 12px">
                    {{ t('backtest.intro.cards.planDesc') }}
                  </n-text>
                </n-card>
              </n-gi>
              <n-gi>
                <n-card size="small" embedded>
                  <n-tag type="default" size="small" :bordered="false">{{ t('backtest.intro.cards.rerunBadge') }}</n-tag>
                  <n-text strong style="display: block; margin: 6px 0 4px">{{ t('backtest.intro.cards.rerunTitle') }}</n-text>
                  <n-text depth="3" style="font-size: 12px">
                    {{ t('backtest.intro.cards.rerunDesc') }}
                  </n-text>
                </n-card>
              </n-gi>
            </n-grid>
          </n-space>
        </n-collapse-item>
      </n-collapse>
    </n-card>

    <n-alert type="info" :show-icon="false">
      {{ t('backtest.disclaimer') }}
    </n-alert>

    <!-- Runs list / detail tabs -->
    <n-tabs v-model:value="activeView" type="line">
      <n-tab-pane name="list" :tab="t('backtest.tabs.list')">
        <n-card>
          <n-data-table
            :columns="listColumns"
            :data="runs"
            :pagination="{ pageSize: 20 }"
            :bordered="false"
            size="small"
            :loading="loadingList"
          />
          <n-empty v-if="!runs.length && !loadingList" :description="t('backtest.empty')" />
        </n-card>
      </n-tab-pane>
      <n-tab-pane v-if="activeRun" name="detail" :tab="t('backtest.tabs.detail', { name: activeRun.name })">
        <BacktestDetail :run-id="activeRun.id" :key="activeRun.id" />
      </n-tab-pane>
    </n-tabs>

    <!-- Create modal -->
    <n-modal v-model:show="showCreate" preset="card" :title="t('backtest.createTitle')" style="width: 640px">
      <n-spin :show="loadingUniverse">
        <n-form label-placement="left" label-width="100">
          <n-form-item :label="t('backtest.fields.name')">
            <n-input v-model:value="form.name" :placeholder="t('backtest.fields.namePlaceholder')" />
          </n-form-item>
          <n-form-item :label="t('backtest.fields.dateRange')">
            <n-date-picker
              v-model:formatted-value="form.dateRange"
              type="daterange"
              format="yyyy-MM-dd"
              value-format="yyyy-MM-dd"
              :default-value="defaultRange"
            />
          </n-form-item>
          <n-form-item :label="t('backtest.fields.source')">
            <n-select
              v-model:value="form.signalSource"
              :options="sourceOptions"
              :disabled="loadingSources"
            />
          </n-form-item>
          <n-form-item :label="t('backtest.fields.universe')">
            <n-select
              v-model:value="form.tickers"
              :options="tickerOptions"
              multiple
              filterable
              :placeholder="t('backtest.fields.universePlaceholder')"
              max-tag-count="5"
            />
          </n-form-item>
          <n-form-item :label="t('backtest.fields.benchmark')">
            <n-input v-model:value="form.benchmark" :placeholder="t('backtest.fields.benchmarkPlaceholder')" />
          </n-form-item>
          <n-form-item :label="t('backtest.fields.initialCash')">
            <n-input-number v-model:value="form.initialCash" :min="10000" :step="10000" />
          </n-form-item>
          <n-form-item :label="t('backtest.fields.sizingMode')">
            <n-radio-group v-model:value="form.sizingMode">
              <n-radio value="equal_weight">{{ t('backtest.fields.equalWeight') }}</n-radio>
              <n-radio value="fixed_cash">{{ t('backtest.fields.fixedCash') }}</n-radio>
              <n-radio value="signal_strength">{{ t('backtest.fields.signalStrength') }}</n-radio>
            </n-radio-group>
          </n-form-item>
          <n-form-item v-if="form.sizingMode !== 'equal_weight'" :label="t('backtest.fields.fixedCashPerSignal')">
            <n-input-number v-model:value="form.fixedCashPerSignal" :min="0" :step="10000" :placeholder="t('backtest.fields.fixedCashPlaceholder')" />
          </n-form-item>
          <n-form-item :label="t('backtest.fields.confidenceFloor')">
            <n-slider
              v-model:value="form.confidenceFloorPct"
              :min="0"
              :max="100"
              :step="5"
              :marks="confidenceMarks"
            />
          </n-form-item>
        </n-form>
      </n-spin>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCreate = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="running" @click="runBacktest">{{ t('backtest.btn.run') }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, h, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMessage, useDialog, NButton, NSpace, NTag } from 'naive-ui'
import api from '../api'
import BacktestDetail from '../components/BacktestDetail.vue'

interface Run {
  id: number
  name: string
  signal_source: string
  tickers: string | null
  benchmark: string | null
  start_date: string
  end_date: string
  initial_cash: number
  status: string
  final_total: number | null
  metrics: any
  created_at: string
  completed_at: string | null
}

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const activeView = ref('list')
const activeRun = ref<Run | null>(null)

const loadingList = ref(false)
const loadingSources = ref(false)
const loadingUniverse = ref(false)
const running = ref(false)

const runs = ref<Run[]>([])
const sourceOptions = ref<{ label: string; value: string; disabled?: boolean }[]>([])
const tickerOptions = ref<{ label: string; value: string }[]>([])
const dateBounds = ref<{ min: string | null; max: string | null }>({ min: null, max: null })

const showCreate = ref(false)
const form = reactive({
  name: '',
  dateRange: null as [string, string] | null,
  signalSource: 'memory_log',
  tickers: [] as string[],
  benchmark: '000300.SH',
  initialCash: 1_000_000,
  sizingMode: 'equal_weight' as 'equal_weight' | 'fixed_cash' | 'signal_strength',
  fixedCashPerSignal: null as number | null,
  confidenceFloorPct: 0,
})

const defaultRange = computed(() => {
  if (dateBounds.value.min && dateBounds.value.max) {
    return [
      new Date(dateBounds.value.min).getTime(),
      new Date(dateBounds.value.max).getTime(),
    ] as [number, number]
  }
  return undefined
})

const confidenceMarks = computed(() => ({
  0: t('backtest.fields.confidenceMarks.none'),
  50: t('backtest.fields.confidenceMarks.med'),
  80: t('backtest.fields.confidenceMarks.high'),
}))

function sourceLabel(key: string): string {
  const map: Record<string, string> = {
    memory_log: t('backtest.sources.memoryLog'),
    rule: t('backtest.sources.rule'),
    live_agent: t('backtest.sources.liveAgent'),
  }
  return map[key] || key
}

function statusLabel(s: string): { label: string; type: 'success' | 'info' | 'warning' | 'error' | 'default' } {
  const map: Record<string, { label: string; type: 'success' | 'info' | 'warning' | 'error' | 'default' }> = {
    complete: { label: t('backtest.statusMap.complete'), type: 'success' },
    running: { label: t('backtest.statusMap.running'), type: 'info' },
    pending: { label: t('backtest.statusMap.pending'), type: 'warning' },
    failed: { label: t('backtest.statusMap.failed'), type: 'error' },
  }
  return map[s] || { label: s, type: 'default' }
}

const listColumns = computed(() => [
  { title: t('backtest.cols.name'), key: 'name', width: 200, ellipsis: { tooltip: true } },
  {
    title: t('backtest.cols.source'),
    key: 'signal_source',
    width: 130,
    render(r: Run) {
      return sourceLabel(r.signal_source)
    },
  },
  {
    title: t('backtest.cols.range'),
    key: 'range',
    width: 200,
    render(r: Run) { return `${r.start_date} → ${r.end_date}` },
  },
  {
    title: t('backtest.cols.status'),
    key: 'status',
    width: 90,
    render(r: Run) {
      const cfg = statusLabel(r.status)
      return h(NTag, { size: 'small', type: cfg.type, bordered: false }, () => cfg.label)
    },
  },
  {
    title: t('backtest.cols.totalReturn'),
    key: 'total_return_pct',
    width: 120,
    render(r: Run) {
      const v = r.metrics?.total_return_pct
      if (v == null) return '—'
      const color = v >= 0 ? '#d03050' : '#18a058'
      return h('span', { style: { color, fontWeight: 600 } },
        `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`)
    },
  },
  {
    title: t('backtest.cols.benchmark'),
    key: 'benchmark_return',
    width: 100,
    render(r: Run) {
      const v = r.metrics?.benchmark_return_pct
      if (v == null) return '—'
      return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
    },
  },
  {
    title: t('backtest.cols.mdd'),
    key: 'mdd',
    width: 100,
    render(r: Run) {
      const v = r.metrics?.max_drawdown_pct
      return v != null ? `-${v.toFixed(2)}%` : '—'
    },
  },
  {
    title: t('backtest.cols.sharpe'),
    key: 'sharpe',
    width: 80,
    render(r: Run) {
      const v = r.metrics?.sharpe
      return v != null ? v.toFixed(2) : '—'
    },
  },
  {
    title: t('backtest.cols.winRate'),
    key: 'win_rate',
    width: 80,
    render(r: Run) {
      const v = r.metrics?.win_rate_pct
      return v != null ? `${v.toFixed(0)}%` : '—'
    },
  },
  {
    title: t('backtest.cols.actions'),
    key: 'actions',
    width: 140,
    render(r: Run) {
      return h(NSpace, { size: 4 }, () => [
        h(NButton, { size: 'tiny', type: 'primary', onClick: () => openDetail(r) }, () => t('backtest.btn.view')),
        h(NButton, { size: 'tiny', type: 'error', onClick: () => confirmDelete(r) }, () => t('backtest.btn.delete')),
      ])
    },
  },
])

function openDetail(r: Run) {
  activeRun.value = r
  activeView.value = 'detail'
}

function confirmDelete(r: Run) {
  dialog.warning({
    title: t('backtest.confirmDeleteTitle'),
    content: t('backtest.confirmDeleteContent', { name: r.name }),
    positiveText: t('common.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      await api.delete(`/api/backtest/${r.id}`)
      message.success(t('common.deleted'))
      if (activeRun.value?.id === r.id) {
        activeRun.value = null
        activeView.value = 'list'
      }
      await loadRuns()
    },
  })
}

async function loadRuns() {
  loadingList.value = true
  try {
    const { data } = await api.get('/api/backtest')
    runs.value = data.items || []
  } finally {
    loadingList.value = false
  }
}

async function loadSources() {
  loadingSources.value = true
  try {
    const { data } = await api.get('/api/backtest/sources')
    sourceOptions.value = (data.items || []).map((s: any) => ({
      label: sourceLabel(s.key),
      value: s.key,
      disabled: !s.available,
    }))
  } finally {
    loadingSources.value = false
  }
}

async function loadUniverse() {
  loadingUniverse.value = true
  try {
    const { data } = await api.get('/api/backtest/universe')
    tickerOptions.value = (data.tickers || []).map((tk: string) => ({ label: tk, value: tk }))
    dateBounds.value = { min: data.min_date, max: data.max_date }
    // Default the date picker to the full available range so the user
    // sees something useful even if they don't touch it.
    if (data.min_date && data.max_date && !form.dateRange) {
      form.dateRange = [data.min_date, data.max_date]
    }
  } finally {
    loadingUniverse.value = false
  }
}

async function runBacktest() {
  if (!form.dateRange || form.dateRange.length !== 2) {
    message.warning(t('backtest.validation.dateRange'))
    return
  }
  running.value = true
  try {
    const payload = {
      name: form.name || null,
      signal_source: form.signalSource,
      tickers: form.tickers.length ? form.tickers : null,
      benchmark: form.benchmark || null,
      start_date: form.dateRange[0],
      end_date: form.dateRange[1],
      initial_cash: form.initialCash,
      sizing_mode: form.sizingMode,
      fixed_cash_per_signal: form.fixedCashPerSignal,
      confidence_floor: form.confidenceFloorPct > 0 ? form.confidenceFloorPct / 100 : null,
    }
    const { data } = await api.post('/api/backtest', payload)
    message.success(t('backtest.msg.done'))
    showCreate.value = false
    await loadRuns()
    // Jump straight to the new run's detail.
    activeRun.value = data
    activeView.value = 'detail'
  } catch (e: any) {
    message.error(t('backtest.msg.failed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    running.value = false
  }
}

onMounted(() => {
  loadRuns()
  loadSources()
  loadUniverse()
})
</script>
