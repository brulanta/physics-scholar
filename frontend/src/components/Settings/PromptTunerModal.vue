<!-- components/Settings/PromptTunerModal.vue — dev-only Prompt 模块调控 GUI -->
<template>
  <Teleport to="body">
    <div class="pt-overlay" @mousedown.self="close">
      <div class="pt-modal" role="dialog" aria-modal="true">

        <!-- 顶部栏：标题 + mode 切换 + 关闭 -->
        <div class="pt-header">
          <div class="pt-header-left">
            <span class="pt-title">Prompt 模块调控</span>
            <span class="pt-dev-tag">dev</span>
            <div class="mode-switch">
              <button v-for="m in modes" :key="m" class="mode-btn" :class="{ active: mode === m }"
                @click="switchMode(m)">{{ m }}</button>
            </div>
          </div>
          <button class="icon-btn" @click="close">
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
            </svg>
          </button>
        </div>

        <!-- 加载失败 -->
        <div v-if="loadError" class="pt-body pt-load-error">
          ⚠ 模块清单加载失败（确认后端为 dev 模式且已挂载 /api/dev）
        </div>

        <!-- 双栏：左模块列表 / 右预览或内容编辑 -->
        <div v-else class="pt-body">

          <!-- 左栏 -->
          <div class="pt-left">
            <div class="pt-hint">共用模块的开关/顺序按 mode 分别记录在各自 yaml</div>
            <div class="module-list">
              <div v-for="(m, i) in modules" :key="m.name" class="module-row" :class="{ off: !m.enabled }">
                <Toggle :model-value="m.enabled" @update:model-value="onToggle(i, $event)" />
                <button class="module-name" :class="{ selected: viewing === m.name && tab === 'content' }"
                  :title="'查看/编辑 ' + m.name + ' 内容'" @click="viewContent(m.name)">{{ m.name }}</button>
                <span v-if="m.source === 'yaml'" class="source-badge yaml" title="yaml 定义模块（可删除）">yaml</span>
                <span v-else class="source-badge" :class="m.source">{{ m.source === 'shared' ? '共用' : '模式' }}</span>
                <span v-if="m.overridden" class="ovr-badge" title="内容已覆盖 .py 默认（可恢复默认）">覆盖</span>
                <div class="arrow-btns">
                  <button class="arrow-btn" :disabled="i === 0" title="上移" @click="move(i, -1)">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                      <path d="M8 3v10M8 3L3.5 7.5M8 3l4.5 4.5" stroke="currentColor" stroke-width="1.6"
                        stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                  </button>
                  <button class="arrow-btn" :disabled="i === modules.length - 1" title="下移" @click="move(i, 1)">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                      <path d="M8 13V3M8 13l-4.5-4.5M8 13l4.5-4.5" stroke="currentColor" stroke-width="1.6"
                        stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                  </button>
                  <button v-if="m.deletable" class="arrow-btn del" title="删除该 yaml 定义模块"
                    @click="removeModule(i)">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                      <path d="M3 4h10M6.5 4V2.5h3V4M4.5 4l.6 9h5.8l.6-9M6.7 6.5v4M9.3 6.5v4"
                        stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
            <!-- 新增模块 -->
            <div class="add-module">
              <template v-if="adding">
                <input v-model="newName" class="add-input" placeholder="UPPER_SNAKE 名" spellcheck="false"
                  @keyup.enter="confirmAdd" @keyup.esc="adding = false" ref="addInput" />
                <button class="mini-btn primary" :disabled="!newNameOk" title="创建" @click="confirmAdd">✓</button>
                <button class="mini-btn" title="取消" @click="adding = false">✕</button>
              </template>
              <button v-else class="add-btn" @click="startAdd">＋ 新增模块（yaml 定义）</button>
            </div>
          </div>

          <!-- 右栏 -->
          <div class="pt-right">
            <div class="pt-right-tabs">
              <button class="right-tab" :class="{ active: tab === 'preview' }" @click="tab = 'preview'">拼装预览</button>
              <button class="right-tab" :class="{ active: tab === 'content' }" @click="tab = 'content'">
                模块内容{{ viewing ? ` · ${viewing}` : '' }}
              </button>
              <!-- 内容 tab 的操作条 -->
              <template v-if="tab === 'content' && viewingModule">
                <button v-if="!editing" class="mini-btn" @click="startEdit">编辑</button>
                <template v-else>
                  <button class="mini-btn primary" @click="confirmEdit">确定</button>
                  <button class="mini-btn" @click="cancelEdit">取消</button>
                </template>
                <button v-if="viewingModule.overridden && !editing" class="mini-btn warn"
                  @click="resetDefault">恢复默认</button>
              </template>
              <span class="pt-right-meta">
                <span v-if="previewing" class="spin">⟳</span>
                <template v-else-if="tab === 'preview' && preview">
                  {{ activeCount }}/{{ modules.length }} 启用 · {{ preview.char_count }} 字符
                </template>
              </span>
            </div>

            <div v-if="tab === 'preview'" class="pt-preview-pane">
              <pre class="pt-pre">{{ previewError || (preview ? preview.prompt : '加载中…') }}</pre>
            </div>
            <div v-else class="pt-preview-pane">
              <template v-if="viewingModule">
                <textarea v-if="editing" v-model="editBuffer" class="pt-editarea" spellcheck="false"></textarea>
                <pre v-else class="pt-pre">{{ viewingModule.content }}</pre>
                <div v-if="editing" class="edit-hint">占位符可用：{history} {citation_plugin} {tool_decision_plugin}（保存时后端校验）</div>
                <div v-else-if="viewingModule.overridden" class="edit-hint">此模块内容已覆盖 .py 默认（「恢复默认」收回 yaml 覆盖）</div>
              </template>
              <pre v-else class="pt-pre">（点击左侧模块名查看内容）</pre>
            </div>
            <div v-if="tab === 'preview'" class="pt-preview-note">结构预览：占位符（对话历史/引用插件）填的是标记文本，非逐字节生产 prompt</div>
          </div>

        </div><!-- end pt-body -->

        <!-- 底部操作栏 -->
        <div class="pt-footer">
          <span v-if="saveMsg" class="save-msg" :class="{ ok: saveOk, err: !saveOk }">{{ saveMsg }}</span>
          <span v-else-if="dirty" class="dirty-hint">有未保存的改动</span>
          <div class="footer-btns">
            <button class="btn-cancel" @click="load">重新加载</button>
            <button class="btn-save" :disabled="saving || !modules.length" @click="doSave">
              <span v-if="saving" class="spin">⟳</span>
              <span v-else>保存配置</span>
            </button>
          </div>
        </div>

      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import Toggle from './Toggle.vue'
