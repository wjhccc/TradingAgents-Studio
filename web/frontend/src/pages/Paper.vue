<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('paper.title')" :subtitle="t('paper.subtitle')">
      <template #extra>
        <n-space>
          <n-button @click="takeSnapshot" :loading="snapshotting">{{ t('paper.snapshotBtn') }}</n-button>
          <n-button type="primary" @click="openOrder">{{ t('paper.manualOrder') }}</n-button>
          <n-button type="error" ghost @click="confirmReset">{{ t('paper.resetAccount') }}</n-button>
        </n-space>
      </template>
    </n-page-header>

    <n-alert type="warning" :show-icon="false">
      {{ t('paper.warning') }}
    </n-alert>

    <!-- Account summary -->
    <n-grid :cols="4" :x-gap="16" v-if="account">
      <n-gi>
        <n-card size="small">
          <n-statistic :label="t('paper.stats.cash')" :value="account.cash.toFixed(2)" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small">
          <n-statistic :label="t('paper.stats.positionsValue')" :value="positionsMarketValue" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small">
          <n-statistic
            :label="t('paper.stats.totalValue')"
            :value="totalValue.toFixed(2)"
            :value-style="{ color: totalPnl >= 0 ? '#d03050' : '#18a058' }"
          />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small">
          <n-statistic
            :label="t('paper.stats.vsInitial', { n: account.initial_cash })"
            :value="`${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)} (${totalPnlPct.toFixed(2)}%)`"
            :value-style="{ color: totalPnl >= 0 ? '#d03050' : '#18a058' }"
          />
        </n-card>
      </n-gi>
    </n-grid>

    <!-- Tabs -->
    <n-card>
      <n-tabs type="line" v-model:value="tab">
        <n-tab-pane name="positions" :tab="t('paper.tabs.positions')">
          <n-data-table
            :columns="positionColumns"
            :data="positions"
            :pagination="{ pageSize: 20 }"
            :bordered="false"
            size="small"
          />
          <n-empty v-if="!positions.length" :description="t('paper.noPositions')" />
        </n-tab-pane>
        <n-tab-pane name="orders" :tab="t('paper.tabs.orders')">
          <n-data-table
            :columns="orderColumns"
            :data="orders"
            :pagination="{ pageSize: 30 }"
            :bordered="false"
            size="small"
          />
          <n-empty v-if="!orders.length" :description="t('paper.noOrders')" />
        </n-tab-pane>
        <n-tab-pane name="nav" :tab="t('paper.tabs.nav')">
          <div v-if="navData.length" style="max-width: 720px; margin: 0 auto">
            <Line :data="chartData" :options="chartOptions" />
          </div>
          <n-empty v-else :description="t('paper.noNav')" />
        </n-tab-pane>
      </n-tabs>
    </n-card>

    <!-- K-line drawer -->
    <n-drawer v-model:show="showKLine" :width="900" placement="right">
      <n-drawer-content :title="t('paper.klineTitle', { ticker: klineTicker })">
        <KLineChart
          v-if="showKLine"
          :ticker="klineTicker"
          :entry-price="klineEntryPrice"
        />
      </n-drawer-content>
    </n-drawer>

    <!-- Sell modal — row-level, pre-fills the target's current holdings -->
    <n-modal v-model:show="showSell" preset="card" :title="t('paper.sellTitle', { ticker: sellTarget?.ticker || '' })" style="width: 460px">
      <n-alert
        v-if="sellTarget && isAShareTicker(sellTarget.ticker)"
        type="warning"
        :show-icon="false"
        style="margin-bottom: 12px; font-size: 12px"
      >
        {{ t('paper.sellAShareWarning') }}
      </n-alert>
      <n-form label-placement="left" label-width="80">
        <n-form-item :label="t('paper.sellFields.current')">
          {{ t('paper.sellFields.currentDesc', { shares: sellTarget?.shares.toFixed(0), cost: sellTarget?.avg_cost.toFixed(2) }) }}
        </n-form-item>
        <n-form-item :label="t('paper.sellFields.sellShares')">
          <n-input-number v-model:value="sellForm.shares" :min="0" :max="sellTarget?.shares" :precision="0" />
        </n-form-item>
        <n-form-item :label="t('paper.sellFields.price')">
          <n-input-number v-model:value="sellForm.price" :min="0" :precision="2" :placeholder="t('paper.sellFields.pricePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('paper.sellFields.empty')">
          <n-text depth="3" style="font-size: 12px">
            {{ t('paper.sellFields.estimated', { amount: ((sellForm.shares || 0) * (sellForm.price || sellTarget?.last_price || 0)).toFixed(2) }) }}
          </n-text>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showSell = false">{{ t('common.cancel') }}</n-button>
          <n-button type="error" :loading="placing" @click="submitSell">{{ t('paper.sellFields.confirm') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Manual order modal -->
    <n-modal v-model:show="showOrder" preset="card" :title="t('paper.manualOrderTitle')" style="width: 480px">
      <n-form label-placement="left" label-width="80">
        <n-form-item :label="t('paper.orderFields.ticker')">
          <n-input v-model:value="orderForm.ticker" :placeholder="t('paper.orderFields.tickerPlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('paper.orderFields.action')">
          <n-radio-group v-model:value="orderForm.action">
            <n-radio value="buy">{{ t('paper.orderFields.buy') }}</n-radio>
            <n-radio value="sell">{{ t('paper.orderFields.sell') }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item :label="t('paper.orderFields.shares')">
          <n-input-number v-model:value="orderForm.shares" :min="0" :precision="2" />
        </n-form-item>
        <n-form-item :label="t('paper.orderFields.price')">
          <n-input-number v-model:value="orderForm.price" :min="0" :precision="2" :placeholder="t('paper.orderFields.pricePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('paper.orderFields.notes')">
          <n-input v-model:value="orderForm.notes" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showOrder = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="placing" @click="placeOrder">{{ t('common.submit') }}</n-button>
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
import KLineChart from '../components/KLineChart.vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import api from '../api'

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
)

interface PaperAccount {
  id: number
  name: string
  initial_cash: number
  cash: number
}
interface PaperPosition {
  id: number
  ticker: string
  asset_type: string
  shares: number
  avg_cost: number
  last_price: number | null
  market_value: number | null
  pnl_amount: number | null
  pnl_pct: number | null
}
interface PaperOrder {
  id: number
  ticker: string
  action: string
  shares: number
  price: number
  source: string
  source_analysis_id: string | null
  notes: string | null
  filled_at: string
}
interface NavRow {
  snapshot_date: string
  cash: number
  positions_value: number
  total_value: number
}

const { t } = useI18n()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const tab = ref('positions')
const account = ref<PaperAccount | null>(null)
const positions = ref<PaperPosition[]>([])
const orders = ref<PaperOrder[]>([])
const navData = ref<NavRow[]>([])

const showOrder = ref(false)
const placing = ref(false)
const snapshotting = ref(false)

const showKLine = ref(false)
const klineTicker = ref('')
const klineEntryPrice = ref<number | null>(null)

function openKLine(ticker: string, avgCost: number) {
  klineTicker.value = ticker
  klineEntryPrice.value = avgCost
  showKLine.value = true
}

// Sell flow — separate from the generic "手动下单" because here the user is
// looking at an existing position and most likely wants to close some
// fraction of it, not type a fresh order from scratch.
const showSell = ref(false)
const sellTarget = ref<PaperPosition | null>(null)
const sellForm = reactive({
  shares: 0,
  price: null as number | null,
})

function isAShareTicker(ticker: string): boolean {
  // Mirror of database.py:_is_a_share_ticker — keep client / server in sync.
  if (!ticker) return false
  const upper = ticker.toUpperCase().trim()
  const digits = upper.replace(/\D/g, '')
  if (digits.length !== 6) return false
  if (upper === digits) return true
  return ['SH', 'SS', 'SZ'].some(m => upper.includes(`.${m}`) || upper.includes(`${m}.`))
}

function openSell(p: PaperPosition) {
  sellTarget.value = p
  sellForm.shares = p.shares
  sellForm.price = p.last_price
  showSell.value = true
}

async function submitSell() {
  if (!sellTarget.value) return
  const target = sellTarget.value
  if (!sellForm.shares || sellForm.shares <= 0) {
    message.warning(t('paper.sellValidation.shares'))
    return
  }
  if (sellForm.shares > target.shares + 1e-9) {
    message.warning(t('paper.sellValidation.notEnough', { max: target.shares }))
    return
  }
  placing.value = true
  try {
    await api.post('/api/paper/orders', {
      ticker: target.ticker,
      action: 'sell',
      shares: sellForm.shares,
      price: sellForm.price || undefined,
      notes: t('paper.sellNote'),
    })
    message.success(t('paper.msg.sold'))
    showSell.value = false
    await loadAll()
  } catch (e: any) {
    message.error(t('paper.msg.sellFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    placing.value = false
  }
}

function confirmFlatten(p: PaperPosition) {
  dialog.warning({
    title: t('paper.flattenTitle'),
    content: t('paper.flattenContent', { shares: p.shares, ticker: p.ticker }),
    positiveText: t('paper.flattenConfirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await api.post('/api/paper/orders', {
          ticker: p.ticker,
          action: 'sell',
          shares: p.shares,
          // No price → backend fills with latest close
          notes: t('paper.flattenNotes'),
        })
        message.success(t('paper.msg.flattened', { ticker: p.ticker }))
        await loadAll()
      } catch (e: any) {
        message.error(t('paper.msg.flattenFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
      }
    },
  })
}
const orderForm = reactive({
  ticker: '',
  action: 'buy' as 'buy' | 'sell',
  shares: 0,
  price: null as number | null,
  notes: '',
})

const positionsMarketValue = computed(() => {
  let sum = 0
  for (const p of positions.value) {
    if (p.market_value != null) sum += p.market_value
    else sum += p.shares * p.avg_cost
  }
  return sum.toFixed(2)
})

const totalValue = computed(() => {
  if (!account.value) return 0
  return account.value.cash + Number(positionsMarketValue.value)
})

const totalPnl = computed(() => {
  if (!account.value) return 0
  return totalValue.value - account.value.initial_cash
})

const totalPnlPct = computed(() => {
  if (!account.value || !account.value.initial_cash) return 0
  return (totalPnl.value / account.value.initial_cash) * 100
})

const positionColumns = computed(() => [
  { title: t('paper.posCols.ticker'), key: 'ticker', width: 110 },
  {
    title: t('paper.posCols.sharesCost'),
    key: 'shares',
    width: 140,
    render(r: PaperPosition) { return `${r.shares} × ${r.avg_cost.toFixed(2)}` },
  },
  {
    title: t('paper.posCols.last'),
    key: 'last_price',
    width: 90,
    render(r: PaperPosition) { return r.last_price != null ? r.last_price.toFixed(2) : '—' },
  },
  {
    title: t('paper.posCols.marketValue'),
    key: 'market_value',
    width: 120,
    render(r: PaperPosition) { return r.market_value != null ? r.market_value.toFixed(2) : '—' },
  },
  {
    title: t('paper.posCols.pnl'),
    key: 'pnl_amount',
    width: 160,
    render(r: PaperPosition) {
      if (r.pnl_amount == null) return '—'
      const color = r.pnl_amount >= 0 ? '#d03050' : '#18a058'
      return h('span', { style: { color } },
        `${r.pnl_amount >= 0 ? '+' : ''}${r.pnl_amount.toFixed(2)} (${r.pnl_pct?.toFixed(2)}%)`)
    },
  },
  {
    title: t('paper.posCols.actions'),
    key: 'actions',
    width: 220,
    render(r: PaperPosition) {
      return h(NSpace, { size: 4 }, () => [
        h(NButton, {
          size: 'tiny',
          type: 'error',
          onClick: () => openSell(r),
        }, () => t('paper.posBtn.sell')),
        h(NButton, {
          size: 'tiny',
          onClick: () => confirmFlatten(r),
        }, () => t('paper.posBtn.flatten')),
        h(NButton, {
          size: 'tiny',
          onClick: () => openKLine(r.ticker, r.avg_cost),
        }, () => t('paper.posBtn.kline')),
      ])
    },
  },
])

const orderColumns = computed(() => [
  { title: t('paper.orderCols.time'), key: 'filled_at', width: 160, render(r: PaperOrder) { return r.filled_at.replace('T', ' ').slice(0, 19) } },
  { title: t('paper.orderCols.ticker'), key: 'ticker', width: 100 },
  {
    title: t('paper.orderCols.action'),
    key: 'action',
    width: 80,
    render(r: PaperOrder) {
      const type = r.action === 'buy' ? 'success' : 'error'
      return h(NTag, { size: 'small', type, bordered: false }, () =>
        r.action === 'buy' ? t('paper.orderFields.buy') : t('paper.orderFields.sell'))
    },
  },
  { title: t('paper.orderCols.shares'), key: 'shares', width: 100 },
  { title: t('paper.orderCols.price'), key: 'price', width: 100, render(r: PaperOrder) { return r.price.toFixed(2) } },
  { title: t('paper.orderCols.amount'), key: 'amount', width: 120, render(r: PaperOrder) { return (r.shares * r.price).toFixed(2) } },
  {
    title: t('paper.orderCols.source'),
    key: 'source',
    width: 130,
    render(r: PaperOrder) {
      if ((r.source === 'decision' || r.source === 'auto') && r.source_analysis_id) {
        const label = r.source === 'auto' ? t('paper.source.auto') : t('paper.source.decision')
        return h('a', {
          style: { color: r.source === 'auto' ? '#d09030' : '#3060d0', cursor: 'pointer' },
          onClick: () => router.push(`/report/${r.source_analysis_id}`),
        }, label)
      }
      if (r.source === 'screen') return t('paper.source.screen')
      return r.source === 'manual' ? t('paper.source.manual') : r.source
    },
  },
  { title: t('paper.orderCols.notes'), key: 'notes', ellipsis: { tooltip: true } },
])

const chartData = computed(() => ({
  labels: navData.value.map(r => r.snapshot_date),
  datasets: [
    {
      label: t('paper.navChart.total'),
      data: navData.value.map(r => r.total_value),
      borderColor: '#d03050',
      backgroundColor: 'rgba(208, 48, 80, 0.12)',
      fill: true,
      tension: 0.3,
    },
    {
      label: t('paper.navChart.initial'),
      data: navData.value.map(() => account.value?.initial_cash || 0),
      borderColor: '#909090',
      borderDash: [4, 4],
      pointRadius: 0,
    },
  ],
}))

const chartOptions = {
  responsive: true,
  maintainAspectRatio: true,
  scales: { y: { beginAtZero: false } },
}

async function loadAll() {
  const [acctRes, posRes, ordRes, navRes] = await Promise.all([
    api.get('/api/paper/account'),
    api.get('/api/paper/positions'),
    api.get('/api/paper/orders'),
    api.get('/api/paper/nav'),
  ])
  account.value = acctRes.data
  positions.value = posRes.data.items || []
  orders.value = ordRes.data.items || []
  navData.value = navRes.data.items || []
}

function openOrder() {
  orderForm.ticker = ''
  orderForm.action = 'buy'
  orderForm.shares = 0
  orderForm.price = null
  orderForm.notes = ''
  showOrder.value = true
}

async function placeOrder() {
  if (!orderForm.ticker || orderForm.shares <= 0) {
    message.warning(t('paper.orderValidation'))
    return
  }
  placing.value = true
  try {
    await api.post('/api/paper/orders', {
      ticker: orderForm.ticker.trim().toUpperCase(),
      action: orderForm.action,
      shares: orderForm.shares,
      price: orderForm.price || undefined,
      notes: orderForm.notes || undefined,
    })
    message.success(t('paper.msg.placed'))
    showOrder.value = false
    await loadAll()
  } catch (e: any) {
    message.error(t('paper.msg.placeFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    placing.value = false
  }
}

async function takeSnapshot() {
  snapshotting.value = true
  try {
    await api.post('/api/paper/nav/snapshot')
    message.success(t('paper.msg.snapshotted'))
    await loadAll()
    tab.value = 'nav'
  } catch (e: any) {
    message.error(t('paper.msg.snapshotFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    snapshotting.value = false
  }
}

function confirmReset() {
  dialog.warning({
    title: t('paper.resetTitle'),
    content: t('paper.resetContent'),
    positiveText: t('paper.resetConfirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await api.post('/api/paper/account/reset', { confirm: true })
        message.success(t('paper.msg.reset'))
        await loadAll()
      } catch (e: any) {
        message.error(t('paper.msg.resetFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
      }
    },
  })
}

onMounted(loadAll)
</script>
