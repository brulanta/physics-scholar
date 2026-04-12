<template>
  <div class="welcome">
    <!-- 固定不动的上半部分 -->
    <div class="welcome-top">
      <div class="welcome-icon">⚛</div>
      <h1 class="welcome-title">PhysicsScholar</h1>
      <p class="welcome-sub">微波光子学学术助手</p>

      <div class="suggestions">
        <button v-for="s in suggestions" :key="s.text" class="sug-btn" @click="send(s.text, s.discuss)">
          <span class="sug-text">{{ s.text }}</span>
          <span v-if="s.discuss" class="sug-tag">讨论</span>
        </button>
      </div>
    </div>

    <!-- 输入框：固定在下方，向上生长 -->
    <div class="welcome-bottom">
      <div class="input-wrap" :class="{ focused }">
        <textarea ref="textareaRef" v-model="input" placeholder="输入问题开始对话…" @keydown.enter.exact.prevent="submit"
          @input="autoResize" @focus="focused = true" @blur="focused = false" />
        <button class="send-btn" :disabled="!input.trim()" @click="submit">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M12 7L2 2l2.5 5L2 12 12 7z" fill="currentColor" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'

const emit = defineEmits(['send'])
const input = ref('')
const focused = ref(false)
const textareaRef = ref(null)

const suggestions = [
  { text: '解释一下微波光子学的基本原理', discuss: false },
  { text: '帮我检索光子芯片相关论文', discuss: false },
  { text: '讨论：微波光子调制技术的最新进展', discuss: true },
]

function send(text, discuss = false) {
  emit('send', { text, discuss })
}

function submit() {
  if (!input.value.trim()) return
  emit('send', { text: input.value.trim(), discuss: false })
  input.value = ''
  nextTick(() => {
    if (textareaRef.value) textareaRef.value.style.height = 'auto'
  })
}

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}
</script>

<style scoped>
.welcome {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0 24px;
}

.welcome-top {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  /* 改这里：flex-end → center */
  padding-bottom: 20px;
  min-height: 0;
}

.welcome-icon {
  font-size: 2.6em;
  line-height: 1;
  margin-bottom: 10px;
}

.welcome-title {
  font-size: 1.75em;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.02em;
  margin-bottom: 4px;
}

.welcome-sub {
  font-size: 0.9em;
  color: var(--text-3);
  margin-bottom: 28px;
}

.suggestions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  max-width: 620px;
}

.sug-btn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  text-align: left;
  padding: 11px 16px;
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-2);
  font-size: 0.88em;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
  gap: 12px;
}

.sug-btn:hover {
  border-color: var(--accent);
  color: var(--text);
  background: var(--accent-glow);
}

.sug-text {
  flex: 1;
  text-align: left;
}

.sug-tag {
  font-size: 0.72em;
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--accent-glow);
  border: 1px solid var(--accent-dim);
  color: var(--accent);
  white-space: nowrap;
  flex-shrink: 0;
}

/* 下半部分：输入框，固定高度区域，输入框向上生长 */
.welcome-bottom {
  flex-shrink: 0;
  padding-bottom: 32px;
  padding-top: 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 620px;
  width: 100%;
  align-self: center;
}

.input-wrap {
  width: 100%;
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 12px 12px 16px;
  transition: border-color 0.18s,
    transform 0.35s cubic-bezier(0.34, 1.56, 0.64, 1),
    box-shadow 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.input-wrap.focused {
  border-color: var(--accent-dim);
  transform: translateY(-3px);
  box-shadow: 0 6px 24px rgba(108, 140, 255, 0.15);
}

textarea {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: 0.95em;
  font-family: inherit;
  line-height: 1.65;
  resize: none;
  min-height: 52px;
  /* 比之前高 */
  max-height: 200px;
  overflow-y: auto;
}

textarea::placeholder {
  color: var(--text-3);
}

/* send按钮固定在底部，align-self: flex-end 保证不随textarea变高上移 */
.send-btn {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  align-self: flex-end;
  background: var(--accent);
  border: none;
  border-radius: var(--radius-sm);
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
</style>