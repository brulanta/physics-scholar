<!-- components/Settings/ConfigModal.vue -->
<template>
  <Teleport to="body">
    <div class="cfg-overlay" @mousedown.self="close">
      <div class="cfg-modal" role="dialog" aria-modal="true">

        <!-- 顶部栏 -->
        <div class="cfg-header">
          <div class="cfg-header-left">
            <svg class="cfg-header-icon" width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="2.2" stroke="currentColor" stroke-width="1.5" />
              <path
                d="M8 1.5v1.2M8 13.3v1.2M1.5 8h1.2M13.3 8h1.2M3.4 3.4l.85.85M11.75 11.75l.85.85M3.4 12.6l.85-.85M11.75 4.25l.85-.85"
                stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
            </svg>
            <span class="cfg-title">系统配置</span>
          </div>
          <button class="icon-btn" @click="close">
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
            </svg>
          </button>
        </div>

        <!-- 正文可滚动 -->
        <div class="cfg-body">

          <!-- ── 主 LLM ── -->
          <section class="cfg-section">
            <div class="cfg-section-title">主 LLM</div>
            <div class="cfg-section-desc">必填，所有核心推理均使用此模型</div>

            <div class="field-row">
              <label>API Key <span class="required">*</span></label>
              <div class="input-with-eye">
                <input v-model="form.llm.api_key" :type="show.llm_key ? 'text' : 'password'" class="cfg-input"
                  placeholder="sk-..." autocomplete="off" />
                <button class="eye-btn" @click="show.llm_key = !show.llm_key" tabindex="-1">
                  <svg v-if="!show.llm_key" width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z" stroke="currentColor" stroke-width="1.4" />
                    <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.4" />
                  </svg>
                  <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path
                      d="M2 2l12 12M6.5 6.6A2 2 0 0 0 9.4 9.5M4.2 4.3C2.6 5.3 1.5 7 1.5 7S4 12 8 12c1.2 0 2.3-.3 3.2-.9M7 3.1C7.3 3 7.7 3 8 3c4 0 6.5 5 6.5 5s-.6 1.1-1.6 2.2"
                      stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="field-row">
              <label>Base URL <span class="required">*</span></label>
              <input v-model="form.llm.base_url" class="cfg-input" placeholder="https://api.openai.com/v1" />
            </div>

            <div class="field-row">
              <label>Model <span class="required">*</span></label>
              <div class="model-row">
                <select v-model="form.llm.model" class="cfg-select" :disabled="!llmModels.length">
                  <option v-if="!llmModels.length" value="">— 先获取模型列表 —</option>
                  <option v-for="m in llmModels" :key="m" :value="m">{{ m }}</option>
                </select>
                <button class="fetch-btn" :class="{ loading: fetching.llm }"
                  :disabled="fetching.llm || !form.llm.base_url || !form.llm.api_key" @click="fetchModels('llm')">
                  <span v-if="fetching.llm" class="spin">⟳</span>
                  <span v-else>获取模型</span>
                </button>
              </div>
              <div v-if="fetchError.llm" class="field-error">{{ fetchError.llm }}</div>
            </div>
          </section>

          <!-- ── 副 LLM ── -->
          <section class="cfg-section">
            <div class="cfg-section-title">副 LLM <span class="optional-tag">可选</span></div>
            <div class="cfg-section-desc">留空则全部复用主 LLM 配置</div>

            <div class="field-row">
              <label>API Key</label>
              <div class="input-with-eye">
                <input v-model="form.sub_llm.api_key" :type="show.sub_key ? 'text' : 'password'" class="cfg-input"
                  placeholder="留空则复用主 LLM" autocomplete="off" />
                <button class="eye-btn" @click="show.sub_key = !show.sub_key" tabindex="-1">
                  <svg v-if="!show.sub_key" width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z" stroke="currentColor" stroke-width="1.4" />
                    <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.4" />
                  </svg>
                  <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path
                      d="M2 2l12 12M6.5 6.6A2 2 0 0 0 9.4 9.5M4.2 4.3C2.6 5.3 1.5 7 1.5 7S4 12 12 12c1.2 0 2.3-.3 3.2-.9M7 3.1C7.3 3 7.7 3 8 3c4 0 6.5 5 6.5 5s-.6 1.1-1.6 2.2"
                      stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="field-row">
              <label>Base URL</label>
              <input v-model="form.sub_llm.base_url" class="cfg-input" placeholder="留空则复用主 LLM" />
            </div>

            <div class="field-row">
              <label>Model</label>
              <div class="model-row">
                <select v-model="form.sub_llm.model" class="cfg-select" :disabled="!subModels.length">
                  <option value="">— 先获取模型列表 —</option>
                  <option v-for="m in subModels" :key="m" :value="m">{{ m }}</option>
                </select>
                <button class="fetch-btn" :class="{ loading: fetching.sub }"
                  :disabled="fetching.sub || !form.sub_llm.base_url || !form.sub_llm.api_key"
                  @click="fetchModels('sub')">
                  <span v-if="fetching.sub" class="spin">⟳</span>
                  <span v-else>获取模型</span>
                </button>
              </div>
              <div v-if="fetchError.sub" class="field-error">{{ fetchError.sub }}</div>
            </div>
          </section>

          <!-- ── Embedding ── -->
          <section class="cfg-section">
            <div class="cfg-section-title">Embedding</div>
            <div class="cfg-section-desc">向量化模型，用于论文语义检索</div>

            <div class="field-row">
              <label>硅基流动 API Key <span class="required">*</span></label>
              <div class="input-with-eye">
                <input v-model="form.embedding.api_key" :type="show.embedding ? 'text' : 'password'" class="cfg-input"
                  placeholder="sk-..." autocomplete="off" />
                <button class="eye-btn" @click="show.embedding = !show.embedding" tabindex="-1">
                  <svg v-if="!show.embedding" width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z" stroke="currentColor" stroke-width="1.4" />
                    <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.4" />
                  </svg>
                  <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path
                      d="M2 2l12 12M6.5 6.6A2 2 0 0 0 9.4 9.5M4.2 4.3C2.6 5.3 1.5 7 1.5 7S4 12 12 12c1.2 0 2.3-.3 3.2-.9M7 3.1C7.3 3 7.7 3 8 3c4 0 6.5 5 6.5 5s-.6 1.1-1.6 2.2"
                      stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
                  </svg>
                </button>
              </div>
            </div>
          </section>

          <!-- ── 第三方工具 ── -->
          <section class="cfg-section">
            <div class="cfg-section-title">第三方工具 <span class="optional-tag">可选</span></div>
            <div class="cfg-section-desc">用于网络检索增强，均可留空</div>

            <div class="field-row">
              <label>Jina API Key</label>
              <div class="input-with-eye">
                <input v-model="form.tools.jina_api_key" :type="show.jina ? 'text' : 'password'" class="cfg-input"
                  placeholder="jina_..." autocomplete="off" />
                <button class="eye-btn" @click="show.jina = !show.jina" tabindex="-1">
                  <svg v-if="!show.jina" width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z" stroke="currentColor" stroke-width="1.4" />
                    <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.4" />
                  </svg>
                  <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path
                      d="M2 2l12 12M6.5 6.6A2 2 0 0 0 9.4 9.5M4.2 4.3C2.6 5.3 1.5 7 1.5 7S4 12 12 12c1.2 0 2.3-.3 3.2-.9M7 3.1C7.3 3 7.7 3 8 3c4 0 6.5 5 6.5 5s-.6 1.1-1.6 2.2"
                      stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="field-row">
              <label>Semantic Scholar API Key</label>
              <div class="input-with-eye">
                <input v-model="form.tools.s2_api_key" :type="show.s2 ? 'text' : 'password'" class="cfg-input"
                  placeholder="可选" autocomplete="off" />
                <button class="eye-btn" @click="show.s2 = !show.s2" tabindex="-1">
                  <svg v-if="!show.s2" width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z" stroke="currentColor" stroke-width="1.4" />
                    <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.4" />
                  </svg>
                  <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path
                      d="M2 2l12 12M6.5 6.6A2 2 0 0 0 9.4 9.5M4.2 4.3C2.6 5.3 1.5 7 1.5 7S4 12 12 12c1.2 0 2.3-.3 3.2-.9M7 3.1C7.3 3 7.7 3 8 3c4 0 6.5 5 6.5 5s-.6 1.1-1.6 2.2"
                      stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="field-row">
              <label>OpenAlex Email</label>
              <input v-model="form.tools.openalex_email" type="email" class="cfg-input"
                placeholder="your@email.com（用于礼貌池限速）" />
            </div>
          </section>

        </div><!-- end cfg-body -->

        <!-- 底部操作栏 -->
        <div class="cfg-footer">
          <span v-if="loadError" class="load-error">⚠ 配置加载失败，请刷新后重试</span>
          <div class="footer-btns">
            <button class="btn-cancel" @click="close">取消</button>
            <button class="btn-save" :disabled="saving || !canSave" @click="doSave">
              <span v-if="saving" class="spin">⟳</span>
              <span v-else>保存配置</span>
            </button>
          </div>
        </div>

      </div>
    </div>

    <!-- 保存成功弹窗 -->
    <div v-if="savedDialog" class="mini-overlay">
      <div class="mini-modal">
        <div class="mini-icon">
          <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
            <circle cx="11" cy="11" r="10" stroke="var(--green)" stroke-width="1.5" />
            <path d="M6.5 11.5l3 3 6-6" stroke="var(--green)" stroke-width="1.8" stroke-linecap="round"
              stroke-linejoin="round" />
          </svg>
        </div>
        <div class="mini-title">配置已保存</div>
        <div class="mini-desc">部分设置需要重启服务后生效</div>
        <div class="mini-actions">
          <button class="btn-cancel" @click="savedDialog = false">稍后重启</button>
          <button class="btn-restart" :disabled="restarting" @click="doRestart">
            <span v-if="restarting" class="spin">⟳</span>
            <span v-else>立即重启</span>
          </button>
        </div>
        <div v-if="restarting" class="restart-hint">正在重启，请稍候…</div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'

