<template>
  <n-space vertical :size="24">
    <n-page-header :title="t('progress.title')" :subtitle="analysisId">
      <template #extra>
        <n-tag :type="statusType">{{ status }}</n-tag>
      </template>
    </n-page-header>

    <n-grid :cols="2" :x-gap="16">
      <!-- Left: Agent Timeline -->
      <n-gi>
        <n-card :title="t('progress.agents')">
          <n-timeline>
            <n-timeline-item
              v-for="(ev, idx) in timelineEvents"
              :key="`${ev.timestamp}-${idx}`"
              :type="timelineType(ev.type)"
              :title="timelineTitle(ev)"
              :content="timelineContent(ev)"
              :time="formatTime(ev.timestamp)"
            />
          </n-timeline>
          <n-empty v-if="!timelineEvents.length" :description="t('progress.waitingStart')" />
        </n-card>

        <!-- Stats -->
        <n-card :title="t('progress.stats')" size="small" style="margin-top: 12px">
          <n-space>
            <n-statistic :label="t('progress.events')" :value="events.length" />
            <n-statistic :label="t('progress.debateTurns')" :value="debateTurns.length" />
            <n-statistic :label="t('progress.elapsed')" :value="elapsed" />
          </n-space>
        </n-card>
      </n-gi>

      <!-- Right: Live Debate + Live Output -->
      <n-gi>
        <n-card :title="t('progress.liveDebate')" v-if="debateTurns.length || isActive">
          <div ref="debateRef" class="debate-scroll">
            <DebateThread
              v-if="debateTurns.length"
              :turns="debateTurns"
              :empty-text="t('progress.waitingDebate')"
            />
            <div v-else class="waiting">
              <n-spin size="small" />
              <span style="margin-left: 8px; color: #999;">{{ t('progress.waitingBullBear') }}</span>
            </div>
          </div>
        </n-card>

        <n-card :title="t('progress.liveOutput')" size="small" style="margin-top: 12px">
          <div ref="outputRef" class="output-scroll">
            {{ liveOutput }}
          </div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- Final Decision -->
    <n-card v-if="finalSignal" :title="t('progress.finalDecision')" :bordered="false" style="background: #f6ffed">
      <n-h2>{{ finalSignal }}</n-h2>
      <n-button type="primary" @click="$router.push(`/report/${analysisId}`)">
        {{ t('progress.viewReport') }}
      </n-button>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useNotification } from 'naive-ui'
import DebateThread from '../components/DebateThread.vue'
import { formatTime } from '../utils/datetime'

const { t } = useI18n()
const route = useRoute()
const notification = useNotification()
const analysisId = route.params.id as string

type DebateRole = 'bull' | 'bear' | 'aggressive' | 'conservative' | 'neutral'

interface AgentEvent {
  type: string
  agent: string
  content: string
  stats: { tokens: number }
  timestamp: string
  // debate_turn extras
  debate?: 'invest' | 'risk'
  role?: DebateRole
  round?: number
}

interface DebateTurnUI {
  role: DebateRole
  round: number
  content: string
  live?: boolean
}

const events = ref<AgentEvent[]>([])
const debateTurns = ref<DebateTurnUI[]>([])
const status = ref('connecting')
const finalSignal = ref('')
const startTime = ref(Date.now())
const elapsed = ref('0s')
const outputRef = ref<HTMLElement>()
const debateRef = ref<HTMLElement>()

let ws: WebSocket | null = null
let timer: number | null = null

const statusType = computed(() => {
  if (status.value === 'complete') return 'success'
  if (status.value === 'error' || status.value === 'failed') return 'error'
  return 'info'
})

const isActive = computed(() => status.value === 'running' || status.value === 'connecting')

// Timeline shows non-debate events; debate turns are rendered as bubbles on the right.
const timelineEvents = computed(() => events.value.filter(e => e.type !== 'debate_turn'))

// Output panel mirrors agent_complete + error/system events as text.
const liveOutput = computed(() =>
  timelineEvents.value.map(e => `[${e.agent}] ${e.content}`).join('\n')
)

function timelineType(eventType: string) {
  if (eventType === 'agent_complete' || eventType === 'analysis_complete') return 'success'
  if (eventType === 'error') return 'error'
  if (eventType === 'agent_start') return 'info'
  return 'default'
}

function timelineTitle(ev: AgentEvent): string {
  return ev.agent || ev.type
}

function timelineContent(ev: AgentEvent): string {
  const txt = ev.content || ''
  return txt.length > 200 ? txt.slice(0, 200) + '…' : txt
}


function connect() {
  const wsUrl = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/analyze/${analysisId}`
  ws = new WebSocket(wsUrl)

  ws.onopen = () => { status.value = 'running' }

  ws.onmessage = (msg) => {
    const event: AgentEvent = JSON.parse(msg.data)
    events.value.push(event)

    if (event.type === 'debate_turn' && event.role && event.round != null) {
      // Mark the previous live turn as stale so only the newest pulses.
      for (const t of debateTurns.value) t.live = false
      debateTurns.value.push({
        role: event.role,
        round: event.round,
        content: event.content || '',
        live: true,
      })
      nextTick(() => {
        if (debateRef.value) debateRef.value.scrollTop = debateRef.value.scrollHeight
      })
    } else if (event.type === 'analysis_complete') {
      status.value = 'complete'
      finalSignal.value = event.content
      // Stop pulsing once the analysis completes.
      for (const t of debateTurns.value) t.live = false
      notification.success({ title: t('progress.analysisCompleted'), content: event.content.slice(0, 100), duration: 5000 })
    } else if (event.type === 'error') {
      status.value = 'error'
      notification.error({ title: t('progress.analysisFailed'), content: event.content, duration: 8000 })
    }

    nextTick(() => {
      if (outputRef.value) outputRef.value.scrollTop = outputRef.value.scrollHeight
    })
  }

  ws.onclose = () => {
    if (status.value === 'running') status.value = 'disconnected'
  }
}

onMounted(() => {
  connect()
  timer = window.setInterval(() => {
    elapsed.value = Math.round((Date.now() - startTime.value) / 1000) + 's'
  }, 1000)
})

onUnmounted(() => {
  ws?.close()
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.debate-scroll {
  max-height: 540px;
  overflow-y: auto;
  padding-right: 4px;
}
.output-scroll {
  max-height: 220px;
  overflow-y: auto;
  font-family: monospace;
  font-size: 12px;
  white-space: pre-wrap;
  color: #666;
}
.waiting {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
  font-size: 13px;
}
</style>
