<template>
  <div class="chat-outer">
    <div class="chat-window" ref="windowRef" @scroll="onScroll">
      <div class="messages">
        <MessageItem v-for="msg in messages" :key="msg.id ?? msg.createdAt"
          :siblings="msg.siblings ?? { total: 1, index: 0 }" :msg-id="msg.id" :parent-id="msg.parentId" :role="msg.role"
          :content="msg.content" :liked="msg.liked" :created-at="msg.createdAt"
          :prev-user-content="getPrevUserContent(msg)" @regenerate="$emit('regenerate', $event)"
          @edit-branch="$emit('edit-branch', $event)" />
        <!-- streaming 占位 -->
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

const emit = defineEmits(['regenerate', 'edit-branch'])

// 给 MessageItem 传入"上一条 user 消息的内容"，用于重发时带原始问题
function getPrevUserContent(msg) {
  if (msg.role !== 'assistant') return ''
  const idx = props.messages.findIndex(m => m.id === msg.id)
  if (idx <= 0) return ''
  const prev = props.messages[idx - 1]
  return prev?.role === 'user' ? prev.content : ''
}

const windowRef = ref(null)
const showScrollBtn = ref(false)
const isNearBottom = ref(true)
const userScrolledUp = ref(false)  // 新增：用户是否主动向上滚

function onScroll() {
  const el = windowRef.value
  if (!el) return
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight
  isNearBottom.value = dist < 80
  showScrollBtn.value = dist > 200

  // 用户主动上滚时标记，接近底部时自动解除
  if (dist > 80) {
    userScrolledUp.value = true
  } else {
    userScrolledUp.value = false
  }
}

// streaming 追底：尊重用户的滚动意图
watch(() => props.streamingContent, async () => {
  if (props.streamingSessionId !== props.currentSessionId) return
  if (userScrolledUp.value) return   // 用户主动上滚，不强制追底
  await nextTick()
  if (isNearBottom.value) scrollToBottom()
})

// 新消息出现时的滚动逻辑也加保护
watch(() => props.messages?.length, async (newLen, oldLen) => {
  if (!newLen) return
  await nextTick()
  const el = windowRef.value
  if (!el) return
  const added = newLen - (oldLen ?? 0)
  if (added <= 0) return
  const lastMsg = props.messages[newLen - 1]
  if (lastMsg.role === 'user') {
    const rows = el.querySelectorAll('.message-row')
    const lastRow = rows[rows.length - 1]
    if (lastRow) el.scrollTo({ top: lastRow.offsetTop - 20, behavior: 'smooth' })
  } else if (lastMsg.role === 'assistant') {
    // 新 assistant 消息出现时（非 streaming），重置上滚标记
    userScrolledUp.value = false
    if (isNearBottom.value) scrollToBottom()
  }
})

function scrollToBottom(smooth = false) {
  const el = windowRef.value
  if (!el) return
  userScrolledUp.value = false  // 主动点击回底，重置标记
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'instant' })
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
    const rows = el.querySelectorAll('.message-row')
    const lastRow = rows[rows.length - 1]
    if (lastRow) el.scrollTo({ top: lastRow.offsetTop - 20, behavior: 'smooth' })
  } else if (lastMsg.role === 'assistant') {
    if (isNearBottom.value) scrollToBottom()
  }
})

watch(() => props.streamingContent, async () => {
  // 只有当前激活 session 的 streaming 才触发滚动
  if (props.streamingSessionId !== props.currentSessionId) return
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