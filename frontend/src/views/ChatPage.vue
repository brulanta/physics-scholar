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
        <div class="session-item new-item" :class="{ active: sessions.currentId === '' }" @click="goWelcome"
          title="新对话">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style="flex-shrink:0">
            <path d="M6.5 1v11M1 6.5h11" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
          </svg>
          <span v-if="!collapsed">新对话</span>
        </div>

        <SessionItem v-for="s in sessions.list" :key="s.id" :session="s" :active="s.id === sessions.currentId"
          :collapsed="collapsed" @click="switchSession(s.id)" @rename="(id, t) => handleRename(id, t)""
          @delete="(id) => confirmDelete(id)" />
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
        <ChatWindow ref="chatWindowRef" :messages="treeMessages" :streaming-session-id="streamingSessionId"
          :streaming-content="streamingContent" :current-session-id="sessions.currentId" @regenerate="handleRegenerate"
          @edit-branch="handleEditBranch" />
        <InputBox ref="inputBoxRef" :loading="loading" :mode="currentMode"
          :draft="sessionDrafts[sessions.currentId] || ''" :edit-mode="editMode" @send="sendMessage"
          @send-edit="sendEditBranch" @cancel-edit="editMode.active = false; editMode.content = ''"
          @toggle-mode="toggleMode" @draft-change="v => sessionDrafts[sessions.currentId] = v" />
      </template>
    </main>

    <!-- 删除确认弹窗 -->
    <!-- 删除确认弹窗，用独立 transition -->
    <Transition name="fade">
      <div v-if="deleteConfirm.show" class="modal-overlay" @click.self="deleteConfirm.show = false">
        <div class="modal-box">
          <p class="modal-title">删除对话</p>
          <p class="modal-desc">确定要删除「{{ deleteConfirm.title }}」吗？此操作不可撤销。</p>
          <div class="modal-actions">
            <button class="modal-btn cancel" @click="deleteConfirm.show = false">取消</button>
            <button class="modal-btn danger" @click="executeDelete">删除</button>
          </div>
        </div>
      </div>
    </Transition>

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
import { ref, computed, reactive, onMounted, provide, nextTick, watch } from 'vue'
import axios from 'axios'
import {
  sessions, settings,
  createSession, getSession,
  renameSession as storeRename,
  deleteSession as storeDelete,
  applyTheme, applyFont
} from '../store/app.js'
import {
  sendChatMessage,
  regenerateMessage,
  getConversationTree,
  deleteConversation,
  listConversations,
  updateConversationTitle,
} from '../api/chat.js'
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

// 替换掉原来的 treeMessages ref 和 activeMessageId ref
const sessionCache = reactive({})
// 结构：{ [convId]: { messages: [], activeMessageId: null } }

// 计算属性：当前 session 的消息列表
const treeMessages = computed(() =>
  sessionCache[sessions.currentId]?.messages ?? []
)
const activeMessageId = computed({
  get: () => sessionCache[sessions.currentId]?.activeMessageId ?? null,
  set: (v) => {
    if (sessionCache[sessions.currentId]) {
      sessionCache[sessions.currentId].activeMessageId = v
    }
  }
})

function ensureCache(convId) {
  if (!sessionCache[convId]) {
    sessionCache[convId] = {
      messages: [],
      activeMessageId: null,
      tree: {},          // 补上这个
      activePath: new Set()
    }
  }
}

// streaming
const streamingSessionId = ref(null)
const streamingContent = ref('')
let streamingCounter = 0

// 草稿：{ [sessionId]: string }
const sessionDrafts = ref({})

// 删除确认
const deleteConfirm = reactive({
  show: false,
  id: null,
  title: ''
})

// InputBox 编辑模式：{ active: bool, content: string, parentId: number|null }
const editMode = reactive({
  active: false,
  content: '',
  parentId: null,
  msgId: null,
})

function showToast(msg, ms = 2200) {
  toastMsg.value = msg
  setTimeout(() => { toastMsg.value = '' }, ms)
}
provide('showToast', showToast)
provide('loading', loading)

const currentMode = computed(() => sessionModes.value[sessions.currentId] || 'normal')
function toggleMode() {
  const id = sessions.currentId
  sessionModes.value[id] = currentMode.value === 'normal' ? 'discuss' : 'normal'
}

// ── 切换 session 时拉取历史 ──
// 用一个 flag 防止 watch 和 handleWelcomeSend 互相干扰
// 替换掉 suppressTreeLoad，改用这个
const freshSessionId = ref(null)  // 标记刚创建、无需从后端拉取的 session
const chatWindowRef = ref(null)
const inputBoxRef = ref(null)

