<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('analyze.title')" :subtitle="t('analyze.subtitle')" />

    <!-- Natural-language quick entry -->
    <n-card :title="t('analyze.smartParse')" :bordered="true">
      <template #header-extra>
        <n-tag size="small" :bordered="false" type="info">Beta</n-tag>
      </template>
      <n-input-group>
        <n-input
          v-model:value="nlQuery"
          :placeholder="t('analyze.smartParsePlaceholder')"
          @keyup.enter="runParse"
          clearable
        />
        <n-button
          type="primary"
          :loading="parsing"
          :disabled="!nlQuery.trim()"
          @click="runParse"
        >{{ t('analyze.runParse') }}</n-button>
      </n-input-group>
      <n-space style="margin-top: 10px" :size="12">
        <n-checkbox v-model:checked="useLlmFallback">
          {{ t('analyze.useLlmFallback') }}
        </n-checkbox>
        <n-text depth="3" style="font-size: 12px">
          {{ t('analyze.parseHint') }}
        </n-text>
      </n-space>
      <n-alert
        v-if="parseResult"
        :type="parseResultType"
        style="margin-top: 12px"
        :bordered="false"
      >
        <template #header>
          <n-space :size="8" align="center">
            <span>{{ parseResultTitle }}</span>
            <n-tag size="tiny" :bordered="false">{{ parseResult.source }}</n-tag>
            <n-tag size="tiny" :bordered="false">{{ t('analyze.confidence') }} {{ parseResult.confidence }}</n-tag>
          </n-space>
        </template>
        {{ parseResult.notes }}
      </n-alert>
    </n-card>

    <n-card>
      <n-form ref="formRef" :model="form" label-placement="left" label-width="120">
        <n-form-item :label="t('common.ticker')" path="ticker">
          <n-input v-model:value="form.ticker" :placeholder="t('analyze.tickerPlaceholder')" />
        </n-form-item>

        <n-form-item :label="t('analyze.tradeDate')" path="trade_date">
          <n-date-picker v-model:formatted-value="form.trade_date" type="date" value-format="yyyy-MM-dd" />
        </n-form-item>

        <n-form-item :label="t('common.assetType')">
          <n-radio-group v-model:value="form.asset_type">
            <n-radio value="stock">{{ t('common.stock') }}</n-radio>
            <n-radio value="crypto">{{ t('common.crypto') }}</n-radio>
          </n-radio-group>
        </n-form-item>

        <n-form-item :label="t('analyze.analystTeam')">
          <n-space vertical :size="4" style="width: 100%">
          <n-checkbox-group v-model:value="form.analysts">
            <n-space>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><n-checkbox value="market" :label="t('analyze.analysts.market')" /></template>
                {{ t('analyze.analysts.marketDesc') }}
              </n-tooltip>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><n-checkbox value="social" :label="t('analyze.analysts.social')" /></template>
                {{ t('analyze.analysts.socialDesc') }}
              </n-tooltip>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><n-checkbox value="news" :label="t('analyze.analysts.news')" /></template>
                {{ t('analyze.analysts.newsDesc') }}
              </n-tooltip>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><n-checkbox value="fundamentals" :label="t('analyze.analysts.fundamentals')" /></template>
                {{ t('analyze.analysts.fundamentalsDesc') }}
              </n-tooltip>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><n-checkbox value="cn_social" :label="t('analyze.analysts.cnSocial')" /></template>
                {{ t('analyze.analysts.cnSocialDesc') }}
              </n-tooltip>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><n-checkbox value="event" :label="t('analyze.analysts.event')" /></template>
                {{ t('analyze.analysts.eventDesc') }}
              </n-tooltip>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><n-checkbox value="capital_flow" :label="t('analyze.analysts.capitalFlow')" /></template>
                {{ t('analyze.analysts.capitalFlowDesc') }}
              </n-tooltip>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><n-checkbox value="macro" :label="t('analyze.analysts.macro')" /></template>
                {{ t('analyze.analysts.macroDesc') }}
              </n-tooltip>
            </n-space>
          </n-checkbox-group>
          <n-text depth="3" style="font-size: 12px">
            {{ analystHint }}
          </n-text>
          </n-space>
        </n-form-item>

        <n-form-item :label="t('analyze.debateDepth')">
          <n-slider v-model:value="form.max_debate_rounds" :min="1" :max="5" :step="2"
                    :marks="debateMarks" />
        </n-form-item>

        <n-form-item :label="t('analyze.riskRounds')">
          <n-slider v-model:value="form.max_risk_discuss_rounds" :min="1" :max="3" :step="1" />
        </n-form-item>

        <n-form-item :label="t('analyze.provider')">
          <n-select v-model:value="form.llm_provider" :options="providerOptions" clearable :placeholder="t('analyze.providerPlaceholder')" />
        </n-form-item>

        <n-form-item :label="t('analyze.deepModel')">
          <ModelPicker
            v-model="form.deep_think_llm"
            :options="deepModelOptions"
            :placeholder="t('analyze.deepModelPlaceholder')"
            :free-text-placeholder="t('analyze.defaultPlaceholder')"
          />
        </n-form-item>

        <n-form-item :label="t('analyze.quickModel')">
          <ModelPicker
            v-model="form.quick_think_llm"
            :options="quickModelOptions"
            :placeholder="t('analyze.quickModelPlaceholder')"
            :free-text-placeholder="t('analyze.defaultPlaceholder')"
          />
        </n-form-item>

        <n-form-item :label="t('analyze.outputLanguage')">
          <n-select v-model:value="form.output_language" :options="langOptions" clearable />
        </n-form-item>

        <n-form-item :label="t('analyze.checkpoint')">
          <n-switch v-model:value="form.checkpoint_enabled" />
        </n-form-item>

        <n-form-item>
          <n-button type="primary" size="large" :loading="submitting" @click="submit">
            {{ t('analyze.startAnalysis') }}
          </n-button>
        </n-form-item>
      </n-form>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter, useRoute } from 'vue-router'
