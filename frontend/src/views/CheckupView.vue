<template>
  <div class="checkup-page">
    <div class="page-card hero-card">
      <div class="page-head">
        <div>
          <h2 class="ph-title">配置体检</h2>
          <p class="ph-desc">确定性规则引擎：规则冲突（遮蔽 / 矛盾）/ 空策略 / 过宽权限 / 高危端口暴露 / 资源异常。风险项附带修复建议，变更请通过 AI 对话（走确认卡片）执行。</p>
        </div>
        <div class="ph-actions">
          <el-button type="primary" @click="runCheckup" :loading="running">
            <el-icon><Odometer /></el-icon>&nbsp;{{ report ? '重新体检' : '运行配置体检' }}
          </el-button>
        </div>
      </div>
    </div>

    <div v-if="report" class="cols">
      <div class="page-card score-card">
        <div class="gauge-wrap">
          <svg viewBox="0 0 140 140" class="gauge" aria-hidden="true">
            <circle cx="70" cy="70" r="58" fill="none" stroke="#EDF0F7" stroke-width="11" />
            <circle cx="70" cy="70" r="58" fill="none" class="gauge-arc"
                    :stroke="scoreColor" stroke-width="11" stroke-linecap="round"
                    :stroke-dasharray="CIRC" :stroke-dashoffset="arcOffset"
                    transform="rotate(-90 70 70)" />
          </svg>
          <div class="gauge-center">
            <div class="gauge-num" :style="{ color: scoreColor }">{{ shownScore }}<span class="gauge-denom">/100</span></div>
            <div class="gauge-grade">{{ report.grade }}</div>
          </div>
        </div>
        <div class="score-meta">{{ report.meta.device_name }} · {{ report.meta.sw_version }}</div>
        <div class="score-counts">
          <div class="cnt high"><div class="cnt-num">{{ report.counts.high }}</div>高危</div>
          <div class="cnt medium"><div class="cnt-num">{{ report.counts.medium }}</div>中危</div>
          <div class="cnt low"><div class="cnt-num">{{ report.counts.low }}</div>低危</div>
        </div>
      </div>

      <div class="page-card items-card">
        <el-collapse v-model="openSeverities">
          <el-collapse-item v-for="sev in ['high', 'medium', 'low']" :key="sev" :name="sev">
            <template #title>
              <span class="sev-dot" :class="sev"></span>
              <span class="sev-name">{{ sevName(sev) }}</span>
              <span class="sev-count">{{ sevItems(sev).length }} 项</span>
            </template>
            <div v-for="item in sevItems(sev)" :key="item.check_id + (item.rule_ids || []).join()" class="risk-item">
              <div class="risk-title">
                <el-tag :type="sevType(sev)" size="small" effect="light">{{ item.category }}</el-tag>
                <b>{{ item.title }}</b>
              </div>
              <div class="risk-row"><span class="risk-k">说明</span>{{ item.evidence }}</div>
              <div class="risk-row"><span class="risk-k">建议</span>{{ item.suggestion }}</div>
            </div>
            <div v-if="!sevItems(sev).length" class="sev-empty">无此级别风险项</div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>

    <div v-else class="page-card empty-card">
      <svg viewBox="0 0 40 40" fill="none" class="empty-mark" aria-hidden="true">
        <path d="M20 3.5 34.3 11.8v16.4L20 36.5 5.7 28.2V11.8L20 3.5Z" stroke="#C6CEDD" stroke-width="2.5" stroke-linejoin="round" />
        <circle cx="20" cy="20" r="3.4" fill="#C6CEDD" />
      </svg>
      <div class="empty-title">尚未运行体检</div>
      <div class="empty-desc">点击右上角「运行配置体检」，约数秒即可获得评分与风险清单</div>
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
const scoreColor = computed(() => (report.value?.score >= 90 ? '#0E9F6E' : report.value?.score >= 75 ? '#3B63FF' : report.value?.score >= 60 ? '#E8930C' : '#E5484D'))

// 得分仪表盘：弧长 + 数字滚动动画
const CIRC = 2 * Math.PI * 58
const shownScore = ref(0)
const arcOffset = computed(() => CIRC * (1 - Math.min(100, shownScore.value) / 100))

