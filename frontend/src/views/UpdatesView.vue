<template>
  <div class="updates-page">
    <ContextBar />
    <div class="page-head">
      <div>
        <h2 class="ph-title">软件更新建议</h2>
        <p class="ph-desc">数据来源：深信服技术支持平台版本发布说明（免认证抓取）+ 官网 PSIRT 安全公告（公开）+ 内置版本知识库快照</p>
      </div>
      <div class="ph-actions">
        <el-button @click="refresh" :loading="refreshing">
          <el-icon><Refresh /></el-icon>&nbsp;刷新官方更新信息
        </el-button>
      </div>
    </div>
    <el-alert v-if="netdevMode || globalMode" type="info" :closable="false" show-icon :title="guardText" />

    <div v-if="advice">
      <!-- 结论卡片 -->
      <div class="page-card advice-card">
        <div class="advice-head">
          <div class="advice-main">
            <div class="advice-title">
              <el-tag :type="riskType" size="large" effect="dark" round>{{ advice.recommendation }}</el-tag>
              <span class="advice-ver mono">
                {{ advice.current_version }} <span class="ver-arrow">→</span> {{ advice.latest_version }}
              </span>
              <span v-if="advice.up_to_date" class="uptodate"><el-icon><CircleCheckFilled /></el-icon> 已是最新</span>
            </div>
            <div class="advice-sub">{{ advice.product_name }} · {{ advice.device_name }}</div>
          </div>
          <div class="advice-path">
            <div class="path-label">升级路径</div>
            <div class="path-steps">
              <template v-for="(hop, i) in advice.upgrade_path.hops" :key="hop">
                <span v-if="i" class="path-sep">→</span>
                <span class="path-hop" :class="{ latest: hop === advice.latest_version }">{{ hop }}</span>
              </template>
              <span v-if="!advice.upgrade_path.hops.length" class="path-hop latest">无需升级</span>
            </div>
            <template v-if="advice.upgrade_path.notes?.length">
              <el-alert v-for="(n, ni) in advice.upgrade_path.notes" :key="ni"
                        type="warning" :closable="false" style="margin-top: 8px; font-size: 12px" :title="n" />
            </template>
          </div>
        </div>
        <!-- 升级理由 -->
        <div class="reasons" v-if="advice.reasons?.length">
          <div v-for="(r, i) in advice.reasons" :key="i" class="reason">
            <el-tag :type="r.level === 'high' ? 'danger' : r.level === 'medium' ? 'warning' : r.level === 'info' ? 'success' : 'info'" size="small">
              {{ r.type }}
            </el-tag>
            <span class="reason-text">{{ r.text }}</span>
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

      <!-- 官方软件更新列表 -->
      <div class="page-card" style="margin-top: 12px">
        <div class="col-title" style="display: flex; align-items: center; gap: 10px">
          官方软件更新列表（support.sangfor.com.cn）
          <el-button size="small" text type="primary" @click="loadSoftwareList(true)" :loading="softLoading">
            重新抓取
          </el-button>
        </div>
        <el-tabs v-model="softTab">
          <el-tab-pane v-for="p in [{ k: 'af', name: '防火墙 AF' }, { k: 'ac', name: '上网行为管理 AC' },
                                   { k: 'scp', name: '云计算平台 SCP' }, { k: 'hci', name: '超融合 HCI' }]"
                       :key="p.k" :label="p.name" :name="p.k">
            <div v-if="softwareList[p.k]?.status === 'ok' && softwareList[p.k]?.items?.length">
              <el-table :data="softwareList[p.k].items" size="small" border stripe max-height="360">
                <el-table-column type="index" label="#" width="50" />
                <el-table-column prop="name" label="版本/升级包" min-width="260">
                  <template #default="{ row }"><span class="mono">{{ row.name }}</span></template>
                </el-table-column>
                <el-table-column label="下载地址" min-width="220">
                  <template #default="{ row }">
                    <a v-if="row.url" :href="row.url" target="_blank" rel="noopener noreferrer"
                       class="dl-link" :title="row.url">
                      <el-icon style="vertical-align: -2px"><Download /></el-icon> 下载
                    </a>
                    <span v-else class="muted">需登录平台获取</span>
                  </template>
                </el-table-column>
                <el-table-column prop="size" label="大小" width="90" />
                <el-table-column prop="published" label="发布时间" width="110" />
                <el-table-column prop="md5" label="MD5" min-width="170">
                  <template #default="{ row }"><span class="mono" style="font-size: 11px">{{ row.md5 || '—' }}</span></template>
                </el-table-column>
              </el-table>
              <div class="hint" style="margin-top: 6px">
                来源：{{ softwareList[p.k].source }} · 抓取时间 {{ softwareList[p.k].fetched_at }}
                <span v-if="softwareList[p.k].from_cache">（缓存）</span>
                · 下载需在官方平台登录后进行
              </div>
            </div>
            <el-alert v-else-if="softwareList[p.k]?.status" type="warning" :closable="false"
                      :title="`未获取到列表：${softwareList[p.k].reason || softwareList[p.k].status}`" />
            <el-empty v-else description="加载中" :image-size="60" />
          </el-tab-pane>
        </el-tabs>
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
import { store, currentDevice, isNetDev, isGlobal } from '../store.js'
import ContextBar from '../components/ContextBar.vue'
import { Updates } from '../api.js'

