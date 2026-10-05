<template>
  <div class="nt-page">
    <div class="page-card nt-toolbar">
      <div class="nt-left">
        <el-select v-model="group" placeholder="全部分组" clearable filterable
                   style="width: 190px" @change="loadGraph(false)">
          <el-option v-for="g in groups" :key="g" :label="g || '（未分组）'" :value="g" />
        </el-select>
        <el-input v-model="query" placeholder="输入 IP / MAC / 设备名，定位资产所在设备与端口"
                  clearable class="nt-search" @keyup.enter="locate" @clear="clearLocate"
                  @input="debouncedLocate">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button @click="locate" :loading="searching">定位</el-button>
      </div>
      <div class="nt-right">
        <el-radio-group v-model="layoutMode" size="small" @change="onModeChange">
          <el-radio-button value="auto">自动布局</el-radio-button>
          <el-radio-button value="manual">手动布局</el-radio-button>
        </el-radio-group>
        <el-tooltip content="重新通过 SSH 采集各设备的 LLDP 邻居与 ARP 表（约 10-20 秒）" placement="top">
          <el-button :loading="collecting" @click="loadGraph(true)">
            <el-icon><Refresh /></el-icon>&nbsp;重新采集
          </el-button>
        </el-tooltip>
        <el-tooltip content="重置手动布局为自动布局结果" placement="top" v-if="layoutMode === 'manual'">
          <el-button @click="resetLayout">重置布局</el-button>
        </el-tooltip>
        <el-button v-if="layoutMode === 'manual'" @click="saveNow" :loading="saving">
          <el-icon><Check /></el-icon>&nbsp;保存布局
        </el-button>
      </div>
    </div>

    <div class="nt-meta">
      <span class="nt-chip">设备 {{ stats.devices || 0 }}</span>
      <span class="nt-chip">LLDP 链路 {{ stats.links || 0 }}</span>
      <span class="nt-chip">ARP 记录 {{ stats.arp_entries || 0 }}</span>
      <span class="nt-chip dim" v-if="stats.fetched_at">采集于 {{ stats.fetched_at.slice(0, 16).replace('T', ' ') }}</span>
      <span class="nt-hint">拖拽节点调整位置（手动布局下自动保存）· 滚轮缩放 · 点击设备节点可编辑或进控制台</span>
    </div>

    <div v-if="hits.length" class="page-card nt-hits">
      <div class="nt-hits-title">
        <el-icon><Aim /></el-icon>
        {{ hitKind === 'asset' ? '资产接入定位' : '设备定位' }}
        <span class="nt-hits-sub" v-if="primaryHit">接入点：<b>{{ primaryHit.device_name }}</b>
          <el-tag size="small" effect="dark" class="nt-port-tag" v-if="primaryHit.port">{{ primaryHit.port }}</el-tag>
        </span>
      </div>
      <div class="nt-hit nt-hit-primary" @click="focusHit(primaryHit)">
        <el-icon class="nt-hit-star"><CircleCheckFilled /></el-icon>
        <span class="nt-hit-name">{{ primaryHit.device_name }}</span>
        <el-tag size="small" type="success" effect="light" v-if="primaryHit.port">{{ primaryHit.port }}</el-tag>
        <span class="nt-hit-attr mono" v-if="primaryHit.ip">{{ primaryHit.ip }}</span>
        <span class="nt-hit-attr mono" v-if="primaryHit.mac">{{ primaryHit.mac }}</span>
        <span class="nt-hit-via">{{ primaryHit.source === 'mac-table' ? 'MAC 表直连' : primaryHit.source === 'mgmt' ? '管理地址' : 'ARP 推断' }}</span>
      </div>
      <div class="nt-hits-more" v-if="otherHits.length">
        <span class="nt-more-label">其他学习点（上联/路径，{{ otherHits.length }}）：</span>
        <span v-for="(h, i) in otherHits.slice(0, 6)" :key="i" class="nt-hit dim" @click="focusHit(h)">
          {{ h.device_name }}<el-tag size="small" effect="plain" v-if="h.port" class="nt-more-port">{{ h.port }}</el-tag>
        </span>
      </div>
    </div>

    <div class="page-card nt-canvas-card" v-loading="loading" element-loading-text="正在采集拓扑（LLDP / ARP）…">
      <div ref="chartRef" class="nt-canvas"></div>
      <el-empty v-if="!loading && !graph.nodes.length" description="暂无拓扑数据"
                class="nt-empty">
        <el-button type="primary" @click="loadGraph(true)">立即采集</el-button>
      </el-empty>
      <transition name="nt-pop-in">
        <div v-if="selected" class="nt-pop">
          <div class="nt-pop-head">
            <span class="nt-pop-name">{{ selected.name }}</span>
            <button class="nt-pop-close" @click="selected = null" aria-label="关闭"><el-icon><Close /></el-icon></button>
          </div>
          <div class="nt-pop-body">
            <div class="nt-pop-row" v-if="selected.vendor"><span class="k">厂家</span>{{ vendorName(selected.vendor) }}</div>
            <div class="nt-pop-row" v-if="selected.model"><span class="k">型号</span>{{ selected.model }}</div>
            <div class="nt-pop-row"><span class="k">管理地址</span><span class="mono">{{ selected.host || '—' }}</span></div>
            <div class="nt-pop-row" v-if="selected.group"><span class="k">分组</span>{{ selected.group }}</div>
          </div>
          <div class="nt-pop-actions" v-if="!selected.id.startsWith('ext:')">
            <el-button size="small" @click="$emit('edit', selected.id)">
              <el-icon><Edit /></el-icon>&nbsp;编辑设备
            </el-button>
            <el-button size="small" type="primary" @click="$emit('console', selected.id)">
              <el-icon><Monitor /></el-icon>&nbsp;进入控制台
            </el-button>
          </div>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { NetDev } from '../api.js'
