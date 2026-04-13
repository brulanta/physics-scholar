<template>
  <div class="multi-upload">
    <!-- 拖拽区 -->
    <div class="drop-zone" :class="{ dragging }" @dragover.prevent="dragging = true" @dragleave="dragging = false"
      @drop.prevent="onDrop" @click="fileInputRef.click()">
      <input ref="fileInputRef" type="file" accept=".pdf" multiple style="display:none" @change="onFileChange" />
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M8 2v9M4 7l4-5 4 5M2 13h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </svg>
      <span>拖拽或点击选择 PDF（支持多选）</span>
    </div>

    <!-- 文件列表面板 -->
    <Teleport to="body">
      <div v-if="files.length > 0" class="panel-overlay">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">上传论文（{{ files.length }} 篇）</span>
            <div class="panel-header-actions">
              <button class="btn-all" :disabled="readyCount === 0" @click="confirmAll">
                全部入库（{{ readyCount }}）
              </button>
              <button class="close-btn" @click="closePanel">
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
                </svg>
              </button>
            </div>
          </div>

          <div class="file-list">
            <div v-for="f in files" :key="f.id" class="file-row" :class="f.phase">
              <!-- 文件名 -->
              <div class="file-name">{{ f.fileName }}</div>

              <!-- 解析中 -->
              <template v-if="f.phase === 'parsing'">
                <div class="file-status">
                  <span class="spin-icon">⟳</span> 解析中…
                </div>
              </template>

              <!-- 解析完，等待确认 -->
              <template v-else-if="f.phase === 'ready'">
                <div class="file-fields">
                  <input v-model="f.title" class="field-input" placeholder="标题 *" />
                  <template v-if="strict">
                    <input v-model="f.author" class="field-input" placeholder="作者" />
                    <input v-model="f.year" class="field-input small" placeholder="年份" />
                  </template>
                </div>
                <button class="btn-confirm-single" :disabled="!f.title.trim()" @click="confirmOne(f)">入库</button>
              </template>

              <!-- 入库中 -->
              <template v-else-if="f.phase === 'confirming'">
                <div class="file-status muted">
                  <span class="spin-icon">⟳</span> 入库中…
                </div>
              </template>

              <!-- 完成 -->
              <template v-else-if="f.phase === 'done'">
                <div class="file-status success">✓ 入库成功</div>
              </template>

              <!-- 失败 -->
              <template v-else-if="f.phase === 'error'">
                <div class="file-status error">✗ {{ f.errorMsg }}</div>
              </template>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, reactive } from 'vue'
import { uploadPaper, confirmPaper } from '../../api/paper.js'

const props = defineProps({
  strict: { type: Boolean, default: false }
})
const emit = defineEmits(['paper-added', 'paper-cancelled'])

const fileInputRef = ref(null)
const dragging = ref(false)
const files = ref([])   // 每个元素是一个reactive的文件状态对象
let idCounter = 0

const readyCount = computed(() =>
  files.value.filter(f => f.phase === 'ready' && f.title.trim()).length
)

function makeFileState(file) {
  return reactive({
    id: ++idCounter,
    fileName: file.name,
    file,
    phase: 'parsing',   // parsing | ready | confirming | done | error
    docId: '',
    title: '',
    author: '',
    year: '',
    errorMsg: ''
  })
}

async function uploadOne(state) {
  try {
    const res = await uploadPaper(state.file, 'default', props.strict)
    const data = res.data
    state.docId = data.doc_id
    state.title = data.title || state.fileName.replace(/\.pdf$/i, '')
    state.author = data.author || ''
    state.year = data.year ? String(data.year) : ''
    state.phase = 'ready'
  } catch (err) {
    state.errorMsg = err.response?.data?.detail || '解析失败'
    state.phase = 'error'
  }
}

async function addFiles(fileList) {
  const newStates = Array.from(fileList).map(makeFileState)
  files.value.push(...newStates)
  // 并发上传
  await Promise.all(newStates.map(uploadOne))
}

function onDrop(e) {
  dragging.value = false
  addFiles(e.dataTransfer.files)
}

function onFileChange(e) {
  addFiles(e.target.files)
  e.target.value = ''
}

async function confirmOne(state) {
  if (!state.title.trim() || state.phase !== 'ready') return
  state.phase = 'confirming'
  try {
    await confirmPaper(state.docId, state.title.trim())
    state.phase = 'done'
    emit('paper-added')
  } catch (err) {
    state.errorMsg = err.response?.data?.detail || '入库失败'
    state.phase = 'error'
  }
}

async function confirmAll() {
  const ready = files.value.filter(f => f.phase === 'ready' && f.title.trim())
  await Promise.all(ready.map(confirmOne))
}

function closePanel() {
  const hasPending = files.value.some(f =>
    f.phase === 'ready' || f.phase === 'error'
    // confirming状态不算，让它跑完
  )
  if (hasPending) emit('paper-cancelled')
  // 只移除已完成和失败的，confirming的等它跑完
  files.value = files.value.filter(f => f.phase === 'confirming')
  if (files.value.length === 0) files.value = []
}
</script>

<style scoped>
.multi-upload {
  padding: 4px 0 12px;
}

.drop-zone {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-3);
  font-size: 0.82em;
  transition: all 0.15s;
  user-select: none;
}

.drop-zone:hover,
.drop-zone.dragging {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-glow);
}

.panel-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 500;
  backdrop-filter: blur(3px);
}

.panel {
  background: var(--bg-2);
  border: 1px solid var(--border-light);
  border-radius: var(--radius);
  width: 580px;
  max-width: 94vw;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.panel-title {
  font-weight: 600;
  font-size: 0.95em;
}

.panel-header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-all {
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  font-size: 0.83em;
  font-weight: 500;
  background: var(--accent);
  border: none;
  color: #fff;
  cursor: pointer;
  transition: all 0.15s;
}

.btn-all:hover:not(:disabled) {
  background: #5a7fff;
}

.btn-all:disabled {
  opacity: 0.4;
  cursor: not-allowed;
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

.file-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border);
  min-height: 52px;
  transition: background 0.12s;
}

.file-row:last-child {
  border-bottom: none;
}

.file-row.done {
  opacity: 0.6;
}

.file-name {
  font-size: 0.83em;
  color: var(--text-2);
  flex-shrink: 0;
  width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-status {
  font-size: 0.82em;
  color: var(--text-3);
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
}

.file-status.success {
  color: var(--green);
}

.file-status.error {
  color: var(--red);
}

.file-status.muted {
  color: var(--text-3);
}

.file-fields {
  flex: 1;
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}

.field-input {
  background: var(--bg-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.83em;
  padding: 5px 9px;
  font-family: inherit;
  transition: border-color 0.15s;
  flex: 1;
  min-width: 80px;
}

.field-input.small {
  flex: 0 0 80px;
}

.field-input:focus {
  outline: none;
  border-color: var(--accent-dim);
}

.field-input::placeholder {
  color: var(--text-3);
}

.btn-confirm-single {
  padding: 5px 12px;
  border-radius: var(--radius-sm);
  font-size: 0.78em;
  font-weight: 500;
  background: var(--accent-glow);
  border: 1px solid var(--accent-dim);
  color: var(--accent);
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: all 0.15s;
}

.btn-confirm-single:hover:not(:disabled) {
  background: rgba(108, 140, 255, 0.25);
}

.btn-confirm-single:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.spin-icon {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>