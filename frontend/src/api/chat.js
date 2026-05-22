import axios from 'axios'

const request = axios.create({
  baseURL: '/api',
})

export function sendChatMessage(message, conv_id, options = {}) {
  return request.post('/ask', {
    question: message,
    conv_id,
    user_id: 'default',
    parent_id: options.parentId ?? null,
    translation: options.translation ?? false,
    mode: options.mode ?? 'normal'
  })
}

export function regenerateMessage(payload) {
  return request.post('/regenerate', {
    ...payload,
    user_id: 'default',
  })
}

export function getConversationTree(conversationId) {
  return request.get(`/conversation/${conversationId}/tree`)
}

export function likeMessage(msgId, liked) {
  return request.patch(`/message/${msgId}/like`, null, {
    params: { liked }
  })
}

export function deleteConversation(conversationId) {
  return request.delete(`/conversation/${conversationId}`)
}

export function listConversations(userId = 'default') {
  return request.get('/conversations', { params: { user_id: userId } })
}

export function updateConversationTitle(conversationId, title) {
  return request.patch(`/conversation/${conversationId}/title`, null, {
    params: { title }
  })
}

export function newConversation() {
  return request.post('/conv_id/new')
}