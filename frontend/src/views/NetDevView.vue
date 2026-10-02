<template>
  <div class="nd-page">
    <!-- 顶部：标题 + 统计 + 操作 -->
    <div class="page-card nd-header">
      <div class="nd-header-top">
        <div class="nd-title">
          <div class="nd-title-icon"><el-icon :size="22"><Monitor /></el-icon></div>
          <div>
            <b>网络设备管理</b>
            <div class="nd-sub">交换机 / 路由器 SSH 远程管理 · 支持华为 / H3C / 思科 / 锐捷 / 中兴</div>
          </div>
        </div>
        <div style="display: flex; gap: 8px">
          <el-button size="small" @click="showBatchAdd = true">
            <el-icon><Upload /></el-icon>&nbsp;批量导入
          </el-button>
          <el-button size="small" :disabled="!devices.length" @click="exportDevices">
            <el-icon><Download /></el-icon>&nbsp;导出设备
          </el-button>
          <el-button size="small" type="primary" @click="openAdd">
            <el-icon><Plus /></el-icon>&nbsp;添加设备
          </el-button>
        </div>
      </div>
      <div class="stat-cards">
        <div class="stat-card"><div class="stat-num">{{ devices.length }}</div><div class="stat-label">设备总数</div></div>
        <div class="stat-card"><div class="stat-num">{{ groupCount }}</div><div class="stat-label">分组</div></div>
        <div class="stat-card"><div class="stat-num nd-ok">{{ okCount }}</div><div class="stat-label">连通正常</div></div>
        <div class="stat-card"><div class="stat-num">{{ devices.length - okCount }}</div><div class="stat-label">待测试</div></div>
      </div>
    </div>

    <el-tabs v-model="tab" class="nd-tabs">
      <!-- ============ Tab 1：设备管理 ============ -->
      <el-tab-pane label="设备管理" name="devices">
        <div class="page-card">
          <div class="nd-filter">
            <el-input v-model="filterText" size="small" clearable
                      placeholder="筛选：IP / 名称 / 分组 / 型号"
                      :prefix-icon="Search" style="width: 260px" />
            <el-select v-model="filterVendor" size="small" clearable placeholder="全部厂家"
                       style="width: 140px">
              <el-option v-for="v in vendors" :key="v.vendor" :label="v.display" :value="v.vendor" />
            </el-select>
            <el-select v-model="filterGroup" size="small" clearable placeholder="全部分组"
                       style="width: 140px">
              <el-option v-for="g in groupOptions" :key="g" :label="g || '（未分组）'" :value="g" />
            </el-select>
            <span class="nd-sub">匹配 {{ filteredDevices.length }} / {{ devices.length }} 台</span>
          </div>
          <el-table :data="filteredDevices" size="small" max-height="540" v-loading="loading"
                    :empty-text="loading ? '加载中…' : '暂无设备，点击右上角「添加设备」或「批量导入」开始'"
                    @selection-change="s => selected = s">
            <el-table-column type="selection" width="42" />
            <el-table-column prop="name" label="设备名称" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="nd-dev-name">{{ row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="厂家" width="120">
              <template #default="{ row }">
                <el-tag size="small" :color="vendorColor(row.vendor)" effect="dark" class="nd-vendor-tag">
                  {{ vendorName(row.vendor) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="管理地址" min-width="150">
              <template #default="{ row }">
                <span class="nd-mono">{{ row.host }}:{{ row.port }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="model" label="型号" width="100" show-overflow-tooltip />
            <el-table-column prop="group_name" label="分组" width="100" show-overflow-tooltip />
            <el-table-column label="连通性" width="100" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.last_ok_at" size="small" type="success" effect="light" round>● 正常</el-tag>
                <el-tag v-else size="small" type="info" effect="light" round>○ 未测</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="132" fixed="right">
              <template #default="{ row }">
                <div class="nd-ops">
                  <el-button size="small" text type="primary" :loading="testingId === row.id"
                             @click="testOne(row)">测试</el-button>
                  <el-button size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
                  <el-button size="small" text type="primary" @click="openConsole(row)">控制台</el-button>
                  <el-button size="small" text type="danger" @click="removeOne(row)">删除</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- ============ Tab 2：批量执行 ============ -->
      <el-tab-pane :label="`批量执行${selected.length ? `（已选 ${selected.length}）` : ''}`" name="exec">
        <el-row :gutter="12" class="nd-exec">
          <el-col :span="8">
            <div class="page-card nd-col-card">
              <div class="col-title">
                <el-icon><Monitor /></el-icon>&nbsp;选择设备
                <el-tag size="small" effect="plain" style="margin-left: 8px">{{ selected.length }} / {{ devices.length }}</el-tag>
                <el-button size="small" text type="primary" style="margin-left: auto" @click="loadDevices">刷新</el-button>
              </div>
              <div style="display: flex; gap: 6px; margin-bottom: 8px">
                <el-input v-model="filterText" size="small" clearable
                          placeholder="筛选：IP / 名称 / 分组 / 型号"
                          :prefix-icon="Search" style="flex: 1" />
                <el-select v-model="filterVendor" size="small" clearable placeholder="厂家"
                           style="width: 104px">
                  <el-option v-for="v in vendors" :key="v.vendor" :label="v.display" :value="v.vendor" />
                </el-select>
                <el-select v-model="filterGroup" size="small" clearable placeholder="分组"
                           style="width: 104px">
                  <el-option v-for="g in groupOptions" :key="g" :label="g || '（未分组）'" :value="g" />
                </el-select>
              </div>
              <el-table ref="devTable" :data="filteredDevices" size="small" max-height="470"
                        @selection-change="s => selected = s" v-loading="loading">
                <el-table-column type="selection" width="40" />
                <el-table-column prop="name" label="设备" min-width="110" show-overflow-tooltip />
                <el-table-column label="厂家" width="90">
                  <template #default="{ row }">{{ vendorName(row.vendor) }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-col>
          <el-col :span="16">
            <div style="display: flex; flex-direction: column; gap: 12px; min-width: 0">
              <div class="page-card">
                <div class="col-title"><el-icon><Promotion /></el-icon>&nbsp;批量执行命令</div>
                <div style="display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap">
                  <el-input v-model="execName" size="small" placeholder="任务名称（可选，如：季度巡检）"
                            style="width: 220px" />
                  <el-input-number v-model="execTimeout" size="small" :min="5" :max="300" style="width: 120px" />
                  <span class="nd-sub">单命令超时（秒）</span>
                </div>
                <el-input v-model="commandText" type="textarea" :rows="4"
                          placeholder="每行一条命令，按顺序执行，如：&#10;display version&#10;display cpu-usage&#10;show ip interface brief"
                          class="nd-cmd-input" />
                <div style="margin-top: 10px; display: flex; align-items: center; gap: 12px">
                  <el-button type="primary" size="small"
                             :disabled="!selected.length || !commandText.trim() || executing"
                             :loading="executing" @click="execute">
                    <el-icon><CaretRight /></el-icon>&nbsp;并行执行（{{ selected.length }} 台 × {{ cmdCount }} 条）
                  </el-button>
                  <span class="nd-sub">同批最多 {{ maxConcurrency }} 台同时在线 · 进度实时刷新</span>
                </div>
              </div>

              <div class="page-card nd-col-card">
                <div class="col-title" style="display: flex; align-items: center">
                  <el-icon><DataLine /></el-icon>&nbsp;执行进度与结果
                  <template v-if="task">
                    <el-tag size="small" :type="task.status === 'running' ? 'primary' : (task.status === 'done' ? 'success' : 'danger')"
                            effect="light" style="margin-left: 8px">
                      {{ task.status === 'running' ? '执行中' : (task.status === 'done' ? '已完成' : '失败') }}
                    </el-tag>
                    <span class="nd-sub" style="margin-left: 8px">{{ doneCount }}/{{ task.items.length }} 台完成</span>
                  </template>
                  <el-button v-if="task?.items?.length" size="small" text @click="exportResult"
                             style="margin-left: auto">导出结果</el-button>
                </div>
                <div v-if="!task" class="nd-empty">
                  <el-icon :size="40" color="#c0c8d4"><DataLine /></el-icon>
                  <p>选择设备、输入命令后点击「并行执行」<br/>进度与结果将实时显示在这里</p>
                </div>
                <el-collapse v-else v-model="openResults" class="nd-results">
                  <el-collapse-item v-for="item in task.items" :key="item.id" :name="item.id">
                    <template #title>
                      <div class="nd-result-head">
                        <el-tag size="small" :type="statusTag(item.status).type" effect="light">
                          {{ statusTag(item.status).label }}
                        </el-tag>
                        <b class="nd-dev-name">{{ item.device_name }}</b>
                        <span class="nd-sub" v-if="item.duration">{{ item.duration }}s</span>
                        <span class="nd-err" v-if="item.error">{{ item.error }}</span>
                      </div>
                    </template>
                    <pre v-if="item.output" class="nd-output">{{ item.output }}</pre>
                    <div v-else-if="item.status === 'running'" class="nd-sub" style="padding: 8px 12px">
                      正在执行…
                    </div>
                    <div v-else class="nd-sub" style="padding: 8px 12px">无输出（{{ item.error || '失败' }}）</div>
                  </el-collapse-item>
                </el-collapse>
              </div>
            </div>
          </el-col>
        </el-row>
      </el-tab-pane>
    </el-tabs>

    <!-- 单台添加 -->
    <el-dialog v-model="showAdd" :title="editId ? '编辑网络设备' : '添加网络设备'" width="500px">
      <el-form :model="form" label-width="92px" size="small">
        <el-form-item label="设备名称" required><el-input v-model="form.name" placeholder="如：核心交换机01" /></el-form-item>
        <el-form-item label="厂家" required>
          <el-select v-model="form.vendor" style="width: 100%">
            <el-option v-for="v in vendors" :key="v.vendor" :label="v.display" :value="v.vendor" />
          </el-select>
        </el-form-item>
        <el-form-item label="管理地址" required>
          <div style="display: flex; gap: 8px; width: 100%">
            <el-input v-model="form.host" placeholder="IP 或域名" style="flex: 1" />
            <el-input-number v-model="form.port" :min="1" :max="65535" style="width: 104px" />
          </div>
        </el-form-item>
        <el-form-item label="型号"><el-input v-model="form.model" placeholder="可选，如 S5735 / USG6000E" /></el-form-item>
        <el-form-item label="分组"><el-input v-model="form.group_name" placeholder="可选，如：总部核心" /></el-form-item>
        <el-form-item label="用户名" required><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="SSH 口令" required><el-input v-model="form.password" type="password" show-password /></el-form-item>
        <el-form-item label="提权口令">
          <el-input v-model="form.enable_password" type="password" show-password placeholder="enable/super 口令，无则留空" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showAdd = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="saveOne">保存</el-button>
      </template>
    </el-dialog>

    <!-- 批量添加 -->
    <el-dialog v-model="showBatchAdd" title="批量导入网络设备" width="660px">
      <div style="display: flex; gap: 8px; margin-bottom: 10px; align-items: center">
        <el-button size="small" @click="downloadTemplate">
          <el-icon><Download /></el-icon>&nbsp;下载 CSV 模板
        </el-button>
        <el-upload :auto-upload="false" :show-file-list="false" accept=".csv,.txt"
                   :on-change="f => importCsv(f.raw)">
          <el-button size="small"><el-icon><Upload /></el-icon>&nbsp;导入 CSV 文件</el-button>
        </el-upload>
        <span class="nd-sub">自动识别 UTF-8 / GBK（Excel 默认）编码；口令含逗号用双引号包裹；首行为表头自动跳过</span>
      </div>
      <el-input v-model="batchText" type="textarea" :rows="9"
                placeholder="核心交换机01,huawei,10.20.1.1,22,admin,Huawei@123,,总部,S5735&#10;出口路由器,h3c,10.20.1.254,22,admin,H3C@123,ensuper,总部,MSR6100"
                class="nd-cmd-input" />
      <div class="nd-sub" style="margin-top: 8px">
        每行一台：<b>名称,厂家,管理地址,端口,用户名,SSH口令,提权口令,分组,型号</b>
        （厂家：huawei / h3c / cisco / ruijie / zte / juniper / aruba / dell / tplink / mikrotik / nokia / other；端口默认 22；末尾字段可省略）
      </div>
      <template #footer>
        <el-button size="small" @click="showBatchAdd = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="saveBatch">解析并保存</el-button>
      </template>
    </el-dialog>

    <!-- 交互式控制台 -->
    <el-dialog v-model="consoleVisible" :title="`控制台 — ${consoleDevice?.name || ''}（${consoleDevice?.host}）`"
               width="880px" top="6vh" destroy-on-close :close-on-click-modal="false"
               @opened="initConsole" @close="closeConsole">
      <div ref="termEl" class="nd-term"></div>
      <div class="nd-sub" style="margin-top: 8px; display: flex; align-items: center; gap: 6px">
        <span class="nd-dot" :class="consoleWsOk ? 'nd-dot-ok' : 'nd-dot-bad'"></span>
        {{ consoleWsOk ? '已连接设备 CLI（输入 help 或 ? 可查看命令）' : '连接中…' }}
        · 关闭弹窗即断开 SSH 会话
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import '@xterm/xterm/css/xterm.css'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Monitor, Plus, Promotion, CaretRight, DataLine, Download, Upload, Search } from '@element-plus/icons-vue'
import { NetDev } from '../api.js'

const tab = ref('devices')
const devices = ref([])
const vendors = ref([])
const loading = ref(false)
const selected = ref([])
const testingId = ref('')
const saving = ref(false)

const showAdd = ref(false)
const showBatchAdd = ref(false)
const editId = ref('')   // 非空 = 编辑已有设备
const form = ref({})
const batchText = ref('')

const execName = ref('')
const execTimeout = ref(30)
const commandText = ref('')
const executing = ref(false)
const task = ref(null)
const pollTimer = ref(null)

// 交互式控制台（xterm + WebSocket）。命名避开全局 console，防止遮蔽内置对象。
const consoleVisible = ref(false)
const consoleDevice = ref(null)
const consoleWsOk = ref(false)
const termEl = ref(null)
let consoleWs = null
let consoleTerm = null

// 设备筛选：单关键字模糊匹配 名称/IP/分组/型号（前端过滤，即时生效）
const filterText = ref('')
const filterVendor = ref('')
const filterGroup = ref('')
const groupOptions = computed(() =>
  [...new Set(devices.value.map(d => d.group_name || ''))].sort())
const filteredDevices = computed(() => {
  const kw = filterText.value.trim().toLowerCase()
  return devices.value.filter(d =>
    (!kw || [d.name, d.host, d.group_name, d.model].some(v => (v || '').toLowerCase().includes(kw)))
    && (!filterVendor.value || d.vendor === filterVendor.value)
    && (!filterGroup.value || (d.group_name || '') === filterGroup.value))
})

const maxConcurrency = 10
const cmdCount = computed(() => commandText.value.split('\n').filter(l => l.trim()).length)
const doneCount = computed(() =>
  (task.value?.items || []).filter(i => ['ok', 'failed'].includes(i.status)).length)
const groupCount = computed(() => new Set(devices.value.map(d => d.group_name).filter(Boolean)).size)
const okCount = computed(() => devices.value.filter(d => d.last_ok_at).length)

const STATIC_VENDORS = ['huawei', 'h3c', 'cisco', 'ruijie', 'zte', 'juniper',
                        'aruba', 'dell', 'tplink', 'mikrotik', 'nokia', 'other']
const validVendors = () => vendors.value.length
  ? vendors.value.map(v => v.vendor) : STATIC_VENDORS

const VENDOR_COLORS = { huawei: '#c0392b', h3c: '#e67e22', cisco: '#2471a3',
                        ruijie: '#7d3c98', zte: '#1e8449',
                        juniper: '#0097a9', aruba: '#d35400', dell: '#007db8',
                        tplink: '#0a9d58', mikrotik: '#2980b9', nokia: '#124191',
                        other: '#5d6d7e' }
const vendorName = v => vendors.value.find(x => x.vendor === v)?.display || v
const vendorColor = v => VENDOR_COLORS[v] || VENDOR_COLORS.other
const statusTag = s => ({ pending: { label: '等待', type: 'info' },
  running: { label: '执行中', type: 'primary' },
  ok: { label: '成功', type: 'success' },
  failed: { label: '失败', type: 'danger' } }[s] || { label: s, type: 'info' })

async function loadDevices() {
  loading.value = true
  try { devices.value = await NetDev.devices() } catch (e) { ElMessage.error(String(e.message || e)) }
  loading.value = false
}

function openAdd() {
  editId.value = ''
  form.value = { name: '', vendor: 'huawei', host: '', port: 22, model: '', group_name: '',
                 username: '', password: '', enable_password: '' }
  showAdd.value = true
}

function openEdit(row) {
  editId.value = row.id
  // 口令不回显（接口脱敏）：留空 = 保留原值
  form.value = { name: row.name, vendor: row.vendor, host: row.host, port: row.port,
                 model: row.model || '', group_name: row.group_name || '',
                 username: row.username || '', password: '', enable_password: '' }
  showAdd.value = true
}

async function saveOne() {
  const f = form.value
  if (!f.name || !f.host || !f.username) { ElMessage.warning('名称 / 管理地址 / 用户名为必填项'); return }
  saving.value = true
  try {
    await NetDev.add(editId.value ? { ...f, device_id: editId.value } : f)
    ElMessage.success(editId.value ? '设备已更新' : '设备已保存')
    showAdd.value = false
    await loadDevices()
  } catch (e) { ElMessage.error(String(e.message || e)) }
  saving.value = false
}

function parseCsvLine(line) {
  // 支持双引号字段（口令含逗号）：逐字符扫描
  const out = []
  let cur = '', inQuote = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (inQuote) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++ }
      else if (ch === '"') inQuote = false
      else cur += ch
    } else if (ch === '"') inQuote = true
    else if (ch === ',') { out.push(cur.trim()); cur = '' }
    else cur += ch
  }
  out.push(cur.trim())
  return out
}

async function exportDevices() {
  try {
    const r = await NetDev.exportDevices()
    const blob = new Blob(['﻿' + r.csv], { type: 'text/csv;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = r.filename || '网络设备导出.csv'
    a.click()
    URL.revokeObjectURL(a.href)
    ElMessage.success(`已导出 ${devices.value.length} 台设备`)
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

function downloadTemplate() {
  const csv = [
    '名称,厂家,管理地址,端口,用户名,SSH口令,提权口令,分组,型号',
    '核心交换机01,huawei,10.20.1.1,22,admin,Huawei@123,,总部,S5735',
    '出口路由器,h3c,10.20.1.254,22,admin,"H3C@123,含逗号示例",ensuper,总部,MSR6100',
    '接入交换机,cisco,10.20.2.1,22,admin,Cisco@123,,分支机构,C9200',
    '核心路由器,juniper,10.20.3.254,22,admin,Jnpr@123,,总部,MX204',
  ].join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })  // BOM 防 Excel 乱码
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '网络设备批量导入模板.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}

// 智能解码：UTF-8 BOM / UTF-16LE BOM 识别；无 BOM 时先按 UTF-8 严格解码，
// 失败则回退 GB18030（中文 Windows Excel 另存 CSV 的默认 ANSI 编码，直接按
// UTF-8 读会得到乱码）
async function readFileText(file) {
  const bytes = new Uint8Array(await file.arrayBuffer())
  if (bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF)
    return new TextDecoder('utf-8').decode(bytes.slice(3))
  if (bytes[0] === 0xFF && bytes[1] === 0xFE)
    return new TextDecoder('utf-16le').decode(bytes.slice(2))
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    return new TextDecoder('gb18030').decode(bytes)
  }
}

async function importCsv(file) {
  const text = await readFileText(file)
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
  if (!lines.length) { ElMessage.warning('CSV 文件为空'); return }
  const rows = lines.map(parseCsvLine)
  if (rows[0][0] === '名称' || /^name$/i.test(rows[0][0] || '')) rows.shift()   // 跳过表头
  batchText.value = rows.map(r => r.join(',')).join('\n')
  ElMessage.success(`已从 CSV 解析 ${rows.length} 台设备，请核对后点击「解析并保存」`)
}

async function saveBatch() {
  const rows = batchText.value.split('\n').map(l => l.trim()).filter(Boolean).map(parseCsvLine)
  if (!rows.length) { ElMessage.warning('请粘贴设备清单'); return }
  const devicesIn = []
  const bad = []
  rows.forEach((line, idx) => {
    const [name, vendor, host, port, username, password, enablePassword, groupName, model] = line
    if (!name || !host) { bad.push(`第${idx + 1}行：名称与管理地址必填`); return }
    if (!validVendors().includes(vendor)) {
      bad.push(`第${idx + 1}行：厂家 ${vendor} 无效`); return
    }
    devicesIn.push({ name, vendor, host, port: Number(port) || 22, username: username || '',
                     password: password || '', enable_password: enablePassword || '',
                     group_name: groupName || '', model: model || '' })
  })
  if (bad.length) { ElMessage.error(bad.join('；')); return }
  saving.value = true
  try {
    const r = await NetDev.addBatch(devicesIn)
    ElMessage.success(`批量添加完成：成功 ${r.saved} 台` +
      (r.failed.length ? `，失败 ${r.failed.length} 台（${r.failed[0].reason}）` : ''))
    showBatchAdd.value = false
    batchText.value = ''
    await loadDevices()
  } catch (e) { ElMessage.error(String(e.message || e)) }
  saving.value = false
}

async function testOne(row) {
  testingId.value = row.id
  try {
    const r = await NetDev.test(row.id)
    if (r.ok) {
      ElMessage.success(`${row.name} 连接正常（${r.duration}s）`)
      row.last_ok_at = new Date().toISOString()
    } else {
      ElMessageBox.alert(r.error, `${row.name} 连接失败`, { type: 'error' })
    }
  } catch (e) { ElMessage.error(String(e.message || e)) }
  testingId.value = ''
}

async function removeOne(row) {
  await ElMessageBox.confirm(`确定删除设备「${row.name}」吗？`, '删除设备', { type: 'warning' })
  await NetDev.remove(row.id)
  ElMessage.success('已删除')
  await loadDevices()
}

async function execute() {
  executing.value = true
  task.value = null
  try {
    const cmds = commandText.value.split('\n').map(l => l.trim()).filter(Boolean)
    const r = await NetDev.execute(selected.value.map(d => d.id), cmds, execName.value, execTimeout.value)
    ElMessage.success(`任务已启动：${r.total} 台并行执行`)
    pollTask(r.task_id)
  } catch (e) {
    ElMessage.error(String(e.message || e))
    executing.value = false
  }
}

function pollTask(taskId) {
  clearTimeout(pollTimer.value)
  const tick = async () => {
    try {
      const t = await NetDev.task(taskId)
      task.value = t
      if (t.status === 'running') { pollTimer.value = setTimeout(tick, 1200); return }
      const ok = t.items.filter(i => i.status === 'ok').length
      ElMessage.success(`批量执行完成：成功 ${ok} / ${t.items.length} 台`)
    } catch { /* 静默，下一轮重试 */ }
    executing.value = false
  }
  tick()
}

function exportResult() {
  const lines = (task.value?.items || []).map(i =>
    `===== ${i.device_name} [${i.status}]${i.error ? ' ' + i.error : ''} =====\n${i.output || ''}`)
  const blob = new Blob([lines.join('\n\n')], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `批量执行结果-${task.value.id}.txt`
  a.click()
  URL.revokeObjectURL(a.href)
}

// ---------- 交互式控制台 ----------
async function openConsole(row) {
  consoleDevice.value = row
  consoleWsOk.value = false
  consoleVisible.value = true
}

async function initConsole() {
  try {
    await nextTick()
    if (!termEl.value || !consoleDevice.value) return
    const [{ Terminal }, { FitAddon }] = await Promise.all([
      import('@xterm/xterm'), import('@xterm/addon-fit')])
    const term = new Terminal({
      fontSize: 13, fontFamily: 'Consolas, "Courier New", monospace',
      theme: { background: '#101828', foreground: '#d6e2f0', cursor: '#4fc3f7' },
      cursorBlink: true, scrollback: 5000,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(termEl.value)
    fit.fit()
    term.onData(text => consoleWs?.send(JSON.stringify({ type: 'data', text })))
    term.onResize(({ cols, rows }) =>
      consoleWs?.send(JSON.stringify({ type: 'resize', cols, rows })))
    consoleTerm = term
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/api/netdev/ws/${consoleDevice.value.id}`)
    consoleWs = ws
    ws.onmessage = ev => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.type === 'data') {
        if (!consoleWsOk.value) consoleWsOk.value = true
        term.write(msg.text)
      } else if (msg.type === 'error') {
        term.write(`\r\n\x1b[31m${msg.text}\x1b[0m\r\n`)
        ws.close()
      }
    }
    ws.onopen = () => { term.focus(); term.emitResize() }
    ws.onclose = () => term.write('\r\n\x1b[33m— 会话已断开 —\x1b[0m\r\n')
    ws.onerror = () => term.write('\r\n\x1b[31m— WebSocket 连接失败 —\x1b[0m\r\n')
  } catch (e) {
    ElMessage.error(`控制台初始化失败：${e.message || e}`)
  }
}

function closeConsole() {
  consoleWs?.close()
  consoleTerm?.dispose()
  consoleWs = null
  consoleTerm = null
}

onBeforeUnmount(() => {
  clearTimeout(pollTimer.value)
  consoleWs?.close()
  consoleTerm?.dispose()
})

onMounted(async () => {
  await loadDevices()
  try { vendors.value = await NetDev.vendors() } catch { /* 静默 */ }
})
</script>

<style scoped>
.nd-page { display: flex; flex-direction: column; gap: 12px; height: 100%; overflow: auto; }

/* 顶部 */
.nd-header-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.nd-title { display: flex; align-items: center; gap: 12px; }
.nd-title-icon { width: 42px; height: 42px; border-radius: 10px; display: flex;
  align-items: center; justify-content: center; color: #fff;
  background: linear-gradient(135deg, #1a73e8, #00b8a9); }
.nd-title b { font-size: 16px; }
.nd-title .nd-sub { margin-top: 2px; }
.nd-sub { color: #8a94a6; font-size: 12px; }
.stat-cards { display: flex; gap: 12px; margin-top: 14px; }
.stat-card { flex: 1; background: #f7f9fc; border-radius: 8px; padding: 10px 14px; }
.stat-num { font-size: 20px; font-weight: 600; color: #1f2d3d; }
.stat-num.nd-ok { color: #27ae60; }
.stat-label { font-size: 12px; color: #8a94a6; }

/* 筛选行 */
.nd-filter { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }

/* 设备表 */
.nd-vendor-tag { border: none; color: #fff; }
.nd-dev-name { font-weight: 600; color: #1f2d3d; }
.nd-mono { font-family: Consolas, monospace; font-size: 12px; }

/* 批量执行 */
.nd-exec { align-items: stretch; }
.nd-col-card { display: flex; flex-direction: column; }
.nd-cmd-input :deep(textarea) { font-family: Consolas, monospace; font-size: 12px; }
.nd-results { border: none; }
.nd-result-head { display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; }
.nd-result-head .nd-dev-name { flex-shrink: 0; }
.nd-err { color: #f56c6c; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nd-output { margin: 0; padding: 10px 12px; background: #101828; color: #d6e2f0;
  font-family: Consolas, 'Courier New', monospace; font-size: 12px; line-height: 1.55;
  max-height: 340px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
.nd-empty { padding: 28px 0; text-align: center; color: #8a94a6; font-size: 13px; line-height: 1.8; }

/* 操作列：2×2 网格对齐（每行两个操作） */
.nd-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 0 6px; }
.nd-ops :deep(.el-button) { margin: 0; padding: 5px 0; justify-content: center; }

/* 控制台 */
.nd-term { height: 520px; background: #101828; border-radius: 8px; overflow: hidden; }
.nd-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.nd-dot-ok { background: #27ae60; box-shadow: 0 0 4px #27ae60; }
.nd-dot-bad { background: #e67e22; }
</style>
