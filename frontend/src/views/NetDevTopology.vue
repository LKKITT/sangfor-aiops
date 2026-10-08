<template>
  <div class="nt-page">
    <div class="page-card nt-toolbar">
      <div class="nt-left">
        <el-select v-model="group" placeholder="全部分组" clearable filterable
                   :teleported="false" style="width: 190px" @change="loadGraph(false)">
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
      <div ref="chartRef" class="nt-canvas" role="img" :aria-label="topoAriaLabel"></div>
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

// 拓扑图对屏幕阅读器的文字化描述（图形本身键盘/读屏不可达）
const topoAriaLabel = computed(() => {
  const n = graph.value.nodes?.length || 0
  const e = graph.value.edges?.length || 0
  const online = (graph.value.nodes || []).filter(x => isOnline(x)).length
  return `网络拓扑图：共 ${n} 个节点、${e} 条链路，其中在线 ${online} 个。`
    + (selected.value ? `当前选中节点「${selected.value.label || selected.value.id}」。` : '')
    + '拓扑交互（拖拽、缩放、定位）需使用鼠标操作。'
})

const chartRef = ref(null)
let chart = null
let saveTimer = null
let locateTimer = null
let resizeObs = null

// 节点配色：在线设备 / 离线设备 / 外部邻居
const ONLINE_WINDOW = 1000 * 60 * 60 * 24 * 14
const isOnline = (n) => n.kind === 'device' && n.last_ok_at
  && (Date.now() - new Date(n.last_ok_at).getTime()) < ONLINE_WINDOW

const { theme } = useTheme()

// 主题化色板：原实现把十六进制散落在各分支里，暗色下渐变/阴影对不上
const C = computed(() => theme.value === 'dark' ? {
  onlineTop: '#6079EE', onlineBot: '#2E48BC', onlineDeep: '#22359A',
  offTop: '#2E3956', offBot: '#222B44',
  extFill0: '#26304A', extFill1: '#1A2338', extBorder: '#3D4A6E',
  coreBorder: '#F8C24D', hitBorder: '#4FCCBB',
  nodeBorder: '#0D1425',
  label: '#E4E9F4', labelDim: '#8B96B0',
  port: '#4FCCBB', core: '#F8C24D',
  link: '#41537A', linkExt: '#2E3A55', linkActive: '#6A87FF',
  zone: 'rgba(106,135,255,.05)', zoneBorder: 'rgba(106,135,255,.16)',
  portLabelBg: 'rgba(19,26,46,.96)', portLabelInk: '#7FE3D4', portLabelBorder: '#2C4A55',
} : {
  onlineTop: '#8AA0F8', onlineBot: '#3450D8', onlineDeep: '#2A3FB0',
  offTop: '#D9E0EE', offBot: '#BFC9DE',
  extFill0: '#F4F6FC', extFill1: '#DDE4F1', extBorder: '#B9C4DC',
  coreBorder: '#EDA200', hitBorder: '#0FB9A4',
  nodeBorder: '#FFFFFF',
  label: '#101828', labelDim: '#667085',
  port: '#0C8F7F', core: '#C4870A',
  link: '#A9B8DC', linkExt: '#CBD4E6', linkActive: '#3B63FF',
  zone: 'rgba(59,99,255,.045)', zoneBorder: 'rgba(59,99,255,.14)',
  portLabelBg: 'rgba(255,255,255,.98)', portLabelInk: '#0C8F7F', portLabelBorder: '#BDEAE0',
})

// —— 设备形态语义：按名称/角色推断图标，替代「全是同一个圆角矩形」——
// 只用 ASCII / 常见几何符号：emoji 与生僻 Unicode 在 canvas 字体下
// 会渲染成 tofu 方块（显示为 □），反而更难看。
function deviceGlyph(n) {
  const s = `${n.name || ''} ${n.model || ''}`.toLowerCase()
  if (/fw|firewall|防火墙|usg|secpath/.test(s)) return 'SHIELD'
  if (/core|核心/.test(s)) return 'CORE'
  if (/acc|access|接入/.test(s)) return 'ACC'
  if (/br|branch|分支/.test(s)) return 'BR'
  if (/sw|switch|交换/.test(s)) return 'SW'
  if (/rt|router|路由|gw|网关/.test(s)) return 'GW'
  return 'DEV'
}

