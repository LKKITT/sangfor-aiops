<template>
  <div class="ndk-panel">
    <div class="ndk-toolbar">
      <el-button type="primary" size="small" :loading="running" @click="run(true)">
        <el-icon><Odometer /></el-icon>&nbsp;运行体检
      </el-button>
      <el-button size="small" @click="run(false)" :disabled="running">读取上次结果</el-button>
      <span v-if="checkedAt" class="ndk-time">体检于 {{ checkedAt.replace('T', ' ') }}</span>
    </div>

    <template v-if="report">
      <div class="ndk-summary">
        <div class="ndk-score" :class="scoreCls">
          <div class="num">{{ report.score }}</div>
          <div class="lbl">{{ report.grade }} · 网络设备配置体检</div>
        </div>
        <div class="ndk-counts">
          <div class="cnt high"><b>{{ report.counts.high }}</b>高危</div>
          <div class="cnt medium"><b>{{ report.counts.medium }}</b>中危</div>
          <div class="cnt low"><b>{{ report.counts.low }}</b>低危</div>
        </div>
      </div>

      <el-empty v-if="!report.items.length" description="未发现风险项，配置状态良好" />
      <div v-for="(it, i) in report.items" :key="i" class="ndk-item">
        <div class="ndk-item-head">
          <el-tag :type="it.severity === 'high' ? 'danger' : it.severity === 'medium' ? 'warning' : 'info'"
                  size="small" effect="dark">
            {{ { high: '高危', medium: '中危', low: '低危' }[it.severity] }}
          </el-tag>
          <b>{{ it.title }}</b>
        </div>
        <div v-if="it.detail" class="ndk-detail mono">{{ it.detail }}</div>
        <div class="ndk-sug">→ {{ it.suggestion }}</div>
      </div>
      <div class="ndk-note">体检为配置文本的规则分析（只读），不影响设备运行；修复建议请经确认流程或在控制台人工执行。</div>
    </template>
    <el-empty v-else-if="!running" description="尚未体检，点击上方按钮运行" />
  </div>
</template>

<script setup>
// 网络设备配置体检面板：拉运行配置做规则分析（明文口令/SNMP 团名/Telnet/vty ACL/
// 日志主机/NTP/SSH），输出结构与深信服体检一致；结果缓存 5 分钟。
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { NetDev } from '../api.js'

const props = defineProps({ device: { type: Object, required: true } })

const running = ref(false)
const report = ref(null)
const checkedAt = ref('')

const scoreCls = ref('')
async function run(force = false) {
  running.value = true
  try {
    const r = await NetDev.checkup(props.device.id, force)
    report.value = r
    checkedAt.value = r.checked_at || ''
    scoreCls.value = r.score >= 90 ? 'ok' : r.score >= 75 ? 'good' : r.score >= 60 ? 'mid' : 'bad'
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.ndk-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.ndk-time { font-size: 12px; color: var(--sfa-text-3); }
.ndk-summary { display: flex; align-items: center; gap: 26px; margin-bottom: 16px; }
.ndk-score { text-align: center; padding: 10px 22px; border-radius: var(--sfa-r-md);
             background: var(--sfa-panel-soft); border: 1px solid var(--sfa-border-soft); }
.ndk-score .num { font-size: 34px; font-weight: 750; line-height: 1.1; }
.ndk-score .lbl { font-size: 11.5px; color: var(--sfa-text-3); margin-top: 2px; }
.ndk-score.ok .num { color: var(--sfa-accent); }
.ndk-score.good .num { color: var(--sfa-primary); }
.ndk-score.mid .num { color: var(--sfa-warning); }
.ndk-score.bad .num { color: var(--sfa-danger); }
.ndk-counts { display: flex; gap: 12px; }
.cnt { padding: 8px 16px; border-radius: 10px; font-size: 12px; color: var(--sfa-text-2);
       background: var(--sfa-surface); border: 1px solid var(--sfa-border-soft); }
.cnt b { display: block; font-size: 22px; }
.cnt.high b { color: var(--sfa-danger); }
.cnt.medium b { color: var(--sfa-warning); }
.cnt.low b { color: var(--sfa-text-3); }
.ndk-item { border: 1px solid var(--sfa-border-soft); border-radius: 10px; padding: 10px 13px; margin-bottom: 9px; }
.ndk-item-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.ndk-detail { margin: 7px 0 0; font-size: 12px; color: var(--sfa-text-3); white-space: pre-wrap; }
.ndk-sug { margin-top: 6px; font-size: 12.5px; color: var(--sfa-primary); }
.ndk-note { margin-top: 12px; font-size: 11.5px; color: var(--sfa-text-4); }
</style>
