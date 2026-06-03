<template>
  <div class="drawer-inner">
    <!-- header固定不动 -->
    <div class="drawer-header">
      <span class="drawer-title">设置 &amp; 论文库</span>
      <div class="header-actions">
        <!-- ✨ 新增：配置按钮 -->
        <button class="icon-btn config-entry-btn" @click="showConfig = true" title="系统配置">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
            <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" stroke="currentColor" stroke-width="1.6"
              stroke-linecap="round" stroke-linejoin="round" />
            <path
              d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"
              stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>

        <button class="icon-btn" @click="$emit('close')">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 可整体滚动的内容区 -->
    <div class="drawer-scroll">

      <!-- 外观section -->
      <section class="section">
        <div class="section-title">外观</div>
        <div class="setting-row">
          <span class="setting-label">主题</span>
          <div class="btn-group">
            <button v-for="t in themes" :key="t.value" class="seg-btn" :class="{ active: settings.theme === t.value }"
              @click="setTheme(t.value)">{{ t.label }}</button>
          </div>
        </div>
        <div class="setting-row">
          <span class="setting-label">字体</span>
          <div class="btn-group">
            <button v-for="(f, key) in FONTS" :key="key" class="seg-btn"
              :class="{ active: settings.fontFamily === key }" @click="setFont(key)">{{ f.label }}</button>
          </div>
        </div>
      </section>

      <!-- 功能section -->
      <section class="section">
        <div class="section-title">功能</div>
        <div class="setting-row">
          <div>
            <div class="setting-label">严格模式</div>
            <div class="setting-desc">上传时提取 title / author / year 后才返回</div>
          </div>
          <Toggle :model-value="settings.strict" @update:model-value="onStrictChange" />
        </div>
        <div class="setting-row">
          <div>
            <div class="setting-label">双语对照</div>
            <div class="setting-desc">回答时翻译英文参考文献</div>
          </div>
          <Toggle v-model="settings.translation" />
        </div>
      </section>

      <!-- 论文库section：section-title用sticky卡住 -->
      <section class="section paper-section">
        <!-- ⑦ 这个title会在滚动到顶时sticky -->
        <div class="sticky-block">
          <div class="section-title">论文库</div>
          <MultiUploadPanel :strict="settings.strict" @paper-added="onPaperAdded" @paper-cancelled="fetchPapers" />
          <div class="list-toolbar">
            <input v-model="search" class="search-input" placeholder="搜索论文…" />
            <select v-model="sortBy" class="sort-select">
              <option value="default">默认顺序</option>
              <option value="az">标题 A→Z</option>
              <option value="za">标题 Z→A</option>
              <option value="year-asc">年份从晚</option>
              <option value="year-desc">年份从早</option>
            </select>
          </div>
        </div>

        <div v-if="papersLoading" class="hint">加载中…</div>
        <div v-else-if="filteredPapers.length === 0" class="hint">{{ search ? '无匹配结果' : '暂无论文' }}</div>
        <div v-else v-for="p in filteredPapers" :key="p.doc_id" class="paper-item">
          <div class="paper-main">
            <div class="paper-title" :title="p.title">{{ p.title || '(未命名)' }}</div>
            <div class="paper-meta">
              <span v-if="p.author" class="meta-text">{{ truncate(p.author, 24) }}</span>
              <span v-if="p.year" class="meta-text">{{ p.year }}</span>
              <span class="status-badge" :class="p.status">{{ statusLabel(p.status) }}</span>
            </div>
          </div>
          <div class="paper-actions">
            <button v-if="p.status === 'pending'" class="resume-btn" @click="startResume(p)">入库</button>
            <button class="delete-btn" :class="{ loading: deleting === p.doc_id }" :disabled="deleting === p.doc_id"
              @click="doDelete(p)" title="删除">
              <span v-if="deleting === p.doc_id" class="spin">⟳</span>
              <svg v-else width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M2 3h8M4.5 3V2h3v1M5 5v3M7 5v3M2.5 3l.5 7h6l.5-7" stroke="currentColor" stroke-width="1.1"
                  stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
          </div>
        </div>
      </section>

    </div><!-- end drawer-scroll -->

    <!-- 继续入库弹窗 -->
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
            <button class="btn-cancel" :disabled="resuming" @click="resumePaper = null">取消</button>
            <button class="btn-confirm" :disabled="resuming || !resumeForm.title.trim()" @click="doResume">
              {{ resuming ? '入库中…' : '确认入库' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
    <!-- ✨ 新增：ConfigModal -->
    <Teleport to="body">
      <ConfigModal v-if="showConfig" @close="showConfig = false" />
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, inject } from 'vue'
import { settings, applyTheme, applyFont, FONTS } from '../../store/app.js'
import { listPapers, confirmPaper, deletePaper } from '../../api/paper.js'
import MultiUploadPanel from '../Paper/MultiUploadPanel.vue'
import Toggle from './Toggle.vue'
import ConfigModal from './ConfigModal.vue'   // ✨ 新增

const showConfig = ref(false)   // ✨ 新增

defineEmits(['close'])

const themes = [{ value: 'dark', label: '深色' }, { value: 'light', label: '浅色' }]
const search = ref('')
const sortBy = ref('default')

// papers状态提升到这里，不依赖PaperList的mount
const papers = ref([])
const papersLoading = ref(false)
const resumePaper = ref(null)
const resumeForm = reactive({ title: '', author: '', year: '' })
const resuming = ref(false)

const filteredPapers = computed(() => {
  let list = [...papers.value]
  if (search.value.trim()) {
    const q = search.value.toLowerCase()
    list = list.filter(p =>
      (p.title || '').toLowerCase().includes(q) ||
      (p.author || '').toLowerCase().includes(q)
    )
  }
  switch (sortBy.value) {
    case 'az': list.sort((a, b) => (a.title || '').localeCompare(b.title || '')); break
    case 'za': list.sort((a, b) => (b.title || '').localeCompare(a.title || '')); break
    case 'year-asc': list.sort((a, b) => (a.year || 9999) - (b.year || 9999)); break
    case 'year-desc': list.sort((a, b) => (b.year || 0) - (a.year || 0)); break
  }
  return list
})

async function fetchPapers() {
  papersLoading.value = true
  try {
    const res = await listPapers()
    papers.value = res.data.papers || []
  } catch (e) {
    console.error(e)
  } finally {
    papersLoading.value = false
  }
}

onMounted(fetchPapers)

// 严格模式切换时立即更新settings，不需要刷新列表
function onStrictChange(val) {
  settings.strict = val
}

function setTheme(t) { settings.theme = t; applyTheme(t) }
function setFont(f) { settings.fontFamily = f; applyFont(f) }

// 上传成功后立即刷新
function onPaperAdded() {
  fetchPapers()
}

function startResume(p) {
  resumePaper.value = p
  resumeForm.title = p.title || ''
  resumeForm.author = p.author || ''
  resumeForm.year = p.year ? String(p.year) : ''
}

async function doResume() {
  if (!resumeForm.title.trim() || resuming.value) return
  resuming.value = true
  try {
    await confirmPaper(resumePaper.value.doc_id, resumeForm.title.trim())
    resumePaper.value = null
    await fetchPapers()
  } catch (e) {
    console.error(e)
  } finally {
    resuming.value = false
  }
}
function cancelConfirm() {
  // upload完成但用户取消confirm，paper已经在注册表里了，刷新列表
  if (pendingPaper.value) {
    fetchPapers()
  }
  pendingPaper.value = null  // 这里是UploadPanel的pendingPaper，实际是emit过来的
}


function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : s }
function statusLabel(s) { return { indexed: '已入库', pending: '待确认', error: '失败' }[s] || s }


