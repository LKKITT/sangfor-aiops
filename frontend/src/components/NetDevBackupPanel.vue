<template>
  <div class="ndb-panel">
    <el-alert type="warning" :closable="false" show-icon class="ndb-guard"
              title="网络设备备份仅作配置存档与差异对比，不支持一键恢复——配置回退请通过控制台或批量执行人工核对后进行" />

    <div class="ndb-toolbar">
      <el-input v-model="label" size="small" placeholder="备份标签（选填）" style="width: 220px" />
      <el-button type="primary" size="small" :loading="creating" @click="create">
        <el-icon><Plus /></el-icon>&nbsp;立即备份
      </el-button>
      <el-button size="small" :disabled="selA === selB || !selA || !selB" @click="doDiff">对比所选</el-button>
    </div>

    <el-empty v-if="!backups.length && !creating" description="暂无备份，点击上方按钮创建" />
    <el-timeline v-else class="ndb-list">
      <el-timeline-item v-for="b in backups" :key="b.id" :timestamp="b.created_at" type="primary">
        <div class="ndb-card" :class="{ 'ndb-highlight': b.id === highlightId }">
          <div class="ndb-check">
            <el-checkbox v-model="checks[b.id]" :disabled="!checks[b.id] && selCount >= 2">对比</el-checkbox>
          </div>
          <div style="flex: 1; min-width: 0">
            <div style="font-weight: 600">{{ b.label }}</div>
            <div class="ndb-meta">
              <el-tag size="small">{{ b.sw_version || '未知版本' }}</el-tag>
              <el-tag v-if="b.file_sha256" size="small" type="success">SHA256 校验</el-tag>
            </div>
            <div class="ndb-actions">
              <el-button size="small" text type="primary" @click="download(b)">下载 .conf</el-button>
              <el-button size="small" text type="danger" @click="remove(b)">删除</el-button>
            </div>
          </div>
        </div>
      </el-timeline-item>
    </el-timeline>

    <el-dialog v-model="diffDialog" title="配置差异对比（unified diff）" width="760px" top="4vh" append-to-body>
      <div class="ndb-diff-sum" v-if="diff">
        新增 <b class="add">{{ diff.added }}</b> 行 · 删除 <b class="del">{{ diff.removed }}</b> 行
      </div>
      <pre v-if="diff" class="ndb-diff mono">{{ diff.diff }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
// 网络设备备份面板：配置文本存档（.conf + SHA256）与差异对比。
// 红线：无恢复按钮/无恢复调用——回退必须经控制台或批量执行人工核对。
import { computed, reactive, ref, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import { NetDev } from '../api.js'

const props = defineProps({ device: { type: Object, required: true } })
const route = useRoute()

const backups = ref([])
const creating = ref(false)
const label = ref('')
const checks = reactive({})
const diff = ref(null)
const diffDialog = ref(false)
const highlightId = ref('')

const selA = computed(() => Object.keys(checks).find(k => checks[k]) || '')
const selB = computed(() => Object.keys(checks).filter(k => checks[k])[1] || '')
const selCount = computed(() => Object.values(checks).filter(Boolean).length)

async function load(highlight = '') {
  backups.value = await NetDev.backups(props.device.id)
  highlightId.value = highlight
  if (highlight) {
    const hit = backups.value.find(b => b.id === highlight)
    if (!hit) {
      ElMessage.warning('该回退点属于其他设备：请先切换到对应设备再打开此链接')
    } else {
      await nextTick()
      document.querySelector('.ndb-highlight')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }
}

async function create() {
  creating.value = true
  try {
    const r = await NetDev.createBackup(props.device.id, label.value)
    ElMessage.success(`备份完成：${r.label}`)
    label.value = ''
    await load()
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    creating.value = false
  }
}

function download(b) {
  const a = document.createElement('a')
  a.href = NetDev.backupFileUrl(props.device.id, b.id)
  a.download = `${b.label}.conf`
  a.click()
}

async function remove(b) {
  await ElMessageBox.confirm(`删除备份「${b.label}」？该操作不可恢复`, '确认删除', { type: 'warning' })
  await NetDev.deleteBackup(props.device.id, b.id)
  ElMessage.success('已删除')
  await load()
}

async function doDiff() {
  try {
    diff.value = await NetDev.backupDiff(props.device.id, selA.value, selB.value)
    diffDialog.value = true
  } catch (e) {
    ElMessage.error(String(e.message || e))
  }
}

onMounted(() => {
  const hl = String(route.query.highlight || '')
  load(hl).catch(e => ElMessage.error(String(e.message || e)))
})
</script>

<style scoped>
.ndb-guard { margin-bottom: 12px; }
.ndb-toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; }
.ndb-list { margin-top: 4px; }
.ndb-card { display: flex; gap: 10px; align-items: flex-start; padding: 10px 13px;
            border: 1px solid var(--sfa-border-soft); border-radius: 10px; background: var(--sfa-surface); }
.ndb-card.ndb-highlight { outline: 2px solid var(--sfa-warning, #F6C344); outline-offset: 2px;
                          animation: ndb-glow 1.2s ease-in-out 3; }
@keyframes ndb-glow { 50% { box-shadow: 0 0 14px 2px rgba(246,195,68,.45); } }
.ndb-check { padding-top: 4px; }
.ndb-meta { margin: 7px 0; display: flex; gap: 6px; flex-wrap: wrap; }
.ndb-actions { display: flex; gap: 2px; }
.ndb-diff-sum { margin-bottom: 8px; font-size: 13px; }
.ndb-diff-sum .add { color: #0E9F6E; }
.ndb-diff-sum .del { color: var(--sfa-danger); }
.ndb-diff {
  margin: 0; padding: 12px; border-radius: 9px; max-height: 62vh; overflow: auto;
  background: var(--sfa-code-bg, #EEF1FA); font-size: 12px; line-height: 1.6; white-space: pre;
}
</style>