const emit = defineEmits(['close'])

// ── 表单状态 ──
const form = reactive({
  llm: { api_key: '', base_url: '', model: '' },
  sub_llm: { api_key: '', base_url: '', model: '' },
  embedding: { api_key: '' },   // ✨ 新增
  tools: { jina_api_key: '', s2_api_key: '', openalex_email: '' }
})

// 密码显示切换
const show = reactive({ llm_key: false, sub_key: false, jina: false, s2: false, embedding: false })  // ✨ 加 embedding

// 模型列表
const llmModels = ref([])
const subModels = ref([])

// 加载/操作状态
const fetching = reactive({ llm: false, sub: false })
const fetchError = reactive({ llm: '', sub: '' })
const saving = ref(false)
const loadError = ref(false)
const savedDialog = ref(false)
const restarting = ref(false)

// 是否可保存：主LLM三项必填
const canSave = computed(() =>
  form.llm.api_key.trim() && form.llm.base_url.trim() && form.llm.model.trim()
)

// ── 初始化：拉取当前配置 ──
onMounted(async () => {
  try {
    const res = await fetch('/api/config')
    if (!res.ok) throw new Error()
    const data = await res.json()
    // 填充表单，后端字段可能缺省，用 || '' 兜底
    if (data.main_llm) {          // ✨ data.llm → data.main_llm
      form.llm.api_key = data.main_llm.api_key || ''
      form.llm.base_url = data.main_llm.base_url || ''
      form.llm.model = data.main_llm.model || ''
      if (form.llm.model) llmModels.value = [form.llm.model]
    }
    if (data.sub_llm) {
      form.sub_llm.api_key = data.sub_llm.api_key || ''
      form.sub_llm.base_url = data.sub_llm.base_url || ''
      form.sub_llm.model = data.sub_llm.model || ''
      if (form.sub_llm.model) subModels.value = [form.sub_llm.model]
    }
    if (data.embedding) {
      form.embedding.api_key = data.embedding.api_key || ''
    }
    if (data.tools) {
      form.tools.jina_api_key = data.tools.jina_api_key || ''
      form.tools.s2_api_key = data.tools.s2_api_key || ''
      form.tools.openalex_email = data.tools.openalex_email || ''
    }
  } catch (e) {
    loadError.value = true
  }
})

