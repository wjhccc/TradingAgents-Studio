<template>
  <div class="causal-chain">
    <div v-for="(node, idx) in nodes" :key="idx" class="chain-node">
      <div class="node-box" :class="`tier-${idx}`">
        <div class="node-tier">{{ tierLabels[idx] || t('causal.fallback') }}</div>
        <div class="node-text">{{ node }}</div>
      </div>
      <div v-if="idx < nodes.length - 1" class="chain-arrow">↓</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  /** Raw causal-chain text. The event analyst prompt asks for a fenced
   *  block with arrows like "[event]\n   ↓\n[direct]\n   ↓\n[supply chain]"
   *  but the LLM sometimes uses "->", "→" or numbered lines instead. */
  raw: string
}>()

const { t } = useI18n()

const tierLabels = computed(() => [
  t('causal.tiers.event'),
  t('causal.tiers.direct'),
  t('causal.tiers.supply'),
  t('causal.tiers.sector'),
  t('causal.tiers.followup'),
])

const nodes = computed<string[]>(() => parseChain(props.raw))

function parseChain(text: string): string[] {
  if (!text) return []
  // Strip code fences and leading/trailing whitespace
  const cleaned = text.replace(/```/g, '').trim()
  // Split on any arrow-ish separator on its own line, or inline "→" / "->"
  const parts = cleaned
    .split(/\n\s*[↓→]\s*\n|\n\s*-+>\s*\n|\s*→\s*|\s*->\s*|\n\s*↓\s*/g)
    .map(p => p.replace(/^[\[\(]|[\]\)]$/g, '').trim())
    .filter(Boolean)
  return parts
}
</script>

<style scoped>
.causal-chain {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0;
  padding: 8px 0;
}
.chain-node {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.node-box {
  width: 100%;
  max-width: 480px;
  border: 1px solid #e0e0e6;
  border-left-width: 4px;
  border-radius: 6px;
  padding: 10px 14px;
  background: #fafafc;
}
.node-box.tier-0 { border-left-color: #2080f0; background: #ecf5ff; }
.node-box.tier-1 { border-left-color: #18a058; background: #f0faf3; }
.node-box.tier-2 { border-left-color: #f0a020; background: #fff7e6; }
.node-box.tier-3 { border-left-color: #8b5cf6; background: #f6f0ff; }
.node-box.tier-4 { border-left-color: #6b7280; background: #f5f5f7; }
.node-tier {
  font-size: 11px;
  color: #999;
  letter-spacing: 0.5px;
  margin-bottom: 2px;
}
.node-text {
  font-size: 13px;
  line-height: 1.5;
  color: #333;
  white-space: pre-wrap;
}
.chain-arrow {
  font-size: 20px;
  color: #999;
  line-height: 1;
  margin: 4px 0;
}
</style>
