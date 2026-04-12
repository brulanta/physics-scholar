<template>
  <div class="upload-area">
    <div class="drop-zone" :class="{ dragging, loading: uploading }" @dragover.prevent="dragging = true"
      @dragleave="dragging = false" @drop.prevent="onDrop" @click="!uploading && fileInputRef.click()">
      <input ref="fileInputRef" type="file" accept=".pdf" style="display:none" @change="onFileChange" />
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M8 2v9M4 7l4-5 4 5M2 13h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </svg>
      <span>{{ uploading ? '解析中…' : '拖拽或点击上传 PDF' }}</span>
    </div>

    <div v-if="statusMsg" class="status-msg" :class="statusType">{{ statusMsg }}</div>

    <Teleport to="body">
      <div v-if="pendingPaper" class="confirm-overlay">
        <div class="confirm-modal">
          <div class="confirm-header">
            <span class="confirm-title">确认论文信息</span>
            <button class="close-btn" @click="cancelConfirm">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
              </svg>
            </button>
          </div>
          <div class="confirm-fields">
            <div class="field-group">
              <label>标题 *</label>
              <input v-model="form.title" class="field-input" placeholder="论文标题" />
            </div>
            <template v-if="strict">
              <div class="field-group">
                <label>作者</label>
                <input v-model="form.author" class="field-input" placeholder="作者" />
              </div>
              <div class="field-group">
                <label>年份</label>
                <input v-model="form.year" class="field-input" placeholder="如 2024" style="width:120px" />
              </div>
            </template>
            <template v-else>
              <div v-if="pendingPaper.author" class="field-meta">
                作者：{{ pendingPaper.author }}
                <span v-if="pendingPaper.year"> · {{ pendingPaper.year }}</span>
              </div>
            </template>
          </div>
          <div class="confirm-actions">
            <button class="btn-cancel" @click="cancelConfirm">取消</button>
            <button class="btn-confirm" :disabled="confirming || !form.title.trim()" @click="doConfirm">
              {{ confirming ? '入库中…' : '确认入库' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { uploadPaper, confirmPaper } from '../../api/paper.js'

const props = defineProps({
  strict: { type: Boolean, default: false }
})
const emit = defineEmits(['paper-added'])

const fileInputRef = ref(null)
const dragging = ref(false)
const uploading = ref(false)
const pendingPaper = ref(null)
const confirming = ref(false)
const statusMsg = ref('')
const statusType = ref('info')
const form = reactive({ title: '', author: '', year: '' })

function showStatus(msg, type = 'info', ms = 3500) {
  statusMsg.value = msg
  statusType.value = type
  setTimeout(() => { statusMsg.value = '' }, ms)
}

async function handleFile(file) {
  if (!file || file.type !== 'application/pdf') {
    showStatus('请选择 PDF 文件', 'error')
    return
  }
  uploading.value = true
  try {
    // 用当前prop值，每次上传时读最新的strict
    const res = await uploadPaper(file, 'default', props.strict)
    const data = res.data
    pendingPaper.value = data
    form.title = data.title || file.name.replace(/\.pdf$/i, '')
    form.author = data.author || ''
    form.year = data.year ? String(data.year) : ''
  } catch (err) {
    showStatus(err.response?.data?.detail || '上传失败', 'error')
  } finally {
    uploading.value = false
  }
}

function onDrop(e) { dragging.value = false; handleFile(e.dataTransfer.files[0]) }
function onFileChange(e) { handleFile(e.target.files[0]); e.target.value = '' }
function cancelConfirm() {
  pendingPaper.value = null
  emit('paper-cancelled')  // 新增
}

async function doConfirm() {
  if (!form.title.trim() || confirming.value) return
  confirming.value = true
  try {
    await confirmPaper(pendingPaper.value.doc_id, form.title.trim())
    showStatus(`《${form.title}》入库成功`, 'success')
    pendingPaper.value = null
    emit('paper-added')
  } catch (err) {
    showStatus(err.response?.data?.detail || '入库失败', 'error')
  } finally {
    confirming.value = false
  }
}
</script>

<style scoped>
.upload-area {
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

.drop-zone.loading {
  opacity: 0.6;
  cursor: wait;
}

.status-msg {
  font-size: 0.78em;
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  margin-top: 6px;
}

.status-msg.info {
  color: var(--text-2);
}

.status-msg.success {
  color: var(--green);
}

.status-msg.error {
  color: var(--red);
}

.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 500;
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

.field-meta {
  font-size: 0.82em;
  color: var(--text-3);
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