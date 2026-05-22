import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '../api'

export interface AnalysisItem {
  id: string
  ticker: string
  trade_date: string
  asset_type: string
  status: string
  signal?: string
  confidence?: number
  created_at: string
  completed_at?: string
}

export const useAnalysisStore = defineStore('analysis', () => {
  const recent = ref<AnalysisItem[]>([])
  const signalDistribution = ref<Record<string, number>>({})

  async function fetchDashboard() {
    const { data } = await api.get('/api/dashboard')
    recent.value = data.recent || []
    signalDistribution.value = data.signal_distribution || {}
  }

  async function startAnalysis(params: any) {
    const { data } = await api.post('/api/analyze', params)
    return data.id as string
  }

  return { recent, signalDistribution, fetchDashboard, startAnalysis }
})