watch(() => sessions.currentId, async (id) => {
  if (!id) return
  if (freshSessionId.value === id) {
    freshSessionId.value = null
    return
  }

  editMode.active = false

  // 已有缓存（包括正在 streaming 的），直接用
  if (sessionCache[id]) {
    await nextTick()
    chatWindowRef.value?.scrollToBottom()
    inputBoxRef.value?.focusInput()
    return
  }

  // 无缓存才从后端拉取
  ensureCache(id)
  await loadConversationTree(id)
  await nextTick()
  chatWindowRef.value?.scrollToBottom()
  inputBoxRef.value?.focusInput()
})

async function loadConversationTree(sessionId) {
  const fullId = `default_${sessionId}`
  try {
    const res = await getConversationTree(fullId)
    const msgs = res.data.messages || []
    ensureCache(sessionId)
    buildTree(sessionId, msgs)
  } catch {
    ensureCache(sessionId)
    sessionCache[sessionId].messages = []
    sessionCache[sessionId].activeMessageId = null
  }
}

function buildTree(convId, msgs) {
  const cache = sessionCache[convId]

  // 建 id → msg 索引
  const nodeMap = {}
  msgs.forEach(m => {
    nodeMap[m.id] = { ...m, children: [] }
  })

  // 建父子关系
  const roots = []
  msgs.forEach(m => {
    if (m.parent_id === null) {
      roots.push(m.id)
    } else if (nodeMap[m.parent_id]) {
      nodeMap[m.parent_id].children.push(m.id)
    }
  })

  cache.tree = nodeMap

  // 计算激活链路：每个节点优先选 status=normal 且 version 最大的子节点
  const activePath = []
  let current = roots[0] ?? null
  while (current !== null) {
    activePath.push(current)
    const node = nodeMap[current]
    if (!node || node.children.length === 0) break
    // 优先 normal，version 最大
    const validChildren = node.children
      .map(id => nodeMap[id])
      .filter(n => n.status !== 'deleted')
    const chosen = validChildren
      .filter(n => n.status === 'normal')
      .sort((a, b) => b.version - a.version)[0]
      ?? validChildren[validChildren.length - 1]
    current = chosen?.id ?? null
  }

  cache.activePath = new Set(activePath)
  cache.messages = activePath
    .map(id => nodeMap[id])
    .filter(Boolean)
    .map(m => ({
      id: m.id,
      parentId: m.parent_id,
      role: m.role,
      content: m.content,
      liked: m.liked ?? 0,
      createdAt: m.created_at,
      // 分支信息：兄弟节点数量和当前是第几个
      siblings: getSiblings(nodeMap, m),
    }))

  const last = cache.messages[cache.messages.length - 1]
  cache.activeMessageId = last?.id ?? null
}

function getSiblings(nodeMap, msg) {
  if (!nodeMap || !msg) return { total: 1, index: 0 }
  if (msg.parent_id === null) {
    const roots = Object.values(nodeMap).filter(n => n.parent_id === null && n.status !== 'deleted')
    const idx = roots.findIndex(n => n.id === msg.id)
    return { total: roots.length || 1, index: Math.max(idx, 0) }
  }
  const parent = nodeMap[msg.parent_id]
  if (!parent) return { total: 1, index: 0 }
  const siblings = parent.children
    .map(id => nodeMap[id])
    .filter(n => n && n.status !== 'deleted')
  const idx = siblings.findIndex(n => n.id === msg.id)
  return { total: siblings.length || 1, index: Math.max(idx, 0) }
}

