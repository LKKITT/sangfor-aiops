<template>
  <div class="backup-page">
    <ContextBar />
    <div class="page-head">
      <div>
        <h2 class="ph-title">备份与恢复</h2>
        <p class="ph-desc">结构化配置快照（可对比 / 可恢复）+ 设备配置文件归档（.conf，SHA256 校验）· 每日 02:00 自动备份</p>
      </div>
      <div class="ph-actions" v-if="!netdevMode">
        <el-button type="primary" @click="createBackup" :loading="creating">
          <el-icon><Plus /></el-icon>&nbsp;立即备份
        </el-button>
      </div>
    </div>
    <NetDevBackupPanel v-if="netdevMode" :device="currentDevice()" />
    <el-alert v-else-if="globalMode" type="info" :closable="false" show-icon
              title="当前为全局模式：本页面需要指定具体设备，请在页顶设备切换器中选择一台深信服设备" />

    <div v-if="globalMode" class="page-card" style="margin-bottom: 12px">
      <div class="col-title" style="margin-bottom: 8px">全部设备备份总览</div>
      <div v-if="bkOverview" style="display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 10px">
        <div class="sfa-stat"><div class="num">{{ bkOverview.total_backups }}</div><div class="lbl">备份总数</div></div>
        <div class="sfa-stat"><div class="num">{{ bkOverview.devices }}</div><div class="lbl">有备份的设备</div></div>
      </div>
      <el-table v-if="bkOverview && bkOverview.items.length" :data="bkOverview.items" size="small" border stripe
                style="cursor: pointer" @row-click="(r) => drillDevice(r.device_id)">
        <el-table-column prop="device_name" label="设备" min-width="180" />
        <el-table-column prop="count" label="备份数" width="90" align="center" />
        <el-table-column prop="latest_at" label="最近备份" min-width="170" />
        <el-table-column prop="latest_label" label="最近备份标签" min-width="200" show-overflow-tooltip />
        <el-table-column label="操作" width="90" align="center">
          <template #default><el-button size="small" link type="primary">进入 →</el-button></template>
        </el-table-column>
      </el-table>
      <div v-else style="opacity: .7">暂无任何备份记录；选择具体设备后可创建首个备份</div>
    </div>

    <div v-if="!netdevMode && !globalMode" class="cols">
      <div class="page-card col-list">
        <div class="col-title">备份时间线</div>
        <el-timeline>
          <el-timeline-item v-for="b in backups" :key="b.id" :timestamp="b.created_at"
                             :type="b.kind === 'pre_change' ? 'warning' : b.kind === 'scheduled' ? 'info' : 'primary'">
            <div class="bk-card" :class="{ 'bk-highlight': b.id === highlightId }">
              <div style="font-weight: 600">{{ b.label }}</div>
              <div class="bk-meta">
                <el-tag size="small">{{ b.sw_version }}</el-tag>
                <el-tag size="small" type="info">{{ kindName(b.kind) }}</el-tag>
                <el-tag v-if="b.file_sha256" size="small" type="success">含配置文件</el-tag>
              </div>
              <div class="bk-actions">
                <el-button size="small" text type="primary" @click="generateReport(b)">生成报告</el-button>
                <el-button size="small" text type="success" @click="exportSnapshot(b)">导出快照 JSON</el-button>
                <el-button size="small" text type="warning" @click="previewRestore(b)">
                  {{ b.id === highlightId ? '从此回退点恢复' : '恢复此备份' }}
                </el-button>
                <el-button size="small" text type="danger" @click="removeBackup(b)">删除</el-button>
              </div>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-if="!backups.length" description="暂无备份，点击上方按钮创建" />
      </div>

      <div class="page-card col-diff">
        <div class="col-title">快照对比</div>
        <div style="display: flex; gap: 8px; margin-bottom: 10px">
          <el-select v-model="diffA" placeholder="基准备份" size="small" style="flex: 1">
            <el-option v-for="b in backups" :key="b.id" :value="b.id" :label="`${b.created_at} ${b.label}`" />
          </el-select>
          <el-select v-model="diffB" placeholder="对比备份" size="small" style="flex: 1">
            <el-option v-for="b in backups" :key="b.id" :value="b.id" :label="`${b.created_at} ${b.label}`" />
          </el-select>
          <el-button size="small" type="primary" :disabled="!diffA || !diffB" @click="doDiff">对比</el-button>
        </div>
        <el-empty v-if="!diff" description="选择两份备份进行差异对比" />
        <div v-else>
          <div style="margin-bottom: 8px; font-size: 13px">
            变更合计：<el-tag type="success" size="small">新增 {{ diff.diff.summary.added }}</el-tag>
            <el-tag type="danger" size="small">删除 {{ diff.diff.summary.removed }}</el-tag>
            <el-tag type="warning" size="small">修改 {{ diff.diff.summary.changed }}</el-tag>
          </div>
          <div v-for="(sec, name) in diff.diff.sections" :key="name">
            <div v-if="sec.added.length || sec.removed.length || sec.changed.length" class="diff-sec">
              <div class="diff-sec-title">{{ sectionName(name) }}</div>
              <div v-for="r in sec.removed" :key="'rm' + r.id" class="diff-removed mono diff-line">- {{ name }} {{ r.name }}（{{ r.id }}）已删除/不存在</div>
              <div v-for="r in sec.added" :key="'ad' + r.id" class="diff-added mono diff-line">+ {{ name }} {{ r.name }}（{{ r.id }}）新增</div>
              <div v-for="r in sec.changed" :key="'ch' + r.id" class="diff-changed mono diff-line">
                ~ {{ name }} {{ r.name }}（{{ r.id }}）：
                <span v-for="f in r.fields" :key="f.field" style="margin-right: 8px">
                  {{ f.field }}: <del>{{ f.old }}</del> → <b>{{ f.new }}</b>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="restoreDialog" title="恢复配置 — 变更计划预览" width="640px" append-to-body>
      <el-alert type="warning" :closable="false" style="margin-bottom: 10px"
                title="执行前系统会自动生成安全备份；失败即停，可随时用安全备份回退" />
      <div v-if="restorePlan">
        目标备份：<b>{{ restorePlan.label }}</b>（{{ restorePlan.backup_sw_version }}）
        <el-divider style="margin: 10px 0" />
        共 <b>{{ restorePlan.total }}</b> 项变更：
        <el-tag type="danger" size="small" style="margin: 2px">删除 {{ restorePlan.delete.length }}</el-tag>
        <el-tag type="warning" size="small" style="margin: 2px">修改 {{ restorePlan.update.length }}</el-tag>
        <el-tag type="success" size="small" style="margin: 2px">重建 {{ restorePlan.create.length }}</el-tag>
        <el-collapse style="margin-top: 8px">
          <el-collapse-item title="详细清单">
            <div v-for="it in restorePlan.delete" :key="'d' + it.target_id" class="mono diff-removed diff-line">
              - 删除 {{ it.resource_cn }} {{ it.name }}（{{ it.target_id }}）
            </div>
            <div v-for="it in restorePlan.update" :key="'u' + it.target_id" class="mono diff-changed diff-line">
              ~ 修改 {{ it.resource_cn }} {{ it.name }}（{{ it.target_id }}）
            </div>
            <div v-for="it in restorePlan.create" :key="'c' + it.target_id" class="mono diff-added diff-line">
              + 重建 {{ it.resource_cn }} {{ it.name }}（{{ it.target_id }}）
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
      <template #footer>
        <el-button @click="restoreDialog = false">取消</el-button>
        <el-button type="warning" :loading="restoring" @click="applyRestore">确认恢复</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import { store, currentDevice, isNetDev, isGlobal } from '../store.js'
