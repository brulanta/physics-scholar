import axios from 'axios'

const BASE = 'http://localhost:8000'

export function sendChatMessage(message, conv_id, options = {}) {
  return axios.post(`${BASE}/ask`, {
    question: message,
    conv_id,
    user_id: 'default',
    parent_id: options.parentId ?? null,
    translation: options.translation ?? false,
    mode: options.mode ?? 'normal'
  })
}

export function regenerateMessage(payload) {
  // payload: { question, conv_id, parent_id, old_agent_msg_id, translation, mode }
  return axios.post(`${BASE}/regenerate`, {
    ...payload,
    user_id: 'default',
  })
}

export function getConversationTree(conversationId) {
  // conversationId 已是完整的 {user_id}_{conv_id}，例如 "default_abc123"
  return axios.get(`${BASE}/conversation/${conversationId}/tree`)
}

export function likeMessage(msgId, liked) {
  // liked: 1 点赞 / -1 点踩 / 0 取消
  return axios.patch(`${BASE}/message/${msgId}/like`, null, {
    params: { liked }
  })
}

export function deleteConversation(conversationId) {
  return axios.delete(`${BASE}/conversation/${conversationId}`)
}

export function listConversations(userId = 'default') {
  return axios.get(`${BASE}/conversations`, { params: { user_id: userId } })
}

export function updateConversationTitle(conversationId, title) {
  return axios.patch(`${BASE}/conversation/${conversationId}/title`, null, {
    params: { title }
  })
}