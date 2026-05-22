<template>
  <div class="debate-thread">
    <div v-if="!turns.length" class="empty-state">
      <n-empty :description="emptyText || t('debate.empty')" />
    </div>
    <div v-else>
      <div
        v-for="(turn, idx) in turns"
        :key="idx"
        class="turn"
        :class="`side-${sideOf(turn.role)}`"
      >
        <div class="bubble" :class="`role-${turn.role}`">
          <div class="bubble-header">
            <n-tag size="small" :type="roleTagType(turn.role)" :bordered="false">
              {{ roleLabel(turn.role) }}
            </n-tag>
            <span class="round">{{ t('debate.round', { n: turn.round }) }}</span>
            <span v-if="turn.live" class="live-dot" :title="t('debate.justArrived')"></span>
          </div>
          <div class="bubble-body markdown-body" v-html="renderMd(turn.content)"></div>
        </div>
      </div>
    </div>

    <n-collapse v-if="rawText" style="margin-top: 16px">
      <n-collapse-item :title="t('debate.rawTitle')" name="raw">
        <pre class="raw-pre">{{ rawText }}</pre>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'

type Role = 'bull' | 'bear' | 'aggressive' | 'conservative' | 'neutral'

interface Turn {
  role: Role
  round: number
  content: string
  live?: boolean
}

const { t } = useI18n()

const props = withDefaults(defineProps<{
  /** Pass either raw history strings to be parsed, or pre-structured turns. */
  history?: string
  bullHistory?: string
  bearHistory?: string
  turns?: Turn[]
  emptyText?: string
}>(), {
  history: '',
  bullHistory: '',
  bearHistory: '',
  emptyText: '',
})

const turns = computed<Turn[]>(() => {
  if (props.turns && props.turns.length) return props.turns
  // Prefer the combined history (preserves the bull→bear→bull ordering)
  if (props.history) return parseCombined(props.history)
  if (props.bullHistory || props.bearHistory) {
    return interleaveBullBear(props.bullHistory, props.bearHistory)
  }
  return []
})

const rawText = computed(() => {
  if (props.history) return props.history
  return [props.bullHistory, props.bearHistory].filter(Boolean).join('\n\n')
})

// --- Parsing ---

const ROLE_PREFIXES: { prefix: RegExp; role: Role }[] = [
  { prefix: /^Bull Analyst\s*[:：]\s*/, role: 'bull' },
  { prefix: /^Bear Analyst\s*[:：]\s*/, role: 'bear' },
  { prefix: /^Aggressive Analyst\s*[:：]\s*/, role: 'aggressive' },
  { prefix: /^Conservative Analyst\s*[:：]\s*/, role: 'conservative' },
  { prefix: /^Neutral Analyst\s*[:：]\s*/, role: 'neutral' },
]

function parseCombined(text: string): Turn[] {
  const lines = text.split('\n')
  const turnsOut: Turn[] = []
  let cur: { role: Role; lines: string[] } | null = null

  const flush = () => {
    if (!cur) return
    const content = cur.lines.join('\n').trim()
    if (content) {
      turnsOut.push({ role: cur.role, round: 0, content })
    }
    cur = null
  }

  for (const ln of lines) {
    const matched = matchRolePrefix(ln)
    if (matched) {
      flush()
      cur = { role: matched.role, lines: [matched.body] }
    } else if (cur) {
      cur.lines.push(ln)
    }
    // Lines before the first matched role prefix are dropped — they're
    // typically empty separators left over from history concatenation.
  }
  flush()

  // Assign per-role round numbers
  const roundCounts: Record<string, number> = {}
  for (const tu of turnsOut) {
    roundCounts[tu.role] = (roundCounts[tu.role] || 0) + 1
    tu.round = roundCounts[tu.role]
  }
  return turnsOut
}

function matchRolePrefix(line: string): { role: Role; body: string } | null {
  for (const { prefix, role } of ROLE_PREFIXES) {
    const m = line.match(prefix)
    if (m) {
      return { role, body: line.slice(m[0].length) }
    }
  }
  return null
}

/** When only per-side histories are present, parse each then interleave. */
function interleaveBullBear(bull: string, bear: string): Turn[] {
  const bulls = parseCombined(bull)
  const bears = parseCombined(bear)
  const out: Turn[] = []
  const max = Math.max(bulls.length, bears.length)
  for (let i = 0; i < max; i++) {
    if (i < bulls.length) out.push(bulls[i])
    if (i < bears.length) out.push(bears[i])
  }
  return out
}

// --- Display helpers ---

function renderMd(md: string): string {
  return marked(md || '') as string
}

function sideOf(role: Role): 'left' | 'right' {
  // Bull / Aggressive on the right (advocates), Bear / Conservative on the
  // left (skeptics), Neutral in the center-ish (left for now).
  return role === 'bull' || role === 'aggressive' ? 'right' : 'left'
}

function roleLabel(role: Role): string {
  return t(`debate.roles.${role}`)
}

function roleTagType(role: Role): 'success' | 'error' | 'warning' | 'info' | 'default' {
  return {
    bull: 'success',
    bear: 'error',
    aggressive: 'error',
    conservative: 'info',
    neutral: 'warning',
  }[role] as any
}
</script>

<style scoped>
.debate-thread {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.turn {
  display: flex;
}
.turn.side-left { justify-content: flex-start; }
.turn.side-right { justify-content: flex-end; }
.bubble {
  max-width: 80%;
  border-radius: 8px;
  padding: 10px 14px;
  background: #f5f7fa;
  border: 1px solid #e0e0e6;
}
.bubble.role-bull { background: #f0faf3; border-color: #b8e6c8; }
.bubble.role-bear { background: #fff1f1; border-color: #f0b8b8; }
.bubble.role-aggressive { background: #fff7e6; border-color: #f0d0a0; }
.bubble.role-conservative { background: #eff5ff; border-color: #b8d4f0; }
.bubble.role-neutral { background: #fafafc; border-color: #d8d8e0; }
.bubble-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
  color: #999;
}
.round {
  font-size: 11px;
  color: #999;
}
.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #18a058;
  animation: pulse 1.4s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.bubble-body {
  font-size: 13px;
  line-height: 1.7;
  color: #333;
}
.markdown-body :deep(p) {
  margin: 6px 0;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 6px 0;
}
.markdown-body :deep(code) {
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 4px;
  border-radius: 3px;
}
.raw-pre {
  background: #fafafc;
  border: 1px solid #e0e0e6;
  border-radius: 4px;
  padding: 12px;
  font-family: monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  max-height: 400px;
  overflow-y: auto;
}
.empty-state {
  padding: 40px 0;
}
</style>
