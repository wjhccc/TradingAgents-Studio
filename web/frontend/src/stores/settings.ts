import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '../api'

export interface Settings {
  llm_provider: string
  deep_think_llm: string
  quick_think_llm: string
  max_debate_rounds: number
  max_risk_discuss_rounds: number
  output_language: string
  checkpoint_enabled: boolean
  benchmark_ticker: string | null
  data_cache_dir: string
  results_dir: string
  memory_log_path: string
}

export interface ModelOption {
  label: string
  value: string
}

/** {provider: {quick: ModelOption[], deep: ModelOption[]}} — empty for
 *  providers without a static catalog (openrouter, azure). */
export type ModelCatalog = Record<string, { quick: ModelOption[]; deep: ModelOption[] }>

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref<Settings | null>(null)
  const modelCatalog = ref<ModelCatalog>({})
  let catalogPromise: Promise<void> | null = null

  async function fetch() {
    const { data } = await api.get('/api/settings')
    settings.value = data
  }

  async function update(partial: Partial<Settings>) {
    await api.put('/api/settings', partial)
    await fetch()
  }

  /** Lazy-load the per-provider model catalog. Cached for the page session
   *  — the catalog only changes when the server upgrades. */
  function fetchModelCatalog(): Promise<void> {
    if (catalogPromise) return catalogPromise
    catalogPromise = api.get('/api/model-catalog').then(({ data }) => {
      modelCatalog.value = data.providers || {}
    })
    return catalogPromise
  }

  return { settings, modelCatalog, fetch, update, fetchModelCatalog }
})
