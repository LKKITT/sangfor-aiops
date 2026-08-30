<template>
  <div class="checkup-page">
    <div class="page-card" style="margin-bottom: 12px; display: flex; gap: 12px; align-items: center">
      <el-button type="primary" @click="runCheckup" :loading="running">
        <el-icon><Odometer /></el-icon> 运行配置体检
      </el-button>
      <span class="hint">确定性规则引擎：规则冲突（遮蔽/矛盾）/ 空策略 / 过宽权限 / 高危端口暴露 / 资源异常。部分风险支持一键修复（进入对话确认流）。</span>
    </div>

    <div v-if="report">
      <div class="cols">
        <div class="page-card score-card">
          <div class="score-num" :style="{ color: scoreColor }">{{ report.score }}</div>
          <div class="score-grade">{{ report.grade }}</div>
          <div class="score-meta">{{ report.meta.device_name }} · {{ report.meta.sw_version }}</div>
          <div class="score-counts">
            <div class="cnt high"><div class="cnt-num">{{ report.counts.high }}</div>高危</div>
            <div class="cnt medium"><div class="cnt-num">{{ report.counts.medium }}</div>中危</div>
            <div class="cnt low"><div class="cnt-num">{{ report.counts.low }}</div>低危</div>
          </div>
          <el-button style="margin-top: 14px" type="warning" plain @click="aiFix">
            <el-icon><MagicStick /></el-icon> 让 AI 一键修复可自动处理的风险
          </el-button>
        </div>

        <div class="page-card items-card">
          <el-collapse v-model="openSeverities">
            <el-collapse-item v-for="sev in ['high', 'medium', 'low']" :key="sev" :name="sev">
              <template #title>
                <el-tag :type="sevType(sev)" size="small" style="margin-right: 8px">{{ sevName(sev) }}</el-tag>
                {{ sevItems(sev).length }} 项
              </template>
              <div v-for="item in sevItems(sev)" :key="item.check_id + (item.rule_ids || []).join()" class="risk-item">
                <div class="risk-title">
                  <el-tag :type="item.auto_fix ? 'warning' : 'info'" size="small">{{ item.category }}</el-tag>
                  <b style="margin-left: 6px">{{ item.title }}</b>
                  <el-tag v-if="item.auto_fix" size="small" type="success" style="margin-left: 6px">可一键修复</el-tag>
                </div>
                <div class="risk-row"><span class="risk-k">说明：</span>{{ item.evidence }}</div>
                <div class="risk-row"><span class="risk-k">建议：</span>{{ item.suggestion }}</div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>
    </div>
    <div v-else>
      <el-empty description="点击上方按钮运行体检" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { store, currentDevice } from '../store.js'
import { Devices } from '../api.js'

const report = ref(null)
const running = ref(false)
const openSeverities = ref(['high', 'medium', 'low'])
const scoreColor = computed(() => (report.value?.score >= 90 ? '#67c23a' : report.value?.score >= 75 ? '#409eff' : report.value?.score >= 60 ? '#e6a23c' : '#f56c6c'))

const sevName = s => ({ high: '高危', medium: '中危', low: '低危' }[s])
const sevType = s => ({ high: 'danger', medium: 'warning', low: 'info' }[s])
const sevItems = s => (report.value?.items || []).filter(i => i.severity === s)

async function runCheckup() {
  const dev = currentDevice()
  if (!dev) return
  running.value = true
  try {
    report.value = await Devices.checkup(dev.id)
    ElMessage.success(`体检完成：${report.value.grade}（${report.value.score} 分）`)
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { running.value = false }
}

async function aiFix() {
  store.view = 'chat'
  ElMessage.info('已切换到对话页，发送"修复体检发现的可自动处理风险"即可执行')
}

watch(() => store.currentDeviceId, () => { report.value = null })
onMounted(async () => {
  const dev = currentDevice()
  if (dev) {
    const last = await Devices.lastCheckup(dev.id).catch(() => null)
    if (last && last.score !== undefined) report.value = last
  }
})
</script>

<style scoped>
.cols { display: flex; gap: 12px; align-items: flex-start; }
.score-card { width: 240px; text-align: center; padding: 24px 16px; }
.score-num { font-size: 52px; font-weight: 700; line-height: 1.1; }
.score-grade { font-size: 16px; font-weight: 600; margin-top: 4px; }
.score-meta { color: #909399; font-size: 12px; margin: 8px 0 14px; }
.score-counts { display: flex; justify-content: space-around; }
.cnt { font-size: 12px; color: #909399; }
.cnt-num { font-size: 22px; font-weight: 700; }
.cnt.high .cnt-num { color: #f56c6c; }
.cnt.medium .cnt-num { color: #e6a23c; }
.cnt.low .cnt-num { color: #909399; }
.items-card { flex: 1; min-height: 300px; }
.risk-item { border-bottom: 1px dashed #ebeef5; padding: 10px 4px; }
.risk-title { margin-bottom: 4px; font-size: 14px; }
.risk-row { font-size: 13px; color: #606266; line-height: 1.6; }
.risk-k { color: #909399; }
.hint { color: #909399; font-size: 12px; }
</style>
