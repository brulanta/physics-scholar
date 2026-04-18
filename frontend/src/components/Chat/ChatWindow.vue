<template>
  <div class="chat-outer">
    <div class="chat-window" ref="windowRef" @scroll="onScroll">
      <div class="messages">
        <MessageItem v-for="(msg, idx) in messages" :key="idx" :role="msg.role" :content="msg.content" />
        <!-- streaming消息只在对应session显示 -->
        <MessageItem v-if="streamingSessionId !== null && streamingSessionId === currentSessionId" role="assistant"
          :content="streamingContent" />
      </div>
    </div>
    <Transition name="scroll-btn">
      <button v-if="showScrollBtn" class="scroll-bottom-btn" @click="scrollToBottom(true)">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M2 4l5 5 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
            stroke-linejoin="round" />
        </svg>
      </button>
    </Transition>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import MessageItem from './MessageItem.vue'

const props = defineProps({
  messages: Array,
  streamingSessionId: { default: null },
  streamingContent: { type: String, default: '' },
  currentSessionId: { type: String, default: '' }
})

const windowRef = ref(null)
const showScrollBtn = ref(false)
const isNearBottom = ref(true)
let lastMessageCount = 0
let lastUserMsgEl = null

function onScroll() {
  const el = windowRef.value
  if (!el) return
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight
  isNearBottom.value = dist < 80
  showScrollBtn.value = dist > 200
}

function scrollToBottom(smooth = false) {
  const el = windowRef.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'instant' })
}

function scrollToTop(smooth = false) {
  const el = windowRef.value
  if (!el) return
  el.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'instant' })
}

defineExpose({ scrollToBottom })

watch(() => props.messages?.length, async (newLen, oldLen) => {
  if (!newLen) return
  await nextTick()
  const el = windowRef.value
  if (!el) return

  const added = newLen - (oldLen ?? 0)
  if (added <= 0) return

  const lastMsg = props.messages[newLen - 1]

  if (lastMsg.role === 'user') {
    // 用户消息：滚动让该消息置顶
    // 找到最后一个message-row元素
    const rows = el.querySelectorAll('.message-row')
    const lastRow = rows[rows.length - 1]
    if (lastRow) {
      const offsetTop = lastRow.offsetTop
      el.scrollTo({
        top: offsetTop - 20,   // 距顶部留20px呼吸感
        behavior: 'smooth'
      })
    }
  } else if (lastMsg.role === 'assistant') {
    // assistant占位出现时，如果接近底部就追底
    if (isNearBottom.value) scrollToBottom()
  }
})

// streaming过程追底
watch(() => props.streamingContent, async () => {
  await nextTick()
  if (isNearBottom.value) scrollToBottom()
})
</script>

<style scoped>
/* chat-outer作为定位容器，包裹滚动区和↓按钮 */
.chat-outer {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  /* ↓按钮相对这里定位 */
}

.chat-window {
  flex: 1;
  overflow-y: auto;
  padding: 20px 0 8px;
}

.messages {
  max-width: 820px;
  margin: 0 auto;
  padding: 0 28px;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ④ ↓按钮：贴在chat-outer底部，即输入框上方 */
.scroll-bottom-btn {
  position: absolute;
  bottom: 12px;
  /* 距chat-outer底边，即输入框上方 */
  left: 50%;
  transform: translateX(-50%);
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--bg-2);
  border: 1px solid var(--border-light);
  color: var(--text-2);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.25);
  transition: color 0.15s, border-color 0.15s;
  z-index: 10;
}

.scroll-bottom-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.scroll-btn-enter-active {
  transition: opacity 0.18s, transform 0.18s;
}

.scroll-btn-leave-active {
  transition: opacity 0.15s;
}

.scroll-btn-enter-from {
  opacity: 0;
  transform: translateX(-50%) translateY(6px);
}

.scroll-btn-leave-to {
  opacity: 0;
}
</style>