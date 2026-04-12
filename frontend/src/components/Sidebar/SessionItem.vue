<template>
  <div class="session-item" :class="{ active, editing }" @click="!editing && $emit('click')" @dblclick="startEdit">
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style="flex-shrink:0">
      <path d="M1 2.5A1.5 1.5 0 012.5 1h8A1.5 1.5 0 0112 2.5v5A1.5 1.5 0 0110.5 9H8l-2 2-2-2H2.5A1.5 1.5 0 011 7.5v-5z"
        stroke="currentColor" stroke-width="1.2" fill="none" />
    </svg>

    <!-- 展开时：标题或编辑框 -->
    <template v-if="!collapsed">
      <input v-if="editing" ref="inputRef" v-model="editTitle" class="rename-input" @keydown.enter.prevent="confirmEdit"
        @keydown.esc.prevent="cancelEdit" @blur="confirmEdit" @click.stop />
      <span v-else class="session-title">{{ session.title }}</span>

      <!-- hover时出现操作按钮 -->
      <div v-if="!editing" class="item-actions">
        <button class="act-btn" @click.stop="startEdit" title="重命名">
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
            <path d="M7.5 1.5l2 2L3 10H1V8L7.5 1.5z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
          </svg>
        </button>
        <button class="act-btn danger" @click.stop="$emit('delete', session.id)" title="删除">
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
            <path d="M2 3h7M4 3V2h3v1M4.5 5v3M6.5 5v3M3 3l.5 6h4L8 3" stroke="currentColor" stroke-width="1.2"
              stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'

const props = defineProps({
  session: Object,
  active: Boolean,
  collapsed: Boolean
})
const emit = defineEmits(['click', 'rename', 'delete'])

const editing = ref(false)
const editTitle = ref('')
const inputRef = ref(null)

function startEdit() {
  if (props.collapsed) return
  editing.value = true
  editTitle.value = props.session.title
  nextTick(() => {
    inputRef.value?.select()
  })
}

function confirmEdit() {
  if (editTitle.value.trim()) {
    emit('rename', props.session.id, editTitle.value.trim())
  }
  editing.value = false
}

function cancelEdit() {
  editing.value = false
}
</script>

<style scoped>
.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-2);
  font-size: 0.83em;
  transition: background 0.12s, color 0.12s;
  white-space: nowrap;
  overflow: hidden;
  position: relative;
}

.session-item:hover {
  background: var(--bg-hover);
  color: var(--text);
}

.session-item.active {
  background: var(--bg-3);
  color: var(--text);
}

.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rename-input {
  flex: 1;
  background: var(--bg);
  border: 1px solid var(--accent-dim);
  border-radius: 4px;
  color: var(--text);
  font-size: inherit;
  font-family: inherit;
  padding: 1px 6px;
  outline: none;
  min-width: 0;
}

.item-actions {
  display: none;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
}

.session-item:hover .item-actions {
  display: flex;
}

.session-item.active .item-actions {
  display: flex;
}

.act-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--text-3);
  padding: 3px;
  border-radius: 3px;
  display: flex;
  align-items: center;
  transition: color 0.15s, background 0.15s;
}

.act-btn:hover {
  color: var(--text);
  background: var(--bg-2);
}

.act-btn.danger:hover {
  color: var(--red);
}
</style>