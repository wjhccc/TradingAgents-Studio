<template>
  <div v-if="!content" class="empty">
    <n-empty :description="t('event.empty')" />
  </div>
  <div v-else class="event-report">
    <!-- Section 一: events with causal chains -->
    <section v-if="parsed.events.length">
      <h3 class="section-title">{{ t('event.eventsTitle') }}</h3>
      <n-grid :cols="1" :y-gap="16">
        <n-gi v-for="(ev, idx) in parsed.events" :key="idx">
          <n-card :bordered="true" size="small" class="event-card" :class="`sentiment-${ev.sentimentClass}`">
            <template #header>
              <div class="event-header">
                <span class="event-name">{{ ev.name || t('event.fallbackEventName', { n: idx + 1 }) }}</span>
                <span class="event-badges">
                  <n-tag v-if="ev.type" size="small" :bordered="false">{{ ev.type }}</n-tag>
                  <n-tag v-if="ev.sentiment" size="small" :type="ev.sentimentClass">{{ ev.sentiment }}</n-tag>
                  <n-tag v-if="ev.duration" size="small" :bordered="false" type="info">{{ ev.duration }}</n-tag>
                  <n-tag v-if="ev.confidence" size="small" :bordered="false">{{ t('event.confidence', { v: ev.confidence }) }}</n-tag>
                </span>
              </div>
            </template>
            <div v-if="ev.affected" class="event-line">
              <span class="lbl">{{ t('event.directlyRelated') }}</span>{{ ev.affected }}
            </div>
            <div v-if="ev.causalChain" class="event-chain-wrap">
              <CausalChain :raw="ev.causalChain" />
            </div>
            <!-- Anything that didn't match a known field, surface as raw markdown -->
            <div v-if="ev.rest" class="event-rest" v-html="renderMd(ev.rest)"></div>
          </n-card>
        </n-gi>
      </n-grid>
    </section>

    <!-- Section 二: stock impact matrix -->
    <section v-if="parsed.stockMatrix.rows.length">
      <h3 class="section-title">{{ t('event.stockMatrix') }}</h3>
      <n-data-table
        :columns="stockColumns"
        :data="parsed.stockMatrix.rows"
        :bordered="true"
        size="small"
      />
    </section>

    <!-- Section 三: sector impact -->
    <section v-if="parsed.sectorMatrix.rows.length">
      <h3 class="section-title">{{ t('event.sectorMatrix') }}</h3>
      <div class="sector-grid">
        <div
          v-for="(row, idx) in parsed.sectorMatrix.rows"
          :key="idx"
          class="sector-pill"
          :class="`dir-${classifyDirection(row.direction)}`"
        >
          <div class="sector-name">{{ row.name }}</div>
          <div class="sector-dir">{{ row.direction }}</div>
          <div class="sector-logic">{{ row.logic }}</div>
          <div v-if="row.strength" class="sector-strength">{{ t('event.sectorStrength', { v: row.strength }) }}</div>
        </div>
      </div>
    </section>

    <!-- Section 四: index impact -->
    <section v-if="parsed.indexMatrix.rows.length">
      <h3 class="section-title">{{ t('event.indexMatrix') }}</h3>
      <n-data-table
        :columns="indexColumns"
        :data="parsed.indexMatrix.rows"
        :bordered="true"
        size="small"
      />
    </section>

    <!-- Section 五: A-share risk -->
    <section v-if="parsed.risk">
      <h3 class="section-title">{{ t('event.riskTitle') }}</h3>
      <n-alert type="warning" :bordered="false">
        <div v-html="renderMd(parsed.risk)" class="markdown-body"></div>
      </n-alert>
    </section>

    <!-- Section 六: conclusion -->
    <section v-if="parsed.conclusion">
      <h3 class="section-title">{{ t('event.conclusionTitle') }}</h3>
      <n-card :bordered="false" class="conclusion-card">
        <div v-html="renderMd(parsed.conclusion)" class="markdown-body"></div>
      </n-card>
    </section>

    <!-- Raw fallback -->
    <n-collapse style="margin-top: 24px">
      <n-collapse-item :title="t('event.rawTitle')" name="raw">
        <div v-html="renderMd(content)" class="markdown-body"></div>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<script setup lang="ts">
