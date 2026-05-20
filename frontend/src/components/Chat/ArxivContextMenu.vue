<template>
  <Teleport to="body">
    <div v-if="visible" class="ctx-overlay" @click="close" @contextmenu.prevent="close" />
    <div v-if="visible" class="ctx-menu" :style="{ top: y + 'px', left: x + 'px' }">
      <div class="ctx-header">
        检测到 {{ links.length }} 篇论文
      </div>

      <div class="ctx-list">
        <div v-for="link in links" :key="link.pdfUrl" class="ctx-item">
          <div class="ctx-item-info">
            <!-- 【改】arXiv 显示 ID，其他显示截断域名 -->
            <span class="ctx-arxiv-id">{{ link.type === 'arxiv' ? link.arxivId : link.displayUrl }}</span>

            <!-- 【改】可用性指示器 -->
            <span v-if="link.urlOk === null" class="ctx-checking spin" title="检测中">⟳</span>
            <span v-else-if="link.urlOk === false" class="ctx-unavail" :title="link.errorMsg">⚠</span>

            <a v-if="link.absUrl" :href="link.absUrl" target="_blank" class="ctx-abs-link" @click.stop>页面</a>
          </div>

          <!-- 【新】不可用时展示错误提示条 -->
          <div v-if="link.urlOk === false" class="ctx-url-error">
            {{ link.errorMsg }}
          </div>

          <div class="ctx-item-actions">
            <!-- 【改】不可用时下载按钮降级提示 -->
            <button class="ctx-btn download" :class="{ unavail: link.urlOk === false }"
              :title="link.urlOk === false ? link.errorMsg : '在浏览器中打开 PDF'" @click="download(link)">
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M5.5 1v6M2.5 5l3 3 3-3M1 9.5h9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"
                  stroke-linejoin="round" />
              </svg>
              {{ link.urlOk === false ? '尝试打开' : '下载' }}
            </button>

            <button class="ctx-btn ingest" :class="{
              done: link.phase === 'done',
              error: link.phase === 'error',
              loading: link.phase === 'loading',
              disabled: link.urlOk === false && link.phase === 'idle'
            }"
              :disabled="link.phase === 'done' || link.phase === 'loading' || (link.urlOk === false && link.phase === 'idle')"
              :title="link.urlOk === false && link.phase === 'idle' ? '链接不可用，无法入库' : ''" @click="ingest(link)">
              <span v-if="link.phase === 'loading'" class="spin">⟳</span>
              <span v-else-if="link.phase === 'done'">✓</span>
              <span v-else-if="link.phase === 'error'">✗</span>
              <svg v-else width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M5.5 7V1M2.5 4l3-3 3 3M1 9.5h9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"
                  stroke-linejoin="round" />
              </svg>
              {{ phaseLabel(link.phase, link.urlOk) }}
            </button>
          </div>

          <div v-if="link.phase === 'error'" class="ctx-error">{{ link.errorMsg }}</div>
        </div>
      </div>

      <div v-if="links.length > 1" class="ctx-footer">
        <!-- 【改】只统计链接可用的 pending 数量 -->
        <button class="ctx-btn-all" :disabled="allDone || availablePendingCount === 0" @click="ingestAll">
          全部入库（{{ availablePendingCount }}）
        </button>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, reactive } from 'vue'
import { ingestFromArxiv, ingestFromUrl, listPapers } from '../../api/paper.js'
import { extractLinks } from '../../utils/extractLinks.js'  // 抽出去的提取函数

const visible = ref(false)
const x = ref(0)
const y = ref(0)
const links = ref([])

async function open(event, text) {
  const extracted = extractLinks(text)
  if (extracted.length === 0) return

  // open() 里替换原来的 existingFileNames 逻辑
  let existingUrls = new Set()
  try {
    const res = await listPapers()
    existingUrls = new Set(
      (res.data.papers || [])
        .map(p => p.source_url)
        .filter(Boolean)
        .map(u => u.replace(/v\d+$/, '').replace(/\/$/, ''))  // 去版本号、去末尾斜杠
    )
  } catch (_) { }

  links.value = extracted.map(link => {
    const normalizedUrl = link.pdfUrl.replace(/v\d+$/, '').replace(/\/$/, '')
    const alreadyIn = existingUrls.has(normalizedUrl)
    return reactive({ ...link, phase: alreadyIn ? 'done' : 'idle' })
  })

  // 菜单定位（原有逻辑不变）
  const menuW = 320, vw = window.innerWidth, vh = window.innerHeight, menuMaxH = 360
  x.value = event.clientX + menuW > vw ? event.clientX - menuW : event.clientX
  y.value = event.clientY + menuMaxH > vh ? Math.max(8, event.clientY - menuMaxH) : event.clientY
  visible.value = true

  // 【新】异步预检所有链接可用性
  links.value.forEach(link => {
    if (link.phase !== 'done') preflight(link)
    else link.urlOk = true
  })
}

// 【新】预检函数
async function preflight(link) {
  if (link.type === 'arxiv') { link.urlOk = true; return }
  try {
    const res = await fetch(`/proxy-head?url=${encodeURIComponent(link.pdfUrl)}`, {
      signal: AbortSignal.timeout(6000)
    })
    const data = await res.json()
    const ct = data.content_type || ''
    if (data.status === 0 || data.status >= 400) {
      link.urlOk = false
      link.errorMsg = `链接无法访问（HTTP ${data.status}）`
    } else if (!ct.includes('pdf') && !ct.includes('octet-stream')) {
      link.urlOk = false
      link.errorMsg = `非 PDF 内容（${ct.split(';')[0]}），可能需要机构权限`
    } else {
      link.urlOk = true
    }
  } catch (e) {
    link.urlOk = false
    link.errorMsg = e.name === 'TimeoutError' ? '访问超时，可能需要机构权限' : '无法验证链接可用性'
  }
}

function close() { visible.value = false }

function download(link) { window.open(link.pdfUrl, '_blank') }

// 【改】不再分 arXiv/URL 两条路，统一传 pdfUrl
async function ingest(link) {
  if (link.phase === 'done' || link.phase === 'loading') return
  if (link.urlOk === false && link.phase === 'idle') return
  link.phase = 'loading'
  try {
    const res = await ingestFromUrl([link.pdfUrl])  // 数组，单个元素
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
  const pending = links.value.filter(l =>
    (l.phase === 'idle' || l.phase === 'error') && l.urlOk !== false
  )
  // 先把所有 pending 标为 loading
  pending.forEach(l => l.phase = 'loading')
  try {
    const res = await ingestFromUrl(pending.map(l => l.pdfUrl))
    res.data.forEach(result => {
      const link = pending.find(l => l.pdfUrl === result.pdf_url)
      if (!link) return
      if (result.success) {
        link.phase = 'done'
      } else {
        link.phase = 'error'
        link.errorMsg = result.detail || '入库失败'
      }
    })
  } catch (err) {
    pending.forEach(l => {
      l.phase = 'error'
      l.errorMsg = '网络错误'
    })
  }
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