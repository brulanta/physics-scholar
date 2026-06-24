<template>
  <div v-if="phase !== 'idle'" class="timeline">
    <!-- 思考 / 工具进行中 -->
    <template v-if="phase !== 'answer'">
      <div v-if="phase === 'thinking'" class="tl-row">
        <span class="spinner" />
        <span class="tl-label">思考中…</span>
        <span class="tl-time">{{ fmt(elapsed) }}</span>
      </div>

      <div v-if="tools.length" class="tl-tools">
        <span v-for="t in tools" :key="t.tool_id" class="chip" :class="t.status">
          <span v-if="t.status === 'running'" class="spinner sm" />
          <span v-else-if="t.status === 'done'" class="ic ok">✓</span>
          <span v-else class="ic err">✕</span>
          <span class="chip-name">{{ toolName(t.name) }}</span>
          <span class="chip-time">{{ fmt(toolDur(t)) }}</span>
        </span>
      </div>
    </template>

    <!-- answer 阶段：折叠为一行摘要，正文由下方 MessageItem 承接 -->
    <div v-else class="tl-row summary">
      <span class="ic ok">✓</span>
      <span class="tl-label">{{ summaryText }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'

const props = defineProps({
  phase: { type: String, default: 'idle' }, // idle | thinking | tool | answer
  tools: { type: Array, default: () => [] }, // [{ tool_id, name, status, startedAt, endedAt }]
})

// 工具名 → 中文（未知名回退原始名）
const TOOL_NAMES = {
  rag_tool: '检索本地论文',
  lookup_local_paper_id: '定位本地论文',
  arxiv_tool: 'arXiv 检索',
  s2_search_tool: 'Semantic Scholar',
  openalex_tool: 'OpenAlex',
  jina_tool: '网页/PDF 阅读',
}
function toolName(n) {
  return TOOL_NAMES[n] || n
}

// 计时：组件挂载（≈流开始）起算，进入 answer 时冻结
const startTs = ref(Date.now())
const answerTs = ref(null)
const now = ref(Date.now())
let timer = null

onMounted(() => {
  startTs.value = Date.now()
  now.value = Date.now()
  timer = setInterval(() => { now.value = Date.now() }, 200)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

watch(() => props.phase, (p) => {
  if (p === 'answer' && answerTs.value === null) {
    answerTs.value = Date.now()
    if (timer) { clearInterval(timer); timer = null } // 正文阶段无需再 tick
  }
})

const elapsed = computed(() => (answerTs.value ?? now.value) - startTs.value)

function toolDur(t) {
  return (t.endedAt ?? now.value) - t.startedAt
}

function fmt(ms) {
  const s = Math.max(0, ms) / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  return `${m}m${Math.round(s % 60)}s`
}

const summaryText = computed(() => {
  const parts = ['已深度思考']
  if (props.tools.length) parts.push(`调用 ${props.tools.length} 个工具`)
  parts.push(fmt(elapsed.value))
  return parts.join(' · ')
})
</script>

<style scoped>
.timeline {
  margin: 4px 0 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 0.82em;
  color: var(--text-2);
}

.tl-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tl-label {
  color: var(--text-2);
}

.tl-time,
.chip-time {
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  font-size: 0.92em;
}

.tl-row.summary .tl-label {
  color: var(--text-3);
}

.tl-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border-radius: 12px;
  background: var(--bg-2);
  border: 1px solid var(--border);
  white-space: nowrap;
}

.chip.running {
  border-color: var(--accent);
}

.chip.error {
  border-color: #c0392b;
}

.chip-name {
  color: var(--text);
}

.ic {
  font-size: 0.9em;
  line-height: 1;
}

.ic.ok {
  color: #2e9e5b;
}

.ic.err {
  color: #c0392b;
}

/* 旋转 spinner */
.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--border-light, #555);
  border-top-color: var(--accent);
  border-radius: 50%;
  display: inline-block;
  animation: tl-spin 0.7s linear infinite;
  flex-shrink: 0;
}

.spinner.sm {
  width: 10px;
  height: 10px;
  border-width: 1.6px;
}

@keyframes tl-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
