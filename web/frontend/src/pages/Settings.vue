<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('settings.title')" :subtitle="t('settings.subtitle')" />

    <n-spin :show="loading">
      <n-card :title="t('settings.llmCard')">
        <n-form label-placement="left" label-width="160">
          <n-form-item :label="t('settings.provider')">
            <n-select v-model:value="form.llm_provider" :options="providerOptions" />
          </n-form-item>
          <n-form-item :label="t('settings.deepModel')">
            <ModelPicker
              v-model="form.deep_think_llm"
              :options="deepModelOptions"
              :placeholder="t('settings.deepPlaceholder')"
            />
          </n-form-item>
          <n-form-item :label="t('settings.quickModel')">
            <ModelPicker
              v-model="form.quick_think_llm"
              :options="quickModelOptions"
              :placeholder="t('settings.quickPlaceholder')"
            />
          </n-form-item>
        </n-form>
      </n-card>

      <n-card :title="t('settings.apiKeysCard')" style="margin-top: 16px">
        <template #header-extra>
          <n-text depth="3" style="font-size: 12px">
            {{ t('settings.apiKeysHeaderExtra') }} <n-text code>.env</n-text>{{ t('settings.apiKeysHeaderSuffix') }}
          </n-text>
        </template>
        <n-alert type="info" :show-icon="false" style="margin-bottom: 16px">
          {{ t('settings.apiKeysInfo') }}
        </n-alert>
        <n-form label-placement="left" label-width="160" :show-feedback="false">
          <n-form-item
            v-for="row in keyRows"
            :key="row.provider"
            :label="row.provider"
          >
            <n-input-group>
              <n-input
                v-model:value="keyInputs[row.provider]"
                type="password"
                show-password-on="click"
                :placeholder="placeholderFor(row)"
                :disabled="!row.required"
              />
              <n-button
                v-if="row.required && row.set"
                @click="clearKey(row.provider)"
                :disabled="clearing === row.provider"
              >{{ t('settings.clearKey') }}</n-button>
            </n-input-group>
            <template #suffix>
              <n-tag v-if="!row.required" size="small" :bordered="false">{{ t('settings.keyNoNeed') }}</n-tag>
              <n-tag v-else-if="row.set" size="small" type="success" :bordered="false">
                {{ t('settings.keySet') }} · {{ row.env_var }}
              </n-tag>
              <n-tag v-else size="small" :bordered="false">{{ t('settings.keyUnset') }} · {{ row.env_var }}</n-tag>
            </template>
          </n-form-item>
        </n-form>
        <n-button
          type="primary"
          :loading="savingKeys"
          :disabled="!hasPendingKeyInput"
          @click="saveKeys"
        >{{ t('settings.saveKeys') }}</n-button>
      </n-card>

      <n-card :title="t('settings.debateCard')" style="margin-top: 16px">
        <n-form label-placement="left" label-width="160">
          <n-form-item :label="t('settings.maxDebateRounds')">
            <n-input-number v-model:value="form.max_debate_rounds" :min="1" :max="10" />
          </n-form-item>
          <n-form-item :label="t('settings.maxRiskRounds')">
            <n-input-number v-model:value="form.max_risk_discuss_rounds" :min="1" :max="5" />
          </n-form-item>
        </n-form>
      </n-card>

      <n-card :title="t('settings.miscCard')" style="margin-top: 16px">
        <n-form label-placement="left" label-width="160">
          <n-form-item :label="t('settings.outputLanguage')">
            <n-select v-model:value="form.output_language" :options="langOptions" />
          </n-form-item>
          <n-form-item :label="t('settings.checkpoint')">
            <n-switch v-model:value="form.checkpoint_enabled" />
          </n-form-item>
          <n-form-item :label="t('settings.benchmarkTicker')">
            <n-input v-model:value="form.benchmark_ticker" :placeholder="t('settings.benchmarkPlaceholder')" />
          </n-form-item>
        </n-form>
      </n-card>

      <n-card :title="t('settings.dirCard')" style="margin-top: 16px" size="small">
        <n-descriptions :column="1" bordered>
          <n-descriptions-item :label="t('settings.dirCache')">{{ settings?.data_cache_dir }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.dirResults')">{{ settings?.results_dir }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.dirMemory')">{{ settings?.memory_log_path }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-button type="primary" style="margin-top: 16px" :loading="saving" @click="save">
        {{ t('settings.saveBtn') }}
      </n-button>
    </n-spin>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsStore } from '../stores/settings'