import { useAnalysisStore } from '../stores/analysis'
import { useSettingsStore } from '../stores/settings'
import { useMessage } from 'naive-ui'
import api from '../api'
import ModelPicker from '../components/ModelPicker.vue'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const store = useAnalysisStore()
const settingsStore = useSettingsStore()
const message = useMessage()
const submitting = ref(false)

// --- NL parse ---

interface ParsedQuery {
  ticker: string
  company_name: string
  trade_date: string
  period_days: number
  period_label: string
  confidence: number
  source: string
  notes: string
}

const nlQuery = ref('')
const useLlmFallback = ref(false)
const parsing = ref(false)
const parseResult = ref<ParsedQuery | null>(null)

const parseResultType = computed<'success' | 'warning' | 'error'>(() => {
  if (!parseResult.value) return 'success'
  const c = parseResult.value.confidence
  if (!parseResult.value.ticker) return 'error'
  if (c >= 0.8) return 'success'
  return 'warning'
})

const parseResultTitle = computed(() => {
  if (!parseResult.value) return ''
  if (!parseResult.value.ticker) return t('analyze.parseFailedTitle')
  const name = parseResult.value.company_name
    ? `${parseResult.value.company_name} (${parseResult.value.ticker})`
    : parseResult.value.ticker
  return t('analyze.parsedAs') + name
})

const debateMarks = computed(() => ({
  1: t('analyze.debateMarks.fast'),
  3: t('analyze.debateMarks.standard'),
  5: t('analyze.debateMarks.deep'),
}))