import { useTheme } from '../composables/useTheme'

const emit = defineEmits(['edit', 'console'])

const VENDOR_LABELS = { huawei: '华为 VRP', h3c: 'H3C Comware', cisco: '思科 IOS', ruijie: '锐捷',
                        zte: '中兴', juniper: 'Juniper', aruba: 'Aruba', dell: 'Dell',
                        tplink: 'TP-Link', mikrotik: 'MikroTik', nokia: 'Nokia', other: '通用' }
const vendorName = (v) => VENDOR_LABELS[v] || v || '未知'

const group = ref('')
const groups = ref([])
const query = ref('')
const searching = ref(false)
const collecting = ref(false)
const loading = ref(false)
const layoutMode = ref('auto')
const saving = ref(false)
const graph = ref({ nodes: [], edges: [], positions: {} })
const stats = ref({})
const hits = ref([])
const hitKind = ref('none')
const selected = ref(null)

// 主答案 = 排序首位（MAC 表直连接入口）；其余为上联/路径学习点
const primaryHit = computed(() => hits.value.find(h => h.access) || hits.value[0] || null)
const otherHits = computed(() => hits.value.filter(h => h !== primaryHit.value))

const chartRef = ref(null)
let chart = null
let saveTimer = null
let locateTimer = null
let resizeObs = null

// 节点配色：在线设备 / 离线设备 / 外部邻居
const ONLINE_WINDOW = 1000 * 60 * 60 * 24 * 14
const isOnline = (n) => n.kind === 'device' && n.last_ok_at
  && (Date.now() - new Date(n.last_ok_at).getTime()) < ONLINE_WINDOW

function nodeStyle(n, hitSet) {
  const online = isOnline(n)
  if (n.kind === 'external') {
    return {
      symbolSize: 30, itemStyle: { color: '#E6EAF3', borderColor: '#C6CEDD', borderWidth: 1.5,
                                   opacity: hitSet && !hitSet.has(n.id) ? .15 : 1 },
      label: { show: hitSet ? hitSet.has(n.id) : graph.value.nodes.length <= 30,
               color: '#667085', fontSize: 10 },
    }
  }
  const hit = hitSet?.has(n.id)
  const deg = n.degree || 0
  // 核心设备最大（金色描边），其余按连接度渐进放大
  const size = n.core ? 62 : Math.min(34 + deg * 3, 54)
  return {
    symbolSize: hit ? Math.max(size + 12, 58) : size,
    itemStyle: {
      color: online
        ? { type: 'radial', x: .4, y: .35, r: .8,
            colorStops: [{ offset: 0, color: '#6A87FF' }, { offset: 1, color: '#2C47D6' }] }
        : '#C3CBDC',
      borderColor: hit ? '#0FB9A4' : n.core ? '#F5A90B' : '#fff',
      borderWidth: hit ? 4 : n.core ? 3.5 : 2,
      shadowBlur: hit ? 24 : n.core ? 16 : 8,
      shadowColor: hit ? 'rgba(15,185,164,.65)' : n.core ? 'rgba(245,169,11,.5)' : 'rgba(16,24,40,.18)',
      opacity: hitSet && !hitSet.has(n.id) ? .12 : 1,
    },
    label: { color: theme.value === 'dark' ? '#E4E9F4' : '#101828', fontWeight: 600 },
  }
}

