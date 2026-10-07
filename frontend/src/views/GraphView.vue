<template>
  <div class="gv-page">
    <div class="page-head">
      <div>
        <h2 class="ph-title">知识图谱</h2>
        <p class="ph-desc">词条为节点、共享标签为连线，节点越大代表引用越多；点击节点查看词条详情</p>
      </div>
      <div class="ph-actions">
        <el-button :loading="loading" @click="loadAll">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button type="primary" @click="router.push('/knowledge')">
          <el-icon><Collection /></el-icon>&nbsp;前往知识库
        </el-button>
      </div>
    </div>

    <!-- 概览统计 -->
    <div class="gv-stats">
      <div class="sfa-stat"><div class="num">{{ stats.total || 0 }}</div><div class="lbl">知识词条</div></div>
      <div class="sfa-stat"><div class="num">{{ stats.categories?.length || 0 }}</div><div class="lbl">分类领域</div></div>
      <div class="sfa-stat"><div class="num">{{ edgeCount }}</div><div class="lbl">关联连线</div></div>
      <div class="sfa-stat"><div class="num">{{ stats.tags?.length || 0 }}</div><div class="lbl">标签</div></div>
    </div>

    <!-- 主视图：图谱 + 侧栏 -->
    <div class="gv-main">
      <div class="page-card gv-card">
        <div class="gv-toolbar">
          <div class="col-title" style="margin: 0">
            <el-icon><Share /></el-icon> 图谱视图
          </div>
          <div class="gv-tools">
            <el-select v-model="activeCat" placeholder="全部分类" clearable size="small"
                       style="width: 150px">
              <el-option v-for="c in stats.categories || []" :key="c" :label="c" :value="c" />
            </el-select>
            <el-input v-model="keyword" placeholder="搜索词条" clearable size="small"
                      style="width: 170px">
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-tooltip content="重置缩放与平移" placement="top">
              <el-button size="small" @click="resetView">
                <el-icon><Aim /></el-icon>
              </el-button>
            </el-tooltip>
          </div>
        </div>
        <div class="gv-chart-wrap" v-loading="loading" element-loading-text="正在加载知识图谱…">
          <div ref="graphRef" class="gv-chart" role="img" :aria-label="ariaLabel"></div>
          <el-empty v-if="!loading && !visibleGraph.nodes.length" description="暂无可视化词条"
                    class="gv-empty" />
        </div>
        <div class="gv-legend" v-if="visibleCategories.length">
          <span v-for="(c, i) in visibleCategories" :key="c" class="gv-legend-item"
                :style="{ '--c': palette(i) }">
            <i class="gv-dot"></i>{{ c }}
            <em>{{ catCount(c) }}</em>
          </span>
        </div>
      </div>

      <div class="gv-side">
        <div class="page-card gv-side-card">
          <div class="col-title"><el-icon><Histogram /></el-icon> 分类分布</div>
          <div ref="catRef" class="chart chart-small"></div>
        </div>
        <div class="page-card gv-side-card">
          <div class="col-title"><el-icon><TrendCharts /></el-icon> 沉淀时间线</div>
          <div ref="timeRef" class="chart chart-small"></div>
        </div>
        <div class="page-card gv-side-card gv-hub-card">
          <div class="col-title"><el-icon><Star /></el-icon> 核心词条</div>
          <div v-if="hubs.length" class="gv-hubs">
            <button v-for="h in hubs" :key="h.id" class="gv-hub" @click="openEntryById(h.id)">
              <span class="gv-hub-name">{{ h.name }}</span>
              <span class="gv-hub-deg">{{ h.deg }} 关联</span>
            </button>
          </div>
          <div v-else class="gv-hub-empty">暂无足够关联数据</div>
        </div>
      </div>
    </div>

    <!-- 词条详情抽屉 -->
    <el-drawer v-model="showDetail" size="520px" :title="detail?.topic || '词条详情'">
      <template v-if="detail">
        <div class="gv-detail-top">
          <el-tag size="small" effect="light">{{ detail.category }}</el-tag>
          <span class="gv-detail-date">{{ detail.created_at }}</span>
        </div>
        <p class="gv-detail-summary">{{ detail.summary }}</p>
        <div v-if="detail.key_points?.length" class="detail-block">
          <b>核心要点</b>
          <ul>
            <li v-for="(p, i) in detail.key_points" :key="i">{{ p }}</li>
          </ul>
        </div>
        <div class="md-body detail-block" v-html="render(detail.content_md)"></div>
        <div v-if="detail.tags?.length" class="detail-block">
          <b>标签</b>
          <div class="gv-detail-tags">
            <el-tag v-for="t in detail.tags" :key="t" size="small" effect="plain">{{ t }}</el-tag>
          </div>
        </div>
        <div v-if="detail.references?.length" class="detail-block">
          <b>官方引用</b>
          <div v-for="(r, i) in detail.references" :key="i" class="ref-item">
            <template v-if="refUrl(r)">
              <a :href="refUrl(r)" target="_blank" rel="noopener noreferrer" class="ref-link">
                <el-icon style="vertical-align: -2px"><Link /></el-icon> {{ refTitle(r) }}
              </a>
            </template>
            <span v-else class="ref-plain">[{{ i + 1 }}] {{ refTitle(r) }}</span>
          </div>
        </div>
        <div class="kb-sub" style="margin-top: 12px">
          来源对话：{{ detail.conv_id ? detail.conv_id.slice(0, 16) + '…' : '未知' }} · 沉淀于 {{ detail.created_at }}
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { renderMarkdown } from '../chat/markdown'
import { useTheme } from '../composables/useTheme'
import { router } from '../router'
import { KB } from '../api.js'