function switchBranch(msgId, direction) {
  // direction: 'prev' | 'next'
  const convId = sessions.currentId
  const cache = sessionCache[convId]
  const node = cache.tree[msgId]
  if (!node) return

  // 找兄弟节点
  let siblings
  if (node.parent_id === null) {
    siblings = Object.values(cache.tree).filter(n => n.parent_id === null && n.status !== 'deleted')
  } else {
    const parent = cache.tree[node.parent_id]
    siblings = parent.children.map(id => cache.tree[id]).filter(n => n.status !== 'deleted')
  }

  const currentIdx = siblings.findIndex(n => n.id === msgId)
  const nextIdx = direction === 'next'
    ? Math.min(currentIdx + 1, siblings.length - 1)
    : Math.max(currentIdx - 1, 0)

  if (nextIdx === currentIdx) return

  const nextNode = siblings[nextIdx]

  // 重新计算从 nextNode 往下的激活链路，接在 nextNode 的父链路后面
  // 先找到 nextNode 之前的链路（父链路）
  const prefixPath = []
  let cur = node.parent_id
  while (cur !== null) {
    prefixPath.unshift(cur)
    cur = cache.tree[cur]?.parent_id ?? null
  }

  // 从 nextNode 往下选默认子链路
  const suffixPath = []
  let current = nextNode.id
  while (current !== null) {
    suffixPath.push(current)
    const n = cache.tree[current]
    if (!n || n.children.length === 0) break
    const validChildren = n.children.map(id => cache.tree[id]).filter(n => n.status !== 'deleted')
    const chosen = validChildren.filter(n => n.status === 'normal').sort((a, b) => b.version - a.version)[0]
      ?? validChildren[validChildren.length - 1]
    current = chosen?.id ?? null
  }

  const newPath = [...prefixPath, ...suffixPath]
  cache.activePath = new Set(newPath)
  cache.messages = newPath.map(id => cache.tree[id]).filter(Boolean).map(m => ({
    id: m.id,
    parentId: m.parent_id,
    role: m.role,
    content: m.content,
    liked: m.liked ?? 0,
    createdAt: m.created_at,
    siblings: getSiblings(cache.tree, m),
  }))

  const last = cache.messages[cache.messages.length - 1]
  cache.activeMessageId = last?.id ?? null
}

provide('switchBranch', switchBranch)


onMounted(async () => {
  applyTheme(settings.theme)
  applyFont(settings.fontFamily)
  sessions.currentId = ''
  localStorage.removeItem('ps-sessions')
  await loadSessionList()
})

async function loadSessionList() {
  try {
    const res = await listConversations('default')
    const convs = res.data.conversations || []
    // 后端返回的字段：conversation_id, title, created_at
    // conv_id 在前端存的是不带 "default_" 前缀的部分
    sessions.list = convs.map(c => ({
      id: c.conversation_id.replace(/^default_/, ''),
      title: c.title || '未命名对话',
      createdAt: c.created_at,
    }))
  } catch {
    sessions.list = []
  }
}

function goWelcome() {
  sessions.currentId = ''
  editMode.active = false
}

function switchSession(id) {
  sessions.currentId = id
}

// ── 删除 ──
function confirmDelete(id) {
  const s = sessions.list.find(s => s.id === id)
  deleteConfirm.id = id
  deleteConfirm.title = s?.title || '该对话'
  deleteConfirm.show = true
}

async function executeDelete() {
  const id = deleteConfirm.id
  deleteConfirm.show = false
  const fullId = `default_${id}`
  try {
    await deleteConversation(fullId)
  } catch { }
  delete sessionCache[id]  // 清理缓存
  storeDelete(id)
  if (sessions.currentId === id || !sessions.currentId) {
    editMode.active = false
  }
  showToast('对话已删除')
}

// ── 欢迎页发送 ──
const welcomeSending = ref(false)

async function handleWelcomeSend({ text, discuss }) {
  if (!text.trim() || welcomeSending.value) return
  welcomeSending.value = true

  let convId
  try {
    const res = await axios.post(`${BASE}/conv_id/new`)
    convId = res.data.conv_id
  } catch {
    convId = 'local-' + Date.now()
  }

  // 压制 watch，避免 createSession 触发 loadConversationTree 清空 treeMessages
  freshSessionId.value = convId   // 提前标记，watch 触发时会看到它
  createSession(convId, text)
  if (discuss) sessionModes.value[convId] = 'discuss'
  ensureCache(convId)
  sessionCache[convId].messages = []
  sessionCache[convId].activeMessageId = null

  await nextTick()

  try {
    await _doSend(convId, text)
  } finally {
    welcomeSending.value = false
  }
}

// ── 普通发送 ──
async function sendMessage(text) {
  if (!sessions.currentId || !text.trim() || loading.value) return
  // 清除草稿
  sessionDrafts.value[sessions.currentId] = ''
  await _doSend(sessions.currentId, text)
}

// ── 编辑模式确认发送 ──
async function sendEditBranch(text) {
  const { msgId, parentId } = editMode
  editMode.active = false
  editMode.content = ''
  editMode.msgId = null

  // 发送时才截断
  const convId = sessions.currentId
  ensureCache(convId)
  if (msgId !== null) {
    const cutIdx = sessionCache[convId].messages.findIndex(m => m.id === msgId)
    if (cutIdx !== -1) {
      sessionCache[convId].messages = sessionCache[convId].messages.slice(0, cutIdx)
    }
  } else {
    sessionCache[convId].messages = []
  }

  await _doSend(convId, text, parentId)
}

