<template>
  <div class="kb-page">
    <!-- 顶部：标题 + 统计卡片 + 操作 -->
    <div class="page-card kb-header">
      <div class="kb-header-top">
        <div>
          <b style="font-size: 16px">个人知识库</b>
          <span class="kb-sub">勾选「查询知识库」的对话经 LLM WIKI 提炼，沉淀为你的个性化知识网络</span>
        </div>
        <div style="display: flex; gap: 8px">
          <el-button size="small" :loading="processing" :disabled="!pendingCount" @click="processPending">
            <el-icon><MagicStick /></el-icon> 立即沉淀{{ pendingCount ? `（${pendingCount} 条对话）` : '' }}
          </el-button>
          <el-button size="small" type="primary" :loading="reflecting" :disabled="!stats.total" @click="makeReflection">
            <el-icon><DataAnalysis /></el-icon> 生成反思报告
          </el-button>
        </div>
      </div>
      <div class="stat-cards">
        <div class="stat-card"><div class="stat-num">{{ stats.total }}</div><div class="stat-label">知识词条</div></div>
        <div class="stat-card"><div class="stat-num">{{ stats.categories?.length || 0 }}</div><div class="stat-label">分类领域</div></div>
        <div class="stat-card"><div class="stat-num">{{ stats.tags?.length || 0 }}</div><div class="stat-label">标签</div></div>
        <div class="stat-card" :class="{ 'stat-warn': pendingCount > 0 }">
          <div class="stat-num">{{ pendingCount }}</div><div class="stat-label">待沉淀对话</div>
        </div>
        <div class="stat-card"><div class="stat-num">{{ stats.reflections || 0 }}</div><div class="stat-label">反思报告</div></div>
      </div>
    </div>

    <!-- 待沉淀对话：查看条目信息，勾选沉淀或忽略 -->
    <div class="page-card" style="margin-top: 12px" v-if="pendingItems.length">
      <div class="col-title" style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
        <el-icon><Files /></el-icon> 待沉淀对话（{{ pendingItems.length }}）
        <span class="kb-sub">勾选要沉淀的对话；不想沉淀的可忽略移出队列</span>
        <div style="margin-left: auto; display: flex; gap: 8px">
          <el-button size="small" type="primary" :disabled="!selPending.length" :loading="processing" @click="processSelected">
            <el-icon><MagicStick /></el-icon> 沉淀选中（{{ selPending.length }}）
          </el-button>
          <el-button size="small" :disabled="!selPending.length" @click="dismissSelected">忽略选中</el-button>
        </div>
      </div>
      <el-table :data="pendingItems" size="small" max-height="260"
                @selection-change="s => selPending = s">
        <el-table-column type="selection" width="42" />
        <el-table-column prop="title" label="对话标题" min-width="160" show-overflow-tooltip />
        <el-table-column label="知识库提问" min-width="260">
          <template #default="{ row }">
            <el-tag v-for="q in row.kb_questions" :key="q" size="small" effect="plain" type="info"
                    style="margin: 1px 4px 1px 0; max-width: 240px; overflow: hidden; text-overflow: ellipsis">
              {{ q }}
            </el-tag>
            <span v-if="!row.kb_questions?.length" class="kb-sub">—</span>
          </template>
        </el-table-column>
        <el-table-column label="沉淀进度" width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="sedimentTag(row).type" effect="light">
              {{ sedimentTag(row).label }}
            </el-tag>
            <el-tooltip v-if="row.sediment_note"
                        :content="`${row.sediment_note}（${(row.sediment_at || '').slice(0, 16)}）`"
                        placement="top">
              <span class="kb-sub" style="margin-left: 4px; cursor: help">ⓘ</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="msg_count" label="消息" width="60" align="center" />
        <el-table-column label="最后活跃" width="140">
          <template #default="{ row }">{{ (row.updated_at || '').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="70">
          <template #default="{ row }">
            <el-button size="small" text type="danger" @click="dismissOne(row)">忽略</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-empty v-if="loaded && !stats.total" description="知识库还是空的"
              style="background: #fff; border-radius: 8px; padding: 40px 0">
      <template #image><el-icon style="font-size: 64px; color: #c0c4cc"><Collection /></el-icon></template>
      <p class="kb-sub" style="margin-bottom: 12px">
        到「AI 对话」勾选「查询知识库」向 Agent 提问深信服技术问题，<br/>
        对话结束后会自动提炼为知识词条沉淀到这里。
      </p>
      <el-button type="primary" @click="store.view = 'chat'">去对话提问</el-button>
    </el-empty>

    <template v-if="stats.total">
      <!-- 可视化：知识图谱 + 分类分布 + 沉淀时间线 -->
      <div class="kb-charts">
        <div class="page-card chart-card" style="flex: 1.5">
          <div class="col-title"><el-icon><Share /></el-icon> 知识图谱 <span class="kb-sub">（节点=词条，连线=共享标签，点击节点查看详情）</span></div>
          <div ref="graphRef" class="chart chart-graph"></div>
        </div>
        <div style="flex: 1; display: flex; flex-direction: column; gap: 12px">
          <div class="page-card chart-card">
            <div class="col-title"><el-icon><Histogram /></el-icon> 分类分布</div>
            <div ref="catRef" class="chart chart-small"></div>
          </div>
          <div class="page-card chart-card">
            <div class="col-title"><el-icon><TrendCharts /></el-icon> 沉淀时间线</div>
            <div ref="timeRef" class="chart chart-small"></div>
          </div>
        </div>
      </div>

      <!-- 反思报告 -->
      <div class="page-card" style="margin-top: 12px" v-if="reflections.length">
        <div class="col-title" style="display: flex; align-items: center; gap: 10px">
          <el-icon><DataAnalysis /></el-icon> 反思与总结
          <span v-if="viewingReflection" class="kb-sub">
            {{ viewingReflection.period }} · {{ viewingReflection.created_at }}
          </span>
          <el-button v-if="viewingReflection" size="small" text type="danger" style="margin-left: auto"
                     @click="deleteReflection(viewingReflection)">
            <el-icon><Delete /></el-icon> 删除此报告
          </el-button>
        </div>
        <div class="refl-controls">
          <el-date-picker v-model="reflRange" type="daterange" value-format="YYYY-MM-DD" size="small"
                          start-placeholder="沉淀开始日期" end-placeholder="沉淀结束日期"
                          :shortcuts="rangeShortcuts" style="width: 250px" :clearable="true" />
          <el-button size="small" type="primary" :loading="reflecting" @click="makeReflection">
            生成报告（不选日期=全部）
          </el-button>
          <el-select v-if="reflections.length > 1" v-model="viewingReflId" size="small"
                     placeholder="历史报告" style="width: 200px; margin-left: auto">
            <el-option v-for="r in reflections" :key="r.id"
                       :label="`${r.period}（${(r.created_at || '').slice(5, 16)}）`" :value="r.id" />
          </el-select>
        </div>
        <div v-if="viewingReflection" class="md-body" v-html="render(viewingReflection.content_md)"></div>
      </div>

      <!-- 词条列表 -->
      <div class="page-card" style="margin-top: 12px">
        <div class="col-title" style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
          <el-icon><Notebook /></el-icon> 知识词条（{{ entries.length }}）
          <el-select v-model="entryOrder" size="small" style="width: 120px" @change="loadEntries">
            <el-option label="最近创建" value="created" />
            <el-option label="最近更新" value="updated" />
          </el-select>
          <el-select v-model="filterCategory" size="small" clearable placeholder="全部分类" style="width: 140px" @change="loadEntries">
            <el-option v-for="c in stats.categories" :key="c.name" :label="`${c.name}（${c.value}）`" :value="c.name" />
          </el-select>
          <el-input v-model="keyword" size="small" clearable placeholder="搜索主题/内容/标签" style="width: 200px"
                    @keyup.enter="loadEntries" @clear="loadEntries" />
          <el-button size="small" text type="primary" @click="loadEntries">搜索</el-button>
        </div>
        <div class="entry-grid">
          <div v-for="e in entries" :key="e.id" class="entry-card" @click="detail = e">
            <div class="entry-top">
              <el-tag size="small" :type="catTagType(e.category)">{{ e.category }}</el-tag>
              <span style="flex: 1; font-weight: 600">{{ e.topic }}</span>
              <el-button size="small" text type="danger" @click.stop="removeEntry(e)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <div class="entry-summary">{{ e.summary }}</div>
            <div class="entry-foot">
              <el-tag v-for="t in e.tags.slice(0, 3)" :key="t" size="small" effect="plain" type="info">{{ t }}</el-tag>
              <span class="kb-sub" style="margin-left: auto">{{ e.created_at?.slice(0, 16) }}</span>
            </div>
          </div>
          <el-empty v-if="!entries.length" description="没有匹配的词条" style="grid-column: 1 / -1" />
        </div>
      </div>
    </template>

    <!-- 词条详情抽屉 -->
    <el-drawer v-model="showDetail" :title="detail?.topic" size="480px">
      <template v-if="detail">
        <div style="margin-bottom: 10px; display: flex; gap: 6px; align-items: center">
          <el-tag size="small" :type="catTagType(detail.category)">{{ detail.category }}</el-tag>
          <el-tag v-for="t in detail.tags" :key="t" size="small" effect="plain" type="info">{{ t }}</el-tag>
        </div>
        <p style="color: #606266">{{ detail.summary }}</p>
        <div v-if="detail.key_points?.length" class="detail-block">
          <b>核心要点</b>
          <ul style="margin: 6px 0 0; padding-left: 18px">
            <li v-for="(p, i) in detail.key_points" :key="i" style="margin: 3px 0">{{ p }}</li>
          </ul>
        </div>
        <div class="md-body detail-block" v-html="render(detail.content_md)"></div>
        <div v-if="detail.references?.length" class="detail-block">
          <b>官方引用</b>
          <div v-for="(r, i) in detail.references" :key="i" class="ref-item">
            <template v-if="refUrl(r)">
              <a :href="refUrl(r)" target="_blank" rel="noopener noreferrer" class="ref-link">
                <el-icon style="vertical-align: -2px"><Link /></el-icon>
                {{ refTitle(r) }}
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
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import * as echarts from 'echarts'
import { store } from '../store.js'
import { KB } from '../api.js'