const render = renderMarkdown
const { theme } = useTheme()

const refUrl = (r) => {
  if (r && typeof r === 'object') return r.url || ''
  if (typeof r === 'string' && /^https?:\/\//.test(r)) return r
  return ''
}
const refTitle = (r) => {
  if (r && typeof r === 'object') return r.title || r.url || '（未命名引用）'
  return String(r)
}

const loading = ref(false)
const stats = ref({ total: 0, categories: [], tags: [], timeline: [], graph: { nodes: [], edges: [], categories: [] } })
const detail = ref(null)
const showDetail = computed({ get: () => !!detail.value, set: v => { if (!v) detail.value = null } })
const activeCat = ref('')
const keyword = ref('')

const graphRef = ref(null)
const catRef = ref(null)
const timeRef = ref(null)
let graphChart = null
let catChart = null
let timeChart = null

// —— 调色板：亮/暗双套，暗色下提亮保证深底对比 ——
const PALETTE = ['#3B63FF', '#0FB9A4', '#7C5CFC', '#F5A90B', '#E5484D', '#6A87FF', '#12B0A0', '#9AA5BB']
const PALETTE_DARK = ['#6A87FF', '#4FCCBB', '#A78BFF', '#F8C24D', '#FF8A8E', '#8FA6E8', '#3FD3C0', '#B4BDD3']
const palette = (i) => (theme.value === 'dark' ? PALETTE_DARK : PALETTE)[i % PALETTE.length]

const fullGraph = computed(() => stats.value.graph || { nodes: [], edges: [], categories: [] })

// 分类 / 关键词过滤：只保留命中的节点及其相互连线
const visibleGraph = computed(() => {
    const g = fullGraph.value
    const kw = keyword.value.trim().toLowerCase()
    const nodes = (g.nodes || []).filter(n => {
      if (activeCat.value && n.category !== activeCat.value) return false
      if (kw && !`${n.name} ${n.category}`.toLowerCase().includes(kw)) return false
      return true
    })
    const ids = new Set(nodes.map(n => n.id))
    const edges = (g.edges || []).filter(e => ids.has(e.source) && ids.has(e.target))
    return { nodes, edges, categories: [...new Set(nodes.map(n => n.category))] }
})

const visibleCategories = computed(() => visibleGraph.value.categories || [])
const edgeCount = computed(() => (fullGraph.value.edges || []).length)
const catCount = (c) => (fullGraph.value.nodes || []).filter(n => n.category === c).length

// 核心词条：按连线度数排名
const hubs = computed(() => {
  const g = fullGraph.value
  const deg = new Map()
  ;(g.edges || []).forEach(e => {
    deg.set(e.source, (deg.get(e.source) || 0) + 1)
    deg.set(e.target, (deg.get(e.target) || 0) + 1)
  })
  return (g.nodes || [])
    .map(n => ({ id: n.id, name: n.name, deg: deg.get(n.id) || 0 }))
    .filter(n => n.deg > 0)
    .sort((a, b) => b.deg - a.deg)
    .slice(0, 6)
})

const ariaLabel = computed(() => {
  const { nodes, edges, categories } = visibleGraph.value
  return `知识图谱：${nodes.length} 个词条节点、${edges.length} 条关联连线，覆盖 ${categories.length} 个分类。`
    + '节点交互（拖拽、缩放、点击查看详情）需使用鼠标操作。'
})

async function openEntryById(id) {
  try {
    detail.value = await KB.entry(id)
  } catch (e) {
    ElMessage.error(`打开知识词条失败：${e?.message || '请稍后重试'}`)
  }
}

/**
 * 标签方位：随节点在圆周上的位置走——左半圆贴左侧、右半圆贴右侧、
 * 顶部/底部贴上下。一律 'right' 时扇区一密就必然互相压字。
 * 判定「偏水平」的容差随横向偏移放大：外圈节点只要径向以水平为主，
 * 就应让标签走左右两侧，避免与同扇区邻居撞字。
 */
function labelSide(x, y) {
  const dx = x - 0.5, dy = y - 0.5
  const horizW = 0.10 + Math.abs(dx) * 0.28
  if (Math.abs(dx) < horizW && dy < 0) return 'top'
  if (Math.abs(dx) < horizW && dy > 0) return 'bottom'
  return dx < 0 ? 'left' : 'right'
}

/**
 * 分类环绕式放射布局：每分类占一个扇区，枢纽词条靠内圈。
 * 输出归一化坐标（0~1），由 echarts 映射到容器可视区。
 */
function layoutRadial(graph) {
  const cats = graph.categories || []
  const nodes = graph.nodes || []
  if (!nodes.length) return { nodes: [], categories: cats }

  const byCat = new Map()
  cats.forEach(c => byCat.set(c, []))
  nodes.forEach(nd => {
    const c = byCat.has(nd.category) ? nd.category : (cats[0] || '')
    byCat.get(c).push(nd)
  })

  const deg = new Map()
  ;(graph.edges || []).forEach(e => {
    deg.set(e.source, (deg.get(e.source) || 0) + 1)
    deg.set(e.target, (deg.get(e.target) || 0) + 1)
  })

  const cx = 0.5, cy = 0.5
  const RX = 0.355, RY = 0.375
  const R_IN = 0.52, R_OUT = 1
  // 窄容器保护：标签是固定像素宽，容器越窄占比越大，整体收缩半径
  const narrowK = (() => {
    const w = graphRef.value?.getBoundingClientRect().width || 0
    if (!w) return 1
    return w < 560 ? 0.78 : w < 760 ? 0.88 : 1
  })()
  const out = []
  const activeCats = cats.filter(c => (byCat.get(c) || []).length)
  const total = activeCats.length

  activeCats.forEach((cat, ci) => {
    const items = (byCat.get(cat) || []).slice()
      .sort((a, b) => (deg.get(b.id) || 0) - (deg.get(a.id) || 0))
    const span = (Math.PI * 2) / Math.max(total, 1)
    const pad = Math.min(span * 0.14, 0.22)
    const a0 = ci * span - Math.PI / 2 + pad
    const a1 = (ci + 1) * span - Math.PI / 2 - pad

    // 按「圈」分批：先外圈（角度铺开间距最大），再中圈，最后内圈。
    // 若每圈只放 1 个且角度都取正中，两点会落在**同一条射线只差径向**，
    // 此时必须强制角度错开，否则无论径向差多大都会撞在一起。
    const RINGS = [R_OUT, (R_IN + R_OUT) / 2, R_IN]
    const per = Math.max(1, Math.ceil(items.length / RINGS.length))
    const solo = per === 1 && items.length > 1
    items.forEach((nd, i) => {
      const ring = Math.min(RINGS.length - 1, Math.floor(i / per))
      const idxInRing = i % per
      const cnt = Math.min(per, items.length - ring * per)
      let t = cnt > 1 ? idxInRing / (cnt - 1) : 0.5
      if (solo) t = 0.5 + (ring % 2 === 0 ? -0.34 : 0.34) + ring * 0.06
      else if (ring === 1 && cnt > 1) t += 1 / (cnt - 1) / 2
      const ang = a0 + (a1 - a0) * Math.min(Math.max(t, 0), 1)
      const r = RINGS[ring] * narrowK
      out.push({
        ...nd,
        catIndex: Math.max(0, cats.indexOf(nd.category)),
        x: cx + RX * r * Math.cos(ang),
        y: cy + RY * r * Math.sin(ang),
      })
    })
  })

  // ---- 后处理：解重叠 ----
  // 约束的是**标签外接矩形**而非节点圆心距：标签比节点宽得多，
  // 只拉开节点距离标签照样会相交。
  const box = graphRef.value?.getBoundingClientRect()
  const cssW = box?.width || 0
  const cssH = box?.height || 0
  const LABEL_W = cssW > 100 ? 118 / cssW : 0.16
  const LABEL_H = cssH > 100 ? 26 / cssH : 0.056
  const labelBox = (n) => {
    const side = labelSide(n.x, n.y)
    let bx = n.x, by = n.y
    if (side === 'right') bx += LABEL_W / 2 + 0.018
    else if (side === 'left') bx -= LABEL_W / 2 + 0.018
    else if (side === 'top') by -= LABEL_H / 2 + 0.03
    else by += LABEL_H / 2 + 0.03
    return { cx: bx, cy: by }
  }
  const rectOverlap = (a, b) =>
    Math.abs(a.cx - b.cx) < LABEL_W && Math.abs(a.cy - b.cy) < LABEL_H

  for (let pass = 0; pass < 40; pass++) {
    let moved = false
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const a = out[i], b = out[j]
        const MIN_NODE = cssW > 100 ? 62 / cssW : 0.086
        const ddx = a.x - b.x, ddy = (a.y - b.y) * 1.18
        const d = Math.hypot(ddx, ddy)
        if (d < MIN_NODE) {
          const push = (MIN_NODE - (d || 0)) / 2 || 0.004
          const ux = d ? ddx / d : 1, uy = d ? ddy / d : 0
          a.x += ux * push; a.y += (uy * push) / 1.18
          b.x -= ux * push; b.y -= (uy * push) / 1.18
          moved = true
        }
        const ba = labelBox(a), bb = labelBox(b)
        if (rectOverlap(ba, bb)) {
          let vx = a.x - b.x, vy = (a.y - b.y) * 1.18
          let vd = Math.hypot(vx, vy)
          if (vd < 1e-6) { vx = 1; vy = 0; vd = 1 }
          const need = MIN_NODE * 1.3
          const push = Math.max(0.008, (need - vd) / 2)
          a.x += (vx / vd) * push; a.y += ((vy / vd) * push) / 1.18
          b.x -= (vx / vd) * push; b.y -= ((vy / vd) * push) / 1.18
          moved = true
        }
      }
    }
    if (!moved) break
  }
  out.forEach(n => {
    n.x = Math.min(0.955, Math.max(0.045, n.x))
    n.y = Math.min(0.95, Math.max(0.05, n.y))
  })

  return { nodes: out, categories: activeCats }
}