const { theme } = useTheme()

function buildOption(hitSet) {
  const positions = graph.value.positions || {}
  const nodes = graph.value.nodes.map(n => ({
    id: n.id, name: n.id,
    x: positions[n.id]?.[0], y: positions[n.id]?.[1],
    ...nodeStyle(n, hitSet),
    label: {
      show: n.kind === 'device' || (hitSet?.has(n.id)) || graph.value.nodes.length <= 30,
      position: 'bottom', distance: 4,
      formatter: hitSet?.has(n.id)
        ? `{name|${n.name}}\n{port|${hitPortOf(n.id)}}`
        : n.core ? `{name|${n.name}}\n{core|核心交换机}`
          : n.cross ? `{name|${n.name}}\n{port|跨分组 · ${n.group || '未分组'}}`
          : n.name,
      rich: { name: { fontWeight: 650, fontSize: 11.5, color: '#101828', lineHeight: 16 },
              port: { fontSize: 10.5, color: 'var(--sfa-ok-ink)', fontFamily: 'JetBrains Mono, Consolas, monospace' },
              core: { fontSize: 10, color: '#C4870A', fontWeight: 650, lineHeight: 14 } },
    },
  }))
  const edges = graph.value.edges.map(e => {
    const isExt = e.source.startsWith('ext:') || e.target.startsWith('ext:')
    const agg = e.aggregated || 0
    return {
      source: e.source, target: e.target,
      lineStyle: {
        color: isExt ? '#C9D2E4' : agg ? '#4163D8' : '#8FA6E8',
        width: isExt ? 1.4 : agg ? 4.5 : e.confirmed === false ? 1.4 : 2.2,
        type: isExt || e.confirmed === false ? 'dashed' : 'solid',
        opacity: hitSet && !hitSet.has(e.source) && !hitSet.has(e.target) ? .08 : .9,
        curveness: 0.08,
      },
      tooltip: {
        formatter: () => `${e.source_name} ${e.from_port || '?'}  ⇌  ${e.target_name} ${e.to_port || '?'}`
          + (agg ? `\n链路聚合 ×${agg}` : e.confirmed === false ? '\n（单侧 LLDP，未互证）' : ''),
      },
    }
  })
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', confine: true,
               textStyle: { fontSize: 12 },
               backgroundColor: theme.value === 'dark' ? 'rgba(24, 32, 54, .96)' : 'rgba(255,255,255,.96)',
               borderColor: theme.value === 'dark' ? '#33406A' : '#E4E8F1',
               extraCssText: 'box-shadow: 0 8px 24px -8px rgba(16,24,40,.2);' },
    series: [{
      type: 'graph', layout: layoutMode.value === 'manual' ? 'none' : 'force',
      data: nodes, links: edges, roam: true,
      draggable: true, z: 3,
      force: { repulsion: Math.max(260, graph.value.nodes.length * 22), edgeLength: [80, 190],
               gravity: .12, layoutAnimation: true },
      emphasis: { focus: 'adjacency', itemStyle: { shadowBlur: 18 } },
      scaleLimit: { min: .3, max: 3 },
    }],
  }
}

const hitPortOf = (id) => {
  const h = hits.value.find(x => x.device_id === id)
  return h?.port || ''
}

async function loadGraph(force = false) {
  loading.value = !force
  collecting.value = force
  try {
    const g = await NetDev.topology(group.value, force)
    graph.value = g
    stats.value = g.stats || {}
    groups.value = g.groups || []
    await nextTick()
    renderChart()
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    loading.value = false
    collecting.value = false
  }
}

// 主题切换：echarts 文字/轴色注册在 init，销毁重建（renderChart 懒加载恢复）
let _lastHitSet = null
watch(theme, () => {
  chart?.dispose()
  chart = null
  if (graph.value?.nodes?.length) nextTick(() => renderChart(_lastHitSet))
})

function renderChart(hitSet = null) {
  _lastHitSet = hitSet
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value, theme.value === 'dark' ? 'dark' : undefined)
    chart.getZr().on('dragend', () => {
      if (layoutMode.value === 'manual') scheduleSave()
    })
    // mouseup 兜底：任何画布交互结束都尝试保存当前布局（幂等，手动模式下才生效）
    chart.getZr().on('mouseup', () => {
      if (layoutMode.value === 'manual') scheduleSave()
    })
    chart.on('click', params => {
      if (params.dataType === 'node') {
        selected.value = graph.value.nodes.find(n => n.id === params.name) || null
      } else {
        selected.value = null
      }
    })
    // 画布空白处点击清除选中
    chart.getZr().on('click', ev => { if (!ev.target) selected.value = null })
  }
  chart.setOption({ backgroundColor: 'transparent', ...buildOption(hitSet) }, true)
  // 手动 kick 一次渲染循环：防御 zrender 动画帧未启动导致的空白
  requestAnimationFrame(() => {
    try { chart?.getZr()?.refresh() } catch { /* 忽略 */ }
  })
}