import { apiGet } from '../api.js'
import NetDevBackupPanel from '../components/NetDevBackupPanel.vue'
import ContextBar from '../components/ContextBar.vue'
import { Backups } from '../api.js'

const backups = ref([])
const creating = ref(false)
const diffA = ref('')
const diffB = ref('')
const diff = ref(null)
const restoreDialog = ref(false)
const restorePlan = ref(null)
const restoreTarget = ref(null)
const restoring = ref(false)

const route = useRoute()
// 回退点深链高亮：确认卡片"查看回退点"带 ?highlight=bk_x 跳转，直达对应备份行
const highlightId = ref('')

const netdevMode = computed(() => isNetDev(currentDevice()))
const globalMode = computed(() => isGlobal(currentDevice()))
// 全局模式：全部设备备份总览
const bkOverview = ref(null)
async function loadBkOverview() {
  try { bkOverview.value = await apiGet('/api/backups-overview') } catch { bkOverview.value = null }
}
watch(globalMode, v => { if (v) loadBkOverview() }, { immediate: true })
function drillDevice(id) { store.currentDeviceId = id }
const guardText = computed(() => netdevMode.value
  ? '当前选中的是网络设备：配置备份/恢复仅支持深信服设备；网络设备可用 AI 对话查询配置'
  : '当前为全局模式：本页面需要指定具体设备，请在页顶设备切换器中选择一台深信服设备')
const kindName = k => ({ manual: '手动', scheduled: '自动', pre_change: '变更前安全备份' }[k] || k)
const sectionName = s => ({ objects: '网络对象', services: '自定义服务', user_bindings: '用户绑定', acl_rules: '访问控制策略', nat_rules: 'NAT 策略', static_routes: '静态路由', interfaces: '网络接口' }[s] || s)