/**
 * 节点造型：发光球体 + 类别色环 + 内层高光。
 * 用三层径向渐变叠出「玻璃球」质感，比单色圆点更有科技感；
 * 枢纽节点额外加一圈外发光环，一眼可辨重要性。
 */
function nodeVisual(n, deg, maxDeg, dark) {
  const base = palette(n.catIndex)
  const t = maxDeg > 0 ? deg / maxDeg : 0
  return {
    color: {
      type: 'radial', x: 0.36, y: 0.30, r: 0.82,
      colorStops: [
        { offset: 0, color: dark ? '#FFFFFF' : '#FFFFFF' },
        { offset: 0.24, color: mix(base, '#FFFFFF', dark ? 0.30 : 0.42) },
        { offset: 0.72, color: base },
        { offset: 1, color: mix(base, '#000000', dark ? 0.30 : 0.18) },
      ],
    },
    borderColor: dark ? 'rgba(255,255,255,.30)' : 'rgba(255,255,255,.92)',
    borderWidth: 1.5,
    // 外发光随连接度增强：枢纽自带光晕
    shadowBlur: 14 + t * 26,
    shadowColor: hexA(base, 0.55 + t * 0.3),
    shadowOffsetY: 0,
  }
}

// 颜色混合：把 hex 与目标色按比例插值（用于渐变停靠点）
function mix(hex, target, k) {
  const p = (h) => {
    const s = h.replace('#', '')
    const v = s.length === 3 ? s.split('').map(c => c + c).join('') : s
    return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)]
  }
  const a = p(hex), b = p(target)
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * k))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}
// hex → rgba 字符串
function hexA(hex, alpha) {
  const s = hex.replace('#', '')
  const v = s.length === 3 ? s.split('').map(c => c + c).join('') : s
  const r = parseInt(v.slice(0, 2), 16), g = parseInt(v.slice(2, 4), 16), b = parseInt(v.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

function buildGraphOption(graph, categories) {
  const dark = theme.value === 'dark'
  const laid = layoutRadial(graph)
  const edgeDeg = new Map()
  ;(graph.edges || []).forEach(e => {
    edgeDeg.set(e.source, (edgeDeg.get(e.source) || 0) + 1)
    edgeDeg.set(e.target, (edgeDeg.get(e.target) || 0) + 1)
  })
  const maxDeg = Math.max(1, ...laid.nodes.map(n => edgeDeg.get(n.id) || 0))

  const data = laid.nodes.map(n => {
    const deg = edgeDeg.get(n.id) || 0
    const base = n.symbolSize || 22
    const size = Math.round(Math.min(base * 1.15 + (deg / maxDeg) * 10, 56))
    const lpos = labelSide(n.x, n.y)
    const align = lpos === 'right' ? 'left' : lpos === 'left' ? 'right' : 'center'
    return {
      id: n.id, name: n.name, value: size,
      category: n.catIndex, x: n.x, y: n.y,
      symbolSize: size,
      itemStyle: nodeVisual(n, deg, maxDeg, dark),
      label: {
        show: true, position: lpos, distance: 7, align,
        formatter: () => (n.name || '').length > 10 ? `${n.name.slice(0, 10)}…` : n.name,
        fontSize: 10.5, fontWeight: 600,
        color: dark ? '#C6CEE2' : '#475467',
        textBorderColor: dark ? '#101728' : '#FFFFFF',
        textBorderWidth: 3,
      },
      emphasis: {
        scale: 1.16,
        itemStyle: { shadowBlur: 34, shadowColor: hexA(palette(n.catIndex), 0.85) },
        label: { fontSize: 12, fontWeight: 700 },
      },
    }
  })

  const links = (graph.edges || []).map(e => ({
    source: e.source, target: e.target,
    lineStyle: {
      color: dark ? '#3D4A6E' : '#D2DAEC',
      width: 1.2,
      curveness: 0.22,
      opacity: 0.8,
    },
  }))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item', confine: true, enterable: false,
      backgroundColor: dark ? 'rgba(19,26,46,.96)' : 'rgba(255,255,255,.98)',
      borderColor: dark ? '#33406A' : '#E4E8F1',
      borderWidth: 1, padding: [9, 13],
      textStyle: { fontSize: 12, color: dark ? '#E4E9F4' : '#101828' },
      extraCssText: 'box-shadow: 0 12px 32px -12px rgba(16,24,40,.28); border-radius: 10px;',
      formatter: (p) => {
        if (p.dataType !== 'node') return ''
        const cat = categories[p.data.category] || p.data.category || ''
        const c = palette(p.data.category)
        const deg = edgeDeg.get(p.data.id) || 0
        return `<div style="font-weight:700;margin-bottom:3px">${p.data.name}</div>`
          + `<div style="display:inline-flex;align-items:center;gap:5px;font-size:11px;opacity:.75">`
          + `<span style="width:7px;height:7px;border-radius:50%;background:${c};display:inline-block"></span>`
          + `${cat} · ${deg} 条关联</div>`
      },
    },
    series: [{
      type: 'graph', layout: 'none', roam: true, draggable: true,
      data, links,
      emphasis: { focus: 'adjacency',
                  lineStyle: { width: 2.4, opacity: 1, color: dark ? '#6A87FF' : '#7C9BFF' } },
      blur: { itemStyle: { opacity: 0.18 }, lineStyle: { opacity: 0.06 } },
      scaleLimit: { min: 0.4, max: 3.2 },
      lineStyle: { color: dark ? '#3D4A6E' : '#D2DAEC' },
    }],
  }
}

