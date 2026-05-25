<template>
  <n-space vertical :size="20">
    <n-page-header :title="t('quality.title')" :subtitle="t('quality.subtitle')">
      <template #extra>
        <n-space>
          <n-radio-group v-model:value="horizon" size="small">
            <n-radio-button :value="5">{{ t('quality.horizon.d5') }}</n-radio-button>
            <n-radio-button :value="30">{{ t('quality.horizon.d30') }}</n-radio-button>
            <n-radio-button :value="60">{{ t('quality.horizon.d60') }}</n-radio-button>
          </n-radio-group>
          <n-button size="small" @click="loadAll" :loading="loading">{{ t('common.refresh') }}</n-button>
        </n-space>
      </template>
    </n-page-header>

    <!-- Explainer block. Folded by default after first use; helps users
         understand what "alpha", "evaluable", "calibration" actually mean. -->
    <n-card size="small">
      <n-collapse arrow-placement="right">
        <n-collapse-item :title="t('quality.intro.header')" name="intro">
          <n-space vertical :size="10">
            <n-text>
              <b>{{ t('quality.intro.whatLabel') }}</b>{{ t('quality.intro.what') }}
            </n-text>
            <n-text>
              <b>{{ t('quality.intro.howLabel') }}</b>{{ t('quality.intro.how') }}
            </n-text>
            <n-text depth="3" style="font-size: 12px">
              {{ t('quality.intro.benchHint') }}
            </n-text>
          </n-space>
        </n-collapse-item>
      </n-collapse>
    </n-card>

    <n-spin :show="loading">
      <n-space vertical :size="20">
        <!-- Empty state — only relevant if no completed analyses exist. -->
        <n-alert v-if="overview && overview.summary.total === 0" type="info" :show-icon="false">
          {{ t('quality.empty') }}
        </n-alert>

        <!-- KPI cards row 1 -->
        <n-grid v-if="overview && overview.summary.total > 0" :cols="4" :x-gap="16">
          <n-gi>
            <n-card size="small">
              <n-statistic :label="t('quality.kpi.totalDecisions')" :value="overview.summary.total" />
              <template #footer>
                <n-text depth="3" style="font-size: 11px">
                  {{ t('quality.kpi.evaluableSuffix', { n: overview.summary.evaluable }) }}
                </n-text>
              </template>
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic
                :label="t('quality.kpi.winRate')"
                :value="formatPctish(overview.summary.win_rate)"
                :value-style="{ color: winRateColor(overview.summary.win_rate) }"
              />
              <template #footer>
                <n-text depth="3" style="font-size: 11px">
                  {{ t('quality.kpi.directionalSuffix', { n: overview.summary.directional }) }}
                </n-text>
              </template>
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic
                :label="t('quality.kpi.avgAlpha')"
                :value="formatPct(overview.summary.avg_alpha)"
                :value-style="{ color: pnlColor(overview.summary.avg_alpha) }"
              />
              <template #footer>
                <n-text depth="3" style="font-size: 11px">
                  {{ t('quality.kpi.medianSuffix', { v: formatPct(overview.summary.median_alpha) }) }}
                </n-text>
              </template>
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic
                :label="t('quality.kpi.alphaSharpe')"
                :value="overview.summary.alpha_sharpe != null ? overview.summary.alpha_sharpe.toFixed(2) : '—'"
              />
              <template #footer>
                <n-text depth="3" style="font-size: 11px">
                  {{ t('quality.kpi.sharpeHint') }}
                </n-text>
              </template>
            </n-card>
          </n-gi>
        </n-grid>

        <n-grid v-if="overview && overview.summary.evaluable > 0" :cols="4" :x-gap="16">
          <n-gi>
            <n-card size="small">
              <n-statistic
                :label="t('quality.kpi.avgRaw')"
                :value="formatPct(overview.summary.avg_raw_return)"
                :value-style="{ color: pnlColor(overview.summary.avg_raw_return) }"
              />
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic
                :label="t('quality.kpi.bestAlpha')"
                :value="formatPct(overview.summary.best_alpha)"
                :value-style="{ color: pnlColor(overview.summary.best_alpha) }"
              />
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic
                :label="t('quality.kpi.worstAlpha')"
                :value="formatPct(overview.summary.worst_alpha)"
                :value-style="{ color: pnlColor(overview.summary.worst_alpha) }"
              />
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic
                :label="t('quality.kpi.signalCount')"
                :value="signalCountSummary"
              />
              <template #footer>
                <n-text depth="3" style="font-size: 11px">
                  {{ signalAlphaSummary }}
                </n-text>
              </template>
            </n-card>
          </n-gi>
        </n-grid>

        <!-- Confidence calibration curve -->
        <n-card v-if="calibration && calibration.buckets.some((b: any) => b.count > 0)"
                :title="t('quality.calibration.title')" size="small">
          <n-grid :cols="2" :x-gap="20">
            <n-gi>
              <div style="max-width: 100%">
                <Bar :data="calibrationChartData" :options="calibrationChartOptions" />
              </div>
            </n-gi>
            <n-gi>
              <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 6px">
                {{ t('quality.calibration.hint') }}
              </n-text>
              <n-data-table
                size="small"
                :columns="calibrationColumns"
                :data="calibration.buckets"
                :bordered="false"
              />
            </n-gi>
          </n-grid>
        </n-card>

        <!-- Dimension breakdown -->
        <n-card v-if="overview && overview.summary.evaluable > 0"
                :title="t('quality.dim.title')" size="small">
          <n-space vertical :size="12">
            <n-radio-group v-model:value="dim" size="small">
              <n-radio-button value="ticker">{{ t('quality.dim.ticker') }}</n-radio-button>
              <n-radio-button value="signal">{{ t('quality.dim.signal') }}</n-radio-button>
              <n-radio-button value="analyst">{{ t('quality.dim.analyst') }}</n-radio-button>
              <n-radio-button value="analyst_combo">{{ t('quality.dim.combo') }}</n-radio-button>
              <n-radio-button value="llm">{{ t('quality.dim.llm') }}</n-radio-button>
            </n-radio-group>
            <n-data-table
              v-if="dimRows.length"
              size="small"
              :columns="dimColumns"
              :data="dimRows"
              :pagination="{ pageSize: 20 }"
              :bordered="false"
            />
            <n-empty v-else :description="t('quality.dim.empty')" />
          </n-space>
        </n-card>

        <!-- Per-day heatmap -->
        <n-card v-if="heatmap && heatmap.days.length"
                :title="t('quality.heatmap.title')" size="small">
          <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 8px">
            {{ t('quality.heatmap.hint') }}
          </n-text>
          <div class="quality-heatmap">
            <div class="heatmap-grid">
              <div
                v-for="cell in heatmapCells"
                :key="cell.date"
                class="heatmap-cell"
                :style="{ background: cell.bg }"
                :title="cell.tooltip"
              />
            </div>
            <div class="heatmap-legend">
              <span>{{ t('quality.heatmap.legendNeg') }}</span>
              <div class="legend-scale">
                <div v-for="bg in legendStops" :key="bg" class="legend-cell" :style="{ background: bg }" />
              </div>
              <span>{{ t('quality.heatmap.legendPos') }}</span>
            </div>
          </div>
        </n-card>

        <!-- Decision table -->
        <n-card v-if="overview && overview.summary.total > 0"
                :title="t('quality.decisions.title')" size="small">
          <n-space vertical :size="10">
            <n-space>
              <n-input
                v-model:value="filterTicker"
                size="small"
                clearable
                :placeholder="t('quality.decisions.filterTicker')"
                style="width: 160px"
              />
              <n-select
                v-model:value="filterSignal"
                size="small"
                clearable
                :placeholder="t('quality.decisions.filterSignal')"
                style="width: 160px"
                :options="signalOptions"
              />
              <n-switch v-model:value="onlyEvaluable" size="small">
                <template #checked>{{ t('quality.decisions.onlyEvaluable') }}</template>
                <template #unchecked>{{ t('quality.decisions.onlyEvaluable') }}</template>
              </n-switch>
            </n-space>
            <n-data-table
              size="small"
              :columns="decisionColumns"
              :data="filteredDecisions"
              :pagination="{ pageSize: 25 }"
              :bordered="false"
            />
          </n-space>
        </n-card>
      </n-space>
    </n-spin>
  </n-space>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, h } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { Bar } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale, LinearScale, BarElement, PointElement, LineElement,
  Title, Tooltip, Legend,
} from 'chart.js'
import { NTag, NButton } from 'naive-ui'
import api from '../api'

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, Title, Tooltip, Legend)

