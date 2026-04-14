<template>
  <!-- 右键菜单 -->
  <Teleport to="body">
    <div v-if="visible" class="ctx-overlay" @click="close" @contextmenu.prevent="close" />
    <div v-if="visible" class="ctx-menu" :style="{ top: y + 'px', left: x + 'px' }">
      <div class="ctx-header">
        检测到 {{ links.length }} 篇 arXiv 论文
      </div>

      <div class="ctx-list">
        <div v-for="link in links" :key="link.arxivId" class="ctx-item">
          <div class="ctx-item-info">
            <span class="ctx-arxiv-id">{{ link.arxivId }}</span>
            <a :href="link.absUrl" target="_blank" class="ctx-abs-link" @click.stop>页面</a>
          </div>
          <div class="ctx-item-actions">
            <button class="ctx-btn download" @click="download(link)">
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M5.5 1v6M2.5 5l3 3 3-3M1 9.5h9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"
                  stroke-linejoin="round" />
              </svg>
              下载
            </button>
            <button class="ctx-btn ingest"
              :class="{ done: link.phase === 'done', error: link.phase === 'error', loading: link.phase === 'loading' }"
              :disabled="link.phase === 'done' || link.phase === 'loading'" @click="ingest(link)">
              <span v-if="link.phase === 'loading'" class="spin">⟳</span>
              <span v-else-if="link.phase === 'done'">✓</span>
              <span v-else-if="link.phase === 'error'">✗</span>
              <svg v-else width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M5.5 7V1M2.5 4l3-3 3 3M1 9.5h9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"
                  stroke-linejoin="round" />
              </svg>
              {{ phaseLabel(link.phase) }}
            </button>
          </div>
          <div v-if="link.phase === 'error'" class="ctx-error">{{ link.errorMsg }}</div>
        </div>
      </div>

      <div v-if="links.length > 1" class="ctx-footer">
        <button class="ctx-btn-all" :disabled="allDone" @click="ingestAll">
          全部入库（{{ pendingCount }}）
        </button>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, reactive } from 'vue'
import { ingestFromArxiv, listPapers } from '../../api/paper.js'

const visible = ref(false)
const x = ref(0)
const y = ref(0)
const links = ref([])

// 从气泡文本提取arxiv链接
function extractArxivLinks(text) {
  const pattern = /https?:\/\/arxiv\.org\/(abs|pdf)\/([0-9]{4}\.[0-9]+(?:v\d+)?)/gi
  const seen = new Set()
  const results = []
  let match
  while ((match = pattern.exec(text)) !== null) {
    const arxivId = match[2]
    const baseId = arxivId.replace(/v\d+$/, '')
    if (!seen.has(baseId)) {
      seen.add(baseId)
      results.push(reactive({
        arxivId,
        pdfUrl: `https://arxiv.org/pdf/${arxivId}`,
        absUrl: `https://arxiv.org/abs/${arxivId}`,
        phase: 'idle',   // idle | loading | done | error
        errorMsg: ''
      }))
    }
  }
  return results
}

async function open(event, text) {
  const extracted = extractArxivLinks(text)
  if (extracted.length === 0) return

  // 查重：拉当前论文列表，比对文件名
  let existingFileNames = new Set()
  try {
    const res = await listPapers()
    const papers = res.data.papers || []
    existingFileNames = new Set(papers.map(p => p.file_name))
  } catch (_) { }

  links.value = extracted.map(link => {
    const fileName = `${link.arxivId.replace(/v\d+$/, '')}.pdf`
    const alreadyIn = existingFileNames.has(fileName) ||
      // 也检查带版本号的
      existingFileNames.has(`${link.arxivId}.pdf`)
    return reactive({
      ...link,
      phase: alreadyIn ? 'done' : 'idle',
      errorMsg: ''
    })
  })

  // 菜单位置
  const menuW = 320
  const vw = window.innerWidth
  const vh = window.innerHeight
  // ① 固定最大高度320px，位置计算不再依赖动态menuH
  x.value = event.clientX + menuW > vw ? event.clientX - menuW : event.clientX
  // 优先在点击位置下方，放不下就往上
  const menuMaxH = 360
  y.value = event.clientY + menuMaxH > vh
    ? Math.max(8, event.clientY - menuMaxH)
    : event.clientY

  visible.value = true
}