function renderCharts() {
  const { categories, timeline } = stats.value
  const g = visibleGraph.value
  if (graphRef.value) {
    if (!graphChart) {
      graphChart = echarts.init(graphRef.value, theme.value === 'dark' ? 'dark' : undefined)
      graphChart.on('click', (p) => { if (p.dataType === 'node' && p.data?.id) openEntryById(p.data.id) })
    }
    graphChart.setOption(buildGraphOption(g, g.categories), true)
  }
  if (catRef.value) {
    catChart = catChart || echarts.init(catRef.value, theme.value === 'dark' ? 'dark' : undefined)
    const cats = [...categories].sort((a, b) => a.value - b.value)
    catChart.setOption({
      backgroundColor: 'transparent',
      tooltip: {},
      grid: { left: 80, right: 24, top: 10, bottom: 24 },
      xAxis: { type: 'value', minInterval: 1 },
      yAxis: { type: 'category', data: cats.map(c => c.name), axisLabel: { fontSize: 11 } },
      series: [{ type: 'bar', data: cats.map(c => c.value), barMaxWidth: 16,
                 itemStyle: { color: '#3B63FF', borderRadius: [0, 6, 6, 0] } }],
    })
  }
  if (timeRef.value) {
    timeChart = timeChart || echarts.init(timeRef.value, theme.value === 'dark' ? 'dark' : undefined)
    timeChart.setOption({
      backgroundColor: 'transparent',
      tooltip: {},
      grid: { left: 36, right: 16, top: 14, bottom: 26 },
      xAxis: { type: 'category', data: timeline.map(t => t.day.slice(5)), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{ type: 'line', data: timeline.map(t => t.value), smooth: true,
                 areaStyle: { opacity: 0.14, color: '#0FB9A4' },
                 itemStyle: { color: '#0FB9A4' }, lineStyle: { color: '#0FB9A4', width: 2.5 } }],
    })
  }
}

