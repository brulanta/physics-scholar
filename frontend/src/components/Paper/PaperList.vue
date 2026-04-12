<template>
  <div class="paper-list">
    <div v-if="loading" class="hint">加载中…</div>
    <div v-else-if="filtered.length === 0" class="hint">{{ search ? '无匹配结果' : '暂无论文' }}</div>
    <div v-else v-for="p in filtered" :key="p.doc_id" class="paper-item">
      <div class="paper-main">
        <div class="paper-title" :title="p.title">{{ p.title || '(未命名)' }}</div>
        <div class="paper-meta">
          <span v-if="p.author" class="meta-text">{{ truncate(p.author, 18) }}</span>
          <span v-if="p.year" class="meta-text">{{ p.year }}</span>
          <span class="status-badge" :class="p.status">{{ statusLabel(p.status) }}</span>
        </div>
      </div>
      <button v-if="p.status === 'pending' && showConfirm" class="resume-btn" @click="startConfirm(p)">入库</button>
    </div>

    <Teleport to="body">
      <div v-if="resumePaper" class="confirm-overlay">
        <div class="confirm-modal">
          <div class="confirm-header">
            <span class="confirm-title">完成入库</span>
            <button class="close-btn" @click="resumePaper = null">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
              </svg>
            </button>
          </div>
          <div class="confirm-fields">
            <div class="field-group">
              <label>标题 *</label>
              <input v-model="resumeForm.title" class="field-input" placeholder="论文标题" />
            </div>
            <template v-if="settings.strict">
              <div class="field-group">
                <label>作者</label>
                <input v-model="resumeForm.author" class="field-input" placeholder="作者" />
              </div>
              <div class="field-group">
                <label>年份</label>
                <input v-model="resumeForm.year" class="field-input" placeholder="如 2024" style="width:120px" />
              </div>
            </template>
          </div>
          <div class="confirm-actions">
            <button class="btn-cancel" @click="resumePaper = null">取消</button>
            <button class="btn-confirm" :disabled="confirming || !resumeTitle.trim()" @click="doResume">
              {{ confirming ? '入库中…' : '确认入库' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { listPapers, confirmPaper } from '../../api/paper.js'
import { settings } from '../../store/app.js'   // 加这行
import { reactive } from 'vue'

const props = defineProps({
  showConfirm: { type: Boolean, default: false },
  search: { type: String, default: '' },
  sortBy: { type: String, default: 'default' }
})

const papers = ref([])
const loading = ref(false)
const resumePaper = ref(null)
const resumeForm = reactive({ title: '', author: '', year: '' })
const confirming = ref(false)

const filtered = computed(() => {
  let list = [...papers.value]
  if (props.search.trim()) {
    const q = props.search.toLowerCase()
    list = list.filter(p =>
      (p.title || '').toLowerCase().includes(q) ||
      (p.author || '').toLowerCase().includes(q)
    )
  }
  switch (props.sortBy) {
    case 'az': list.sort((a, b) => (a.title || '').localeCompare(b.title || '')); break
    case 'za': list.sort((a, b) => (b.title || '').localeCompare(a.title || '')); break
    case 'year-asc': list.sort((a, b) => (a.year || 9999) - (b.year || 9999)); break
    case 'year-desc': list.sort((a, b) => (b.year || 0) - (a.year || 0)); break
  }
  return list
})

async function fetchPapers() {
  loading.value = true
  try {
    const res = await listPapers()
    papers.value = res.data.papers || []
  } catch (e) {
    console.error('获取论文列表失败', e)
  } finally {
    loading.value = false
  }
}

onMounted(fetchPapers)
defineExpose({ fetchPapers })

function startConfirm(p) {
  resumePaper.value = p
  resumeForm.title = p.title || ''
  resumeForm.author = p.author || ''
  resumeForm.year = p.year ? String(p.year) : ''
}

async function doResume() {
  if (!resumeForm.title.trim() || confirming.value) return
  confirming.value = true
  try {
    await confirmPaper(resumePaper.value.doc_id, resumeForm.title.trim())
    resumePaper.value = null
    await fetchPapers()
  } catch (e) {
    console.error(e)
  } finally { confirming.value = false }
}

function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : s }
function statusLabel(s) { return { indexed: '已入库', pending: '待确认', error: '失败' }[s] || s }
</script>

<style scoped>
.paper-list {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 16px;
}

.hint {
  font-size: 0.8em;
  color: var(--text-3);
  text-align: center;
  padding: 20px 0;
}

.paper-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 2px;
  border-bottom: 1px solid var(--border);
  transition: background 0.12s;
}

.paper-item:last-child {
  border-bottom: none;
}

.paper-item:hover {
  background: var(--bg-hover);
}

.paper-main {
  flex: 1;
  min-width: 0;
}

.paper-title {
  font-size: 0.85em;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.paper-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  flex-wrap: wrap;
}

.meta-text {
  font-size: 0.75em;
  color: var(--text-3);
}

.status-badge {
  font-size: 0.7em;
  padding: 1px 7px;
  border-radius: 10px;
  font-weight: 500;
}

.status-badge.indexed {
  background: rgba(74, 222, 128, 0.12);
  color: var(--green);
}

.status-badge.pending {
  background: rgba(251, 191, 36, 0.12);
  color: var(--yellow);
}

.status-badge.error {
  background: rgba(248, 113, 113, 0.12);
  color: var(--red);
}

.resume-btn {
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  font-size: 0.75em;
  font-weight: 500;
  background: var(--accent-glow);
  border: 1px solid var(--accent-dim);
  color: var(--accent);
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: all 0.15s;
}

.resume-btn:hover {
  background: rgba(108, 140, 255, 0.25);
}

/* 弹窗 */
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 600;
  backdrop-filter: blur(3px);
}

.confirm-modal {
  background: var(--bg-2);
  border: 1px solid var(--border-light);
  border-radius: var(--radius);
  width: 400px;
  max-width: 92vw;
  box-shadow: var(--shadow);
}

.confirm-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}

.confirm-title {
  font-weight: 600;
  font-size: 0.95em;
}

.close-btn {
  background: transparent;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  transition: color 0.15s;
}

.close-btn:hover {
  color: var(--text);
}

.confirm-fields {
  padding: 16px 20px;
}

.field-group {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.field-group label {
  font-size: 0.75em;
  font-weight: 600;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.field-input {
  background: var(--bg-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.9em;
  padding: 8px 12px;
  font-family: inherit;
  transition: border-color 0.15s;
  width: 100%;
}

.field-input:focus {
  outline: none;
  border-color: var(--accent-dim);
}

.confirm-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  padding: 14px 20px;
  border-top: 1px solid var(--border);
}

.btn-cancel,
.btn-confirm {
  padding: 7px 18px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  border: 1px solid transparent;
}

.btn-cancel {
  background: transparent;
  border-color: var(--border);
  color: var(--text-2);
}

.btn-cancel:hover {
  border-color: var(--border-light);
  color: var(--text);
}

.btn-confirm {
  background: var(--accent);
  color: #fff;
}

.btn-confirm:hover:not(:disabled) {
  background: #5a7fff;
}

.btn-confirm:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>