import { computed, h } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'
import { NTag } from 'naive-ui'
import CausalChain from './CausalChain.vue'

const { t } = useI18n()
const props = defineProps<{ content: string }>()

interface EventItem {
  name: string
  type: string
  affected: string
  causalChain: string
  sentiment: string
  sentimentClass: 'success' | 'error' | 'default' | 'warning'
  duration: string
  confidence: string
  rest: string
}

interface ParsedReport {
  events: EventItem[]
  stockMatrix: { headers: string[]; rows: Record<string, string>[] }
  sectorMatrix: { headers: string[]; rows: any[] }
  indexMatrix: { headers: string[]; rows: any[] }
  risk: string
  conclusion: string
}

const parsed = computed<ParsedReport>(() => parseReport(props.content))

function renderMd(md: string): string {
  return marked(md || '') as string
}

// --- Parsing ---

function parseReport(md: string): ParsedReport {
  const empty: ParsedReport = {
    events: [],
    stockMatrix: { headers: [], rows: [] },
    sectorMatrix: { headers: [], rows: [] },
    indexMatrix: { headers: [], rows: [] },
    risk: '',
    conclusion: '',
  }
  if (!md) return empty

  const sections = splitSections(md)
  return {
    events: parseEvents(sections['一'] || sections['1'] || ''),
    stockMatrix: parseMatrix(sections['二'] || sections['2'] || ''),
    sectorMatrix: parseSectorMatrix(sections['三'] || sections['3'] || ''),
    indexMatrix: parseIndexMatrix(sections['四'] || sections['4'] || ''),
    risk: (sections['五'] || sections['5'] || '').trim(),
    conclusion: (sections['六'] || sections['6'] || '').trim(),
  }
}

/** Split by markdown headers that start with "### 一、" or "## 一、" etc. */
function splitSections(md: string): Record<string, string> {
  const out: Record<string, string> = {}
  // Matches ### 一、识别到的关键事件...  /  ## 二、股票影响矩阵 / ### 1. xxx
  const re = /^#{2,4}\s*([一二三四五六1-6])[、.\s]/gm
  const matches: { num: string; start: number; headerEnd: number }[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(md)) !== null) {
    matches.push({
      num: m[1],
      start: m.index,
      headerEnd: m.index + m[0].length,
    })
  }
  for (let i = 0; i < matches.length; i++) {
    const cur = matches[i]
    const end = i + 1 < matches.length ? matches[i + 1].start : md.length
    // Skip the header line itself: find the first newline after headerEnd
    const headerLineEnd = md.indexOf('\n', cur.headerEnd)
    const bodyStart = headerLineEnd >= 0 ? headerLineEnd + 1 : cur.headerEnd
    out[cur.num] = md.slice(bodyStart, end).trim()
  }
  return out
}

/** Section 一: events. Split by bullet groups, extract fields. */
function parseEvents(body: string): EventItem[] {
  if (!body) return []
  // Each event block is a sequence of "- **字段**: 值" lines. Adjacent events
  // are separated by a blank line OR by another "- **事件名称**:" prefix.
  const blocks = splitEventBlocks(body)
  return blocks.map(blockToEvent).filter(ev =>
    ev.name || ev.affected || ev.causalChain || ev.rest
  )
}

function splitEventBlocks(body: string): string[] {
  // Split on a "- **事件名称" line: that marks the start of a new event.
  const lines = body.split('\n')
  const blocks: string[] = []
  let cur: string[] = []
  for (const line of lines) {
    if (/^\s*-?\s*\*\*事件名称/.test(line) && cur.length) {
      blocks.push(cur.join('\n'))
      cur = [line]
    } else {
      cur.push(line)
    }
  }
  if (cur.length) blocks.push(cur.join('\n'))
  return blocks.map(b => b.trim()).filter(Boolean)
}