async function load() {
  const dev = currentDevice()
  if (!dev || isNetDev(dev) || isGlobal(dev)) return
  backups.value = await Backups.list(dev.id)
  // 已有两份备份时自动选最近两份做对比，方便快速查看差异
  if (!diffA.value && !diffB.value && backups.value.length >= 2) {
    diffA.value = backups.value[1].id
    diffB.value = backups.value[0].id
    doDiff()
  }
}

watch(() => store.currentDeviceId, () => { diff.value = null; diffA.value = ''; diffB.value = ''; load() })
onMounted(async () => {
  await load()
  highlightId.value = String(route.query.highlight || '')
  if (highlightId.value) {
    const hit = backups.value.find(b => b.id === highlightId.value)
    if (!hit) {
      ElMessage.warning('该回退点属于其他设备：请先在侧栏切换到对应设备，再打开此链接')
    } else {
      await nextTick()
      document.querySelector('.bk-highlight')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }
})

async function createBackup() {
  const dev = currentDevice()
  if (!dev || isNetDev(dev) || isGlobal(dev)) return
  creating.value = true
  try {
    await Backups.create(dev.id, `手动备份 ${new Date().toLocaleString('zh-CN')}`)
    ElMessage.success('备份完成')
    await load()
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { creating.value = false }
}

function generateReport(b) {
  const dev = currentDevice()
  window.open(Backups.reportUrl(dev.id, b.id), '_blank')
}

function exportSnapshot(b) {
  const dev = currentDevice()
  // 可读 JSON 快照（网络对象/服务/路由/策略/绑定全量），供第三方设备迁移或审计存档
  window.open(Backups.exportUrl(dev.id, b.id), '_blank')
}

async function removeBackup(b) {
  const dev = currentDevice()
  await ElMessageBox.confirm(`确认删除备份「${b.label}」？`, '提示', { type: 'warning' })
  await Backups.remove(dev.id, b.id)
  load()
}

async function doDiff() {
  const dev = currentDevice()
  diff.value = await Backups.diff(diffA.value, diffB.value)
}

async function previewRestore(b) {
  const dev = currentDevice()
  restoreTarget.value = b
  restorePlan.value = await Backups.restorePreview(dev.id, b.id)
  restoreDialog.value = true
}

async function applyRestore() {
  const dev = currentDevice()
  restoring.value = true
  try {
    const result = await Backups.restoreApply(dev.id, restoreTarget.value.id)
    if (result.ok) {
      ElMessage.success(`恢复完成（${result.executed.length} 项变更，安全备份 ${result.safety_backup_id}）`)
    } else {
      ElMessage.error(`恢复部分失败：${result.errors[0]?.error}，已停止；可用安全备份回退`)
    }
    restoreDialog.value = false
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { restoring.value = false }
}
</script>

<style scoped>
.backup-page { animation: sfa-fade-up .3s var(--ease-out); }
.cols { display: flex; gap: 16px; align-items: flex-start; }
.col-list { flex: 1; min-width: 380px; }
.col-diff { flex: 1.2; min-height: 300px; }
.col-title { font-weight: 650; margin-bottom: 12px; }

.bk-card {
  border: 1px solid var(--sfa-border); border-radius: var(--sfa-r-md);
  padding: 10px 13px; background: var(--sfa-panel-soft);
  transition: transform var(--dur-2) var(--ease-out), box-shadow var(--dur-2), border-color var(--dur-2);
}
.bk-card:hover { transform: translateY(-1px); box-shadow: var(--sfa-shadow-2); border-color: #D6DDF0; }
.bk-meta { margin: 7px 0; display: flex; gap: 6px; flex-wrap: wrap; }
.bk-actions { display: flex; flex-wrap: wrap; gap: 2px; }

/* 时间线节点与连线令牌化 */
.col-list :deep(.el-timeline-item__wrapper) { padding-left: 22px; }
.col-list :deep(.el-timeline-item__timestamp) { color: var(--sfa-text-4); font-size: 11.5px; font-family: var(--sfa-mono); }
.col-list :deep(.el-timeline-item__node) { box-shadow: 0 0 0 3px rgba(59, 99, 255, .12); }

.diff-sec { margin-bottom: 14px; }
.diff-sec-title { font-weight: 650; margin-bottom: 6px; font-size: 12.5px; color: var(--sfa-text-2); }
.diff-line { padding: 2.5px 0; font-size: 12px; }

/* 回退点深链高亮：确认卡片跳转直达的备份行 */
.bk-highlight {
  outline: 2px solid var(--sfa-warning, #F6C344);
  outline-offset: 2px;
  border-radius: 10px;
  animation: bk-glow 1.2s ease-in-out 3;
}
@keyframes bk-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(246, 195, 68, 0); }
  50% { box-shadow: 0 0 14px 2px rgba(246, 195, 68, .45); }
}
</style>
