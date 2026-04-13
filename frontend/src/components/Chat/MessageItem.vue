<template>
  <div class="message-row" :class="role" @mouseenter="hovered = true" @mouseleave="hovered = false">
    <div class="avatar">
      <span v-if="role === 'user'">你</span>
      <svg v-else width="14" height="14" viewBox="0 0 14 14" fill="none">
        <circle cx="7" cy="7" r="6" stroke="var(--accent)" stroke-width="1.2" />
        <circle cx="7" cy="7" r="2.5" fill="var(--accent)" />
      </svg>
    </div>

    <div class="bubble-wrap">
      <div class="bubble">
        <div v-if="!content" class="typing-indicator">
          <span /><span /><span />
        </div>
        <div v-else class="md-body" v-html="rendered" />
      </div>

      <!-- ④ 操作栏：always in DOM，透明度控制显隐，不撑开布局 -->
      <div v-if="content" class="action-bar" :class="[role, { visible: hovered }]">
        <template v-if="role === 'assistant'">
          <button class="act" @click="copyContent">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <rect x="3.5" y="3.5" width="7" height="7" rx="1.2" stroke="currentColor" stroke-width="1.1" />
              <path d="M1.5 8V1.5H8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
            </svg>
            {{ copied ? '已复制' : '复制' }}
          </button>
          <button class="act" @click="toast('点赞功能暂未实装')">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1 5.5h1.5v5H1v-5zM3.5 5.5l2-4a.9.9 0 01.9.9v1.6H9l.5.9L8.5 9H3.5V5.5z" stroke="currentColor"
                stroke-width="1" stroke-linejoin="round" />
            </svg>
          </button>
          <button class="act" @click="toast('点踩功能暂未实装')">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M11 6.5H9.5v-5H11v5zM8.5 6.5l-2 4a.9.9 0 01-.9-.9V8H3l-.5-.9L3.5 3H8.5v3.5z"
                stroke="currentColor" stroke-width="1" stroke-linejoin="round" />
            </svg>
          </button>
          <button class="act" @click="toast('重新生成功能暂未实装')">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1.5 6A4.5 4.5 0 016 1.5a4.5 4.5 0 013.5 1.7" stroke="currentColor" stroke-width="1.1"
                stroke-linecap="round" />
              <path d="M9.5 3.2V1.5H11" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
              <path d="M10.5 6A4.5 4.5 0 016 10.5a4.5 4.5 0 01-3.5-1.7" stroke="currentColor" stroke-width="1.1"
                stroke-linecap="round" />
            </svg>
          </button>
        </template>

        <template v-else>
          <button class="act" @click="copyContent">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <rect x="3.5" y="3.5" width="7" height="7" rx="1.2" stroke="currentColor" stroke-width="1.1" />
              <path d="M1.5 8V1.5H8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
            </svg>
            复制
          </button>
          <button class="act" @click="toast('编辑功能暂未实装')">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M7.5 1.5l3 3L3.5 11H1V8.5L7.5 1.5z" stroke="currentColor" stroke-width="1.1"
                stroke-linejoin="round" />
            </svg>
            编辑
          </button>
          <button class="act" @click="toast('重新生成功能暂未实装')">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1.5 6A4.5 4.5 0 016 1.5a4.5 4.5 0 013.5 1.7" stroke="currentColor" stroke-width="1.1"
                stroke-linecap="round" />
              <path d="M9.5 3.2V1.5H11" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
            </svg>
            重发
          </button>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, inject } from 'vue'
import { renderMarkdown } from '../../utils/markdown.js'

const props = defineProps({ role: String, content: String })
const showToast = inject('showToast')

const hovered = ref(false)
const copied = ref(false)
const rendered = computed(() => renderMarkdown(props.content))

function toast(msg) { showToast?.(msg) }

function copyContent() {
  navigator.clipboard.writeText(props.content || '').then(() => {
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  })
}
</script>

<style scoped>
.message-row {
  display: flex;
  gap: 12px;
  padding: 5px 0;
  align-items: flex-start;
}

.message-row.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 0.72em;
  font-weight: 600;
  margin-top: 3px;
}

.user .avatar {
  background: var(--accent-dim);
  color: #fff;
}

.assistant .avatar {
  background: var(--bg-3);
  border: 1px solid var(--border);
}

/* MessageItem.vue */

.bubble-wrap {
  /* 不要flex:1，用min-width:0防止溢出 */
  min-width: 0;
  max-width: calc(100% - 80px);
  /* 40px = avatar宽度28px + gap12px */
  display: flex;
  flex-direction: column;
}

.message-row.user .bubble-wrap {
  align-items: flex-end;
}

.bubble {
  /* 关键：inline-block让气泡包裹内容，但不超过父容器 */
  display: inline-block;
  max-width: 100%;
  padding: 11px 16px;
  border-radius: var(--radius);
  font-size: 0.92em;
  line-height: 1.75;
  word-break: break-word;
}

.user .bubble {
  background: var(--accent-dim);
  color: #fff;
  border-bottom-right-radius: 3px;
}

.assistant .bubble {
  background: var(--bg-2);
  border: 1px solid var(--border);
  color: var(--text);
  border-bottom-left-radius: 3px;
}

/* ④ 操作栏：固定高度24px，透明度切换，不影响布局 */
.action-bar {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 24px;
  margin-top: 3px;
  padding: 0 2px;
  opacity: 0;
  transition: opacity 0.15s ease;
  pointer-events: none;
}

.action-bar.visible {
  opacity: 1;
  pointer-events: auto;
}

.action-bar.user {
  flex-direction: row-reverse;
}

.act {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border-radius: 5px;
  font-size: 0.74em;
  background: transparent;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  transition: color 0.12s, background 0.12s;
  white-space: nowrap;
  height: 22px;
}

.act:hover {
  color: var(--text);
  background: var(--bg-3);
}

/* 打字指示器 */
.typing-indicator {
  display: flex;
  gap: 4px;
  align-items: center;
  height: 18px;
}

.typing-indicator span {
  width: 5px;
  height: 5px;
  background: var(--text-3);
  border-radius: 50%;
  animation: bounce 1.2s ease-in-out infinite;
}

.typing-indicator span:nth-child(2) {
  animation-delay: .2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: .4s;
}

@keyframes bounce {

  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: .4;
  }

  40% {
    transform: translateY(-4px);
    opacity: 1;
  }
}
</style>