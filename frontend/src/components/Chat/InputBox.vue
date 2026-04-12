<template>
  <div class="input-wrap">
    <div class="input-area" :class="{ focused }">
      <textarea ref="textareaRef" v-model="input" placeholder="输入问题，Enter 发送，Shift+Enter 换行…"
        @keydown.enter.exact.prevent="submit" @input="autoResize" @focus="focused = true" @blur="focused = false" />
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
import { ref, nextTick } from 'vue'

const props = defineProps({ loading: Boolean, mode: String })
const emit = defineEmits(['send', 'toggle-mode'])

const input = ref('')
const focused = ref(false)
const textareaRef = ref(null)

function submit() {
  if (!input.value.trim() || props.loading) return
  emit('send', input.value.trim())
  input.value = ''
  nextTick(() => { if (textareaRef.value) textareaRef.value.style.height = 'auto' })
}

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}
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
</style>