function animateScore(to) {
  const from = shownScore.value, start = performance.now(), dur = 950
  const step = (t) => {
    const p = Math.min(1, (t - start) / dur)
    const eased = 1 - Math.pow(1 - p, 3)
    shownScore.value = Math.round(from + (to - from) * eased)
    if (p < 1) requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}
watch(() => report.value?.score, s => { if (s !== undefined) animateScore(s) })

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
.checkup-page { max-width: 1160px; margin: 0 auto; }
.hero-card { margin-bottom: 16px; }

.cols { display: flex; gap: 16px; align-items: flex-start; }

/* 得分仪表盘（吸附置顶，长风险清单滚动时保持可见） */
.score-card {
  width: 264px; flex-shrink: 0; text-align: center;
  position: sticky; top: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 28px 20px;
}
.gauge-wrap { position: relative; width: 164px; height: 164px; }
.gauge { width: 100%; height: 100%; }
.gauge-arc { transition: stroke-dashoffset 1s var(--ease-out), stroke .4s; filter: drop-shadow(0 4px 10px rgba(59, 99, 255, .25)); }
.gauge-center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.gauge-num { font-size: 42px; font-weight: 800; letter-spacing: -.03em; line-height: 1; font-feature-settings: "tnum" 1; }
.gauge-denom { font-size: 13px; font-weight: 600; color: var(--sfa-text-4); margin-left: 3px; letter-spacing: 0; }
.gauge-grade { font-size: 13px; font-weight: 650; color: var(--sfa-text-2); margin-top: 6px; letter-spacing: .06em; }
.score-meta { color: var(--sfa-text-3); font-size: 12px; margin: 16px 0 14px; font-family: var(--sfa-mono); }
.score-counts { display: flex; gap: 22px; justify-content: center; width: 100%; border-top: 1px solid var(--sfa-border-soft); padding-top: 14px; }
.cnt { font-size: 11.5px; color: var(--sfa-text-3); letter-spacing: .03em; }
.cnt-num { font-size: 22px; font-weight: 750; font-feature-settings: "tnum" 1; }
.cnt.high .cnt-num { color: var(--sfa-danger); }
.cnt.medium .cnt-num { color: var(--sfa-warning); }
.cnt.low .cnt-num { color: var(--sfa-text-3); }

.items-card { flex: 1; min-height: 320px; }
.sev-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 8px; }
.sev-dot.high { background: var(--sfa-danger); box-shadow: 0 0 8px rgba(229, 72, 77, .5); }
.sev-dot.medium { background: var(--sfa-warning); box-shadow: 0 0 8px rgba(245, 169, 11, .45); }
.sev-dot.low { background: #98A2B3; }
.sev-name { font-weight: 650; }
.sev-count { color: var(--sfa-text-3); font-size: 12px; margin-left: 8px; }
.sev-empty { color: var(--sfa-text-4); font-size: 12.5px; padding: 8px 2px; }

.risk-item { border-bottom: 1px dashed var(--sfa-border-soft); padding: 12px 4px; animation: sfa-fade-up .3s var(--ease-out) both; }
.risk-item:last-child { border-bottom: none; }
.risk-title { margin-bottom: 6px; font-size: 13.5px; display: flex; align-items: center; gap: 7px; }
.risk-title :deep(.el-tag) { min-width: 60px; justify-content: center; flex-shrink: 0; }
.risk-row { font-size: 12.5px; color: var(--sfa-text-2); line-height: 1.7; display: flex; gap: 8px; }
.risk-k {
  flex-shrink: 0; color: var(--sfa-text-4); font-size: 11px; font-weight: 600;
  background: var(--sfa-bg-deep); border-radius: 5px; padding: 1px 7px; height: fit-content; margin-top: 2px;
}

.empty-card { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 64px 20px; text-align: center; }
.empty-mark { width: 60px; height: 60px; margin-bottom: 16px; }
.empty-title { font-size: 16px; font-weight: 700; color: var(--sfa-text-2); margin-bottom: 6px; }
.empty-desc { font-size: 12.5px; color: var(--sfa-text-4); }

@media (max-width: 900px) {
  .cols { flex-direction: column; }
  .score-card { width: 100%; }
}
</style>