import { useMessage } from 'naive-ui'
import api from '../api'
import ModelPicker from '../components/ModelPicker.vue'

interface ApiKeyRow {
  provider: string
  env_var: string | null
  masked: string
  set: boolean
  required: boolean
}

const { t } = useI18n()
const settingsStore = useSettingsStore()
const message = useMessage()
const loading = ref(true)
const saving = ref(false)
const savingKeys = ref(false)
const clearing = ref<string | null>(null)
const settings = computed(() => settingsStore.settings)

const form = reactive({
  llm_provider: '',
  deep_think_llm: '',
  quick_think_llm: '',
  max_debate_rounds: 1,
  max_risk_discuss_rounds: 1,
  output_language: 'Chinese',
  checkpoint_enabled: false,
  benchmark_ticker: '',
})

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

// --- Model catalog (loaded from backend; per-provider quick/deep options) -

const modelCatalog = computed(() => settingsStore.modelCatalog)

const deepModelOptions = computed(() => {
  const p = (form.llm_provider || '').toLowerCase()
  return modelCatalog.value[p]?.deep || []
})

const quickModelOptions = computed(() => {
  const p = (form.llm_provider || '').toLowerCase()
  return modelCatalog.value[p]?.quick || []
})

// --- API key state ------------------------------------------------------

const keyRows = ref<ApiKeyRow[]>([])
const keyInputs = reactive<Record<string, string>>({})

const hasPendingKeyInput = computed(() =>
  Object.values(keyInputs).some(v => v && v.trim().length > 0),
)

function placeholderFor(row: ApiKeyRow): string {
  if (!row.required) return t('settings.placeholderNoNeed')
  return row.set
    ? t('settings.placeholderSet', { masked: row.masked })
    : t('settings.placeholderUnset')
}

async function fetchKeys() {
  const { data } = await api.get('/api/api-keys')
  keyRows.value = data.providers || []
  for (const row of keyRows.value) {
    if (!(row.provider in keyInputs)) keyInputs[row.provider] = ''
  }
}

async function saveKeys() {
  const payload: Record<string, string> = {}
  for (const [provider, value] of Object.entries(keyInputs)) {
    if (value && value.trim().length > 0) payload[provider] = value.trim()
  }
  if (!Object.keys(payload).length) {
    message.warning(t('settings.keyMsg.nothing'))
    return
  }
  savingKeys.value = true
  try {
    const { data } = await api.put('/api/api-keys', { keys: payload })
    const names = (data.updated || []).join(', ') || t('settings.keyMsg.updatedNone')
    message.success(t('settings.keyMsg.updated', { names }))
    // Clear inputs and refresh masked view
    for (const k of Object.keys(payload)) keyInputs[k] = ''
    await fetchKeys()
  } catch (e: any) {
    message.error(t('settings.keyMsg.saveFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    savingKeys.value = false
  }
}

async function clearKey(provider: string) {
  clearing.value = provider
  try {
    await api.put('/api/api-keys', { keys: { [provider]: '' } })
    message.success(t('settings.keyMsg.cleared', { provider }))
    await fetchKeys()
  } catch (e: any) {
    message.error(t('settings.keyMsg.clearFailed') + (e?.response?.data?.detail || e?.message || t('common.unknownError')))
  } finally {
    clearing.value = null
  }
}

// --- Lifecycle ----------------------------------------------------------

async function load() {
  loading.value = true
  await Promise.all([
    settingsStore.fetch(),
    settingsStore.fetchModelCatalog(),
    fetchKeys(),
  ])
  if (settingsStore.settings) {
    Object.assign(form, settingsStore.settings)
  }
  loading.value = false
}

async function save() {
  saving.value = true
  try {
    await settingsStore.update(form)
    message.success(t('settings.saved'))
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