function resizeAll() {
  graphChart?.resize(); catChart?.resize(); timeChart?.resize()
}

function resetView() {
  activeCat.value = ''
  keyword.value = ''
}

// 过滤变化时重绘
watch([activeCat, keyword], () => {
  nextTick(renderCharts)
})

// 主题切换：echarts 的文字/轴色注册在 init，需销毁重建
watch(theme, () => {
  graphChart?.dispose(); catChart?.dispose(); timeChart?.dispose()
  graphChart = catChart = timeChart = null
  if (stats.value) nextTick(renderCharts)
})

async function loadAll() {
  loading.value = true
  try {
    stats.value = await KB.stats()
    await nextTick()
    renderCharts()
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadAll()
  window.addEventListener('resize', resizeAll)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeAll)
  graphChart?.dispose(); catChart?.dispose(); timeChart?.dispose()
  graphChart = catChart = timeChart = null
})
</script>

<style scoped>
.gv-page { display: flex; flex-direction: column; animation: sfa-fade-up .3s var(--ease-out); }
.gv-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 14px 0; }

.gv-main { display: flex; gap: 16px; align-items: stretch; }
.gv-card { flex: 1 1 auto; min-width: 0; padding: 14px 16px; display: flex; flex-direction: column; }
.gv-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.gv-tools { display: flex; align-items: center; gap: 8px; }