async function runParse() {
  if (!nlQuery.value.trim()) return
  parsing.value = true
  parseResult.value = null
  try {
    const { data } = await api.post('/api/parse-query', {
      text: nlQuery.value,
      use_llm_fallback: useLlmFallback.value,
    })
    const r: ParsedQuery = data.result
    parseResult.value = r
    if (r.ticker) {
      form.ticker = r.ticker
      if (r.trade_date) form.trade_date = r.trade_date
      message.success(t('analyze.parseSuccessTip'))
    } else {
      message.warning(t('analyze.parseEmpty'))
    }
  } catch (e: any) {
    message.error(t('analyze.parseFailPrefix') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    parsing.value = false
  }
}

const form = reactive({
  ticker: '',
  trade_date: null as string | null,
  asset_type: 'stock',
  analysts: ['market', 'social', 'news', 'fundamentals', 'cn_social', 'event'] as string[],
  max_debate_rounds: 1,
  max_risk_discuss_rounds: 1,
  llm_provider: null as string | null,
  deep_think_llm: '',
  quick_think_llm: '',
  output_language: null as string | null,
  checkpoint_enabled: false,
})

const deepModelOptions = computed(() => {
  const p = (form.llm_provider || '').toLowerCase()
  return settingsStore.modelCatalog[p]?.deep || []
})
const quickModelOptions = computed(() => {
  const p = (form.llm_provider || '').toLowerCase()
  return settingsStore.modelCatalog[p]?.quick || []
})

onMounted(() => {
  settingsStore.fetchModelCatalog()
  // Pre-fill from query params (typically from the Holdings "Analyze" button).
  const qTicker = route.query.ticker
  const qAsset = route.query.asset_type
  if (typeof qTicker === 'string' && qTicker) form.ticker = qTicker
  if (typeof qAsset === 'string' && qAsset) form.asset_type = qAsset
})

// --- Auto-tune analyst defaults by ticker class ---
// Picks the most useful analysts per asset class. Only fires when the
// classification actually CHANGES, so typo-typing inside the same class
// doesn't trample manual unchecks.
type TickerClass = 'cn' | 'us' | 'hk' | 'crypto' | 'unknown'

function classifyTicker(ticker: string, assetType: string): TickerClass {
  if (assetType === 'crypto') return 'crypto'
  const tk = (ticker || '').trim().toUpperCase()
  if (!tk) return 'unknown'
  if (/\.(SS|SZ|SH|BJ)$/i.test(tk)) return 'cn'   // 600519.SS / 300750.SZ / 688981.SH / 430090.BJ
  if (/^\d{6}$/.test(tk)) return 'cn'             // bare A-share code
  if (/\.HK$/i.test(tk)) return 'hk'              // 0700.HK
  if (/^\d{4,5}$/.test(tk)) return 'hk'           // bare HK code
  return 'us'
}

const DEFAULTS_BY_CLASS: Record<TickerClass, string[]> = {
  cn:      ['market', 'news', 'fundamentals', 'cn_social', 'event', 'capital_flow', 'macro'],
  us:      ['market', 'social', 'news', 'fundamentals', 'event'],
  hk:      ['market', 'social', 'news', 'fundamentals', 'event', 'capital_flow'],
  crypto:  ['market', 'news', 'event'],
  unknown: ['market', 'social', 'news', 'fundamentals', 'cn_social', 'event'],
}

let currentClass: TickerClass = classifyTicker(form.ticker, form.asset_type)

const analystHint = computed(() => {
  const c = classifyTicker(form.ticker, form.asset_type)
  const cls = t(`analyze.tickerClass.${c}`)
  return t('analyze.analystHint', { cls })
})

watch(
  [() => form.ticker, () => form.asset_type],
  ([newTicker, newAsset]) => {
    const next = classifyTicker(newTicker, newAsset)
    if (next === currentClass) return
    currentClass = next
    form.analysts = [...DEFAULTS_BY_CLASS[next]]
  },
)

const providerOptions = [
  { label: 'DeepSeek', value: 'deepseek' },
  { label: 'OpenAI', value: 'openai' },
  { label: 'Anthropic', value: 'anthropic' },
  { label: 'Google', value: 'google' },
  { label: 'xAI', value: 'xai' },
  { label: 'Qwen', value: 'qwen' },
  { label: 'GLM', value: 'glm' },
  { label: 'MiniMax', value: 'minimax' },
  { label: 'OpenRouter', value: 'openrouter' },
  { label: 'Ollama', value: 'ollama' },
  { label: 'Azure', value: 'azure' },
]

const langOptions = [
  { label: '中文', value: 'Chinese' },
  { label: 'English', value: 'English' },
  { label: '日本語', value: 'Japanese' },
]

async function submit() {
  if (!form.ticker || !form.trade_date) return
  submitting.value = true
  try {
    const id = await store.startAnalysis(form)
    router.push(`/progress/${id}`)
  } finally {
    submitting.value = false
  }
}
</script>