function blockToEvent(block: string): EventItem {
  const fields = extractBulletFields(block)
  const causalChain = extractFencedOrIndented(block) || fields['因果链'] || ''
  const sentiment = fields['情绪方向'] || fields['情绪'] || ''
  const sentimentClass = classifySentiment(sentiment)
  // Anything not captured by known fields, dump as rest
  const rest = stripKnownFields(block).trim()
  return {
    name: fields['事件名称'] || '',
    type: fields['事件类型'] || '',
    affected: fields['直接受益/受损方'] || fields['直接受益方'] || fields['直接受损方'] || '',
    causalChain,
    sentiment,
    sentimentClass,
    duration: fields['影响持续时间'] || fields['持续时间'] || '',
    confidence: fields['置信度'] || '',
    rest,
  }
}

const _KNOWN_FIELDS = [
  '事件名称', '事件类型',
  '直接受益/受损方', '直接受益方', '直接受损方',
  '因果链', '情绪方向', '情绪',
  '影响持续时间', '持续时间', '置信度',
]

/** Pull values for "- **字段**: 值" or "- **字段**：值". */
function extractBulletFields(block: string): Record<string, string> {
  const out: Record<string, string> = {}
  const re = /^\s*-?\s*\*\*([^*]+?)\*\*\s*[:：]\s*(.+?)\s*$/gm
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) {
    const key = m[1].trim()
    if (!out[key]) out[key] = m[2].trim()
  }
  return out
}

/** Pull a fenced code block (```...```) which the prompt asks for the causal chain. */
function extractFencedOrIndented(block: string): string {
  const fence = block.match(/```[\w]*\n([\s\S]*?)```/)
  if (fence) return fence[1].trim()
  return ''
}

function stripKnownFields(block: string): string {
  // Remove "- **字段**: ..." lines for known fields, and any fenced blocks (already shown).
  const fieldRe = new RegExp(
    `^\\s*-?\\s*\\*\\*(${_KNOWN_FIELDS.join('|')})\\*\\*\\s*[:：].*$`,
    'gm',
  )
  return block.replace(fieldRe, '').replace(/```[\s\S]*?```/g, '').trim()
}

function classifySentiment(s: string): 'success' | 'error' | 'warning' | 'default' {
  if (!s) return 'default'
  if (/正面|利好|看多|bullish|positive/i.test(s)) return 'success'
  if (/负面|利空|看空|bearish|negative/i.test(s)) return 'error'
  if (/中性|neutral/i.test(s)) return 'warning'
  return 'default'
}

/** Parse a Markdown pipe table into { headers, rows[] of key→value }. */
function parseMatrix(body: string) {
  if (!body) return { headers: [], rows: [] }
  const tableLines = extractTableBlock(body)
  if (tableLines.length < 2) return { headers: [], rows: [] }
  const headers = splitRow(tableLines[0])
  // tableLines[1] is the separator (---). Skip.
  const rows: Record<string, string>[] = []
  for (let i = 2; i < tableLines.length; i++) {
    const cells = splitRow(tableLines[i])
    if (!cells.length) continue
    const row: Record<string, string> = {}
    headers.forEach((h, j) => {
      row[h] = (cells[j] || '').trim()
    })
    rows.push(row)
  }
  return { headers, rows }
}

function parseSectorMatrix(body: string) {
  const m = parseMatrix(body)
  // Normalize columns into { name, direction, logic, strength }
  const rows = m.rows.map(r => {
    const name = r['板块'] || r[m.headers[0]] || ''
    const direction = r['影响方向'] || r[m.headers[1]] || ''
    const logic = r['逻辑'] || r[m.headers[2]] || ''
    const strength = r['强度'] || r[m.headers[3]] || ''
    return { name, direction, logic, strength }
  })
  return { headers: m.headers, rows }
}

function parseIndexMatrix(body: string) {
  return parseMatrix(body)
}

function extractTableBlock(body: string): string[] {
  const lines = body.split('\n')
  const out: string[] = []
  let inTable = false
  for (const ln of lines) {
    const trimmed = ln.trim()
    const isPipeLine = trimmed.startsWith('|') && trimmed.endsWith('|') && trimmed.includes('|', 1)
    if (isPipeLine) {
      inTable = true
      out.push(trimmed)
    } else if (inTable) {
      // Table ended on first non-pipe line.
      break
    }
  }
  return out
}