import { getPromptConfig, previewPrompt, savePromptConfig } from '../../api/prompt.js'

const emit = defineEmits(['close'])
const close = () => emit('close')

// mode 列表（后端 MODES 真相源；frozen 下 /api/dev 404，本组件根本打不开）
const modes = ['normal', 'discuss']
const mode = ref('normal')

// 模块列表（顺序即拼装序）。字段：name/source/enabled/order/content/
// deletable(yaml 定义模块)/overridden(内容覆盖 .py 默认)/default_content(.py 原版)
const modules = ref([])
const loadError = ref(false)

// 预览状态
const preview = ref(null)
const previewing = ref(false)
const previewError = ref('')

// 右栏 tab：拼装预览 / 模块内容
const tab = ref('preview')
const viewing = ref('') // 当前查看的模块名
const viewingModule = computed(() => modules.value.find(x => x.name === viewing.value) || null)

// 内容编辑
const editing = ref(false)
const editBuffer = ref('')

// 新增模块
const adding = ref(false)
const newName = ref('')
const addInput = ref(null)
const newNameOk = computed(() => /^[A-Z][A-Z0-9_]*$/.test(newName.value.trim())
  && !modules.value.some(m => m.name === newName.value.trim()))

// 保存状态
const saving = ref(false)
const saveMsg = ref('')
const saveOk = ref(true)
const dirty = ref(false)