const md = new MarkdownIt({ breaks: true })
// 正文里的链接新窗口打开，避免点走整个应用页面
const defaultLinkOpen = md.renderer.rules.link_open || ((tokens, idx, opts, _, self) => self.renderToken(tokens, idx, opts))
md.renderer.rules.link_open = (tokens, idx, opts, _, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, opts, _, self)
}
const render = (text) => md.render(text || '')

// 引用条目兼容两种形态：{title, url?} 对象（新）与纯字符串（历史数据）
const refUrl = (r) => {
  if (r && typeof r === 'object') return r.url || ''
  if (typeof r === 'string' && /^https?:\/\//.test(r)) return r
  return ''
}
const refTitle = (r) => {
  if (r && typeof r === 'object') return r.title || r.url || '（未命名引用）'
  return String(r)
}

const loaded = ref(false)
const stats = ref({ total: 0, categories: [], tags: [], timeline: [], reflections: 0, graph: { nodes: [], edges: [], categories: [] } })
const entries = ref([])
const reflections = ref([])
const pendingCount = ref(0)
const filterCategory = ref('')
const keyword = ref('')
const entryOrder = ref('created')   // 排序：created=最近创建 / updated=最近更新（合并更新可见）
const detail = ref(null)
const showDetail = computed({ get: () => !!detail.value, set: v => { if (!v) detail.value = null } })
const processing = ref(false)
const reflecting = ref(false)
const reflRange = ref(null)   // 反思报告自定义时间节点 [start, end]
const pendingItems = ref([])  // 待沉淀对话明细（标题/提问/消息数/沉淀进度）
const selPending = ref([])    // 勾选的待沉淀对话

// 沉淀进度展示：waiting=尚未调度 pending=排队 running=提炼中 done/skipped/failed=结果
const SEDIMENT_TAGS = {
  waiting: { label: '等待沉淀', type: 'info' },
  pending: { label: '排队中', type: 'warning' },
  running: { label: '沉淀中', type: 'primary' },
  done: { label: '已完成', type: 'success' },
  skipped: { label: '已跳过', type: 'info' },
  failed: { label: '失败', type: 'danger' },
}
const sedimentTag = (row) => SEDIMENT_TAGS[row.sediment_status] || SEDIMENT_TAGS.waiting

const latestReflection = computed(() => reflections.value[0] || null)
const viewingReflId = ref('')
// 当前查看的报告：优先下拉选中的，否则最新一份
const viewingReflection = computed(() =>
  reflections.value.find(r => r.id === viewingReflId.value) || reflections.value[0] || null)
// 反思日期快捷选项
const rangeShortcuts = [
  { text: '最近7天', value: () => [new Date(Date.now() - 6 * 864e5), new Date()] },
  { text: '最近30天', value: () => [new Date(Date.now() - 29 * 864e5), new Date()] },
  { text: '最近90天', value: () => [new Date(Date.now() - 89 * 864e5), new Date()] },
]
// 点击图谱节点：按 id 取词条并打开详情抽屉
async function openEntryById(id) {
  try { detail.value = await KB.entry(id) } catch { /* 静默 */ }
}

// ---------- ECharts ----------
const graphRef = ref(null)
const catRef = ref(null)
const timeRef = ref(null)
let graphChart = null
let catChart = null
let timeChart = null
const PALETTE = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4']

function renderCharts() {
  const { graph, categories, timeline } = stats.value
  if (graphRef.value) {
    if (!graphChart) {
      graphChart = echarts.init(graphRef.value)
      // 点击图谱节点 → 打开对应词条详情
      graphChart.on('click', (p) => { if (p.dataType === 'node' && p.data?.id) openEntryById(p.data.id) })
    }
    graphChart.setOption({
      tooltip: { formatter: (p) => p.dataType === 'node' ? `${p.data.category || ''} · ${p.name}` : '' },
      legend: [{ data: graph.categories, bottom: 0, type: 'scroll', textStyle: { fontSize: 11, color: '#606266' } }],
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        data: graph.nodes.map(n => ({
          id: n.id, name: n.name,
          category: Math.max(0, graph.categories.indexOf(n.category)),
          symbolSize: n.symbolSize,
          label: { show: true, position: 'right', fontSize: 10 }
        })),
        links: graph.edges,
        categories: graph.categories.map((c, i) => ({ name: c, itemStyle: { color: PALETTE[i % PALETTE.length] } })),
        force: { repulsion: 300, edgeLength: [70, 140], gravity: 0.08 },
        lineStyle: { color: '#c0c4cc', curveness: 0.15 },
        emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
      }]
    })
  }
  if (catRef.value) {
    catChart = catChart || echarts.init(catRef.value)
    const cats = [...categories].sort((a, b) => a.value - b.value)
    catChart.setOption({
      tooltip: {},
      grid: { left: 80, right: 24, top: 10, bottom: 24 },
      xAxis: { type: 'value', minInterval: 1 },
      yAxis: { type: 'category', data: cats.map(c => c.name), axisLabel: { fontSize: 11 } },
      series: [{ type: 'bar', data: cats.map(c => c.value), barMaxWidth: 16, itemStyle: { color: '#5470c6', borderRadius: [0, 4, 4, 0] } }]
    })
  }
  if (timeRef.value) {
    timeChart = timeChart || echarts.init(timeRef.value)
    timeChart.setOption({
      tooltip: {},
      grid: { left: 36, right: 16, top: 14, bottom: 26 },
      xAxis: { type: 'category', data: timeline.map(t => t.day.slice(5)), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{ type: 'line', data: timeline.map(t => t.value), smooth: true, areaStyle: { opacity: 0.15 }, itemStyle: { color: '#91cc75' }, lineStyle: { color: '#91cc75' } }]
    })
  }
}

