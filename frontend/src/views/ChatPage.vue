<template>
  <div class="layout">
    <aside class="sidebar" :class="{ collapsed }">
      <div class="sidebar-top">
        <span v-if="!collapsed" class="logo">⚛ PhysicsScholar</span>
        <button class="icon-btn" @click="collapsed = !collapsed">
          <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
            <path :d="collapsed ? 'M5 3l5 4.5L5 12' : 'M10 3L5 7.5l5 4.5'" stroke="currentColor" stroke-width="1.6"
              stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </div>

      <div class="session-list">
        <div class="session-item new-item" :class="{ active: sessions.currentId === '' }" @click="goWelcome" title="新对话">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style="flex-shrink:0">
            <path d="M6.5 1v11M1 6.5h11" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
          </svg>
          <span v-if="!collapsed">新对话</span>
        </div>

        <SessionItem v-for="s in sessions.list" :key="s.id" :session="s" :active="s.id === sessions.currentId"
          :collapsed="collapsed" @click="switchSession(s.id)" @rename="(id, t) => storeRename(id, t)"
          @delete="(id) => storeDelete(id)" />
      </div>

      <div class="sidebar-bottom">
        <button class="user-btn" @click="showSettings = true">
          <div class="user-avatar">U</div>
          <span v-if="!collapsed" class="user-label">设置 &amp; 论文库</span>
        </button>
      </div>
    </aside>

    <main class="main">
      <WelcomePage v-if="sessions.currentId === ''" @send="handleWelcomeSend" />
      <template v-else>
        <ChatWindow ref="chatWindowRef" :messages="currentMessages" :streaming-session-id="streamingSessionId"
          :streaming-content="streamingContent" :current-session-id="sessions.currentId" />
        <InputBox :loading="loading" :mode="currentMode" @send="sendMessage" @toggle-mode="toggleMode" />
      </template>
    </main>

    <Transition name="toast">
      <div v-if="toastMsg" class="toast">{{ toastMsg }}</div>
    </Transition>

    <Transition name="drawer">
      <div v-if="showSettings" class="drawer-overlay" @click.self="showSettings = false">
        <div class="drawer">
          <SettingsDrawer @close="showSettings = false" />
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, provide, nextTick, watch } from 'vue'
import axios from 'axios'
import {
  sessions, settings,
  createSession, getSession,
  renameSession as storeRename,
  deleteSession as storeDelete,
  applyTheme, applyFont
} from '../store/app.js'
import { sendChatMessage } from '../api/chat.js'
import ChatWindow from '../components/Chat/ChatWindow.vue'
import InputBox from '../components/Chat/InputBox.vue'
import WelcomePage from '../components/Chat/WelcomePage.vue'
import SessionItem from '../components/Sidebar/SessionItem.vue'
import SettingsDrawer from '../components/Settings/SettingsDrawer.vue'

const BASE = 'http://localhost:8000'
const collapsed = ref(false)
const showSettings = ref(false)
const loading = ref(false)
const toastMsg = ref('')
const sessionModes = ref({})

// streaming状态提升到顶层，用唯一ID标识当前正在streaming的消息
const streamingMsgId = ref(null)   // 一个递增数字，用来让ChatWindow知道哪条在streaming
const streamingSessionId = ref(null)  // 记录哪个sessionId在streaming（不是消息id）
const streamingContent = ref('')
let streamingCounter = 0

function showToast(msg, ms = 2200) {
  toastMsg.value = msg
  setTimeout(() => { toastMsg.value = '' }, ms)
}
provide('showToast', showToast)

const currentMode = computed(() => sessionModes.value[sessions.currentId] || 'normal')
function toggleMode() {
  const id = sessions.currentId
  sessionModes.value[id] = currentMode.value === 'normal' ? 'discuss' : 'normal'
}


// currentMessages不再slice，streaming消息单独追加渲染
// 只有当前session且正在streaming时，才在末尾追加streamingMsg
const currentMessages = computed(() => {
  return getSession(sessions.currentId)?.messages ?? []
})

// 切换session时滚到底
const chatWindowRef = ref(null)
watch(() => sessions.currentId, async () => {
  if (!sessions.currentId) return
  await nextTick()
  chatWindowRef.value?.scrollToBottom()
})

onMounted(() => {
  applyTheme(settings.theme)
  applyFont(settings.fontFamily)
  // 始终从欢迎页开始
  sessions.currentId = ''
})

function goWelcome() {
  if (sessions.currentId === '') return
  sessions.currentId = ''
}

function switchSession(id) {
  // streaming过程中允许切换，streaming会继续写store，切回来能看到完整内容
  sessions.currentId = id
}

