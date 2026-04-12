<template>
  <button class="toggle" :class="{ on: modelValue }" @click="$emit('update:modelValue', !modelValue)" role="switch"
    :aria-checked="modelValue">
    <span class="knob" />
  </button>
</template>

<script setup>
defineProps({ modelValue: Boolean })
defineEmits(['update:modelValue'])
</script>

<style scoped>
.toggle {
  width: 40px;
  height: 22px;
  border-radius: 11px;
  border: none;
  background: var(--border-light);
  cursor: pointer;
  position: relative;
  transition: background 0.2s;
  flex-shrink: 0;
  padding: 0;
}

.toggle.on {
  background: var(--accent);
}

.knob {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.2s;
  display: block;
}

.toggle.on .knob {
  transform: translateX(18px);
}
</style>