function resizeAll() {
  graphChart?.resize(); catChart?.resize(); timeChart?.resize()
}

// ---------- 数据加载 ----------
async function loadAll() {
  await Promise.all([loadStats(), loadEntries(), loadReflections(), loadPending()])
  loaded.value = true
  await nextTick()
  renderCharts()
}

async function loadStats() {
  try { stats.value = await KB.stats() } catch (e) { ElMessage.error(String(e.message || e)) }
}
async function loadEntries() {
  try { entries.value = await KB.entries(filterCategory.value, keyword.value, entryOrder.value) } catch (e) { ElMessage.error(String(e.message || e)) }
}
async function loadReflections() {
  try { reflections.value = await KB.reflections() } catch { /* 静默 */ }
}
let pendingTimer = null
async function loadPending() {
  try {
    const p = await KB.pending()
    pendingCount.value = p.count
    pendingItems.value = p.items || []
    selPending.value = []
    // 有排队/提炼中的对话时定时刷新，实时展示自动沉淀进度
    const busy = (p.items || []).some(x => ['pending', 'running'].includes(x.sediment_status))
    clearTimeout(pendingTimer)
    if (busy) pendingTimer = setTimeout(loadPending, 3000)
  } catch { /* 静默 */ }
}

async function processSelected() {
  processing.value = true
  try {
    const ids = selPending.value.map(x => x.conv_id)
    const r = await KB.process(20, ids)
    if (r.saved > 0) ElMessage.success(`已沉淀 ${r.saved} 条知识词条`)
    else {
      const reason = r.results?.find(x => x.reason)?.reason
      ElMessage.warning(reason || '没有沉淀出新词条')
    }
    await loadAll()
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { processing.value = false }
}

async function dismissOne(row) {
  try {
    await KB.dismissPending(row.conv_id)
    ElMessage.success('已移出沉淀队列')
    await loadPending()
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

async function dismissSelected() {
  try {
    await ElMessageBox.confirm(`确定把选中的 ${selPending.value.length} 条对话移出沉淀队列吗？移出后自动/手动沉淀都会跳过它们。`, '忽略待沉淀对话', { type: 'warning' })
    await Promise.allSettled(selPending.value.map(x => KB.dismissPending(x.conv_id)))
    ElMessage.success('已移出沉淀队列')
    await loadPending()
  } catch (e) { if (e !== 'cancel') ElMessage.error(String(e.message || e)) }
}

// ---------- 操作 ----------
async function processPending() {
  processing.value = true
  try {
    const r = await KB.process(10)
    if (r.saved > 0) ElMessage.success(`已沉淀 ${r.saved} 条知识词条`)
    else ElMessage.warning(r.results?.[0]?.reason || '没有沉淀出新词条')
    await loadAll()
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { processing.value = false }
}

async function makeReflection() {
  reflecting.value = true
  try {
    const range = reflRange.value || []
    const r = await KB.reflection(range[0] || '', range[1] || '')
    if (r.status === 'ok') {
      ElMessage.success('反思报告已生成')
      viewingReflId.value = ''   // 切回最新报告
      await loadReflections()
    } else ElMessage.warning(r.reason || '未能生成报告')
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { reflecting.value = false }
}

async function deleteReflection(r) {
  try {
    await ElMessageBox.confirm(`确定删除「${r.period}」的反思报告吗？删除后不可恢复。`, '删除反思报告', { type: 'warning' })
    await KB.deleteReflection(r.id)
    viewingReflId.value = ''
    ElMessage.success('报告已删除')
    await loadReflections()
  } catch (e) { if (e !== 'cancel') ElMessage.error(String(e.message || e)) }
}

async function removeEntry(e) {
  try {
    await ElMessageBox.confirm(`确定删除词条「${e.topic}」吗？`, '删除词条', { type: 'warning' })
    await KB.removeEntry(e.id)
    ElMessage.success('已删除')
    await loadAll()
  } catch (err) { if (err !== 'cancel') ElMessage.error(String(err.message || err)) }
}

const catTagType = (c) => ({ 配置方法: 'primary', 故障排查: 'danger', 版本升级: 'warning', 安全策略: 'danger', 最佳实践: 'success' }[c] || 'info')

onMounted(() => {
  loadAll()
  window.addEventListener('resize', resizeAll)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeAll)
  graphChart?.dispose(); catChart?.dispose(); timeChart?.dispose()
})
</script>

<style scoped>
.kb-page { display: flex; flex-direction: column; gap: 0; }
.kb-header { padding: 14px 16px; }
.kb-header-top { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.kb-sub { color: #909399; font-size: 12px; margin-left: 8px; }
.stat-cards { display: flex; gap: 10px; flex-wrap: wrap; }
.stat-card { flex: 1; min-width: 110px; background: #f5f7fa; border-radius: 8px; padding: 10px 14px; text-align: center; }
.stat-card.stat-warn { background: #fdf6ec; }
.stat-num { font-size: 22px; font-weight: 700; color: #303133; }
.stat-label { font-size: 12px; color: #909399; margin-top: 2px; }
.kb-charts { display: flex; gap: 12px; margin-top: 12px; }
.chart-card { padding: 12px 14px; }
.col-title { font-weight: 600; margin-bottom: 8px; display: flex; align-items: center; }
.chart { width: 100%; }
.chart-graph { height: 380px; }
.chart-small { height: 178px; }
.entry-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; margin-top: 10px; }
.entry-card { border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: box-shadow .2s; }
.entry-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,.08); }
.entry-top { display: flex; align-items: center; gap: 6px; }
.entry-summary { font-size: 12px; color: #606266; margin: 6px 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.entry-foot { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.refl-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.detail-block { margin-top: 14px; }
.ref-item { font-size: 13px; margin-top: 6px; word-break: break-all; }
.ref-link { color: #409eff; text-decoration: none; }
.ref-link:hover { text-decoration: underline; }
.ref-plain { color: #606266; }
.refl-hist { font-size: 12px; color: #409eff; padding: 3px 0; cursor: pointer; }
</style>