const activeCount = computed(() => modules.value.filter(m => m.enabled).length)

// ── 加载（也用于「重新加载」弃改动） ──
async function load() {
  editing.value = false
  adding.value = false
  try {
    const { data } = await getPromptConfig(mode.value)
    modules.value = data.modules
    loadError.value = false
    dirty.value = false
    saveMsg.value = ''
    schedulePreview()
  } catch (e) {
    loadError.value = true
  }
}

// ── 防抖预览：任何 toggle/移动/mode 切换/内容变更后 400ms 重取 ──
let previewTimer = null
let previewSeq = 0 // 请求序号：防过期响应覆盖新状态
function schedulePreview() {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(doPreview, 400)
}
async function doPreview() {
  if (!modules.value.length) return
  const seq = ++previewSeq
  previewing.value = true
  previewError.value = ''
  try {
    const { data } = await previewPrompt(mode.value, payloadModules())
    if (seq !== previewSeq) return // 过期响应丢弃
    preview.value = data
  } catch (e) {
    if (seq !== previewSeq) return
    previewError.value = e?.response?.data?.detail || `预览失败：${e.message}`
  } finally {
    if (seq === previewSeq) previewing.value = false
  }
}

// preview/save 的 payload：全量有序列表；content 仅在「覆盖中或 yaml 定义」时带
// （未覆盖的 .py 模块带 content=null = 不改，后端同理省略）
function payloadModules() {
  return modules.value.map(m => ({
    name: m.name,
    enabled: m.enabled,
    content: m.overridden || m.source === 'yaml' ? m.content : null,
  }))
}

// ── 编辑操作 ──
function markDirty() { dirty.value = true; schedulePreview() }

function onToggle(i, enabled) {
  modules.value[i].enabled = enabled
  markDirty()
}
function move(i, delta) {
  const j = i + delta
  if (j < 0 || j >= modules.value.length) return
  const arr = modules.value
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
  markDirty()
}
function switchMode(m) {
  if (m === mode.value) return
  mode.value = m
  tab.value = 'preview'
  viewing.value = ''
  load()
}
function viewContent(name) {
  viewing.value = name
  tab.value = 'content'
}

// ── 内容编辑 ──
function startEdit() {
  if (!viewingModule.value) return
  editBuffer.value = viewingModule.value.content
  editing.value = true
}
function confirmEdit() {
  if (!viewingModule.value) return
  viewingModule.value.content = editBuffer.value
  // yaml 定义模块始终 overridden 语义；.py 模块编辑后标覆盖（恢复默认即收回）
  viewingModule.value.overridden =
    viewingModule.value.source === 'yaml'
    || editBuffer.value !== viewingModule.value.default_content
  editing.value = false
  markDirty()
}
function cancelEdit() { editing.value = false }
function resetDefault() {
  if (!viewingModule.value || viewingModule.value.default_content == null) return
  viewingModule.value.content = viewingModule.value.default_content
  viewingModule.value.overridden = false
  markDirty()
}

// ── 新增 / 删除 ──
function startAdd() {
  adding.value = true
  newName.value = ''
  nextTick(() => addInput.value?.focus())
}
function confirmAdd() {
  const name = newName.value.trim()
  if (!newNameOk.value) return
  modules.value.push({
    name,
    source: 'yaml',
    enabled: true,
    order: (modules.value.length + 1) * 10,
    content: `## ${name}\n\n（新模块内容，点击模块名 → 编辑）\n`,
    deletable: true,
    overridden: false, // yaml 定义模块无「覆盖」语义（无 .py 默认）
    default_content: null,
  })
  adding.value = false
  viewContent(name)
  markDirty()
}
function removeModule(i) {
  const m = modules.value[i]
  if (!m.deletable) return // .py 模块「仅可禁用」——删除按钮只对 yaml 模块渲染
  modules.value.splice(i, 1)
  if (viewing.value === m.name) { viewing.value = ''; tab.value = 'preview' }
  markDirty()
}