// 图标语义只用于 tooltip / 无障碍描述，画布上不再画符号，避免字体缺失
const GLYPH_LABEL = { SHIELD: '安全设备', CORE: '核心设备', ACC: '接入交换机',
                      BR: '分支', SW: '交换机', GW: '网关', DEV: '网络设备' }
const glyphLabel = (n) => GLYPH_LABEL[deviceGlyph(n)] || '网络设备'

// 节点视觉半径：核心 / 命中 / 按连接度渐进
function nodeSize(n, hit) {
  if (n.core) return [86, 62]
  if (hit) return [66, 50]
  const deg = n.degree || 0
  return [52 + Math.min(deg * 3, 18), 40 + Math.min(deg * 2.2, 13)]
}

/**
 * 设备造型：自定义 SVG path，做出「带端口面板的机架设备」质感，
 * 替代原来单纯的圆角矩形色块。
 *
 * path 尺寸基于 100×72 视口，echarts 会按 symbolSize 缩放。
 * 造型要素：
 *   1) 机身圆角矩形（主体，渐变色填充）
 *   2) 顶部一条窄横条 → 视觉上像机架的上沿/散热槽
 *   3) 底部一排小方点 → 端口指示灯面板，这是网络设备的辨识特征
 */
const DEVICE_PATH = 'path://M6,0 L94,0 A6,6 0 0 1 100,6 L100,66 A6,6 0 0 1 94,72 '
  + 'L6,72 A6,6 0 0 1 0,66 L0,6 A6,6 0 0 1 6,0 Z '
  // 顶部横条（机架上沿）
  + 'M8,8 L92,8 L92,13 L8,13 Z '
  // 底部端口面板：6 个小方点
  + 'M12,55 L20,55 L20,62 L12,62 Z M26,55 L34,55 L34,62 L26,62 Z '
  + 'M40,55 L48,55 L48,62 L40,62 Z M54,55 L62,55 L62,62 L54,62 Z '
  + 'M68,55 L76,55 L76,62 L68,62 Z M82,55 L90,55 L90,62 L82,62 Z'

// 外部邻居：六边形（表示「未知/非纳管」，与设备矩形形成明确区分）
const EXTERNAL_PATH = 'path://M50,0 L93,25 L93,75 L50,100 L7,75 L7,25 Z'

function nodeStyle(n, hitSet) {
  const c = C.value
  const online = isOnline(n)
  if (n.kind === 'external') {
    const dim = hitSet && !hitSet.has(n.id)
    return {
      symbol: EXTERNAL_PATH, symbolSize: 30,
      itemStyle: {
        color: { type: 'radial', x: .38, y: .3, r: .85,
                 colorStops: [{ offset: 0, color: c.extFill0 }, { offset: 1, color: c.extFill1 }] },
        borderColor: c.extBorder, borderWidth: 1.6,
        opacity: dim ? .14 : 1,
        shadowBlur: 10, shadowColor: 'rgba(16,24,40,.18)',
      },
      label: { show: hitSet ? hitSet.has(n.id) : graph.value.nodes.length <= 30,
               color: c.labelDim, fontSize: 10 },
    }
  }
  const hit = hitSet?.has(n.id)
  const size = nodeSize(n, hit)
  return {
    symbol: DEVICE_PATH, symbolSize: size,
    itemStyle: {
      // 垂直渐变 + 顶部提亮，塑造金属机身的光照感
      color: online
        ? { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: c.onlineTop }, { offset: 0.55, color: c.onlineBot },
                         { offset: 1, color: c.onlineDeep }] }
        : { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: c.offTop }, { offset: 1, color: c.offBot }] },
      borderColor: hit ? c.hitBorder : n.core ? c.coreBorder : c.nodeBorder,
      borderWidth: hit ? 3.2 : n.core ? 2.6 : 1.4,
      shadowBlur: hit ? 26 : n.core ? 18 : 10,
      shadowColor: hit ? (theme.value === 'dark' ? 'rgba(79,204,187,.55)' : 'rgba(15,185,164,.5)')
                       : n.core ? 'rgba(245,169,11,.45)' : 'rgba(16,24,40,.22)',
      shadowOffsetY: 4,
      opacity: hitSet && !hit ? .12 : 1,
    },
    label: { color: c.label, fontWeight: 600 },
  }
}

