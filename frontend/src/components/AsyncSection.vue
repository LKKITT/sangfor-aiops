<template>
  <div v-if="state === 'loading'" class="as-block">
    <el-skeleton :rows="rows" animated />
  </div>
  <div v-else-if="state === 'error'" class="as-block as-error">
    <el-icon class="as-err-ico" :size="26"><WarningFilled /></el-icon>
    <p class="as-err-text">{{ errorMessage }}</p>
    <el-button size="small" type="primary" plain @click="run">重试</el-button>
  </div>
  <div v-else-if="state === 'empty'" class="as-block as-empty-box">
    <el-empty :description="emptyText" />
  </div>
  <slot v-else :data="data" />
</template>

<script setup>
// 请求四态容器：loading 骨架 / error+重试 / empty / 内容。
// load 返回数据本体；emptyWhen 判定空态（缺省为数组为空）；视图副作用经 loaded 事件回传。
import { ref, onMounted } from 'vue'

const props = defineProps({
  load: { type: Function, required: true },
  emptyWhen: { type: Function, default: null },
  rows: { type: Number, default: 5 },
  immediate: { type: Boolean, default: true },
  emptyText: { type: String, default: '暂无数据' },
})
const emit = defineEmits(['loaded'])

const state = ref('loading')   // loading | error | empty | ready
const errorMessage = ref('')
const data = ref(null)

async function run() {
  state.value = 'loading'
  errorMessage.value = ''
  try {
    const result = await props.load()
    data.value = result
    const empty = props.emptyWhen ? !!props.emptyWhen(result) : Array.isArray(result) && result.length === 0
    state.value = empty ? 'empty' : 'ready'
    emit('loaded', result)
  } catch (e) {
    errorMessage.value = String(e.message || e)
    state.value = 'error'
  }
}

if (props.immediate) onMounted(run)
defineExpose({ run, state, data })
</script>

<style scoped>
.as-block { padding: 8px 0; }
.as-error { text-align: center; padding: 18px 0; }
.as-err-ico { color: var(--sfa-danger, #E5484D); }
.as-err-text { color: var(--sfa-text-3, #909399); font-size: 12.5px; margin: 8px 0 10px; }
.as-empty-box :deep(.el-empty) { padding: 18px 0; }
</style>
