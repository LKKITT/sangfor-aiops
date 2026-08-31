<template>
  <div class="backup-page">
    <div class="page-card" style="margin-bottom: 12px; display: flex; gap: 10px; align-items: center">
      <el-button type="primary" @click="createBackup" :loading="creating">
        <el-icon><Plus /></el-icon> 立即备份
      </el-button>
      <span class="hint">备份 = 结构化配置快照（可对比/可恢复） + 设备配置文件归档（.conf，SHA256 校验）。每日 {{ '02:00' }} 自动备份。</span>
    </div>

    <div class="cols">
      <div class="page-card col-list">
        <div class="col-title">备份时间线</div>
        <el-timeline>
          <el-timeline-item v-for="b in backups" :key="b.id" :timestamp="b.created_at"
                             :type="b.kind === 'pre_change' ? 'warning' : b.kind === 'scheduled' ? 'info' : 'primary'">
            <div class="bk-card">
              <div style="font-weight: 600">{{ b.label }}</div>
              <div class="bk-meta">
                <el-tag size="small">{{ b.sw_version }}</el-tag>
                <el-tag size="small" type="info">{{ kindName(b.kind) }}</el-tag>
                <el-tag v-if="b.file_sha256" size="small" type="success">含配置文件</el-tag>
              </div>
              <div class="bk-actions">
                <el-button size="small" text type="primary" @click="download(b)">下载配置文件</el-button>
                <el-button size="small" text type="success" @click="exportSnapshot(b)">导出快照 JSON</el-button>
                <el-button size="small" text type="warning" @click="previewRestore(b)">恢复此备份</el-button>
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

    <el-dialog v-model="restoreDialog" title="恢复配置 — 变更计划预览" width="640px">
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
import { ref, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { store, currentDevice } from '../store.js'
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

const kindName = k => ({ manual: '手动', scheduled: '自动', pre_change: '变更前安全备份' }[k] || k)
const sectionName = s => ({ objects: '网络对象', services: '自定义服务', user_bindings: '用户绑定', acl_rules: '访问控制策略', nat_rules: 'NAT 策略', static_routes: '静态路由', interfaces: '网络接口' }[s] || s)

async function load() {
  const dev = currentDevice()
  if (!dev) return
  backups.value = await Backups.list(dev.id)
  // 已有两份备份时自动选最近两份做对比，方便快速查看差异
  if (!diffA.value && !diffB.value && backups.value.length >= 2) {
    diffA.value = backups.value[1].id
    diffB.value = backups.value[0].id
    doDiff()
  }
}

watch(() => store.currentDeviceId, () => { diff.value = null; diffA.value = ''; diffB.value = ''; load() })
onMounted(load)

async function createBackup() {
  const dev = currentDevice()
  if (!dev) return
  creating.value = true
  try {
    await Backups.create(dev.id, `手动备份 ${new Date().toLocaleString('zh-CN')}`)
    ElMessage.success('备份完成')
    await load()
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { creating.value = false }
}

function download(b) {
  const dev = currentDevice()
  window.open(Backups.downloadUrl(dev.id, b.id), '_blank')
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
.cols { display: flex; gap: 12px; align-items: flex-start; }
.col-list { flex: 1; min-width: 380px; }
.col-diff { flex: 1.2; min-height: 300px; }
.col-title { font-weight: 600; margin-bottom: 12px; }
.hint { color: #909399; font-size: 12px; }
.bk-card { border: 1px solid #ebeef5; border-radius: 8px; padding: 8px 12px; }
.bk-meta { margin: 6px 0; display: flex; gap: 6px; }
.bk-actions { display: flex; }
.diff-sec { margin-bottom: 12px; }
.diff-sec-title { font-weight: 600; margin-bottom: 4px; font-size: 13px; }
.diff-line { padding: 2px 0; font-size: 12px; }
</style>