function buildOption(hitSet) {
  const positions = graph.value.positions || {}
  // layout:'none' 要求每个节点都有有限坐标：缺失的按层次布局 / 网格兜底，
  // 否则 echarts 直接不渲染该节点（表现为"手动布局没有显示"）
  const c = C.value
  const cols = Math.ceil(Math.sqrt(Math.max(graph.value.nodes.length, 1)))
  // 固定逻辑画布：layout:'none' 下若不给坐标系，echarts 会把节点坐标
  // 自行适配压缩，表现为「全部挤在中心、外圈全是空白」
  // 自动布局输出归一化坐标（0~1），echarts 按容器尺寸自动映射，
  // 不需要也不应该再给 series 指定像素级画布尺寸
  const auto = layoutMode.value === 'auto'
    ? hierarchicalPositions(graph.value.nodes, graph.value.edges)
    : {}
  const nodes = graph.value.nodes.map((n, i) => {
    let x = auto[n.id]?.[0] ?? positions[n.id]?.[0]
    let y = auto[n.id]?.[1] ?? positions[n.id]?.[1]
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      x = 140 + (i % cols) * 170
      y = 110 + Math.floor(i / cols) * 130
    }
    const st = nodeStyle(n, hitSet)
    const hit = hitSet?.has(n.id)
    return {
    id: n.id, name: n.id,
    x, y,
    ...st,
    // 节点悬停：补全设备形态、型号、地址与在线状态（画布上不画图标）
    tooltip: {
      formatter: () => {
        if (n.kind === 'external') {
          return `<b>${n.name}</b><br/>外部邻居（非纳管设备）`
        }
        const online = isOnline(n)
        return `<b>${n.name}</b><br/>`
          + `<span style="opacity:.75">${glyphLabel(n)}${n.model ? ' · ' + n.model : ''}</span><br/>`
          + `管理地址：<span style="font-family:monospace">${n.host || '—'}</span><br/>`
          + `状态：<b style="color:${online ? c.port : c.labelDim}">${online ? '在线' : '离线/未知'}</b>`
          + (n.core ? ' · <b style="color:' + c.core + '">核心设备</b>' : '')
          + (n.group ? `<br/>分组：${n.group}` : '')
      },
    },
    label: {
      show: n.kind === 'device' || hit || graph.value.nodes.length <= 30,
      position: 'bottom', distance: 6,
      // 用函数式 formatter：字符串模板在「图标为空/含富文本段」时会出现
      // `undefined` 字面量与双 name 段叠加的乱码
      formatter: () => {
        if (hit) return `${n.name}\n${hitPortOf(n.id)}`
        if (n.core) return `${n.name}\n核心设备`
        if (n.cross) return `${n.name}\n跨分组 · ${n.group || '未分组'}`
        return n.name
      },
      rich: { name: { fontWeight: 650, fontSize: 11.5,
                      color: c.label, lineHeight: 16 },
              port: { fontSize: 10.5, color: c.port,
                      fontFamily: 'JetBrains Mono, Consolas, monospace' },
              core: { fontSize: 10, color: c.core, fontWeight: 650, lineHeight: 14 } },
      color: n.core ? c.core : c.label,
      fontWeight: n.core || hit ? 700 : 600,
    }
    }
  })
  const edges = graph.value.edges.map((e, ei) => {
    const isExt = e.source.startsWith('ext:') || e.target.startsWith('ext:')
    const agg = e.aggregated || 0
    const active = hitSet && (hitSet.has(e.source) || hitSet.has(e.target))
    const dimmed = hitSet && !active
    return {
      source: e.source, target: e.target,
      lineStyle: {
        color: dimmed ? c.linkExt : active ? c.linkActive : isExt ? c.linkExt : agg ? c.linkActive : c.link,
        width: agg ? 4.5 : active ? 2.6 : isExt ? 1.4 : e.confirmed === false ? 1.6 : 2.2,
        type: isExt || e.confirmed === false ? 'dashed' : 'solid',
        opacity: dimmed ? .08 : .92,
        curveness: 0.12,
        // 链路阴影：让连线浮起来，不再是平贴的细线
        shadowBlur: active ? 10 : 0,
        shadowColor: c.linkActive,
      },
      // 接口名：默认关闭边上的 label——ECharts 对 position:'middle' 的边标签
      // 会强制按链路切向旋转（Line.js#updateLayout），近垂直链路会把文字转成
      // 竖排并与连线重叠。因此改为在 renderChart 里用组件自己的 hoverEdge
      // 状态绘制**水平胶囊**（见 renderChart 的 overlay 段）。
      label: { show: false },
      emphasis: {
        lineStyle: { width: agg ? 5.5 : 3.4, opacity: 1 },
      },
      // 边级 tooltip 关闭：完整接口信息由自绘悬停胶囊承载（原先两者同时弹出，
      // 出现一大一小两个气泡；且此处的「链路聚合 ×N」摘要不符合查阅习惯）
      tooltip: { show: false },
    }
  })
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', confine: true,
               textStyle: { fontSize: 12, color: c.label },
               backgroundColor: theme.value === 'dark' ? 'rgba(19, 26, 46, .97)' : 'rgba(255,255,255,.98)',
               borderColor: theme.value === 'dark' ? '#33406A' : '#E4E8F1',
               borderWidth: 1, padding: [9, 13],
               extraCssText: 'box-shadow: 0 12px 32px -12px rgba(16,24,40,.28); border-radius: 10px; white-space: pre-line;' },
    series: [{
      type: 'graph', layout: 'none',
      // 不能给 left/top/width/height：显式尺寸下 echarts 把画布左上角对齐到
      // 容器中心，图形整体偏出可视区。改为坐标归一化（见 normalize），
      // 让 echarts 按容器尺寸自动缩放贴合。
      data: nodes, links: edges, roam: true,
      draggable: true, z: 3,
      emphasis: { focus: 'adjacency',
                  itemStyle: { shadowBlur: 24 },
                  lineStyle: { width: 3.2, opacity: 1, color: c.linkActive } },
      blur: { itemStyle: { opacity: .16 }, lineStyle: { opacity: .05 } },
      scaleLimit: { min: .35, max: 3.2 },
    }],
    // 图例：把「在线/离线/外部/核心」的语义显式化，颜色不再是黑箱
    graphic: legendBadges(c),
  }
}

