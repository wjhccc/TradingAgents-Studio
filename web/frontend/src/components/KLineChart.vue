<template>
  <div ref="wrapEl" class="k-line-wrap" :class="{ 'is-fullscreen': isFullscreen }">
    <div class="k-line-toolbar">
      <n-space size="small" align="center" :wrap-item="false" wrap>
        <n-radio-group v-model:value="interval" size="small" @update:value="onIntervalChange">
          <n-radio-button value="1min" :disabled="!isAShare">1m</n-radio-button>
          <n-radio-button value="5min" :disabled="!isAShare">5m</n-radio-button>
          <n-radio-button value="15min" :disabled="!isAShare">15m</n-radio-button>
          <n-radio-button value="30min" :disabled="!isAShare">30m</n-radio-button>
          <n-radio-button value="60min" :disabled="!isAShare">60m</n-radio-button>
          <n-radio-button value="daily">{{ t('kline.daily') }}</n-radio-button>
        </n-radio-group>
        <n-radio-group v-if="interval === 'daily'" v-model:value="lookback" size="small" @update:value="reload">
          <n-radio-button :value="30">{{ t('kline.days30') }}</n-radio-button>
          <n-radio-button :value="60">{{ t('kline.days60') }}</n-radio-button>
          <n-radio-button :value="120">{{ t('kline.days120') }}</n-radio-button>
          <n-radio-button :value="250">{{ t('kline.year1') }}</n-radio-button>
        </n-radio-group>
        <n-switch v-model:value="autoRefresh" size="small" @update:value="onAutoRefreshChange">
          <template #checked>{{ t('kline.autoRefresh') }}</template>
          <template #unchecked>{{ t('kline.manualRefresh') }}</template>
        </n-switch>
        <n-button size="small" :loading="loading" @click="reload">{{ t('kline.refresh') }}</n-button>
        <n-button size="small" quaternary @click="toggleFullscreen">
          {{ isFullscreen ? t('kline.exitFullscreen') : t('kline.fullscreen') }}
        </n-button>
      </n-space>
      <n-text depth="3" style="font-size: 12px; margin-left: 4px">
        <template v-if="interval !== 'daily'">
          {{ t('kline.minuteHint') }}
        </template>
        <template v-else>
          {{ t('kline.dailyHint') }}
        </template>
        <template v-if="latestBarLabel"> · {{ t('kline.latestBar', { label: latestBarLabel }) }}</template>
      </n-text>
    </div>
    <div ref="chartEl" class="k-line-chart"></div>
    <n-empty v-if="error" :description="error" size="small" style="padding: 24px" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { init, dispose } from 'klinecharts'
import type { Chart } from 'klinecharts'
import api from '../api'

const { t, locale } = useI18n()

const props = defineProps<{
  ticker: string
  entryPrice?: number | null
  targetPrice?: number | null
  stopLoss?: number | null
  redUp?: boolean
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const wrapEl = ref<HTMLDivElement | null>(null)
const interval = ref<'1min' | '5min' | '15min' | '30min' | '60min' | 'daily'>('daily')
const lookback = ref(60)
const loading = ref(false)
const error = ref('')
const latestBarLabel = ref('')
const autoRefresh = ref(true)
const isFullscreen = ref(false)

// "Is this an A-share code?" — 6-digit number, optionally with .SH/.SZ/.SS
// suffix, or sh/sz prefix. Used to disable minute-bar tabs for non-A-share.
const isAShare = computed(() => {
  const tk = (props.ticker || '').toUpperCase().trim()
  if (/^\d{6}$/.test(tk)) return true
  if (/^\d{6}\.(SH|SS|SZ)$/.test(tk)) return true
  if (/^(SH|SZ)\.?\d{6}$/.test(tk)) return true
  return false
})

let chart: Chart | null = null
let refreshTimer: number | null = null
let intervalDebounce: number | null = null

function createChart() {
  if (!chartEl.value) return
  chart = init(chartEl.value)
  if (!chart) return
  const upColor = props.redUp === false ? '#18a058' : '#d03050'
  const downColor = props.redUp === false ? '#d03050' : '#18a058'
  chart.setStyles({
    candle: {
      bar: {
        upColor, downColor,
        upBorderColor: upColor, downBorderColor: downColor,
        upWickColor: upColor, downWickColor: downColor,
      },
      tooltip: { showRule: 'always' },
    },
    indicator: {
      lines: [
        { color: '#f0a020' }, { color: '#3060d0' }, { color: '#7060d0' },
      ],
    },
    xAxis: {
      tickText: {
        // Format tick labels to skip the 00:00 on daily bars — purely
        // cosmetic so the axis looks like Eastmoney/Snowball not like a
        // tick-level chart with everything stuck at midnight.
        // (klinecharts honours the global timezone; we leave that default
        // and just shorten the format.)
      },
    },
  })
  chart.createIndicator('MA', false, { id: 'candle_pane' })
  chart.createIndicator('VOL', false)
}

function disposeChart() {
  stopAutoRefresh()
  if (intervalDebounce) {
    window.clearTimeout(intervalDebounce)
    intervalDebounce = null
  }
  if (chartEl.value) {
    dispose(chartEl.value)
  }
  chart = null
}

function onIntervalChange() {
  // Debounce rapid interval switches — when the user clicks through
  // several tabs in quick succession we only want one network request.
  // Without this, eastmoney's push2his endpoint will RST the 2nd+
  // request and the page flashes errors for every aborted load.
  if (!chart) return
  if (intervalDebounce) {
    window.clearTimeout(intervalDebounce)
  }
  intervalDebounce = window.setTimeout(() => {
    intervalDebounce = null
    reload()
    restartAutoRefresh()
  }, 300)
}

function onAutoRefreshChange() {
  restartAutoRefresh()
}

function toggleFullscreen() {
  // Use the CSS-overlay approach instead of the Fullscreen API: avoids the
  // pop-up "is now full screen" prompt + always works inside iframes/PWAs.
  isFullscreen.value = !isFullscreen.value
  // klinecharts measures its container at init time and doesn't auto-resize
  // when the container's dimensions change. Call its resize() on the next
  // tick after CSS has reflowed.
  setTimeout(() => {
    chart?.resize()
  }, 50)
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && isFullscreen.value) {
    isFullscreen.value = false
    setTimeout(() => chart?.resize(), 50)
  }
}