// ── 保存 ──
async function doSave() {
  if (editing.value) confirmEdit() // 编辑中的缓冲先落进列表
  saving.value = true
  saveMsg.value = ''
  try {
    const { data } = await savePromptConfig(mode.value, payloadModules())
    saveOk.value = true
    saveMsg.value = `✓ ${data.message}（${data.path}）`
    dirty.value = false
    load() // 重取：overridden/deletable 等标记以后端为准
  } catch (e) {
    saveOk.value = false
    saveMsg.value = `✗ ${e?.response?.data?.detail || e.message}`
  } finally {
    saving.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { if (previewTimer) clearTimeout(previewTimer) })
</script>

<style scoped>
/* ── 遮罩（对齐 ConfigModal，z-index 低于 ServiceMask 9999） ── */
.pt-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 700;
  animation: pt-overlay-in 0.18s ease;
}

@keyframes pt-overlay-in {
  from { opacity: 0 }
  to { opacity: 1 }
}

/* ── 主弹窗：宽双栏 ── */
.pt-modal {
  background: var(--bg-2);
  border: 1px solid var(--border-light, var(--border));
  border-radius: var(--radius, 10px);
  width: 860px;
  max-width: 96vw;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.5);
  animation: pt-modal-in 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes pt-modal-in {
  from { opacity: 0; transform: scale(0.96) translateY(8px) }
  to { opacity: 1; transform: scale(1) translateY(0) }
}

/* ── Header ── */
.pt-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.pt-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pt-title {
  font-weight: 600;
  font-size: 0.95em;
  color: var(--text);
}

.pt-dev-tag {
  font-size: 0.68em;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--accent);
  color: #fff;
  letter-spacing: 0.5px;
}

.mode-switch {
  display: flex;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  margin-left: 8px;
}

.mode-btn {
  background: transparent;
  border: none;
  padding: 4px 14px;
  font-size: 0.8em;
  cursor: pointer;
  color: var(--text-2);
  font-family: inherit;
  transition: background 0.15s, color 0.15s;
}

.mode-btn.active {
  background: var(--accent);
  color: #fff;
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
  transition: color 0.15s, background 0.15s;
}

.icon-btn:hover {
  color: var(--text);
  background: var(--bg-hover);
}

/* ── 双栏 Body ── */
.pt-body {
  flex: 1;
  display: flex;
  gap: 0;
  min-height: 0;
  overflow: hidden;
}

.pt-load-error {
  align-items: center;
  justify-content: center;
  color: var(--yellow, #fbbf24);
  font-size: 0.85em;
  padding: 40px;
  display: flex;
}

/* 左栏：模块列表 */
.pt-left {
  width: 340px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.pt-hint {
  padding: 8px 14px;
  font-size: 0.72em;
  color: var(--text-3);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.module-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 8px;
}

.module-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  border-radius: 6px;
  transition: background 0.12s, opacity 0.15s;
}

.module-row:hover {
  background: var(--bg-hover);
}

.module-row.off .module-name,
.module-row.off .source-badge,
.module-row.off .ovr-badge {
  opacity: 0.45;
}

.module-name {
  background: transparent;
  border: none;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.78em;
  color: var(--text);
  cursor: pointer;
  text-align: left;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: background 0.12s;
}

.module-name:hover {
  background: var(--bg-2);
}

.module-name.selected {
  background: var(--accent);
  color: #fff;
}

.source-badge {
  font-size: 0.66em;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--border);
  color: var(--text-3);
  flex-shrink: 0;
}

.source-badge.yaml {
  color: var(--accent);
  border-color: var(--accent);
}

.ovr-badge {
  font-size: 0.66em;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--yellow, #fbbf24);
  color: #1a1a2e;
  flex-shrink: 0;
}