const advice = ref(null)
const refreshing = ref(false)

// 官方软件更新列表
const softTab = ref('af')
const softwareList = ref({})
const softLoading = ref(false)

async function loadSoftwareList(force = false) {
  softLoading.value = true
  try {
    const products = ['af', 'ac', 'scp', 'hci']
    const results = await Promise.all(products.map(p => Updates.softwareList(p, force)))
    softwareList.value = Object.fromEntries(products.map((p, i) => [p, results[i]]))
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { softLoading.value = false }
}

async function loadCookieState() {
  // Cookie 配置已改为 .env 通道，不再展示状态标签（保留函数以兼容 onMounted 调用序）
}

const netdevMode = computed(() => isNetDev(currentDevice()))
const globalMode = computed(() => isGlobal(currentDevice()))
const guardText = computed(() => netdevMode.value
  ? '当前选中的是网络设备：升级建议仅支持深信服设备'
  : '当前为全局模式：本页面需要指定具体设备，请在侧栏「目标设备」中选择一台深信服设备')
const riskType = computed(() => ({ high: 'danger', medium: 'warning', low: 'info' }[advice.value?.risk] || 'info'))
const catIconName = cat => ({ 新增功能: 'CirclePlusFilled', 安全修复: 'WarningFilled', 已知问题修复: 'CircleCheckFilled', 优化: 'TopRight' }[cat] || 'InfoFilled')
const catIcon = cat => ({ 新增功能: 'c-green', 安全修复: 'c-red', 已知问题修复: 'c-blue', 优化: 'c-orange' }[cat] || '')

async function load() {
  const dev = currentDevice()
  if (!dev || isNetDev(dev) || isGlobal(dev)) return
  try { advice.value = await Updates.advice(dev.id) } catch (e) { console.error(e) }
}

async function refresh() {
  refreshing.value = true
  try {
    const r = await Updates.refresh()
    ElMessage.info(`官方平台：${r.official.status}${r.official.reason ? '（' + r.official.reason + '）' : ''}；PSIRT：${r.psirt.status}`)
    await load()
    await loadSoftwareList()
  } finally { refreshing.value = false }
}

watch(() => store.currentDeviceId, load)
onMounted(() => { load(); loadSoftwareList(); loadCookieState() })
</script>

<style scoped>
.updates-page { animation: sfa-fade-up .3s var(--ease-out); }

.advice-card {
  margin-bottom: 16px;
  background:
    radial-gradient(560px 200px at 8% 0%, rgba(59, 99, 255, .06), transparent 65%),
    radial-gradient(420px 200px at 95% 10%, rgba(15, 185, 164, .05), transparent 65%),
    var(--sfa-surface);
}
.advice-head { display: flex; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
.advice-title { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.advice-ver { font-size: 15px; font-weight: 650; letter-spacing: -.01em; }
.ver-arrow { color: var(--sfa-text-4); margin: 0 2px; }
.uptodate { display: inline-flex; align-items: center; gap: 4px; color: #0E9F6E; font-size: 12px; font-weight: 600; }
.advice-sub { color: var(--sfa-text-3); font-size: 12px; margin-top: 8px; }

.path-label { color: var(--sfa-text-3); font-size: 11px; letter-spacing: .1em; margin-bottom: 8px; font-weight: 650; }
.path-steps { display: flex; gap: 7px; align-items: center; flex-wrap: wrap; max-width: 480px; }
.path-hop {
  font-family: var(--sfa-mono); font-size: 12px; font-weight: 600;
  background: var(--sfa-bg-deep); color: var(--sfa-text-2);
  border: 1px solid var(--sfa-border); border-radius: 8px; padding: 3.5px 10px;
}
.path-hop.latest {
  background: var(--sfa-ok-bg); border-color: var(--sfa-ok-border); color: var(--sfa-ok-ink);
  box-shadow: 0 0 0 3px rgba(15, 185, 164, .1);
}
.path-sep { color: var(--sfa-text-4); font-size: 12px; }

.reasons { margin-top: 16px; display: flex; flex-direction: column; gap: 7px; border-top: 1px dashed var(--sfa-border-soft); padding-top: 13px; }
.reason { display: flex; align-items: baseline; gap: 9px; }
.reason-text { font-size: 12.5px; color: var(--sfa-text-2); line-height: 1.65; }

.cols { display: flex; gap: 16px; align-items: flex-start; }
.col-changes { flex: 1.2; }
.col-timing { flex: 1; }
.change-item { padding: 7px 0; font-size: 12.5px; display: flex; gap: 8px; align-items: baseline; border-bottom: 1px dashed var(--sfa-border-soft); line-height: 1.6; }
.change-item:last-child { border-bottom: none; }
.c-green { color: #0E9F6E; }
.c-red { color: #E5484D; }
.c-blue { color: var(--sfa-primary); }
.c-orange { color: #E8930C; }
</style>