function splitRow(row: string): string[] {
  // "| a | b | c |" → ["a", "b", "c"]
  const inner = row.replace(/^\|/, '').replace(/\|$/, '')
  return inner.split('|').map(s => s.trim())
}

function classifyDirection(dir: string): 'up' | 'down' | 'flat' {
  if (!dir) return 'flat'
  if (/↑|受益|上涨|看多|positive/i.test(dir)) return 'up'
  if (/↓|受损|下跌|看空|negative/i.test(dir)) return 'down'
  return 'flat'
}

// --- Data table columns ---

const stockColumns = computed(() => {
  const headers = parsed.value.stockMatrix.headers
  if (!headers.length) return []
  return headers.map(h => {
    if (h === '情绪') {
      return {
        title: h,
        key: h,
        render: (row: Record<string, string>) => {
          const v = row[h] || ''
          const cls = classifySentiment(v)
          return h_render(NTag, { type: cls, size: 'small', bordered: false }, { default: () => v })
        },
      }
    }
    if (h === '风险等级') {
      return {
        title: h,
        key: h,
        render: (row: Record<string, string>) => {
          const v = row[h] || ''
          const type = /高/.test(v) ? 'error' : /中/.test(v) ? 'warning' : 'success'
          return h_render(NTag, { type, size: 'small', bordered: false }, { default: () => v })
        },
      }
    }
    return { title: h, key: h }
  })
})

const indexColumns = computed(() => {
  const headers = parsed.value.indexMatrix.headers
  return headers.map(h => {
    if (h === '预计方向') {
      return {
        title: h,
        key: h,
        render: (row: Record<string, string>) => {
          const v = row[h] || ''
          const dir = classifyDirection(v)
          const type = dir === 'up' ? 'success' : dir === 'down' ? 'error' : 'default'
          return h_render(NTag, { type, size: 'small', bordered: false }, { default: () => v })
        },
      }
    }
    return { title: h, key: h }
  })
})

function h_render(component: any, props: any, slots: any) {
  return h(component, props, slots)
}
</script>

<style scoped>
.event-report section {
  margin-bottom: 28px;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 12px;
  color: #333;
  padding-left: 8px;
  border-left: 3px solid currentColor;
  opacity: 0.85;
}
.event-card.sentiment-success { border-top: 2px solid #18a058; }
.event-card.sentiment-error { border-top: 2px solid #d03050; }
.event-card.sentiment-warning { border-top: 2px solid #f0a020; }
.event-card.sentiment-default { border-top: 2px solid #d0d0d6; }
.event-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.event-name {
  font-weight: 600;
  font-size: 14px;
}
.event-badges {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.event-line {
  font-size: 13px;
  color: #555;
  margin: 6px 0 10px;
}
.event-line .lbl {
  color: #999;
  margin-right: 4px;
}
.event-chain-wrap {
  margin: 8px 0 4px;
}
.event-rest {
  font-size: 13px;
  margin-top: 8px;
  color: #555;
}
.sector-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
}
.sector-pill {
  border: 1px solid #e0e0e6;
  border-radius: 6px;
  padding: 10px 12px;
  background: #fff;
}
.sector-pill.dir-up { border-left: 4px solid #d03050; }
.sector-pill.dir-down { border-left: 4px solid #18a058; }
.sector-pill.dir-flat { border-left: 4px solid #999; }
.sector-name {
  font-weight: 600;
  font-size: 13px;
}
.sector-dir {
  font-size: 12px;
  color: #666;
  margin: 2px 0;
}
.sector-logic {
  font-size: 12px;
  color: #555;
  margin-top: 4px;
  line-height: 1.4;
}
.sector-strength {
  margin-top: 4px;
  font-size: 11px;
  color: #999;
}
.conclusion-card :deep(.n-card__content) {
  background: #fafafc;
  border-radius: 6px;
}
.markdown-body {
  font-size: 13px;
  line-height: 1.7;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #e0e0e6;
  padding: 6px 10px;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
}
.empty {
  padding: 40px 0;
}
</style>
