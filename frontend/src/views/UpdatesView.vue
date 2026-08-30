<template>
  <div class="updates-page">
    <div class="page-card" style="margin-bottom: 12px; display: flex; gap: 10px; align-items: center">
      <el-button @click="refresh" :loading="refreshing">
        <el-icon><Refresh /></el-icon> 刷新官方更新信息
      </el-button>
      <span class="hint">
        数据来源：深信服技术支持平台（正文需认证，可配置 Cookie 抓取）+ 官网 PSIRT 安全公告（公开）+ 内置版本知识库快照。
      </span>
    </div>

    <div v-if="advice">
      <!-- 结论卡片 -->
      <div class="page-card" style="margin-bottom: 12px">
        <div class="advice-head">
          <div>
            <div class="advice-title">
              <el-tag :type="riskType" size="large">{{ advice.recommendation }}</el-tag>
              <span style="margin-left: 10px" class="mono">
                {{ advice.current_version }} → {{ advice.latest_version }}
              </span>
              <el-tag v-if="advice.up_to_date" type="success" size="small" style="margin-left: 8px">已是最新</el-tag>
            </div>
            <div class="advice-sub">{{ advice.product_name }} · {{ advice.device_name }}</div>
          </div>
          <div class="advice-path">
            <div class="path-label">升级路径</div>
            <div class="path-steps">
              <template v-for="(hop, i) in advice.upgrade_path.hops" :key="hop">
                <el-tag v-if="i" size="small" type="info">→</el-tag>
                <el-tag size="small" :type="hop === advice.latest_version ? 'success' : 'info'">{{ hop }}</el-tag>
              </template>
              <el-tag v-if="!advice.upgrade_path.hops.length" size="small" type="success">无需升级</el-tag>
            </div>
            <el-alert v-if="advice.upgrade_path.cross_arch_migration" type="error" :closable="false"
                      style="margin-top: 8px; font-size: 12px"
                      :title="advice.upgrade_path.notes[0]" />
          </div>
        </div>
        <!-- 升级理由 -->
        <div class="reasons">
          <div v-for="(r, i) in advice.reasons" :key="i" class="reason">
            <el-tag :type="r.level === 'high' ? 'danger' : r.level === 'medium' ? 'warning' : r.level === 'info' ? 'success' : 'info'" size="small">
              {{ r.type }}
            </el-tag>
            <span style="margin-left: 8px; font-size: 13px">{{ r.text }}</span>
          </div>
        </div>
      </div>

      <div class="cols">
        <!-- 关键变更点 -->
        <div class="page-card col-changes">
          <div class="col-title">本次更新的关键变更点（{{ advice.current_version }} → {{ advice.latest_version }}）</div>
          <el-tabs>
            <el-tab-pane v-for="(items, cat) in advice.key_changes" :key="cat"
                         :label="`${cat}（${items.length}）`" :disabled="!items.length">
              <div v-for="(c, i) in items" :key="i" class="change-item">
                <el-icon :class="catIcon(cat)"><component :is="catIconName(cat)" /></el-icon>
                <span>{{ c }}</span>
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>

        <!-- 时机与清单 -->
        <div class="page-card col-timing">
          <div class="col-title">升级时机与前置条件</div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="业务影响">{{ advice.timing.interruption }}</el-descriptions-item>
            <el-descriptions-item label="建议窗口">{{ advice.timing.window }}</el-descriptions-item>
            <el-descriptions-item label="双机环境">{{ advice.timing.ha }}</el-descriptions-item>
          </el-descriptions>
          <div class="col-title" style="margin-top: 14px">行动清单</div>
          <el-timeline>
            <el-timeline-item v-for="s in advice.checklist" :key="s.step"
                              :type="s.agent_action ? 'primary' : undefined">
              {{ s.step }}. {{ s.action }}
              <el-tag v-if="s.agent_action" size="small" style="margin-left: 4px">可由 Agent 执行</el-tag>
            </el-timeline-item>
          </el-timeline>
          <el-alert type="info" :closable="false" style="margin-top: 8px; font-size: 12px"
                    title="升级固件操作本身涉及业务中断，须由工程师在维护窗口执行；Agent 负责建议、备份与检查。" />
        </div>
      </div>
    </div>
    <div v-else>
      <el-empty description="加载中" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { store, currentDevice } from '../store.js'
import { Updates } from '../api.js'

const advice = ref(null)
const refreshing = ref(false)

const riskType = computed(() => ({ high: 'danger', medium: 'warning', low: 'info' }[advice.value?.risk] || 'info'))
const catIconName = cat => ({ 新增功能: 'CirclePlusFilled', 安全修复: 'WarningFilled', 已知问题修复: 'CircleCheckFilled', 优化: 'TopRight' }[cat] || 'InfoFilled')
const catIcon = cat => ({ 新增功能: 'c-green', 安全修复: 'c-red', 已知问题修复: 'c-blue', 优化: 'c-orange' }[cat] || '')

async function load() {
  const dev = currentDevice()
  if (!dev) return
  try { advice.value = await Updates.advice(dev.id) } catch (e) { console.error(e) }
}

async function refresh() {
  refreshing.value = true
  try {
    const r = await Updates.refresh()
    ElMessage.info(`官方平台：${r.official.status}${r.official.reason ? '（' + r.official.reason + '）' : ''}；PSIRT：${r.psirt.status}`)
    await load()
  } finally { refreshing.value = false }
}

watch(() => store.currentDeviceId, load)
onMounted(load)
</script>

<style scoped>
.cols { display: flex; gap: 12px; align-items: flex-start; }
.col-changes { flex: 1.2; }
.col-timing { flex: 1; }
.col-title { font-weight: 600; margin-bottom: 10px; }
.advice-head { display: flex; justify-content: space-between; gap: 20px; flex-wrap: wrap; }
.advice-title { font-size: 16px; font-weight: 600; display: flex; align-items: center; }
.advice-sub { color: #909399; font-size: 12px; margin-top: 6px; }
.path-label { color: #909399; font-size: 12px; margin-bottom: 6px; }
.path-steps { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; max-width: 460px; }
.reasons { margin-top: 14px; display: flex; flex-direction: column; gap: 6px; }
.reason { display: flex; align-items: center; }
.change-item { padding: 6px 0; font-size: 13px; display: flex; gap: 8px; align-items: baseline; border-bottom: 1px dashed #f0f2f5; }
.c-green { color: #67c23a; }
.c-red { color: #f56c6c; }
.c-blue { color: #409eff; }
.c-orange { color: #e6a23c; }
.hint { color: #909399; font-size: 12px; }
.mono { font-family: Consolas, monospace; }
</style>
