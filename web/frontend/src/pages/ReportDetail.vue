<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('report.titlePrefix', { ticker: analysis?.ticker || '' })" :subtitle="analysis?.trade_date">
      <template #extra>
        <n-space>
          <n-tag :type="signalType" size="large">{{ analysis?.signal || 'N/A' }}</n-tag>
          <n-button @click="exportMd">{{ t('report.exportMd') }}</n-button>
          <n-button :disabled="!canPaperOrder" @click="showPaperOrder = true">{{ t('report.paperOrderBtn') }}</n-button>
          <n-button type="primary" @click="reAnalyze">{{ t('report.reAnalyze') }}</n-button>
        </n-space>
      </template>
    </n-page-header>

    <n-spin :show="loading">
      <!-- Summary Card -->
      <n-card v-if="analysis" size="small">
        <n-descriptions :column="4" bordered>
          <n-descriptions-item :label="t('report.summary.ticker')">{{ analysis.ticker }}</n-descriptions-item>
          <n-descriptions-item :label="t('report.summary.date')">{{ analysis.trade_date }}</n-descriptions-item>
          <n-descriptions-item :label="t('report.summary.signal')">{{ analysis.signal }}</n-descriptions-item>
          <n-descriptions-item :label="t('report.summary.confidence')">{{ analysis.confidence || 'N/A' }}</n-descriptions-item>
          <n-descriptions-item :label="t('report.summary.status')">{{ analysis.status }}</n-descriptions-item>
          <n-descriptions-item :label="t('report.summary.createdAt')">{{ analysis.created_at }}</n-descriptions-item>
          <n-descriptions-item :label="t('report.summary.completedAt')">{{ analysis.completed_at || '-' }}</n-descriptions-item>
          <n-descriptions-item :label="t('report.summary.assetType')">{{ analysis.asset_type }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- Report Tabs -->
      <n-card style="margin-top: 16px">
        <n-tabs type="line">
          <n-tab-pane
            v-for="tab in tabs"
            :key="tab.key"
            :name="tab.key"
            :tab="tab.label"
          >
            <!-- Event report → structured -->
            <EventReport v-if="tab.key === 'event'" :content="tab.content" />

            <!-- Investment debate → bubbles -->
            <DebateThread
              v-else-if="tab.key === 'invest_debate'"
              :bull-history="bullHistory"
              :bear-history="bearHistory"
              :empty-text="t('report.investDebateEmpty')"
            />

            <!-- Risk debate → bubbles -->
            <DebateThread
              v-else-if="tab.key === 'risk_debate'"
              :history="tab.content"
              :empty-text="t('report.riskDebateEmpty')"
            />

            <!-- Other reports → markdown -->
            <div v-else class="markdown-body" v-html="renderMd(tab.content)"></div>
          </n-tab-pane>
        </n-tabs>
        <n-empty v-if="!tabs.length" :description="t('report.noReports')" />
      </n-card>

      <!-- Event Timeline -->
      <n-card :title="t('report.timelineTitle')" style="margin-top: 16px" v-if="events.length">
        <n-timeline>
          <n-timeline-item
            v-for="ev in events"
            :key="ev.id"
            :type="ev.event_type === 'error' ? 'error' : 'success'"
            :title="ev.agent_name"
            :time="ev.timestamp"
          />
        </n-timeline>
      </n-card>
    </n-spin>

    <!-- Paper-order modal — fires POST /api/paper/orders/from-decision -->
    <n-modal v-model:show="showPaperOrder" preset="card" :title="t('report.paperOrder.title')" style="width: 480px">
      <n-alert type="warning" :show-icon="false" style="margin-bottom: 12px">
        {{ t('report.paperOrder.warning') }}
      </n-alert>
      <n-form label-placement="left" label-width="100">
        <n-form-item :label="t('report.paperOrder.mode')">
          <n-radio-group v-model:value="paperForm.mode">
            <n-radio value="shares">{{ t('report.paperOrder.modeShares') }}</n-radio>
            <n-radio value="fraction">{{ t('report.paperOrder.modeFraction') }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="paperForm.mode === 'shares'" :label="t('report.paperOrder.shares')">
          <n-input-number v-model:value="paperForm.shares" :min="0" :precision="2" />
        </n-form-item>
        <n-form-item v-else :label="t('report.paperOrder.fraction')">
          <n-slider v-model:value="paperForm.cashFractionPct" :min="5" :max="100" :step="5" :marks="fractionMarks" />
        </n-form-item>
        <n-form-item :label="t('report.paperOrder.priceOverride')">
          <n-input-number v-model:value="paperForm.price" :min="0" :precision="2" :placeholder="t('report.paperOrder.priceOverridePlaceholder')" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showPaperOrder = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="placing" @click="submitPaperOrder">{{ t('common.submit') }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import { useMessage } from 'naive-ui'
import api from '../api'
import EventReport from '../components/EventReport.vue'
import DebateThread from '../components/DebateThread.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const message = useMessage()
const analysisId = route.params.id as string

const loading = ref(true)
const analysis = ref<any>(null)
const reports = ref<any[]>([])
const events = ref<any[]>([])

const showPaperOrder = ref(false)
const placing = ref(false)
const paperForm = reactive({
  mode: 'fraction' as 'shares' | 'fraction',
  shares: 0,
  cashFractionPct: 25,
  price: null as number | null,
})

const fractionMarks = computed(() => ({
  25: t('report.paperOrder.fractionMarks.q'),
  50: t('report.paperOrder.fractionMarks.h'),
  100: t('report.paperOrder.fractionMarks.f'),
}))

// Enable the button only when the analysis is complete and its signal is
// actionable (BUY/SELL). HOLD intentionally cannot place a paper order.
const canPaperOrder = computed(() => {
  if (!analysis.value || analysis.value.status !== 'complete') return false
  return analysis.value.signal === 'BUY' || analysis.value.signal === 'SELL'
})

async function submitPaperOrder() {
  const payload: any = { analysis_id: analysisId }
  if (paperForm.mode === 'shares') {
    if (!paperForm.shares || paperForm.shares <= 0) {
      message.warning(t('report.paperOrder.validation'))
      return
    }
    payload.shares = paperForm.shares
  } else {
    payload.cash_fraction = paperForm.cashFractionPct / 100
  }
  if (paperForm.price && paperForm.price > 0) payload.price = paperForm.price
  placing.value = true
  try {
    const { data } = await api.post('/api/paper/orders/from-decision', payload)
    const action = data.action === 'buy' ? t('report.paperOrder.buy') : t('report.paperOrder.sell')
    message.success(t('report.paperOrder.placed', { action, shares: data.shares, price: data.price }))
    showPaperOrder.value = false
  } catch (e: any) {
    message.error(t('report.paperOrder.failed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    placing.value = false
  }
}

const signalType = computed(() => {
  const s = analysis.value?.signal
  if (s === 'BUY') return 'success'
  if (s === 'SELL') return 'error'
  return 'warning'
})

// Order in which we want tabs to appear in the UI.
const TAB_ORDER = [
  'macro',
  'market',
  'sentiment',
  'cn_sentiment',
  'news',
  'fundamentals',
  'capital_flow',
  'event',
  'invest_debate',   // synthetic: combines bull_debate + bear_debate
  'research_plan',
  'trader_proposal',
  'risk_debate',
  'final_decision',
]

const tabLabels = computed<Record<string, string>>(() => ({
  macro: t('report.tabs.macro'),
  market: t('report.tabs.market'),
  sentiment: t('report.tabs.sentiment'),
  cn_sentiment: t('report.tabs.cnSentiment'),
  news: t('report.tabs.news'),
  fundamentals: t('report.tabs.fundamentals'),
  capital_flow: t('report.tabs.capitalFlow'),
  event: t('report.tabs.event'),
  invest_debate: t('report.tabs.investDebate'),
  research_plan: t('report.tabs.researchPlan'),
  trader_proposal: t('report.tabs.traderProposal'),
  risk_debate: t('report.tabs.riskDebate'),
  final_decision: t('report.tabs.finalDecision'),
}))

interface Tab {
  key: string
  label: string
  content: string
}

const bullHistory = computed(() => reportByType('bull_debate'))
const bearHistory = computed(() => reportByType('bear_debate'))

function reportByType(ty: string): string {
  return reports.value.find(r => r.report_type === ty)?.content || ''
}

const tabs = computed<Tab[]>(() => {
  // Index reports by report_type so we can look them up cheaply.
  const byType: Record<string, string> = {}
  for (const r of reports.value) {
    byType[r.report_type] = r.content
  }
  // Synthesize a single invest_debate tab from bull+bear histories.
  const investContent = byType['bull_debate'] || byType['bear_debate']
    ? '__has_invest_debate__'
    : ''
  if (investContent) {
    byType['invest_debate'] = investContent
  }

  const out: Tab[] = []
  for (const key of TAB_ORDER) {
    const content = byType[key]
    if (!content) continue
    out.push({ key, label: tabLabels.value[key] || key, content })
  }
  // Surface any unknown report types at the end so nothing gets lost.
  for (const r of reports.value) {
    if (TAB_ORDER.includes(r.report_type)) continue
    if (r.report_type === 'bull_debate' || r.report_type === 'bear_debate') continue
    out.push({
      key: r.report_type,
      label: tabLabels.value[r.report_type] || r.report_type,
      content: r.content,
    })
  }
  return out
})

function renderMd(content: string) {
  return marked(content || '')
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get(`/api/reports/${analysisId}`)
    analysis.value = data.analysis
    reports.value = data.reports || []
    events.value = data.events || []
  } finally {
    loading.value = false
  }
}

async function exportMd() {
  const { data } = await api.get(`/api/reports/${analysisId}/export?format=md`)
  const blob = new Blob([data.content], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `report_${analysis.value?.ticker}_${analysis.value?.trade_date}.md`
  a.click()
  URL.revokeObjectURL(url)
}

function reAnalyze() {
  router.push('/analyze')
}

onMounted(load)
</script>

<style scoped>
.markdown-body {
  font-size: 14px;
  line-height: 1.8;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin-top: 16px;
  margin-bottom: 8px;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #e0e0e6;
  padding: 8px 12px;
  text-align: left;
}
.markdown-body :deep(th) {
  background: #f7f7fa;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
}
.markdown-body :deep(code) {
  background: #f5f5f5;
  padding: 2px 4px;
  border-radius: 3px;
}
.markdown-body :deep(blockquote) {
  border-left: 4px solid #d0d0d0;
  padding-left: 12px;
  color: #666;
  margin: 12px 0;
}
</style>
