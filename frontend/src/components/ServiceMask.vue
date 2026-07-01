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
            <div class="mask-title">连接已中断</div>
            <div class="mask-desc">正在尝试重新连接，请稍候…</div>
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
// 重启期间是否已确认旧后端下线过一次。restarting 一置位时旧后端往往还活着
// （_do_restart 有 0.5s 延迟 + 进程退出延迟），若此时的 200 就 reload，会刷回
// 尚未退出的旧后端（配置弹窗随整页重载消失=看似「立刻刷新」，刷完是干净 ok 态=空白，
// 旧后端稍后才真死→再走 down 检测=遮罩延迟出现）。故需先见到一次下线，之后的 200
// 才是新后端就绪。
let sawDown = false

async function checkHealth() {
  // 重启中：先确认旧后端已下线过一次，再于新后端就绪（200）时刷新页面
  if (serviceState.state === 'restarting') {
    try {
      const r = await fetch('/api/health')
      if (r.ok) {
        if (sawDown) {
          clearInterval(timer)
          location.reload()
        }
        // 否则：这是尚未退出的旧后端，继续等它先下线
      } else {
        sawDown = true
      }
    } catch (_) {
      sawDown = true  // 旧后端已下线，等新后端起来
    }
    return
  }

  // 正常运行中：连续2次失败才判定断开
  try {
    const r = await fetch('/api/health')
    if (r.ok) {
      failCount = 0
      // 后端主动重启（托盘/配置页触发）会在下线前置 restarting=true。托盘重启时旧页面
      // 本不知情，靠这里读到标志切到「正在重启」转圈遮罩，与「意外断联/退出」(X)区分。
      const body = await r.json().catch(() => ({}))
      serviceState.state = body.restarting ? 'restarting' : 'ok'
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