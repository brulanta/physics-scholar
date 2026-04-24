<template>
  <div class="input-wrap">
    <!-- 编辑模式提示条 -->
    <div v-if="editMode.active" class="edit-banner">
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style="flex-shrink:0">
        <path d="M7.5 1.5l3 3L3.5 11H1V8.5L7.5 1.5z" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
      </svg>
      正在编辑消息，发送后将创建新分支
      <button class="edit-cancel" @click="$emit('cancel-edit')">取消</button>
    </div>

    <div class="input-area" :class="{ focused, 'edit-active': editMode.active }">
      <textarea ref="textareaRef" v-model="input"
        :placeholder="editMode.active ? '修改消息内容…' : '输入问题，Enter 发送，Shift+Enter 换行…'"
        @keydown.enter.exact.prevent="submit" @input="onInput" @focus="focused = true" @blur="focused = false" />
      <div class="input-actions">
        <button class="mode-btn" :class="{ active: mode === 'discuss' }" @click="$emit('toggle-mode')">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M1 2A1 1 0 012 1h8a1 1 0 011 1v5a1 1 0 01-1 1H7.5L6 9.5 4.5 8H2a1 1 0 01-1-1V2z"
              stroke="currentColor" stroke-width="1.2" fill="none" />
          </svg>
          {{ mode === 'discuss' ? '讨论中' : '讨论' }}
        </button>
        <button class="send-btn" :disabled="loading || !input.trim()" @click="submit">
          <svg v-if="!loading" width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M12 7L2 2l2.5 5L2 12 12 7z" fill="currentColor" />
          </svg>
          <span v-else class="spin">⟳</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted } from 'vue'

const props = defineProps({
  loading: Boolean,
  mode: String,
  draft: { type: String, default: '' },
  editMode: {
    type: Object,
    default: () => ({ active: false, content: '', parentId: null })
  },
})

const emit = defineEmits(['send', 'send-edit', 'cancel-edit', 'toggle-mode', 'draft-change'])

const input = ref(props.draft || '')
const focused = ref(false)
const textareaRef = ref(null)

// 切换 session 时恢复草稿
watch(() => props.draft, (v) => {
  // 只在非编辑模式下恢复草稿，避免覆盖编辑内容
  if (!props.editMode.active) {
    input.value = v || ''
    nextTick(autoResize)
  }
})

// 进入编辑模式时填入原消息内容并聚焦
watch(() => props.editMode.active, async (active) => {
  if (active) {
    input.value = props.editMode.content || ''
    await nextTick()
    autoResize()
    textareaRef.value?.focus()
    // 光标移到末尾
    const el = textareaRef.value
    if (el) {
      el.selectionStart = el.selectionEnd = el.value.length
    }
  } else {
    // 退出编辑模式，恢复草稿
    input.value = props.draft || ''
    await nextTick()
    autoResize()
  }
})

function submit() {
  if (!input.value.trim() || props.loading) return
  const text = input.value.trim()
  if (props.editMode.active) {
    emit('send-edit', text)
  } else {
    emit('send', text)
  }
  input.value = ''
  nextTick(() => {
    if (textareaRef.value) textareaRef.value.style.height = 'auto'
  })
}

function onInput() {
  autoResize()
  // 非编辑模式下同步草稿
  if (!props.editMode.active) {
    emit('draft-change', input.value)
  }
}

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}

onMounted(() => {
  textareaRef.value?.focus()
})

defineExpose({
  focusInput() {
    textareaRef.value?.focus()
  }
})
</script>

<style scoped>
/* 关键：外层wrap不设background，彻底去掉白条 */
.input-wrap {
  padding: 8px 20px 16px;
  /* 不设background，继承main的--bg */
}

.input-area {
  max-width: 760px;
  margin: 0 auto;
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  /* 弹簧动画：快上慢停 */
  transition: border-color 0.18s,
    transform 0.35s cubic-bezier(0.34, 1.56, 0.64, 1),
    box-shadow 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.input-area.focused {
  border-color: var(--accent-dim);
  transform: translateY(-3px);
  box-shadow: 0 6px 22px rgba(108, 140, 255, 0.14);
}

textarea {
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: 0.93em;
  font-family: inherit;
  line-height: 1.65;
  padding: 12px 14px 6px;
  resize: none;
  min-height: 44px;
  max-height: 180px;
  overflow-y: auto;
}

textarea::placeholder {
  color: var(--text-3);
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px 8px;
}

.mode-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 0.78em;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  transition: all 0.15s;
}

.mode-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.mode-btn.active {
  background: var(--accent-glow);
  border-color: var(--accent);
  color: var(--accent);
}

.send-btn {
  width: 34px;
  height: 34px;
  border-radius: var(--radius-sm);
  background: var(--accent);
  border: none;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, opacity 0.15s;
}

.send-btn:hover:not(:disabled) {
  background: #5a7fff;
}

.send-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.spin {
  animation: spin 1s linear infinite;
  display: inline-block;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.edit-banner {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 14px;
  font-size: 0.78em;
  color: var(--text-2);
  background: var(--bg-3);
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  border: 1px solid var(--border);
  border-bottom: none;
}

.edit-cancel {
  margin-left: auto;
  background: transparent;
  border: none;
  color: var(--text-3);
  font-size: 0.9em;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: color 0.12s, background 0.12s;
}

.edit-cancel:hover {
  color: var(--text);
  background: var(--bg-hover);
}

.input-area.edit-active {
  border-color: var(--accent);
  border-top-left-radius: 0;
  border-top-right-radius: 0;
}
</style>