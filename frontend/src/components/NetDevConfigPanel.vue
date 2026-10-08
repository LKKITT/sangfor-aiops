<template>
  <div class="ndc-panel">
    <div class="ndc-toolbar">
      <el-button size="small" :loading="loading" @click="load(true)">
        <el-icon><Refresh /></el-icon>&nbsp;强制重新采集
      </el-button>
      <span v-if="collectedAt" class="ndc-time">采集于 {{ collectedAt.replace('T', ' ') }}</span>
      <span v-if="deviceName" class="ndc-dev">当前设备：<b>{{ deviceName }}</b></span>
    </div>

    <el-tabs v-if="sections.length" v-model="activeTab" class="ndc-tabs">
      <el-tab-pane v-for="s in filteredSections" :key="s.key" :name="s.key" lazy>
        <template #label>
          {{ s.title }}
          <el-badge v-if="s.key === 'config' && filter" :value="hitCount(s)" size="small" class="ndc-badge" />
        </template>
        <div class="ndc-sec-tools">
          <template v-if="s.key === 'config'">
            <el-input v-model="filter" size="small" clearable placeholder="本地过滤：输入关键词只看匹配行（如 interface / vlan）"
                      class="ndc-filter" />
            <span class="ndc-meta">{{ hitCount(s) }} / {{ lineCount(s) }} 行</span>
          </template>
          <span v-else class="ndc-meta mono">采集命令：{{ s.command }}</span>
          <el-button size="small" text type="primary" @click="copySection(s)">复制本节</el-button>
        </div>
        <div v-if="filter && s.key === 'config' && !hitCount(s)" class="ndc-empty">无匹配行</div>
        <pre class="ndc-pre mono">{{ s.key === 'config' && filter ? matchLines(s) : s.output }}</pre>
        <div v-if="s.truncated" class="ndc-trunc">本节输出超长已截断，完整内容请通过控制台/批量执行获取</div>
      </el-tab-pane>
    </el-tabs>

    <el-empty v-else-if="!loading" description="暂无快照数据，点击上方按钮采集" />
  </div>
</template>

<script>
// 模块级快照缓存（跨实例共享）：切换设备/重进页面先回放缓存，不重复 SSH 采集；
// 「强制重新采集」才实拉并更新缓存。
const ndSnapshotCache = new Map()   // device_id -> { sections, collectedAt }
</script>

<script setup>
// 网络设备配置可视化面板：只读命令分区快照（状态/接口/VLAN/路由/ARP/MAC/运行配置）。
// 运行配置支持前端本地行过滤（不打设备）；强制刷新走 force。
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { NetDev } from '../api.js'

const props = defineProps({ device: { type: Object, required: true } })

const loading = ref(false)
const sections = ref([])
const collectedAt = ref('')
const activeTab = ref('status')
const filter = ref('')

const deviceName = computed(() => props.device?.name || '')

const filteredSections = computed(() =>
  sections.value.map(s => (s.key === 'config' && filter.value ? { ...s } : s)))

const lineCount = (s) => s.output.split('\n').length
const matchLines = (s) => {
  const kw = filter.value.toLowerCase()
  return s.output.split('\n').filter(l => l.toLowerCase().includes(kw)).join('\n')
}
const hitCount = (s) => matchLines(s).split('\n').filter(Boolean).length

async function load(force = false) {
  const id = props.device?.id
  if (!id) return
  // 缓存回放：切换设备/重进页面不重复采集
  const cached = ndSnapshotCache.get(id)
  if (!force && cached) {
    sections.value = cached.sections
    collectedAt.value = cached.collectedAt
    if (!sections.value.some(s => s.key === activeTab.value)) activeTab.value = 'status'
    return
  }
  loading.value = true
  try {
    const r = await NetDev.snapshot(id, force)
    sections.value = r.sections || []
    collectedAt.value = r.collected_at || ''
    if (!sections.value.some(s => s.key === activeTab.value)) activeTab.value = 'status'
    ndSnapshotCache.set(id, { sections: sections.value, collectedAt: collectedAt.value })
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    loading.value = false
  }
}

// 设备切换：清理过滤态并加载目标设备数据（缓存优先，面板与选中设备强同步）
watch(() => props.device?.id, (nv, ov) => {
  if (!nv || nv === ov) return
  filter.value = ''
  activeTab.value = 'status'
  load(false)
})

async function copySection(s) {
  try {
    await navigator.clipboard.writeText(s.output)
    ElMessage.success(`已复制「${s.title}」`)
  } catch { ElMessage.error('复制失败') }
}

onMounted(() => load(false))
</script>

<style scoped>
.ndc-panel { animation: sfa-fade-up .3s var(--ease-out); }
.ndc-toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.ndc-time { font-size: 12px; color: var(--sfa-text-3); }
.ndc-dev { font-size: 12px; color: var(--sfa-text-3); margin-left: auto; }
.ndc-dev b { color: var(--sfa-text); font-weight: 650; }
.ndc-badge { margin-left: 4px; }
.ndc-sec-tools { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.ndc-filter { width: 320px; }
.ndc-meta { font-size: 12px; color: var(--sfa-text-3); }
.ndc-pre {
  margin: 0; padding: 12px 14px; border-radius: var(--sfa-r-md);
  background: var(--sfa-code-bg, #EEF1FA); color: var(--sfa-text);
  font-size: 12px; line-height: 1.6; overflow: auto; max-height: 62vh; white-space: pre;
}
.ndc-empty { text-align: center; color: var(--sfa-text-4); font-size: 12.5px; padding: 14px 0; }
.ndc-trunc { margin-top: 6px; font-size: 12px; color: var(--sfa-warning, #B88230); }
</style>
