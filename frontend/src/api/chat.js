import axios from 'axios'

export function sendChatMessage(message, conv_id, options = {}) {
  return axios.post('http://localhost:8000/ask', {
    question: message,
    conv_id: conv_id,
    user_id: 'default',
    translation: options.translation ?? false,
    mode: options.mode ?? 'normal'
  })
}