const deleting = ref(null)  // 正在删除的doc_id

async function doDelete(p) {
  if (deleting.value) return
  deleting.value = p.doc_id
  try {
    await deletePaper(p.doc_id)
    await fetchPapers()  // 删完刷新列表
  } catch (e) {
    console.error('删除失败', e)
  } finally {
    deleting.value = null
  }
}
</script>

<style scoped>
.drawer-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  /* header不滚动 */
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.drawer-title {
  font-weight: 600;
  font-size: 1em;
}

/* ⑦ 整体可滚动区 */
.drawer-scroll {
  flex: 1;
  overflow-y: auto;
  /* 为sticky提供滚动容器 */
}

.icon-btn {
  background: transparent;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  padding: 4px;
  border-radius: 5px;
  display: flex;
  align-items: center;
  transition: color 0.15s;
}

.icon-btn:hover {
  color: var(--text);
  background: var(--bg-hover);
}

.section {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}

.paper-section {
  padding-bottom: 32px;
}

.sticky-block {
  position: sticky;
  top: 0;
  background: var(--bg-2);
  z-index: 1;
  /* 抵消section的padding，让背景撑满宽度 */
  margin: -16px -20px 0;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--border);
}

.section-title {
  font-size: 0.72em;
  font-weight: 600;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.07em;
  margin-bottom: 14px;
}

.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 0;
}

.setting-label {
  font-size: 0.9em;
  font-weight: 500;
  color: var(--text);
}

.setting-desc {
  font-size: 0.78em;
  color: var(--text-3);
  margin-top: 2px;
}

.btn-group {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.seg-btn {
  padding: 5px 12px;
  border-radius: 20px;
  font-size: 0.8em;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.seg-btn.active {
  background: var(--accent-glow);
  border-color: var(--accent);
  color: var(--accent);
}

.list-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  background: var(--bg-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.83em;
  padding: 6px 10px;
  font-family: inherit;
  transition: border-color 0.15s;
}

.search-input:focus {
  outline: none;
  border-color: var(--accent-dim);
}

.search-input::placeholder {
  color: var(--text-3);
}

.sort-select {
  background: var(--bg-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.8em;
  padding: 6px 8px;
  cursor: pointer;
  font-family: inherit;
}

/* 论文列表可滚动 */
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
  width: 420px;
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
  display: flex;
  flex-direction: column;
  gap: 12px;
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

.paper-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.delete-btn {
  opacity: 0;
  padding: 4px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  display: flex;
  align-items: center;
  transition: opacity 0.15s, color 0.15s;
}

.delete-btn.loading {
  opacity: 0.6;
  cursor: wait;
}

.paper-item:hover .delete-btn {
  opacity: 1;
}

.delete-btn:hover {
  color: var(--red);
}

/* 原有样式不变，新增以下 */
.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.config-entry-btn {
  color: var(--text-3);
}

.config-entry-btn:hover {
  color: var(--accent);
}
</style>