// ── 核心发送 ──
async function _doSend(convId, text, overrideParentId = undefined) {
  loading.value = true
  ensureCache(convId)

  const parentId = overrideParentId !== undefined
    ? overrideParentId
    : sessionCache[convId].activeMessageId

  const tempUserMsg = {
    id: null,
    parentId,
    role: 'user',
    content: text,
    liked: 0,
    createdAt: new Date().toISOString(),
  }
  sessionCache[convId].messages.push(tempUserMsg)

  const myCount = ++streamingCounter
  streamingSessionId.value = convId
  streamingContent.value = ''

  try {
    const res = await sendChatMessage(text, convId, {
      parentId,
      translation: settings.translation,
      mode: sessionModes.value[convId] || 'normal',
    })

    const { answer, user_msg_id, agent_msg_id } = res.data
    const fullText = answer || '（无回复）'
    tempUserMsg.id = user_msg_id

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
      // ── 先同步树结构 ──
      const userNode = {
        id: user_msg_id, parent_id: parentId, role: 'user',
        content: text, status: 'normal', version: 1, liked: 0,
        created_at: new Date().toISOString(), children: [agent_msg_id]
      }
      const agentNode = {
        id: agent_msg_id, parent_id: user_msg_id, role: 'assistant',
        content: fullText, status: 'normal', version: 1, liked: 0,
        created_at: new Date().toISOString(), children: []
      }
      // 把 tempUserMsg 的真实 id 也写入树
      if (sessionCache[convId].tree) {
        // user 节点：找它的父节点，把 user_msg_id 加入父节点 children
        const parentNode = sessionCache[convId].tree[parentId]
        if (parentNode && !parentNode.children.includes(user_msg_id)) {
          parentNode.children.push(user_msg_id)
        }
        sessionCache[convId].tree[user_msg_id] = userNode
        sessionCache[convId].tree[agent_msg_id] = agentNode
      }

      // ── 再更新 messages 数组（含 siblings 重算）──
      // 先回填 tempUserMsg 的 siblings
      const tempIdx = sessionCache[convId].messages.findIndex(m => m.id === user_msg_id)
      if (tempIdx !== -1) {
        sessionCache[convId].messages[tempIdx].siblings =
          getSiblings(sessionCache[convId].tree, userNode)
      }
      // push agent 消息
      sessionCache[convId].messages.push({
        id: agent_msg_id,
        parentId: user_msg_id,
        role: 'assistant',
        content: fullText,
        liked: 0,
        createdAt: new Date().toISOString(),
        siblings: getSiblings(sessionCache[convId].tree, agentNode),
      })
      sessionCache[convId].activeMessageId = agent_msg_id
      streamingSessionId.value = null
      streamingContent.value = ''
    }
  } catch (err) {
    console.error('[_doSend catch]', err)
    const idx = sessionCache[convId].messages.indexOf(tempUserMsg)
    if (idx !== -1) sessionCache[convId].messages.splice(idx, 1)
    sessionDrafts.value[convId] = text
    showToast('请求失败，请检查后端是否运行')
    streamingSessionId.value = null
    streamingContent.value = ''
  } finally {
    loading.value = false
  }
}

