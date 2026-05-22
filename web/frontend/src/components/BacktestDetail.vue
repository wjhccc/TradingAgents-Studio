<template>
  <n-spin :show="loading">
    <n-space vertical :size="20">
      <!-- Metrics summary cards -->
      <n-grid :cols="4" :x-gap="16" v-if="run">
        <n-gi>
          <n-card size="small">
            <n-statistic
              :label="t('backtestDetail.metrics.totalReturn')"
              :value="formatPct(metrics?.total_return_pct)"
              :value-style="{ color: pnlColor(metrics?.total_return_pct) }"
            />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small">
            <n-statistic
              :label="t('backtestDetail.metrics.annualised')"
              :value="formatPct(metrics?.annualised_return_pct)"
              :value-style="{ color: pnlColor(metrics?.annualised_return_pct) }"
            />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small">
            <n-statistic
              :label="t('backtestDetail.metrics.vsBench')"
              :value="formatPct(metrics?.alpha_pct)"
              :value-style="{ color: pnlColor(metrics?.alpha_pct) }"
            />
            <template #footer>
              <n-text depth="3" style="font-size: 11px">
                {{ run.benchmark || '—' }} · {{ t('backtestDetail.metrics.benchSuffix', { v: formatPct(metrics?.benchmark_return_pct) }) }}
              </n-text>
            </template>
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small">
            <n-statistic
              :label="t('backtestDetail.metrics.mdd')"
              :value="formatPct(metrics?.max_drawdown_pct, true)"
              :value-style="{ color: '#18a058' }"
            />
          </n-card>
        </n-gi>
      </n-grid>

      <n-grid :cols="4" :x-gap="16" v-if="run">
        <n-gi>
          <n-card size="small">
            <n-statistic :label="t('backtestDetail.metrics.sharpe')" :value="metrics?.sharpe?.toFixed(2) || '—'" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small">
            <n-statistic :label="t('backtestDetail.metrics.sortino')" :value="metrics?.sortino?.toFixed(2) || '—'" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small">
            <n-statistic
              :label="t('backtestDetail.metrics.winRate')"
              :value="metrics?.win_rate_pct != null ? `${metrics.win_rate_pct.toFixed(0)}%` : '—'"
            />
            <template #footer>
              <n-text depth="3" style="font-size: 11px">
                {{ t('backtestDetail.metrics.roundTrips', { n: metrics?.n_round_trips || 0 }) }}
              </n-text>
            </template>
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small">
            <n-statistic
              :label="t('backtestDetail.metrics.profitFactor')"
              :value="metrics?.profit_factor != null ? (
                isFinite(metrics.profit_factor) ? metrics.profit_factor.toFixed(2) : '∞'
              ) : '—'"
            />
            <template #footer>
              <n-text depth="3" style="font-size: 11px">
                {{ t('backtestDetail.metrics.avgWinLoss', { win: formatPct(metrics?.avg_win_pct), loss: formatPct(metrics?.avg_loss_pct) }) }}
              </n-text>
            </template>
          </n-card>
        </n-gi>
      </n-grid>

      <!-- NAV curve -->
      <n-card :title="t('backtestDetail.navTitle')" size="small">
        <div v-if="navData.length" style="max-width: 100%">
          <Line :data="chartData" :options="chartOptions" />
        </div>
        <n-empty v-else :description="t('backtestDetail.navEmpty')" />
      </n-card>

      <!-- Trades -->
      <n-card :title="t('backtestDetail.tradesTitle')" size="small">
        <n-alert v-if="!trades.length && metrics" type="warning" :show-icon="false" style="margin-bottom: 12px">
          {{ t('backtestDetail.noTradesAlert') }} <b>{{ t('backtestDetail.noTradesAlertEmph') }}</b>{{ t('backtestDetail.noTradesAlertSuffix') }}
          <n-ul style="margin: 8px 0 0">
            <n-li>{{ t('backtestDetail.noTradesReasons.r1') }}</n-li>
            <n-li>{{ t('backtestDetail.noTradesReasons.r2') }}</n-li>
            <n-li>{{ t('backtestDetail.noTradesReasons.r3') }}</n-li>
          </n-ul>
        </n-alert>
        <n-data-table
          v-if="trades.length"
          :columns="tradeColumns"
          :data="trades"
          :pagination="{ pageSize: 30 }"
          :bordered="false"
          size="small"
        />
      </n-card>

      <!-- Warnings -->
      <n-card v-if="run?.warnings" :title="t('backtestDetail.warningsTitle')" size="small">
        <pre style="white-space: pre-wrap; margin: 0; font-size: 12px; color: #909090">{{ run.warnings }}</pre>
      </n-card>
    </n-space>
  </n-spin>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, h } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
} from 'chart.js'
import { NTag } from 'naive-ui'
import api from '../api'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler)