// ---- 手动布局：拖拽后读取节点坐标并保存（防抖） ----
function collectPositions() {
  if (!chart) return {}
  try {
    const graphModel = chart.getModel().getSeriesByIndex(0).getGraph()
    const pos = {}
    graphModel.eachNode(n => {
      // getLayout() 可能返回 {x,y} 或 [x,y]（不同布局模式），统一兼容
      const l = n.getLayout()
      const lx = Array.isArray(l) ? l[0] : l?.x
      const ly = Array.isArray(l) ? l[1] : l?.y
      if (Number.isFinite(lx) && Number.isFinite(ly)) pos[n.name] = [Math.round(lx), Math.round(ly)]
    })
    return pos
  } catch { return {} }
}

function scheduleSave() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(saveNow, 700)
}

async function saveNow() {
  const pos = collectPositions()
  if (!Object.keys(pos).length) return
  graph.value.positions = pos
  saving.value = true
  try {
    await NetDev.saveTopologyPositions(group.value, pos)
    ElMessage.success('布局已保存')
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    saving.value = false
  }
}

function onModeChange(mode) {
  if (mode === 'manual' && !Object.keys(graph.value.positions || {}).length) {
    // 自动 → 手动：把当前力导向坐标固化为初始手动布局；读不到则网格兜底
    const pos = collectPositions()
    graph.value.positions = Object.keys(pos).length ? pos : fallbackPositions()
    NetDev.saveTopologyPositions(group.value, graph.value.positions).catch(() => {})
  }
  chart?.dispose()
  chart = null            // force→none 干净重建，避免内部布局状态残留
  renderChart(hitSetFromHits())
  if (mode === 'manual') ElMessage.info('手动布局：拖拽节点后自动保存；重置布局可回到自动排布')
}

// 无保存坐标时的网格兜底：保证手动模式下节点可见可拖
function fallbackPositions() {
  const nodes = graph.value.nodes
  const cols = Math.ceil(Math.sqrt(Math.max(nodes.length, 1)))
  const pos = {}
  nodes.forEach((n, i) => {
    pos[n.id] = [140 + (i % cols) * 150, 110 + Math.floor(i / cols) * 115]
  })
  return pos
}

function resetLayout() {
  graph.value.positions = {}
  layoutMode.value = 'auto'
  chart?.dispose()
  chart = null
  renderChart(hitSetFromHits())
  NetDev.saveTopologyPositions(group.value, {}).catch(() => {})
  ElMessage.success('已重置为自动布局')
}

// ---- 资产定位 ----
const debouncedLocate = () => {
  clearTimeout(locateTimer)
  locateTimer = setTimeout(locate, 500)
}

async function locate() {
  const q = query.value.trim()
  if (!q) { clearLocate(); return }
  searching.value = true
  try {
    const r = await NetDev.topologySearch(group.value, q)
    hits.value = r.hits || []
    hitKind.value = r.kind || 'none'
    if (!hits.value.length) {
      renderChart(null)
      ElMessage.warning(r.reason || '未找到匹配的资产或设备')
      return
    }
    applyHits()
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    searching.value = false
  }
}

function hitSetFromHits() {
  return hits.value.length ? new Set(hits.value.map(h => h.device_id)) : null
}

function applyHits() {
  renderChart(hitSetFromHits())
}

function focusHit(h) {
  // 双击条目：切换到命中设备所在分组（若不在当前视图）
  const node = graph.value.nodes.find(n => n.id === h.device_id)
  if (node && node.group !== group.value && !group.value) return
  selected.value = node || null
}

function clearLocate() {
  hits.value = []
  hitKind.value = 'none'
  query.value = ''
  renderChart(null)
}

onMounted(async () => {
  await loadGraph(false)
  resizeObs = new ResizeObserver(() => chart?.resize())
  resizeObs.observe(chartRef.value)
})

onBeforeUnmount(() => {
  resizeObs?.disconnect()
  clearTimeout(saveTimer)
  clearTimeout(locateTimer)
  chart?.dispose()
  chart = null
})