// 画布右下角的语义图例（原实现完全没有，用户看不懂配色含义）
function legendBadges(c) {
  const items = [
    { label: '在线设备', color: c.onlineBot },
    { label: '离线设备', color: theme.value === 'dark' ? '#39456A' : '#C6CFE0' },
    { label: '核心设备', color: c.coreBorder, ring: true },
    { label: '外部邻居', color: c.extBorder, ring: true },
  ]
  return items.map((it, i) => ({
    type: 'group', right: 20, bottom: 16 + i * 20,
    silent: true,
    children: [
      { type: 'circle', shape: { cx: 0, cy: 0, r: 5 },
        style: { fill: it.ring ? 'transparent' : it.color,
                 stroke: it.ring ? it.color : 'none', lineWidth: it.ring ? 1.8 : 0 } },
      { type: 'text', left: 12, top: -6,
        style: { text: it.label, fontSize: 11, fill: theme.value === 'dark' ? '#8B96B0' : '#667085' } },
    ],
  }))
}

const hitPortOf = (id) => {
  const h = hits.value.find(x => x.device_id === id)
  return h?.port || ''
}

/**
 * 自动布局：不再用纯力导向（节点乱飘、核心与接入端无序）。
 * 改为**按网络层级定位**——外部邻居在最外圈，接入/分支在中间，
 * 核心设备居中压阵。视觉上立刻读出「核心—接入—外部」三层结构。
 *
 * 输出归一化坐标（0~1）：layout:'none' 下 echarts 会把归一化坐标映射到
 * 容器可视区，无需我们猜像素尺寸，也不会出现图形溢出/被裁切。
 */