// ── 重新生成 ──
async function handleRegenerate({ msgId, parentId, question }) {
  if (loading.value) return
  loading.value = true

  const convId = sessions.currentId
  ensureCache(convId)

  const messages = sessionCache[convId].messages

  // 1️⃣ 立刻从缓存中移除旧 agent 消息
  const idx = messages.findIndex(m => m.id === Number(msgId))
  if (idx !== -1) messages.splice(idx, 1)

  const myCount = ++streamingCounter
  streamingSessionId.value = convId
  streamingContent.value = ''

  try {
    const res = await regenerateMessage({
      question,
      conv_id: convId,
      parent_id: Number(parentId),
      old_agent_msg_id: Number(msgId),
      translation: settings.translation,
      mode: sessionModes.value[convId] || 'normal',
    })

    const { answer, agent_msg_id } = res.data
    const fullText = answer || '（无回复）'

    // 2️⃣ 打字机效果
    let i = 0
    await new Promise(resolve => {
      const timer = setInterval(() => {
        if (streamingCounter !== myCount) {
          clearInterval(timer)
          resolve()
          return
        }
        if (i < fullText.length) {
          streamingContent.value += fullText[i++]
        } else {
          clearInterval(timer)
          resolve()
        }
      }, 8)
    })

    // 3️⃣ 写回缓存（而不是 treeMessages）
    if (streamingCounter === myCount) {
      const cache = sessionCache[sessions.currentId]

      // ── 同步树结构 ──
      if (cache.tree) {
        // 旧节点标记 regenerated
        if (cache.tree[Number(msgId)]) {
          cache.tree[Number(msgId)].status = 'regenerated'
        }
        // 新节点入树
        const newNode = {
          id: agent_msg_id, parent_id: Number(parentId), role: 'assistant',
          content: fullText, status: 'normal', version: 2, liked: 0,
          created_at: new Date().toISOString(), children: []
        }
        cache.tree[agent_msg_id] = newNode
        const parentNode = cache.tree[Number(parentId)]
        if (parentNode && !parentNode.children.includes(agent_msg_id)) {
          parentNode.children.push(agent_msg_id)
        }

        // ── 重算所有现有消息的 siblings（因为新增了兄弟节点）──
        cache.messages = cache.messages.map(m => ({
          ...m,
          siblings: cache.tree[m.id]
            ? getSiblings(cache.tree, cache.tree[m.id])
            : m.siblings,
        }))

        // push 新 agent 消息
        cache.messages.push({
          id: agent_msg_id,
          parentId: Number(parentId),
          role: 'assistant',
          content: fullText,
          liked: 0,
          createdAt: new Date().toISOString(),
          siblings: getSiblings(cache.tree, newNode),
        })
      }

      cache.activeMessageId = agent_msg_id
      streamingSessionId.value = null
      streamingContent.value = ''
    }

  } catch {
    showToast('重新生成失败，请重试')
    streamingSessionId.value = null
    streamingContent.value = ''

  } finally {
    loading.value = false
  }
}

// ── 编辑消息（进入编辑模式，不立即发送） ──
function handleEditBranch({ msgId, content, parentId }) {
  // 不再立刻截断，只记录编辑状态
  editMode.active = true
  editMode.msgId = msgId      // 新增，记录被编辑消息的 id
  editMode.content = content
  editMode.parentId = parentId
}

// ── 标题同步 ──
watch(() => sessions.currentId, (id) => {
  if (!id) { document.title = 'PhysicsScholar'; return }
  const session = getSession(id)
  document.title = session?.title ? `${session.title} · PhysicsScholar` : 'PhysicsScholar'
}, { immediate: true })

async function handleRename(id, newTitle) {
  storeRename(id, newTitle)
  const fullId = `default_${id}`
  try {
    await updateConversationTitle(fullId, newTitle)
  } catch {
    showToast('重命名同步失败')
  }
}

function updateMsgLiked(msgId, liked) {
  const convId = sessions.currentId
  if (!sessionCache[convId]) return
  const msg = sessionCache[convId].messages.find(m => m.id === msgId)
  if (msg) msg.liked = liked
}
provide('updateMsgLiked', updateMsgLiked)
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
  width: 600px;
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

/* 删除确认弹窗 */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.modal-box {
  background: var(--bg2, #1e1e1e);
  border: 1px solid var(--b, #333);
  border-radius: 12px;
  padding: 24px 28px;
  min-width: 280px;
  max-width: 360px;
}

.modal-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--color-text-primary, #fff);
  margin: 0 0 8px;
}

.modal-desc {
  font-size: 13px;
  color: var(--color-text-secondary, #aaa);
  margin: 0 0 20px;
  line-height: 1.5;
}

.modal-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

.modal-btn {
  padding: 7px 18px;
  border-radius: 7px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: opacity 0.15s;
}

.modal-btn:hover {
  opacity: 0.82;
}

.modal-btn.cancel {
  background: transparent;
  border-color: var(--b, #444);
  color: var(--color-text-secondary, #aaa);
}

.modal-btn.danger {
  background: #c0392b;
  color: #fff;
}

/* 弹窗 fade */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* 弹窗内容（跟随主题） */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.modal-box {
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px 28px;
  min-width: 280px;
  max-width: 360px;
}

.modal-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--text);
  margin: 0 0 8px;
}

.modal-desc {
  font-size: 13px;
  color: var(--text-2);
  margin: 0 0 20px;
  line-height: 1.5;
}

.modal-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

.modal-btn {
  padding: 7px 18px;
  border-radius: 7px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: opacity 0.15s;
}

.modal-btn:hover {
  opacity: 0.82;
}

.modal-btn.cancel {
  background: transparent;
  border-color: var(--border);
  color: var(--text-2);
}

.modal-btn.danger {
  background: #c0392b;
  color: #fff;
}
</style>