// ── 获取模型列表 ──
async function fetchModels(which) {
  const src = which === 'llm' ? form.llm : form.sub_llm
  fetching[which === 'llm' ? 'llm' : 'sub'] = true
  fetchError[which === 'llm' ? 'llm' : 'sub'] = ''
  const key = which === 'llm' ? 'llm' : 'sub'
  try {
    const res = await fetch('/api/config/fetch-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: src.base_url, api_key: src.api_key })
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.message || `HTTP ${res.status}`)
    }
    const data = await res.json()
    const models = data.models || data  // 兼容不同返回结构
    if (which === 'llm') {
      llmModels.value = models
      if (models.length && !models.includes(form.llm.model)) form.llm.model = models[0]
    } else {
      subModels.value = models
      if (models.length && !models.includes(form.sub_llm.model)) form.sub_llm.model = models[0]
    }
  } catch (e) {
    if (which === 'llm') fetchError.llm = e.message || '获取失败，请检查 URL 和 Key'
    else fetchError.sub = e.message || '获取失败，请检查 URL 和 Key'
  } finally {
    fetching[key] = false
  }
}

// ── 保存 ──
async function doSave() {
  if (!canSave.value || saving.value) return
  saving.value = true
  try {
    const payload = {
      main_llm: { ...form.llm },
      embedding: { ...form.embedding },
      tools: { ...form.tools }
    }
    // 副LLM全部为空时不传，否则传
    const subEmpty = !form.sub_llm.api_key && !form.sub_llm.base_url && !form.sub_llm.model
    if (!subEmpty) payload.sub_llm = { ...form.sub_llm }

    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    savedDialog.value = true
  } catch (e) {
    alert('保存失败：' + (e.message || '未知错误'))
  } finally {
    saving.value = false
  }
}