function hierarchicalPositions(nodes, edges) {
  const devices = nodes.filter(n => n.kind === 'device')
  const externals = nodes.filter(n => n.kind === 'external')

  // 分层：核心(0) → 中度数(1) → 低度数/接入(2)
  const layers = new Map()
  const layerOf = (n) => (n.core ? 0 : (n.degree || 0) >= 2 ? 1 : 2)
  devices.forEach(n => {
    const L = layerOf(n)
    if (!layers.has(L)) layers.set(L, [])
    layers.get(L).push(n)
  })

  const pos = {}
  // 归一化半径：把 [0,1] 视作画布，中心 0.5。留出 0.5/0.38 的边距给节点与标签
  const RX = 0.34, RY = 0.36

  // 核心层：居中；多核心时横向并列
  const core = layers.get(0) || []
  core.forEach((n, i) => {
    const spread = core.length > 1 ? (i - (core.length - 1) / 2) * 0.22 : 0
    pos[n.id] = [0.5 + spread, 0.5]
  })

  // 中间层 / 接入层：整层均分角度，第二层旋转半个间隔错开（避免两层叠在一起）
  ;[1, 2].forEach((L) => {
    const arr = layers.get(L) || []
    const step = (Math.PI * 2) / Math.max(arr.length, 1)
    const offset = L === 2 ? step / 2 : 0
    const k = L === 1 ? 0.6 : 1
    arr.forEach((n, i) => {
      const ang = i * step - Math.PI / 2 + offset
      pos[n.id] = [0.5 + RX * k * Math.cos(ang), 0.5 + RY * k * Math.sin(ang)]
    })
  })

  // 外圈：外部邻居贴最外层，且朝向它连的设备（视觉上成星形辐射）
  externals.forEach((n, i) => {
    const linked = edges.find(e => e.source === n.id || e.target === n.id)
    const other = linked ? (linked.source === n.id ? linked.target : linked.source) : null
    let ang
    if (other && pos[other]) {
      ang = Math.atan2(pos[other][1] - 0.5, pos[other][0] - 0.5)
    } else {
      ang = (i / Math.max(externals.length, 1)) * Math.PI * 2 - Math.PI / 2
    }
    pos[n.id] = [
      0.5 + RX * 1.32 * Math.cos(ang),
      0.5 + RY * 1.28 * Math.sin(ang),
    ]
  })
  return pos
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
let _hoverEdge = -1
watch(theme, () => {
  chart?.dispose()
  chart = null
  if (graph.value?.nodes?.length) nextTick(() => renderChart(_lastHitSet, _hoverEdge))
})

// 卸载清理：组件随页签 v-if 反复销毁/重建，必须释放 ECharts 实例与自绘图元，
// 否则 canvas 实例与悬停胶囊跨挂载残留（曾致下拉浮层 popper 堆积出现重复框）。
onBeforeUnmount(() => {
  try {
    if (_edgeOverlay && chart) chart.getZr().remove(_edgeOverlay)
  } catch { /* 实例已释放 */ }
  _edgeOverlay = null
  try { chart?.dispose() } catch { /* 已释放 */ }
  chart = null
  if (typeof window !== 'undefined') delete window.__topoChart
})

// 悬停链路的接口简写 → 在链路中点绘制**水平胶囊**。
// 为什么不用 ECharts 自带 label：graph 边的 label 恒按链路切向旋转
// （Line.js#updateLayout 里 label.rotation = -atan2(...)），近垂直链路上
// 文字会竖排并与连线重叠。自绘到 zrender 顶层（z:10）可完全掌控水平排布，
// 并保证胶囊始终压在所有图元之上，不被节点/连线遮挡。
let _edgeOverlay = null
// 胶囊宽度：canvas measureText 实测（与绘制同一字体栈，像素级贴合）；
// canvas 不可用时回退逐字符估算。
const HOVER_FONT = '600 10.5px "JetBrains Mono", Consolas, monospace'
let _measureCtx = null
function portLabelWidth(text) {
  try {
    if (!_measureCtx) _measureCtx = document.createElement('canvas').getContext('2d')
    if (_measureCtx) {
      _measureCtx.font = HOVER_FONT
      return _measureCtx.measureText(text).width
    }
  } catch { /* 回退估算 */ }
  let w = 0
  for (const ch of text) {
    if (ch === '/' || ch === '↔' || ch === ' ') w += 7.2
    else if (ch === 'X' || ch === 'G') w += 7.4
    else if (ch.codePointAt(0) > 0x2e7f) w += 10.5
    else w += 6.5
  }
  return w
}
function drawHoverEdgeLabel(hoverEdge) {
  try {
    const zr = chart?.getZr()
    if (!zr) return
    if (_edgeOverlay) { try { zr.remove(_edgeOverlay) } catch { /* 已移除 */ } }
    _edgeOverlay = null
    if (hoverEdge < 0) return
    const e = graph.value.edges[hoverEdge]
    if (!e) return
    const pairLines = (e.links?.length ? e.links : [{ frm_port: e.from_port, to_port: e.to_port }])
      .map(l => `${l.frm_port || '?'}  ↔  ${l.to_port || '?'}`)
    if (e.confirmed === false) pairLines.push('（单侧 LLDP，未互证）')
    const pair = pairLines.join('\n')
    if (!pair.trim()) return

    // 取两端节点的像素坐标。
    // 踩坑 1：graph 节点的 getLayout() 返回**归一化 0~1 坐标**
    //         （buildOption 用 hierarchicalPositions 输出的就是归一化值）；
    // 踩坑 2：eachNode 回调里的 node.name 为 undefined，无法按 id 匹配 —— 因此
    //         按「节点在 graph.value.nodes 中的下标」定位（series 数据顺序一致）；
    // 踩坑 3：归一化坐标**不能直接乘容器尺寸** —— echarts 在 layout:'none' 下
    //         会做自适应缩放并居中，真实映射矩阵挂在边图元的 transform 上
    //         （实测 [s,0,0,s,tx,ty]，s≈918.52、容器 1238×620，二者不等）。
    //         所以这里直接读 transform，用 像素 = 归一化*s + t 换算。
    const idxOf = new Map(graph.value.nodes.map((n, i) => [n.id, i]))
    const si = idxOf.get(e.source)
    const ti = idxOf.get(e.target)
    if (si == null || ti == null) return
    const graphModel = chart.getModel().getSeriesByIndex(0).getGraph()

    let childEl = null
    graphModel.eachEdge(ge => {
      if (ge.dataIndex === hoverEdge) {
        const el = ge.getGraphicEl()
        const kids = el?.children ? el.children() : []
        childEl = kids.find(k => k.type === 'ec-line') || kids[0] || null
      }
    })
    if (!childEl) return
    // transform 形如 [a,b,c,d,e,f]：x' = a*x + c*y + e，y' = b*x + d*y + f
    const tr = childEl.transform || [1, 0, 0, 1, 0, 0]
    const toPx = (nx, ny) => [tr[0] * nx + tr[2] * ny + tr[4], tr[1] * nx + tr[3] * ny + tr[5]]

    const layouts = []
    graphModel.eachNode(n => {
      const nl = n.getLayout()
      layouts.push([Array.isArray(nl) ? nl[0] : nl?.x, Array.isArray(nl) ? nl[1] : nl?.y])
    })
    if (!layouts[si] || !layouts[ti]) return
    // 边中点：沿**实际链路曲线**取参数中点，再沿曲线法向推开一段距离。
    // 直接用两端直线中点会偏离（连线带 curveness，二次贝塞尔中间明显拱起），
    // 表现为胶囊压在连线上。这里按边图元 shape 里的控制点做同样的贝塞尔求值。
    const sh = childEl.shape || {}
    const p0 = [sh.x1, sh.y1]
    const p2 = [sh.x2, sh.y2]
    // ECharts 的 cpx1/cpy1 已是二次贝塞尔控制点（归一化坐标系内）
    const p1 = (Number.isFinite(sh.cpx1) && Number.isFinite(sh.cpy1))
      ? [sh.cpx1, sh.cpy1]
      : [(p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2]
    const at = t => [
      (1 - t) * (1 - t) * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
      (1 - t) * (1 - t) * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1],
    ]
    const A = toPx(...at(0.42))
    const B = toPx(...at(0.58))
    const M = toPx(...at(0.5))
    // 曲线在中点处的方向 → 法向量
    let nx = -(B[1] - A[1]), ny = B[0] - A[0]
    const nlen = Math.hypot(nx, ny) || 1
    nx /= nlen; ny /= nlen

    // 胶囊锚点：从曲线中点沿**法向**推开，偏移量需盖过「连线半宽 + 胶囊在法向
    // 上的投影」。胶囊是水平矩形，斜线场景下其左右端仍会与斜线相交，所以投影
    // 长度取 (bw/2)*|nx| + (bh/2)*|ny|，再加安全间隙。
    const fs = 10.5
    const lineH = fs + 4
    const textW = Math.max(...pairLines.map(portLabelWidth))
    const padX = 7, padY = 3.5
    const bw = textW + padX * 2
    const bh = pairLines.length * lineH + padY * 2
    // 胶囊在法向上的半投影 + 连线半宽 + 间隙
    const proj = (bw / 2) * Math.abs(nx) + (bh / 2) * Math.abs(ny)
    const OFF = proj + 6
    let ox = nx * OFF, oy = ny * OFF
    let cx = M[0] + ox
    let cy = M[1] + oy
    // 贴近画布边缘时反向推开，避免胶囊被裁切
    const W = zr.getWidth(), H = zr.getHeight()
    if (cx - bw / 2 < 2 || cx + bw / 2 > W - 2 || cy - bh / 2 < 2 || cy + bh / 2 > H - 2) {
      cx = M[0] - ox
      cy = M[1] - oy
    }
    const isDark = theme.value === 'dark'
    const bg = isDark ? 'rgba(19,26,46,.96)' : 'rgba(255,255,255,.98)'
    const ink = isDark ? '#7FE3D4' : '#0C8F7F'
    const border = isDark ? '#2C4A55' : '#BDEAE0'
    const g = new echarts.graphic.Group({ z: 10, silent: true, x: cx, y: cy })
    g.add(new echarts.graphic.Rect({
      shape: { x: -bw / 2, y: -bh / 2, r: 5, width: bw, height: bh },
      style: { fill: bg, stroke: border, lineWidth: 1, shadowBlur: 10, shadowColor: 'rgba(16,24,40,.22)' },
    }))
    g.add(new echarts.graphic.Text({
      style: {
        text: pair, x: 0, y: 0,
        // zrender 5 的 Text 样式属性是 align / verticalAlign（textAlign/textVerticalAlign
        // 是旧版名，会被静默忽略 → 文字按默认左上角锚定，与中心锚定的背景胶囊错位）
        align: 'center', verticalAlign: 'middle',
        fill: ink, font: HOVER_FONT,
        lineHeight: lineH,
      },
    }))
    zr.add(g)
    _edgeOverlay = g
  } catch { /* 绘制失败不应影响主流程 */ }
}

function renderChart(hitSet = null, hoverEdge = -1) {
  _lastHitSet = hitSet
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value, theme.value === 'dark' ? 'dark' : undefined)
    // 调试钩子：便于自动化验证（E2E）读取实例状态与模拟悬停
    if (typeof window !== 'undefined') window.__topoChart = chart
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

    // ---- 链路悬停 → 浮出接口简写胶囊 ----
    // 不整体 setOption（那会打断 ECharts 的 tooltip 与高亮动画），
    // 改为在 zrender 顶层叠加/移除一个水平胶囊图元。
    chart.on('mouseover', params => {
      if (params.dataType !== 'edge') return
      if (params.dataIndex === _hoverEdge) return
      _hoverEdge = params.dataIndex
      drawHoverEdgeLabel(_hoverEdge)
    })
    chart.on('mouseout', params => {
      if (params.dataType !== 'edge') return
      if (_hoverEdge === -1) return
      _hoverEdge = -1
      drawHoverEdgeLabel(-1)
    })
    // 鼠标离开画布时兜底清理，避免胶囊残留
    chart.getZr().on('globalout', () => {
      if (_hoverEdge === -1) return
      _hoverEdge = -1
      drawHoverEdgeLabel(-1)
    })
  }
  chart.setOption({ backgroundColor: 'transparent', ...buildOption(hitSet) }, true)
  // setOption 会重建 zrender 元素，叠加层需重绘（悬停态跨重绘保持）
  drawHoverEdgeLabel(_hoverEdge)
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
  // 单击条目：聚焦命中设备节点（原注释误写「双击」，与实际 @click 行为不符）
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
  _edgeOverlay = null
  _hoverEdge = -1
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
.nt-canvas {
  height: 620px; width: 100%;
  /* 层次布局是放射状的，中心留一点微光把「核心」托起来 */
  background:
    radial-gradient(ellipse 40% 44% at 50% 50%, var(--sfa-tint-primary-soft) 0%, transparent 70%),
    radial-gradient(circle at 1px 1px, var(--sfa-border-soft) 1px, transparent 0) 0 0 / 22px 22px;
  border-radius: var(--sfa-r-md);
}
.nt-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }

.nt-pop {
  position: absolute; top: 18px; right: 18px; width: 260px; z-index: 5;
  /* 原为硬编码 rgba(255,255,255,.97) → 暗色主题下整块漏白；改用主题令牌 */
  background: var(--sfa-surface); backdrop-filter: blur(8px);
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
