import { reactive } from 'vue'

// state: 'ok' | 'restarting' | 'down'
export const serviceState = reactive({ state: 'ok' })