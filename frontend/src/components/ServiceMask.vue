<template>
  <Teleport to="body">
    <Transition name="mask">
      <div v-if="visible" class="service-mask">
        <div class="mask-content">

          <!-- 后端断开 -->
          <template v-if="state === 'down'">
            <div class="mask-icon dead">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="1.5" />
                <path d="M15 9l-6 6M9 9l6 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
              </svg>
            </div>
            <div class="mask-title">程序已退出</div>
            <div class="mask-desc">请关闭此页面</div>
          </template>

          <!-- 重启中 -->
          <template v-else-if="state === 'restarting'">
            <div class="mask-icon restarting">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
                <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" stroke="currentColor" stroke-width="1.6"
                  stroke-linecap="round" stroke-linejoin="round" />
                <path d="M21 3v5h-5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
                  stroke-linejoin="round" />
              </svg>
            </div>
            <div class="mask-title">正在重启</div>
            <div class="mask-desc">请稍候...</div>
          </template>

        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { serviceState } from '../store/service.js'

const state = computed(() => serviceState.state)
const visible = computed(() => serviceState.state !== 'ok')

let timer = null

onMounted(() => {
  timer = setInterval(checkHealth, 2000)
})

onUnmounted(() => {
  clearInterval(timer)
})

let failCount = 0

async function checkHealth() {
  // 重启中：收到200就刷新页面
  if (serviceState.state === 'restarting') {
    try {
      const r = await fetch('/api/health')
      if (r.ok) {
        clearInterval(timer)
        location.reload()
      }
    } catch (_) { /* 还没起来，继续等 */ }
    return
  }

  // 正常运行中：连续2次失败才判定断开
  try {
    const r = await fetch('/api/health')
    if (r.ok) {
      failCount = 0
      serviceState.state = 'ok'
    } else {
      throw new Error()
    }
  } catch (_) {
    failCount++
    if (failCount >= 2) {
      serviceState.state = 'down'
    }
  }
}
</script>

<style scoped>
.service-mask {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: all;
  user-select: none;
}

.mask-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.mask-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  border-radius: 50%;
  margin-bottom: 4px;
}

.mask-icon.dead {
  background: rgba(248, 113, 113, 0.12);
  color: #f87171;
}

.mask-icon.restarting {
  background: rgba(108, 140, 255, 0.12);
  color: var(--accent, #6c8cff);
  animation: spin 1.2s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg)
  }

  to {
    transform: rotate(360deg)
  }
}

.mask-title {
  font-size: 1.1em;
  font-weight: 600;
  color: #fff;
  letter-spacing: 0.02em;
}

.mask-desc {
  font-size: 0.85em;
  color: rgba(255, 255, 255, 0.5);
}

/* 过渡动画 */
.mask-enter-active,
.mask-leave-active {
  transition: opacity 0.25s ease;
}

.mask-enter-from,
.mask-leave-to {
  opacity: 0;
}
</style>