function restartAutoRefresh() {
  stopAutoRefresh()
  if (!autoRefresh.value) return
  // Minute bars: refresh every 30s (AKShare data lags ~30-60s anyway).
  // Daily: refresh every 60s during A-share trading hours.
  const periodMs = interval.value === 'daily' ? 60_000 : 30_000
  refreshTimer = window.setInterval(() => {
    // Skip refresh outside A-share trading hours when on intraday — the
    // data won't change and we're just spending requests for nothing.
    if (interval.value !== 'daily' && !isAShareTradingNow()) return
    reload()
  }, periodMs)
}

function stopAutoRefresh() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
}

function isAShareTradingNow(): boolean {
  // Browser local time — fine in practice since users running this UI
  // are almost always in CST. Sessions: 09:30-11:30, 13:00-15:00 weekdays.
  const now = new Date()
  if (now.getDay() === 0 || now.getDay() === 6) return false
  const hm = now.getHours() * 60 + now.getMinutes()
  return (hm >= 9 * 60 + 25 && hm <= 11 * 60 + 32)
      || (hm >= 12 * 60 + 58 && hm <= 15 * 60 + 5)
}

async function reload() {
  if (!props.ticker || !chart) return
  loading.value = true
  error.value = ''
  try {
    const params: Record<string, any> = { interval: interval.value }
    if (interval.value === 'daily') {
      params.days = lookback.value
    } else {
      // Cap minute-bar history at ~240 bars (~1 trading day for 1-min,
      // multiple days for coarser intervals).
      params.days = 240
    }
    const { data } = await api.get(`/api/quote/${encodeURIComponent(props.ticker)}/ohlc`, { params })
    if (!data.bars || !data.bars.length) {
      error.value = t('kline.noData')
      chart.applyNewData([])
      latestBarLabel.value = ''
      return
    }
    chart.applyNewData(data.bars)
    const last = data.bars[data.bars.length - 1]
    const fmt = interval.value === 'daily'
      ? { year: 'numeric', month: '2-digit', day: '2-digit' } as const
      : { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' } as const
    latestBarLabel.value = new Date(last.timestamp).toLocaleString(locale.value, fmt)
    addPriceLines()
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || t('kline.loadFailed')
    error.value = detail
    // For minute bars on a non-A-share ticker, downgrade gracefully to daily.
    if (interval.value !== 'daily' && !isAShare.value) {
      interval.value = 'daily'
      reload()
    }
  } finally {
    loading.value = false
  }
}

function addPriceLines() {
  if (!chart) return
  for (const id of ['line-entry', 'line-target', 'line-stop']) {
    try { chart.removeOverlay(id) } catch { /* not present yet */ }
  }
  const overlays: Array<[string, number, string]> = []
  if (props.entryPrice && props.entryPrice > 0) {
    overlays.push(['line-entry', props.entryPrice, '#909090'])
  }
  if (props.targetPrice && props.targetPrice > 0) {
    overlays.push(['line-target', props.targetPrice, '#18a058'])
  }
  if (props.stopLoss && props.stopLoss > 0) {
    overlays.push(['line-stop', props.stopLoss, '#d03050'])
  }
  for (const [id, price, color] of overlays) {
    try {
      chart.createOverlay({
        id,
        name: 'horizontalStraightLine',
        points: [{ value: price }],
        styles: { line: { color, style: 'dashed', size: 1 } },
      })
    } catch {
      // Older klinecharts builds may not have this overlay; non-essential.
    }
  }
}

onMounted(() => {
  createChart()
  // Default to 5-min for A-share, daily otherwise.
  if (isAShare.value) {
    interval.value = '5min'
  }
  reload()
  restartAutoRefresh()
  window.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  disposeChart()
})

watch(() => props.ticker, () => {
  // Re-evaluate the default interval for the new ticker.
  interval.value = isAShare.value ? '5min' : 'daily'
  reload()
  restartAutoRefresh()
})

watch(
  () => [props.entryPrice, props.targetPrice, props.stopLoss],
  addPriceLines,
)
</script>

<style scoped>
.k-line-wrap {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.k-line-toolbar {
  padding: 4px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.k-line-chart {
  width: 100%;
  height: 420px;
  border: 1px solid #f0f0f5;
  border-radius: 4px;
}

/* Fullscreen mode: take over the viewport with a fixed overlay so the
   chart isn't constrained by the drawer width. ESC also exits. */
.k-line-wrap.is-fullscreen {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  width: 100vw;
  height: 100vh;
  background: #ffffff;
  z-index: 4000;
  padding: 16px;
  gap: 12px;
}
.k-line-wrap.is-fullscreen .k-line-chart {
  flex: 1;
  height: auto;
}
</style>