const { t } = useI18n()
const router = useRouter()

const horizon = ref<number>(30)
const dim = ref<'ticker' | 'signal' | 'analyst' | 'analyst_combo' | 'llm'>('ticker')
const loading = ref(false)
const overview = ref<any>(null)
const calibration = ref<any>(null)
const heatmap = ref<any>(null)
const decisions = ref<any[]>([])
const dimResult = ref<any>(null)

const filterTicker = ref<string>('')
const filterSignal = ref<string | null>(null)
const onlyEvaluable = ref(false)

const signalOptions = [
  { label: 'BUY', value: 'BUY' },
  { label: 'OVERWEIGHT', value: 'OVERWEIGHT' },
  { label: 'HOLD', value: 'HOLD' },
  { label: 'UNDERWEIGHT', value: 'UNDERWEIGHT' },
  { label: 'SELL', value: 'SELL' },
]

function formatPct(v: number | null | undefined, decimals = 2): string {
  if (v == null || !isFinite(v)) return '—'
  const pct = v * 100
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(decimals)}%`
}

function formatPctish(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function pnlColor(v: number | null | undefined): string {
  if (v == null) return ''
  return v >= 0 ? '#d03050' : '#18a058'
}

function winRateColor(v: number | null | undefined): string {
  if (v == null) return ''
  // 50% is the coin-flip line; above is red (good in CN convention), below green.
  return v >= 0.5 ? '#d03050' : '#18a058'
}

const signalCountSummary = computed(() => {
  if (!overview.value) return '—'
  const mix = overview.value.signal_mix || []
  return mix.map((m: any) => `${m.signal} ${m.count}`).join(' · ') || '—'
})

const signalAlphaSummary = computed(() => {
  if (!overview.value) return ''
  const mix = overview.value.signal_mix || []
  const segs = mix
    .filter((m: any) => m.avg_alpha != null)
    .map((m: any) => `${m.signal} α=${formatPct(m.avg_alpha)}`)
  return segs.join(' · ')
})

// ---- Calibration ----

const calibrationChartData = computed(() => {
  if (!calibration.value) return { labels: [], datasets: [] }
  const buckets = calibration.value.buckets || []
  return {
    labels: buckets.map((b: any) => b.bucket),
    datasets: [
      {
        type: 'bar' as const,
        label: t('quality.calibration.actualWinRate'),
        data: buckets.map((b: any) => b.win_rate != null ? b.win_rate * 100 : 0),
        backgroundColor: 'rgba(208, 48, 80, 0.6)',
        borderColor: '#d03050',
        borderWidth: 1,
      },
      {
        type: 'bar' as const,
        label: t('quality.calibration.ideal'),
        data: buckets.map((b: any) => ((b.lo + b.hi) / 2) * 100),
        backgroundColor: 'rgba(144, 144, 144, 0.25)',
        borderColor: '#909090',
        borderDash: [4, 4],
        borderWidth: 1,
      },
    ],
  }
})

const calibrationChartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: true,
  plugins: { legend: { position: 'bottom' as const } },
  scales: {
    y: { beginAtZero: true, max: 100, title: { display: true, text: '%' } },
  },
}))

const calibrationColumns = computed(() => [
  { title: t('quality.calibration.cols.bucket'), key: 'bucket', width: 90 },
  { title: t('quality.calibration.cols.count'), key: 'count', width: 70 },
  {
    title: t('quality.calibration.cols.winRate'),
    key: 'win_rate',
    render(r: any) { return r.win_rate != null ? `${(r.win_rate * 100).toFixed(0)}%` : '—' },
  },
  {
    title: t('quality.calibration.cols.avgAlpha'),
    key: 'avg_alpha',
    render(r: any) {
      if (r.avg_alpha == null) return '—'
      return h('span', { style: { color: pnlColor(r.avg_alpha) } }, formatPct(r.avg_alpha))
    },
  },
])

// ---- Dimensions ----

const dimRows = computed(() => dimResult.value?.items || [])

const dimColumns = computed(() => [
  { title: t('quality.dim.cols.key'), key: 'key', minWidth: 180 },
  { title: t('quality.dim.cols.total'), key: 'total', width: 80 },
  { title: t('quality.dim.cols.evaluable'), key: 'evaluable', width: 90 },
  { title: t('quality.dim.cols.directional'), key: 'directional', width: 100 },
  {
    title: t('quality.dim.cols.winRate'),
    key: 'win_rate',
    width: 90,
    render(r: any) {
      return r.win_rate != null
        ? h('span', { style: { color: winRateColor(r.win_rate) } }, `${(r.win_rate * 100).toFixed(0)}%`)
        : '—'
    },
  },
  {
    title: t('quality.dim.cols.avgAlpha'),
    key: 'avg_alpha',
    width: 110,
    render(r: any) {
      return r.avg_alpha != null
        ? h('span', { style: { color: pnlColor(r.avg_alpha) } }, formatPct(r.avg_alpha))
        : '—'
    },
  },
  {
    title: t('quality.dim.cols.medianAlpha'),
    key: 'median_alpha',
    width: 110,
    render(r: any) {
      return r.median_alpha != null
        ? h('span', { style: { color: pnlColor(r.median_alpha) } }, formatPct(r.median_alpha))
        : '—'
    },
  },
  {
    title: t('quality.dim.cols.sharpe'),
    key: 'alpha_sharpe',
    width: 90,
    render(r: any) {
      return r.alpha_sharpe != null ? r.alpha_sharpe.toFixed(2) : '—'
    },
  },
])

// ---- Heatmap ----

const heatmapCells = computed(() => {
  if (!heatmap.value) return []
  const days = heatmap.value.days || []
  return days.map((d: any) => {
    const bg = alphaToColor(d.avg_alpha)
    const tooltip = `${d.date} · ${d.count} ${t('quality.heatmap.cellCount')} · α=${formatPct(d.avg_alpha)}`
    return { date: d.date, bg, tooltip }
  })
})

function alphaToColor(alpha: number | null | undefined): string {
  if (alpha == null) return '#ebedf0'
  const v = Math.max(-0.1, Math.min(0.1, alpha))   // clamp ±10 %
  const norm = v / 0.1                              // [-1, 1]
  if (norm >= 0) {
    // Red ascending (positive in CN). 0 → light, 1 → deep red.
    const intensity = Math.round(40 + norm * 200)
    return `rgb(${Math.min(255, 200 + intensity * 0.3)}, ${100 - norm * 80}, ${110 - norm * 80})`
  }
  // Green descending.
  const intensity = Math.round(40 + (-norm) * 200)
  return `rgb(${100 - (-norm) * 80}, ${Math.min(255, 200 + intensity * 0.3)}, ${110 - (-norm) * 80})`
}

const legendStops = computed(() => [
  alphaToColor(-0.1),
  alphaToColor(-0.05),
  alphaToColor(0),
  alphaToColor(0.05),
  alphaToColor(0.1),
])

// ---- Decisions ----

const filteredDecisions = computed(() => {
  let rows = decisions.value || []
  if (filterTicker.value) {
    const q = filterTicker.value.trim().toUpperCase()
    rows = rows.filter(r => r.ticker.toUpperCase().includes(q))
  }
  if (filterSignal.value) {
    rows = rows.filter(r => (r.signal || '').toUpperCase() === filterSignal.value)
  }
  if (onlyEvaluable.value) {
    rows = rows.filter(r => r.evaluable)
  }
  return rows
})

const decisionColumns = computed(() => [
  { title: t('quality.decisions.cols.tradeDate'), key: 'trade_date', width: 110 },
  { title: t('quality.decisions.cols.ticker'), key: 'ticker', width: 110 },
  {
    title: t('quality.decisions.cols.signal'),
    key: 'signal',
    width: 110,
    render(r: any) {
      if (!r.signal) return '—'
      const sig = r.signal.toUpperCase()
      const isBuy = sig === 'BUY' || sig === 'OVERWEIGHT'
      const isSell = sig === 'SELL' || sig === 'UNDERWEIGHT'
      const type = isBuy ? 'error' : (isSell ? 'success' : 'default')
      return h(NTag, { size: 'small', type, bordered: false }, () => sig)
    },
  },
  {
    title: t('quality.decisions.cols.confidence'),
    key: 'confidence',
    width: 100,
    render(r: any) { return r.confidence != null ? r.confidence.toFixed(2) : '—' },
  },
  {
    title: t('quality.decisions.cols.rawReturn'),
    key: 'raw_return',
    width: 110,
    render(r: any) {
      return r.raw_return != null
        ? h('span', { style: { color: pnlColor(r.raw_return) } }, formatPct(r.raw_return))
        : '—'
    },
  },
  {
    title: t('quality.decisions.cols.alpha'),
    key: 'alpha',
    width: 110,
    render(r: any) {
      return r.alpha != null
        ? h('span', { style: { color: pnlColor(r.alpha) } }, formatPct(r.alpha))
        : '—'
    },
  },
  {
    title: t('quality.decisions.cols.win'),
    key: 'win',
    width: 80,
    render(r: any) {
      if (!r.evaluable) {
        return h(NTag, { size: 'small', type: 'default', bordered: false }, () => t('quality.decisions.pending'))
      }
      if (r.win === true) return h(NTag, { size: 'small', type: 'error', bordered: false }, () => t('quality.decisions.win'))
      if (r.win === false) return h(NTag, { size: 'small', type: 'success', bordered: false }, () => t('quality.decisions.loss'))
      return '—'
    },
  },
  {
    title: t('quality.decisions.cols.actions'),
    key: 'actions',
    width: 90,
    render(r: any) {
      return h(NButton, {
        size: 'tiny',
        text: true,
        type: 'primary',
        onClick: () => router.push(`/report/${r.id}`),
      }, () => t('quality.decisions.openReport'))
    },
  },
])

// ---- Loading ----

async function loadOverview() {
  const { data } = await api.get('/api/quality/overview', { params: { horizon: horizon.value } })
  overview.value = data
}

async function loadCalibration() {
  const { data } = await api.get('/api/quality/calibration', { params: { horizon: horizon.value } })
  calibration.value = data
}

async function loadHeatmap() {
  const { data } = await api.get('/api/quality/heatmap', { params: { horizon: horizon.value } })
  heatmap.value = data
}

async function loadDecisions() {
  const { data } = await api.get('/api/quality/decisions', { params: { horizon: horizon.value, limit: 1000 } })
  decisions.value = data.items || []
}

async function loadDim() {
  const { data } = await api.get('/api/quality/by-dimension', {
    params: { horizon: horizon.value, dim: dim.value, min_count: 1 },
  })
  dimResult.value = data
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadOverview(), loadCalibration(), loadHeatmap(), loadDecisions(), loadDim()])
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
watch(horizon, loadAll)
watch(dim, loadDim)
</script>

<style scoped>
.quality-heatmap {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.heatmap-grid {
  display: grid;
  /* auto-fill so the grid fluidly reflows on narrow screens */
  grid-template-columns: repeat(auto-fill, minmax(14px, 1fr));
  gap: 3px;
}
.heatmap-cell {
  aspect-ratio: 1 / 1;
  border-radius: 2px;
  border: 1px solid rgba(0, 0, 0, 0.04);
}
.heatmap-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #909090;
}
.legend-scale {
  display: flex;
  gap: 2px;
}
.legend-cell {
  width: 14px;
  height: 14px;
  border-radius: 2px;
}
</style>