const props = defineProps<{ runId: number }>()
const { t } = useI18n()
const router = useRouter()

const loading = ref(false)
const run = ref<any>(null)
const navData = ref<any[]>([])
const trades = ref<any[]>([])

const metrics = computed(() => run.value?.metrics || null)

function formatPct(v: number | null | undefined, isLoss = false): string {
  if (v == null) return '—'
  const sign = isLoss ? '-' : (v >= 0 ? '+' : '')
  return `${sign}${Math.abs(v).toFixed(2)}%`
}
function pnlColor(v: number | null | undefined): string {
  if (v == null) return ''
  return v >= 0 ? '#d03050' : '#18a058'
}

const chartData = computed(() => ({
  labels: navData.value.map(r => r.snapshot_date.slice(0, 10)),
  datasets: [
    {
      label: t('backtestDetail.chart.strategy'),
      data: navData.value.map(r => r.total_value),
      borderColor: '#d03050',
      backgroundColor: 'rgba(208, 48, 80, 0.12)',
      fill: true,
      tension: 0.2,
      pointRadius: 0,
    },
    ...(navData.value[0]?.benchmark_value != null ? [{
      label: t('backtestDetail.chart.benchmark', { b: run.value?.benchmark || '' }),
      data: navData.value.map(r => r.benchmark_value),
      borderColor: '#909090',
      borderDash: [4, 4],
      tension: 0.2,
      pointRadius: 0,
    }] : []),
  ],
}))

const chartOptions = {
  responsive: true,
  maintainAspectRatio: true,
  interaction: { mode: 'index' as const, intersect: false },
  scales: { y: { beginAtZero: false } },
}

const tradeColumns = computed(() => [
  { title: t('backtestDetail.cols.date'), key: 'timestamp', width: 110, render(r: any) { return r.timestamp.slice(0, 10) } },
  { title: t('backtestDetail.cols.ticker'), key: 'ticker', width: 100 },
  {
    title: t('backtestDetail.cols.action'),
    key: 'action',
    width: 80,
    render(r: any) {
      const type = r.action === 'buy' ? 'success' : 'error'
      return h(NTag, { size: 'small', type, bordered: false }, () =>
        r.action === 'buy' ? t('backtestDetail.actions.buy') : t('backtestDetail.actions.sell'),
      )
    },
  },
  { title: t('backtestDetail.cols.shares'), key: 'shares', width: 100, render(r: any) { return r.shares.toFixed(0) } },
  { title: t('backtestDetail.cols.price'), key: 'price', width: 100, render(r: any) { return r.price.toFixed(2) } },
  { title: t('backtestDetail.cols.fee'), key: 'fee', width: 90, render(r: any) { return r.fee.toFixed(2) } },
  {
    title: t('backtestDetail.cols.realisedPnl'),
    key: 'realised_pnl',
    width: 130,
    render(r: any) {
      if (r.action !== 'sell') return '—'
      const v = r.realised_pnl || 0
      const color = v >= 0 ? '#d03050' : '#18a058'
      return h('span', { style: { color } }, `${v >= 0 ? '+' : ''}${v.toFixed(2)}`)
    },
  },
  {
    title: t('backtestDetail.cols.relatedAnalysis'),
    key: 'source_analysis_id',
    width: 140,
    render(r: any) {
      if (!r.source_analysis_id) return '—'
      return h('a', {
        style: { color: '#3060d0', cursor: 'pointer', textDecoration: 'underline' },
        onClick: () => router.push(`/report/${r.source_analysis_id}`),
      }, r.source_analysis_id.slice(0, 8))
    },
  },
])

async function load() {
  loading.value = true
  try {
    const [runRes, curveRes, tradesRes] = await Promise.all([
      api.get(`/api/backtest/${props.runId}`),
      api.get(`/api/backtest/${props.runId}/curve`),
      api.get(`/api/backtest/${props.runId}/trades`),
    ])
    run.value = runRes.data
    navData.value = curveRes.data.items || []
    trades.value = tradesRes.data.items || []
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.runId, load)
</script>