async function handleWelcomeSend({ text, discuss }) {
  if (!text.trim()) return
  let convId
  try {
    const res = await axios.post(`${BASE}/conv_id/new`)
    convId = res.data.conv_id
  } catch {
    convId = 'local-' + Date.now()
  }
  const session = createSession(convId, text)
  if (discuss) sessionModes.value[convId] = 'discuss'
  // 等两帧，让ChatPage完成从欢迎页→对话页的DOM切换
  await nextTick()
  await nextTick()
  await _doSend(session, text)
}

async function sendMessage(text) {
  const session = getSession(sessions.currentId)
  if (!session || !text.trim() || loading.value) return
  await _doSend(session, text)
}


async function _doSend(session, text) {
  loading.value = true
  session.messages.push({ role: 'user', content: text })
  const assistantMsg = { role: 'assistant', content: '' }
  session.messages.push(assistantMsg)

  const myCount = ++streamingCounter
  streamingSessionId.value = session.id
  streamingContent.value = ''

  try {
    const res = await sendChatMessage(text, session.id, {
      translation: settings.translation,
      mode: sessionModes.value[session.id] || 'normal'
    })
    const fullText = res.data.answer || '（无回复）'

    let i = 0
    await new Promise(resolve => {
      const timer = setInterval(() => {
        if (streamingCounter !== myCount) { clearInterval(timer); resolve(); return }
        if (i < fullText.length) {
          streamingContent.value += fullText[i++]
        } else {
          clearInterval(timer); resolve()
        }
      }, 8)
    })

    if (streamingCounter === myCount) {
      assistantMsg.content = fullText
      streamingSessionId.value = null
      streamingContent.value = ''
    }
  } catch {
    assistantMsg.content = '❌ 请求失败，请检查后端是否运行。'
    streamingSessionId.value = null
    streamingContent.value = ''
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  width: 100%;
  overflow: hidden;
}

.sidebar {
  width: 240px;
  min-width: 240px;
  background: var(--bg-2);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.22s ease, min-width 0.22s ease;
}

.sidebar.collapsed {
  width: 52px;
  min-width: 52px;
}

.sidebar-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 12px;
  border-bottom: 1px solid var(--border);
  min-height: 50px;
}

.sidebar.collapsed .sidebar-top {
  justify-content: center;
}

.logo {
  font-size: 0.88em;
  font-weight: 600;
  color: var(--accent);
  white-space: nowrap;
  overflow: hidden;
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
  flex-shrink: 0;
}

.icon-btn:hover {
  color: var(--text);
  background: var(--bg-hover);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-2);
  font-size: 0.83em;
  transition: background 0.12s, color 0.12s;
  white-space: nowrap;
  overflow: hidden;
}

.session-item:hover {
  background: var(--bg-hover);
  color: var(--text);
}

.session-item.active {
  background: var(--bg-3);
  color: var(--text);
}

.session-item.new-item {
  color: var(--text-3);
  border-bottom: 1px solid var(--border);
  margin-bottom: 6px;
  padding-bottom: 10px;
}

.session-item.new-item.active {
  background: var(--bg-3);
  color: var(--accent);
}

.sidebar.collapsed .session-item {
  justify-content: center;
}

.sidebar-bottom {
  border-top: 1px solid var(--border);
  padding: 8px;
}

.user-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-2);
  transition: background 0.12s;
}

.user-btn:hover {
  background: var(--bg-hover);
}

.sidebar.collapsed .user-btn {
  justify-content: center;
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--accent-dim);
  color: #fff;
  font-size: 0.78em;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.user-label {
  font-size: 0.83em;
  white-space: nowrap;
  overflow: hidden;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.toast {
  position: fixed;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--bg-3);
  border: 1px solid var(--border-light);
  color: var(--text);
  font-size: 0.85em;
  padding: 8px 20px;
  border-radius: 20px;
  box-shadow: var(--shadow);
  z-index: 1000;
  pointer-events: none;
}

.toast-enter-active {
  transition: opacity 0.2s, transform 0.2s;
}

.toast-leave-active {
  transition: opacity 0.3s;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(-50%) translateY(-8px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-8px);
}

.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 200;
  display: flex;
  justify-content: flex-end;
  backdrop-filter: blur(2px);
}

.drawer {
  width: 480px;
  max-width: 92vw;
  height: 100%;
  background: var(--bg-2);
  border-left: 1px solid var(--border);
  overflow-y: auto;
}

.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.2s;
}

.drawer-enter-active .drawer,
.drawer-leave-active .drawer {
  transition: transform 0.22s ease;
}

.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}

.drawer-enter-from .drawer,
.drawer-leave-to .drawer {
  transform: translateX(100%);
}
</style>