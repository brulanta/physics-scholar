<template>
  <div v-if="phase !== 'idle'" class="timeline">
    <!-- 进行中：竖向时间轴（思考节点 + 各工具节点，节点间竖线连接）-->
    <div v-if="phase !== 'answer'" class="steps">
      <div v-for="s in steps" :key="s.key" class="step" :class="{ last: s.last }">
        <div class="rail">
          <span class="node" :class="s.status">
            <span v-if="s.spinning" class="spinner" />
            <span v-else-if="s.status === 'done'" class="ic ok">✓</span>
            <span v-else-if="s.status === 'error'" class="ic err">✕</span>
            <span v-else class="node-dot" />
          </span>
        </div>
        <div class="step-body">
          <div class="step-row">
            <span class="step-name">{{ s.label }}</span>
            <span class="step-time">{{ fmt(s.dur) }}</span>
          </div>
          <!-- 嵌套子时间轴：retrieve 内部子 agent 步骤（思考 + 各检索工具），1 层 -->
          <div v-if="s.children && s.children.length" class="sub-steps">
            <div v-for="c in s.children" :key="c.key" class="sub-step">
              <span class="node-sm" :class="c.status">
                <span v-if="c.spinning" class="spinner spinner-sm" />
                <span v-else-if="c.status === 'done'" class="ic ok">✓</span>
                <span v-else-if="c.status === 'error'" class="ic err">✕</span>
                <span v-else class="node-dot" />
              </span>
              <span class="sub-name">{{ c.label }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- answer 阶段：折叠为一行摘要，正文由下方 MessageItem 承接 -->
    <div v-else class="summary">
      <span class="ic ok">✓</span>
      <span class="summary-text">{{ summaryText }}</span>
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
  retrieve: '检索',  // 主 agent 的 retrieve 壳（内部跑子 agent 检索循环，步骤挂 children）
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

// 时间轴节点：头节点=思考，其后每个工具一个节点
const steps = computed(() => {
  const list = [
    {
      key: 'think',
      status: props.phase === 'thinking' ? 'active' : 'done',
      spinning: props.phase === 'thinking',
      label: props.phase === 'thinking' ? '思考中…' : '已思考',
      dur: elapsed.value,
    },
    ...props.tools.map(t => ({
      key: t.tool_id,
      status: t.status, // running | done | error
      spinning: t.status === 'running',
      label: toolName(t.name),
      dur: toolDur(t),
      // retrieve 的子 agent 内部步骤（思考 + 各检索工具），1 层嵌套
      children: (t.children || []).map(c => ({
        key: c.key,
        status: c.status,
        spinning: c.status === 'running',
        kind: c.kind,
        label: c.kind === 'thinking'
          ? (c.status === 'running' ? '思考中…' : '已思考')
          : toolName(c.name || ''),
      })),
    })),
  ]
  list.forEach((s, i) => { s.last = i === list.length - 1 })
  return list
})

const summaryText = computed(() => {
  const parts = ['已深度思考']
  if (props.tools.length) parts.push(`调用 ${props.tools.length} 个工具`)
  parts.push(fmt(elapsed.value))
  return parts.join(' · ')
})
</script>

<style scoped>
.timeline {
  margin: 4px 0 12px;
  font-size: 0.82em;
  color: var(--text-2);
}

/* ── 竖向时间轴 ── */
.steps {
  display: flex;
  flex-direction: column;
}

.step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-height: 30px;
}

.rail {
  position: relative;
  width: 16px;
  flex-shrink: 0;
  align-self: stretch;
  display: flex;
  justify-content: center;
}

/* 连接竖线：从本节点中心向下延伸至下一节点中心（最后一个节点不画）*/
.step:not(.last) .rail::before {
  content: '';
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  top: 9px;
  height: 100%;
  width: 2px;
  background: var(--border);
  z-index: 0;
}

.node {
  position: relative;
  z-index: 1;
  margin-top: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--bg-2);
  border: 1.5px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.node.done {
  border-color: #2e9e5b;
}

.node.error {
  border-color: #c0392b;
}

.node.active {
  border-color: var(--accent);
}

.node-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-3);
}

.ic {
  font-size: 0.78em;
  line-height: 1;
}

.ic.ok {
  color: #2e9e5b;
}

.ic.err {
  color: #c0392b;
}

.step-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 3px;
  padding-bottom: 8px;
}

.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.step-name {
  color: var(--text);
}

.step-time {
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  font-size: 0.92em;
}

/* ── 嵌套子时间轴（retrieve 内部子 agent 步骤）── */
.sub-steps {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-left: 2px;
  padding: 2px 0 2px 10px;
  border-left: 1.5px dashed var(--border);
}

.sub-step {
  display: flex;
  align-items: center;
  gap: 6px;
}

.node-sm {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: var(--bg-2);
  border: 1.3px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.node-sm.done {
  border-color: #2e9e5b;
}

.node-sm.error {
  border-color: #c0392b;
}

.node-sm.active {
  border-color: var(--accent);
}

.spinner-sm {
  width: 7px;
  height: 7px;
}

.sub-name {
  color: var(--text-3);
  font-size: 0.95em;
}

/* ── answer 折叠摘要 ── */
.summary {
  display: flex;
  align-items: center;
  gap: 8px;
}

.summary-text {
  color: var(--text-3);
}

/* 旋转 spinner（思考/工具进行中）*/
.spinner {
  width: 9px;
  height: 9px;
  border: 1.6px solid var(--border-light, #555);
  border-top-color: var(--accent);
  border-radius: 50%;
  display: inline-block;
  animation: tl-spin 0.7s linear infinite;
}

@keyframes tl-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