.arrow-btns {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.arrow-btn {
  background: transparent;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  padding: 3px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  transition: color 0.12s, background 0.12s;
}

.arrow-btn:hover:not(:disabled) {
  color: var(--text);
  background: var(--bg-hover);
}

.arrow-btn:disabled {
  opacity: 0.25;
  cursor: default;
}

.arrow-btn.del:hover:not(:disabled) {
  color: var(--red, #f87171);
}

/* 新增模块条 */
.add-module {
  padding: 8px 10px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
  display: flex;
  gap: 6px;
  align-items: center;
}

.add-btn {
  width: 100%;
  background: transparent;
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 6px;
  font-size: 0.76em;
  color: var(--text-3);
  cursor: pointer;
  font-family: inherit;
  transition: color 0.12s, border-color 0.12s;
}

.add-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.add-input {
  flex: 1;
  min-width: 0;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 0.78em;
  font-family: Consolas, 'Courier New', monospace;
  color: var(--text);
  outline: none;
}

.add-input:focus {
  border-color: var(--accent);
}

.mini-btn {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 3px 10px;
  font-size: 0.74em;
  cursor: pointer;
  color: var(--text-2);
  font-family: inherit;
  transition: all 0.12s;
}

.mini-btn:hover:not(:disabled) {
  color: var(--text);
  border-color: var(--border-light, var(--border));
}

.mini-btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.mini-btn.primary:disabled {
  opacity: 0.4;
  cursor: default;
}

.mini-btn.warn {
  color: var(--yellow, #fbbf24);
  border-color: var(--yellow, #fbbf24);
}

/* 右栏：预览/内容 */
.pt-right {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.pt-right-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.right-tab {
  background: transparent;
  border: none;
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 0.8em;
  cursor: pointer;
  color: var(--text-2);
  font-family: inherit;
  transition: background 0.12s, color 0.12s;
}

.right-tab.active {
  background: var(--bg-hover);
  color: var(--text);
  font-weight: 500;
}

.pt-right-meta {
  margin-left: auto;
  font-size: 0.72em;
  color: var(--text-3);
}

.pt-preview-pane {
  flex: 1;
  overflow: auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.pt-pre {
  margin: 0;
  padding: 14px 16px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.74em;
  line-height: 1.55;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
  flex: 1;
}

.pt-editarea {
  flex: 1;
  margin: 10px 12px;
  padding: 12px 14px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.74em;
  line-height: 1.55;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--accent);
  border-radius: 8px;
  resize: none;
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
}

.edit-hint {
  padding: 6px 14px 10px;
  font-size: 0.7em;
  color: var(--text-3);
  flex-shrink: 0;
}

.pt-preview-note {
  padding: 6px 14px;
  font-size: 0.7em;
  color: var(--text-3);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

/* ── Footer ── */
.pt-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
  gap: 12px;
}

.save-msg {
  font-size: 0.78em;
}

.save-msg.ok {
  color: var(--green, #34d399);
}

.save-msg.err {
  color: var(--red, #f87171);
}

.dirty-hint {
  font-size: 0.78em;
  color: var(--yellow, #fbbf24);
}

.footer-btns {
  display: flex;
  gap: 10px;
  margin-left: auto;
}

.btn-cancel,
.btn-save {
  padding: 8px 20px;
  border-radius: var(--radius-sm, 6px);
  font-size: 0.88em;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  border: 1px solid transparent;
  display: flex;
  align-items: center;
  gap: 6px;
}

.btn-cancel {
  background: transparent;
  border-color: var(--border);
  color: var(--text-2);
}

.btn-cancel:hover {
  border-color: var(--border-light, var(--border));
  color: var(--text);
}

.btn-save {
  background: var(--accent);
  color: #fff;
}

.btn-save:hover:not(:disabled) {
  background: #5a7fff;
}

.btn-save:disabled {
  opacity: 0.5;
  cursor: default;
}

.spin {
  display: inline-block;
  animation: pt-spin 0.8s linear infinite;
}

@keyframes pt-spin {
  to { transform: rotate(360deg) }
}
</style>
