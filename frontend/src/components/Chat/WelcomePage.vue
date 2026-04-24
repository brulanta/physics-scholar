<template>
  <div class="welcome" ref="rootRef">
    <!-- 上半区：icon + 提问，绝对定位，垂直居中偏上 -->
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

    <!-- 输入框区：绝对定位在底部，向上生长 -->
    <div class="welcome-input-outer">
      <div class="input-wrap" :class="{ focused }">
        <textarea ref="textareaRef" v-model="input" placeholder="输入问题开始对话…" @keydown.enter.exact.prevent="submit"
          @input="autoResize" @focus="focused = true" @blur="focused = false" />
        <div class="input-bottom-row">
          <!-- ④ 讨论模式按钮 -->
          <button class="discuss-btn" :class="{ active: discussMode }" @click="discussMode = !discussMode">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1 2A1 1 0 012 1h8a1 1 0 011 1v5a1 1 0 01-1 1H7.5L6 9.5 4.5 8H2a1 1 0 01-1-1V2z"
                stroke="currentColor" stroke-width="1.2" fill="none" />
            </svg>
            {{ discussMode ? '讨论中' : '讨论' }}
          </button>
          <button class="send-btn" :disabled="!input.trim()" @click="submit">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M12 7L2 2l2.5 5L2 12 12 7z" fill="currentColor" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'

const emit = defineEmits(['send'])
const input = ref('')
const focused = ref(false)
const discussMode = ref(false)
const textareaRef = ref(null)

const suggestions = [
  { text: '解释一下微波光子学的基本原理', discuss: false },
  { text: '帮我检索光子芯片相关论文', discuss: false },
  { text: '讨论：微波光子调制技术的最新进展', discuss: true },
]

function send(text, discuss = false) {
  emit('send', { text, discuss })
}

const submitting = ref(false)

function submit() {
  if (!input.value.trim() || submitting.value) return
  submitting.value = true
  emit('send', { text: input.value.trim(), discuss: discussMode.value })
  input.value = ''
  discussMode.value = false
  nextTick(() => {
    if (textareaRef.value) textareaRef.value.style.height = 'auto'
    submitting.value = false
  })
}

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  // 向下生长：max-height限制，超出滚动
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

onMounted(() => {
  textareaRef.value?.focus()
})
</script>

<style scoped>
.welcome {
  flex: 1;
  position: relative;
  /* 绝对定位的基准 */
  overflow: hidden;
  min-height: 0;
}

/* 上半区：绝对居中，底边对齐视觉中线略偏上（55%处） */
.welcome-top {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  /* 底边在容器55%位置，让提问行底部正好在视觉中线 */
  bottom: 45%;
  width: 100%;
  max-width: 620px;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0 24px 24px;
}

.welcome-icon {
  font-size: 2.4em;
  line-height: 1;
  margin-bottom: 8px;
}

.welcome-title {
  font-size: 1.7em;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.02em;
  margin-bottom: 4px;
}

.welcome-sub {
  font-size: 0.88em;
  color: var(--text-3);
  margin-bottom: 24px;
}

.suggestions {
  display: flex;
  flex-direction: column;
  gap: 7px;
  width: 100%;
}

.sug-btn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  text-align: left;
  padding: 10px 14px;
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-2);
  font-size: 0.86em;
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

/* 输入框区：绝对定位在底部，内容向上生长 */
.welcome-input-outer {
  position: absolute;
  bottom: 32px;
  left: 50%;
  transform: translateX(-50%);
  width: 100%;
  max-width: 620px;
  padding: 0 24px;
  /* 关键：display flex column-reverse，内容从底部向上撑 */
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

.input-wrap {
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
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
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: 0.93em;
  font-family: inherit;
  line-height: 1.65;
  resize: none;
  min-height: 56px;
  max-height: 200px;
  overflow-y: auto;
  padding: 14px 16px 8px;
}

textarea::placeholder {
  color: var(--text-3);
}

.input-bottom-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px 8px;
}

.discuss-btn {
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

.discuss-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.discuss-btn.active {
  background: var(--accent-glow);
  border-color: var(--accent);
  color: var(--accent);
}

.send-btn {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
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