defineExpose({ reload: loadGraph })
</script>

<style scoped>
.nt-page { display: flex; flex-direction: column; gap: 12px; }

.nt-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 14px 16px; }
.nt-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.nt-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.nt-search { width: 320px; }

.nt-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 0 2px; }
.nt-chip {
  font-size: 11.5px; padding: 2.5px 10px; border-radius: 999px;
  background: var(--sfa-tint-primary-2); color: var(--sfa-primary); font-weight: 600;
  font-feature-settings: "tnum" 1;
}
.nt-chip.dim { background: var(--sfa-bg-deep); color: var(--sfa-text-3); font-weight: 500; }
.nt-hint { color: var(--sfa-text-4); font-size: 11px; margin-left: auto; }

.nt-hits { padding: 10px 14px; }
.nt-hits-title { display: flex; align-items: center; gap: 6px; font-weight: 650; font-size: 12.5px; color: var(--sfa-ok-ink); margin-bottom: 7px; }
.nt-hits-sub { color: var(--sfa-text-2); font-weight: 500; margin-left: 6px; }
.nt-hits-sub b { color: var(--sfa-text); }
.nt-port-tag { margin-left: 4px; font-family: var(--sfa-mono); }

.nt-hit { display: inline-flex; align-items: center; gap: 7px; margin: 2px 14px 2px 0; cursor: pointer;
          padding: 3px 9px; border-radius: 8px; transition: background var(--dur-1) var(--ease-out); }
.nt-hit:hover { background: var(--sfa-tint-primary); }
.nt-hit-primary {
  display: flex; width: fit-content; align-items: center; gap: 9px;
  background: linear-gradient(90deg, #EDFBEF, #F4FCF6); border: 1px solid #CBEED8;
  padding: 7px 14px; border-radius: 10px; margin: 0 0 6px;
}
.nt-hit-primary:hover { border-color: #9FE0BC; }
.nt-hit-star { color: #0E9F6E; font-size: 16px; }
.nt-hit-name { font-weight: 650; font-size: 13px; }
.nt-hit-attr { color: var(--sfa-text-3); font-size: 11.5px; }
.nt-hit-via { font-size: 10.5px; color: var(--sfa-ok-ink); background: var(--sfa-ok-bg); border-radius: 999px; padding: 1.5px 8px; }
.nt-hits-more { display: flex; align-items: center; gap: 4px 10px; flex-wrap: wrap; }
.nt-more-label { color: var(--sfa-text-4); font-size: 11px; }
.nt-hit.dim { color: var(--sfa-text-3); font-size: 12px; padding: 2px 7px; }
.nt-more-port { margin-left: 4px; font-family: var(--sfa-mono); font-size: 10px; height: auto; padding: 0 5px; }

.nt-canvas-card { position: relative; padding: 8px; }
.nt-canvas { height: 600px; width: 100%; }
.nt-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }

.nt-pop {
  position: absolute; top: 18px; right: 18px; width: 260px; z-index: 5;
  background: rgba(255, 255, 255, .97); backdrop-filter: blur(8px);
  border: 1px solid var(--sfa-border); border-radius: 14px;
  box-shadow: var(--sfa-shadow-2); overflow: hidden;
}
.nt-pop-in-enter-active { transition: all .22s var(--ease-spring); }
.nt-pop-in-enter-from { opacity: 0; transform: translateY(-6px) scale(.97); }
.nt-pop-head { display: flex; align-items: center; justify-content: space-between; padding: 11px 14px 0; }
.nt-pop-name { font-weight: 700; font-size: 13.5px; letter-spacing: -.01em; }
.nt-pop-close {
  width: 24px; height: 24px; border: none; border-radius: 7px; cursor: pointer;
  background: transparent; color: var(--sfa-text-3);
  display: inline-flex; align-items: center; justify-content: center;
  transition: all var(--dur-1) var(--ease-out);
}
.nt-pop-close:hover { background: var(--sfa-hover-soft); color: var(--sfa-text); }
.nt-pop-body { padding: 8px 14px 4px; }
.nt-pop-row { font-size: 12px; color: var(--sfa-text-2); padding: 3.5px 0; display: flex; gap: 10px; }
.nt-pop-row .k { color: var(--sfa-text-4); width: 52px; flex-shrink: 0; }
.nt-pop-actions { display: flex; gap: 8px; padding: 10px 14px 13px; }
.nt-pop-actions .el-button { flex: 1; margin: 0; }

@media (max-width: 820px) {
  .nt-canvas { height: 460px; }
  .nt-search { width: 100%; }
}
</style>