// ── 重启流程 ──
async function doRestart() {
  restarting.value = true
  try {
    await fetch('/api/config/restart', { method: 'POST' })
  } catch (_) { /* 重启中断连接是正常的 */ }

  // 轮询 health
  const poll = setInterval(async () => {
    try {
      const r = await fetch('/api/health')
      if (r.ok) {
        clearInterval(poll)
        location.reload()
      }
    } catch (_) { /* 还没起来，继续等 */ }
  }, 1000)
}

function close() {
  if (!restarting.value) emit('close')
}
</script>

<style scoped>
/* ── 遮罩 ── */
.cfg-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 700;
  animation: overlay-in 0.18s ease;
}

@keyframes overlay-in {
  from {
    opacity: 0
  }

  to {
    opacity: 1
  }
}

/* ── 主弹窗 ── */
.cfg-modal {
  background: var(--bg-2);
  border: 1px solid var(--border-light, var(--border));
  border-radius: var(--radius, 10px);
  width: 540px;
  max-width: 96vw;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.5);
  animation: modal-in 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes modal-in {
  from {
    opacity: 0;
    transform: scale(0.96) translateY(8px)
  }

  to {
    opacity: 1;
    transform: scale(1) translateY(0)
  }
}

/* ── Header ── */
.cfg-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.cfg-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cfg-header-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.cfg-title {
  font-weight: 600;
  font-size: 0.95em;
  color: var(--text);
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

/* ── Body（可滚动） ── */
.cfg-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

/* ── Section ── */
.cfg-section {
  padding: 18px 24px;
  border-bottom: 1px solid var(--border);
}

.cfg-section:last-child {
  border-bottom: none;
}

.cfg-section-title {
  font-size: 0.72em;
  font-weight: 700;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.cfg-section-desc {
  font-size: 0.78em;
  color: var(--text-3);
  margin-bottom: 14px;
  opacity: 0.75;
}

.optional-tag {
  font-size: 0.85em;
  font-weight: 500;
  padding: 1px 7px;
  border-radius: 8px;
  background: rgba(108, 140, 255, 0.1);
  color: var(--accent);
  text-transform: none;
  letter-spacing: 0;
}

/* ── 字段行 ── */
.field-row {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 12px;
}

.field-row:last-child {
  margin-bottom: 0;
}

.field-row>label {
  font-size: 0.75em;
  font-weight: 600;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.required {
  color: var(--red, #f87171);
  margin-left: 2px;
}

/* ── 输入框 ── */
.cfg-input,
.cfg-select {
  background: var(--bg-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm, 6px);
  color: var(--text);
  font-size: 0.88em;
  padding: 8px 12px;
  font-family: inherit;
  width: 100%;
  transition: border-color 0.15s;
}

.cfg-input:focus,
.cfg-select:focus {
  outline: none;
  border-color: var(--accent-dim, var(--accent));
}

.cfg-input::placeholder {
  color: var(--text-3);
  opacity: 0.6;
}

.cfg-select {
  cursor: pointer;
  flex: 1;
}

.cfg-select:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── 密码框 + 眼睛按钮 ── */
.input-with-eye {
  position: relative;
}

.input-with-eye .cfg-input {
  padding-right: 36px;
}

.eye-btn {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  background: transparent;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  padding: 2px;
  display: flex;
  align-items: center;
  transition: color 0.15s;
}

.eye-btn:hover {
  color: var(--text);
}

/* ── 模型行 ── */
.model-row {
  display: flex;
  gap: 8px;
}

.fetch-btn {
  flex-shrink: 0;
  padding: 7px 14px;
  border-radius: var(--radius-sm, 6px);
  font-size: 0.82em;
  font-weight: 500;
  border: 1px solid var(--border);
  background: var(--bg-3);
  color: var(--text-2);
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}

.fetch-btn:hover:not(:disabled) {
  border-color: var(--accent-dim, var(--accent));
  color: var(--accent);
}

.fetch-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.fetch-btn.loading {
  opacity: 0.6;
  cursor: wait;
}

.field-error {
  font-size: 0.76em;
  color: var(--red, #f87171);
  margin-top: 2px;
}

/* ── Footer ── */
.cfg-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
  gap: 12px;
}

.load-error {
  font-size: 0.78em;
  color: var(--yellow, #fbbf24);
}

.footer-btns {
  display: flex;
  gap: 10px;
  margin-left: auto;
}

/* ── 按钮 ── */
.btn-cancel,
.btn-save,
.btn-restart {
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
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-restart {
  background: var(--accent);
  color: #fff;
}

.btn-restart:hover:not(:disabled) {
  background: #5a7fff;
}

.btn-restart:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── 保存成功小弹窗 ── */
.mini-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 800;
  backdrop-filter: blur(4px);
}

.mini-modal {
  background: var(--bg-2);
  border: 1px solid var(--border-light, var(--border));
  border-radius: var(--radius, 10px);
  width: 340px;
  max-width: 92vw;
  padding: 28px 28px 22px;
  text-align: center;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
  animation: modal-in 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.mini-icon {
  display: flex;
  justify-content: center;
  margin-bottom: 12px;
}

.mini-title {
  font-size: 1em;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 6px;
}

.mini-desc {
  font-size: 0.82em;
  color: var(--text-3);
  margin-bottom: 20px;
  line-height: 1.5;
}

.mini-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}

.restart-hint {
  margin-top: 14px;
  font-size: 0.78em;
  color: var(--text-3);
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {

  0%,
  100% {
    opacity: 0.5
  }

  50% {
    opacity: 1
  }
}

/* ── 旋转动画 ── */
.spin {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg)
  }

  to {
    transform: rotate(360deg)
  }
}
</style>