.gv-chart-wrap { position: relative; flex: 1; min-height: 540px; }
.gv-chart {
  width: 100%; height: 100%; min-height: 540px;
  border-radius: var(--sfa-r-md);
  /* 中心微光 + 极淡网格：衬托放射布局的中心枢纽 */
  background:
    radial-gradient(ellipse 46% 50% at 50% 50%, var(--sfa-tint-primary-soft) 0%, transparent 72%),
    radial-gradient(circle at 1px 1px, var(--sfa-border-soft) 1px, transparent 0) 0 0 / 24px 24px;
}
.gv-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }

.gv-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; padding: 10px 2px 2px; }
.gv-legend-item { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--sfa-text-2); }
.gv-legend-item .gv-dot {
  width: 9px; height: 9px; border-radius: 50%; background: var(--c);
  box-shadow: 0 0 6px var(--c);
}
.gv-legend-item em { font-style: normal; color: var(--sfa-text-4); font-size: 10.5px; }

.gv-side { flex: 0 0 300px; display: flex; flex-direction: column; gap: 12px; }
.gv-side-card { padding: 14px 16px; }
.chart { width: 100%; }
.chart-small { height: 160px; }

.gv-hubs { display: flex; flex-direction: column; gap: 5px; }
.gv-hub {
  display: flex; align-items: center; gap: 8px; width: 100%;
  background: var(--sfa-panel-soft); border: 1px solid var(--sfa-border);
  border-radius: var(--sfa-r-sm); padding: 7px 10px; cursor: pointer;
  font-family: inherit; font-size: 12px; color: var(--sfa-text); text-align: left;
  transition: all var(--dur-1) var(--ease-out);
}
.gv-hub:hover { background: var(--sfa-tint-primary); border-color: var(--sfa-chip-border); transform: translateX(2px); }
.gv-hub-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gv-hub-deg { font-size: 10.5px; color: var(--sfa-text-4); font-family: var(--sfa-mono); flex-shrink: 0; }
.gv-hub-empty { color: var(--sfa-text-4); font-size: 12px; padding: 6px 0; }

.gv-detail-top { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.gv-detail-date { color: var(--sfa-text-4); font-size: 11.5px; font-family: var(--sfa-mono); }
.gv-detail-summary { color: var(--sfa-text-2); font-size: 13px; line-height: 1.7; margin: 0; }
.gv-detail-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.detail-block { margin-top: 14px; }
.detail-block ul { margin: 6px 0 0; padding-left: 18px; }
.detail-block li { margin: 3px 0; font-size: 13px; }
.ref-item { font-size: 13px; margin-top: 6px; word-break: break-all; }
.ref-link { color: var(--sfa-primary); text-decoration: none; }
.ref-link:hover { text-decoration: underline; }
.ref-plain { color: var(--sfa-text-2); }
.kb-sub { color: var(--sfa-text-3); font-size: 12px; margin-left: 8px; }

@media (max-width: 1100px) {
  .gv-main { flex-direction: column; }
  .gv-side { flex: 1 1 auto; flex-direction: row; }
  .gv-side .gv-side-card { flex: 1; }
}
@media (max-width: 720px) {
  .gv-side { flex-direction: column; }
  .gv-chart, .gv-chart-wrap { min-height: 420px; }
}
</style>
