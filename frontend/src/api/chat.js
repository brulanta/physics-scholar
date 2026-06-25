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

// ============================================================
// 真流式（SSE）：fetch + ReadableStream，替代上面 axios 的 /ask /regenerate
// 旧的 sendChatMessage/regenerateMessage 保留为非流式兜底，不删。
// ============================================================

// 消费 SSE 流：按 \n\n 切帧（残帧留缓冲），解析 data: 行后按 evt.type 派发。
// handlers 形如 { thinking_start, thinking_end, tool_start, tool_end,
//                 answer_start, answer_delta, answer_end, done, error }，各项可选。
async function consumeSSE(body, handlers) {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, idx).trim()
        buf = buf.slice(idx + 2)
        if (!frame.startsWith('data:')) continue // 跳过心跳 ": ping" 等非数据帧
        let evt
        try {
          evt = JSON.parse(frame.slice(5).trim())
        } catch {
          continue // 坏帧/残帧，跳过
        }
        handlers[evt.type]?.(evt)
      }
    }
  } finally {
    reader.releaseLock?.()
  }
}

// 发起流式 POST 并消费。signal 来自 AbortController，用于停止/切会话取消。
// 主动取消（AbortError）静默返回；其余异常走 handlers.error。
async function streamSSE(url, payload, handlers, signal) {
  let res
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    })
  } catch (e) {
    if (e?.name === 'AbortError') return
    handlers.error?.({ type: 'error', message: String(e?.message ?? e) })
    return
  }
  if (!res.ok || !res.body) {
    handlers.error?.({ type: 'error', message: `HTTP ${res.status}` })
    return
  }
  try {
    await consumeSSE(res.body, handlers)
  } catch (e) {
    if (e?.name === 'AbortError') return
    handlers.error?.({ type: 'error', message: String(e?.message ?? e) })
  }
}

// payload: { question, conv_id, parent_id?, translation?, mode? }
export function streamChat(payload, handlers, signal) {
  return streamSSE('/api/ask', { user_id: 'default', ...payload }, handlers, signal)
}

// payload: { question, conv_id, parent_id, old_agent_msg_id, translation?, mode? }
export function streamRegenerate(payload, handlers, signal) {
  return streamSSE('/api/regenerate', { user_id: 'default', ...payload }, handlers, signal)
}