function close() {
  visible.value = false
}

function download(link) {
  window.open(link.pdfUrl, '_blank')
}

async function ingest(link) {
  if (link.phase === 'done' || link.phase === 'loading') return
  link.phase = 'loading'
  try {
    const res = await ingestFromArxiv([link.arxivId])
    const result = res.data[0]
    if (result.success) {
      link.phase = 'done'
    } else {
      link.phase = 'error'
      link.errorMsg = result.detail || '入库失败'
    }
  } catch (err) {
    link.phase = 'error'
    link.errorMsg = err.response?.data?.detail || '网络错误'
  }
}

async function ingestAll() {
  const pending = links.value.filter(l => l.phase === 'idle' || l.phase === 'error')
  await Promise.all(pending.map(ingest))
}

const pendingCount = computed(() =>
  links.value.filter(l => l.phase === 'idle' || l.phase === 'error').length
)

const allDone = computed(() =>
  links.value.every(l => l.phase === 'done')
)

function phaseLabel(phase) {
  return { idle: '入库', loading: '入库中', done: '已入库', error: '重试' }[phase] || '入库'
}

defineExpose({ open })
</script>

<style scoped>
.ctx-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
}

.ctx-menu {
  position: fixed;
  z-index: 901;
  background: var(--bg-2);
  border: 1px solid var(--border-light);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  width: 320px;
  max-height: 360px;
  /* 整体限高 */
  display: flex;
  flex-direction: column;
  /* 去掉 overflow: hidden */
}

.ctx-header {
  flex-shrink: 0;
  /* 加这行，header不参与滚动 */
  padding: 10px 14px 8px;
  font-size: 0.75em;
  font-weight: 600;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  border-bottom: 1px solid var(--border);
}

.ctx-list {
  padding: 4px 0;
  max-height: 280px;
  /* 限高 */
  overflow-y: auto;
}

.ctx-item {
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ctx-item:last-child {
  border-bottom: none;
}

.ctx-item-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ctx-arxiv-id {
  font-size: 0.82em;
  color: var(--text-2);
  font-family: 'JetBrains Mono', monospace;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ctx-abs-link {
  font-size: 0.75em;
  color: var(--accent);
  text-decoration: none;
  flex-shrink: 0;
}

.ctx-abs-link:hover {
  text-decoration: underline;
}

.ctx-item-actions {
  display: flex;
  gap: 6px;
}

.ctx-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  font-size: 0.78em;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-2);
  transition: all 0.15s;
  white-space: nowrap;
}

.ctx-btn:hover:not(:disabled) {
  border-color: var(--border-light);
  color: var(--text);
}

.ctx-btn.download:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.ctx-btn.ingest:hover:not(:disabled) {
  border-color: var(--green);
  color: var(--green);
}

.ctx-btn.ingest.done {
  border-color: var(--green);
  color: var(--green);
  opacity: 0.7;
  cursor: default;
}

.ctx-btn.ingest.error {
  border-color: var(--red);
  color: var(--red);
}

.ctx-btn.ingest.loading {
  opacity: 0.6;
  cursor: wait;
}

.ctx-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ctx-error {
  font-size: 0.75em;
  color: var(--red);
  padding: 0 2px;
}

.ctx-footer {
  flex-shrink: 0;
  /* 加这行，footer不参与滚动 */
  padding: 8px 14px;
  border-top: 1px solid var(--border);
}

.ctx-btn-all {
  width: 100%;
  padding: 7px;
  border-radius: var(--radius-sm);
  font-size: 0.83em;
  font-weight: 500;
  background: var(--accent-glow);
  border: 1px solid var(--accent-dim);
  color: var(--accent);
  cursor: pointer;
  transition: all 0.15s;
}

.ctx-btn-all:hover:not(:disabled) {
  background: rgba(108, 140, 255, 0.25);
}

.ctx-btn-all:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.spin {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>