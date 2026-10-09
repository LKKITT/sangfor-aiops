<template>
  <div class="nd-page">
    <!-- 顶部：标题 + 统计 + 操作 -->
    <div class="page-head">
      <div>
        <h2 class="ph-title">网络设备管理</h2>
        <p class="ph-desc">交换机 / 路由器 SSH 远程管理 · 支持华为 / H3C / 思科 / 锐捷 / 中兴等 12 家厂商</p>
      </div>
      <div class="ph-actions">
        <el-button @click="showBatchAdd = true">
          <el-icon><Upload /></el-icon>&nbsp;批量导入
        </el-button>
        <el-button :disabled="!devices.length" @click="exportDevices">
          <el-icon><Download /></el-icon>&nbsp;导出设备
        </el-button>
        <el-button type="primary" @click="openAdd">
          <el-icon><Plus /></el-icon>&nbsp;添加设备
        </el-button>
      </div>
    </div>
    <div class="nd-stats">
      <div class="sfa-stat"><div class="num">{{ devices.length }}</div><div class="lbl">设备总数</div></div>
      <div class="sfa-stat"><div class="num">{{ groupCount }}</div><div class="lbl">分组</div></div>
      <div class="sfa-stat nd-ok"><div class="num">{{ okCount }}</div><div class="lbl">连通正常</div></div>
      <div class="sfa-stat"><div class="num">{{ devices.length - okCount }}</div><div class="lbl">待测试</div></div>
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

      <!-- ============ Tab 3：网络拓扑（激活时才渲染，保证 ECharts 容器可见） ============ -->
      <el-tab-pane label="网络拓扑" name="topo">
        <NetDevTopology v-if="tab === 'topo'" @edit="editById" @console="consoleById" />
      </el-tab-pane>
    </el-tabs>

    <!-- 单台添加 -->
    <el-dialog v-model="showAdd" :title="editId ? '编辑网络设备' : '添加网络设备'" width="500px" append-to-body>
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
    <el-dialog v-model="showBatchAdd" title="批量导入网络设备" width="660px" append-to-body>
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

    <!-- 交互式控制台：左侧终端 + 右侧 AI 运维助手 -->
    <el-dialog v-model="consoleVisible" :title="`控制台 — ${consoleDevice?.name || ''}（${consoleDevice?.host}）`"
               width="min(1340px, 96vw)" top="3vh" destroy-on-close :close-on-click-modal="false" append-to-body
               @opened="initConsole" @close="closeConsole">
      <div class="nd-console">
        <div class="nd-console-main">
          <div ref="termEl" class="nd-term"></div>
          <div class="nd-console-status">
            <span class="nd-dot" :class="consoleWsOk ? 'nd-dot-ok' : 'nd-dot-bad'"></span>
            <span>{{ consoleWsOk ? '已连接设备 CLI' : '连接中…' }} · 关闭弹窗即断开 SSH 会话</span>
            <span class="nd-copy-hint">选中即复制 · Ctrl+Shift+C 复制 · Ctrl+V 粘贴</span>
          </div>
        </div>

        <aside class="nd-side">
          <div class="nd-side-head">
            <el-radio-group v-model="sideTab" size="small">
              <el-radio-button value="ai"><el-icon><MagicStick /></el-icon>&nbsp;AI 助手</el-radio-button>
              <el-radio-button value="cmd"><el-icon><Files /></el-icon>&nbsp;命令速查</el-radio-button>
            </el-radio-group>
          </div>

          <!-- AI 助手：意图输入 → 自动执行只读命令 → 基于回显作答 -->
          <template v-if="sideTab === 'ai'">
            <div class="nd-quick">
              <button v-for="q in QUICK_OPS" :key="q" type="button" class="nd-quick-chip"
                      :disabled="aiBusy" @click="sendAssist(q)">{{ q }}</button>
              <button type="button" class="nd-quick-chip is-analyze" :disabled="aiBusy"
                      title="把终端最近约 150 行回显（已脱敏）交给 AI 研判"
                      @click="runAiAnalysis">⚡ 分析最近回显</button>
            </div>
            <div ref="threadEl" class="nd-thread">
              <div v-if="!aiMessages.length" class="nd-thread-empty">
                用一句话描述你要做的运维操作，例如「查看 CPU 利用率」「分析最近的 20 条日志」。<br/>
                AI 需要设备数据时会自动代你执行<b>只读命令</b>（display/show），修改类命令仅插入终端、由你人工回车。
              </div>
              <template v-for="m in aiMessages" :key="m.id">
                <div v-if="m.role === 'user'" class="nd-msg nd-msg-user">{{ m.text }}</div>
                <div v-else-if="m.role === 'step'" class="nd-msg-step" :class="`is-${m.tone || 'info'}`">
                  <el-icon v-if="m.tone === 'err'"><CircleCloseFilled /></el-icon>
                  <el-icon v-else><InfoFilled /></el-icon>
                  <span class="mono">{{ m.text }}</span>
                  <span v-if="m.note" class="nd-msg-note">{{ m.note }}</span>
                </div>
                <div v-else class="nd-msg nd-msg-ai">
                  <div class="md-body" v-html="render(m.text)"></div>
                  <div v-if="m.commands?.length" class="nd-msg-cmds">
                    <span class="nd-msg-cmds-label">建议命令（只读命令点击直接执行）：</span>
                    <button v-for="c in m.commands" :key="c" type="button" class="nd-cmd-chip mono"
                            @click="runSuggested(c)">{{ c }}</button>
                  </div>
                </div>
              </template>
              <div v-if="aiBusy" class="nd-thinking">
                <el-icon class="is-loading"><Loading /></el-icon>{{ aiStep || 'AI 处理中…' }}
              </div>
            </div>
            <div class="nd-input">
              <el-input v-model="aiInput" type="textarea" :autosize="{ minRows: 1, maxRows: 3 }"
                        resize="none" placeholder="输入运维请求，如：查看接口流量TOP5"
                        :disabled="aiBusy" @keydown.enter.exact.prevent="sendAssist()" />
              <el-button type="primary" :loading="aiBusy" @click="sendAssist()">发送</el-button>
            </div>
          </template>

          <!-- 命令速查：本地字典零延迟补全，点击仅插入终端 -->
          <template v-else>
            <el-input v-model="cmdQuery" size="small" clearable placeholder="过滤命令（如 display / interface / show）"
                      style="margin-bottom: 8px" />
            <div class="nd-cmdlist">
              <button v-for="c in consoleSuggests" :key="c.cmd" type="button" class="nd-cmd-row"
                      :title="c.desc" @click="insertToTerm(c.cmd)">
                <span class="mono">{{ c.cmd }}</span><span class="nd-cmd-desc">{{ c.desc }}</span>
              </button>
              <div v-if="!consoleSuggests.length" class="nd-thread-empty">无匹配命令</div>
            </div>
            <div class="nd-cmd-tip">点击命令仅插入终端，回车执行由你确认</div>
          </template>
        </aside>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import '@xterm/xterm/css/xterm.css'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Monitor, Plus, Promotion, CaretRight, DataLine, Download, Upload, Search,
         MagicStick, Files, CircleCloseFilled, InfoFilled, Loading } from '@element-plus/icons-vue'
import { NetDev } from '../api.js'
import NetDevTopology from './NetDevTopology.vue'
import { suggestCommands } from './netdevConsoleDict'
import { renderMarkdown } from '../chat/markdown'

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
let consoleReconnects = 0
let lastConsoleDeviceId = ''   // 控制台闪断自动重连计数（成功连接后清零）
// ---- 控制台右侧 AI 助手：意图对话线程 + 命令速查 ----
// 只读命令（display/show 等）AI 可代发执行；修改类命令仅插入终端由人工回车。
const sideTab = ref('ai')
const cmdQuery = ref('')
const aiMessages = ref([])      // { id, role: user|assistant|step, text, commands?, note?, tone? }
const aiInput = ref('')
const aiBusy = ref(false)
const aiStep = ref('')
const threadEl = ref(null)
const consoleSuggests = computed(() => suggestCommands(consoleDevice.value?.vendor || '', cmdQuery.value))
const QUICK_OPS = ['查看 CPU 利用率', '查看内存利用率', '查看接口状态概览', '分析最近的 20 条日志',
                   '查看版本与运行时间', '查看 ARP 与 MAC 表', '巡检设备关键状态']

const render = renderMarkdown
let _msgSeq = 0
function pushMsg(m) {
  aiMessages.value.push({ id: ++_msgSeq, ...m })
  nextTick(() => {
    const el = threadEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 可选的上下文命令（帮助 AI 定位当前在排查什么）
const aiCurrentCmd = ref('')

function insertToTerm(text) {
  if (!consoleTerm) { ElMessage.warning('控制台未连接'); return }
  consoleTerm.write(text)
  ElMessage.success('已插入终端，回车执行')
}

function readTermTail(lines = 150) {
  return tailLines(lines).slice(-8000)
}

// 自动代发护栏（与后端白名单同口径双保险）：仅查询类命令可代发
const ASSIST_ALLOW_RE = /^(display|show|screen-length|terminal length|ping|tracert|traceroute|dir|more|head|tail|transceiver)\b|^\s*display\s+(counters|packet-drop|optic|diagnostic|environment|transceiver)\b|^\s*show\s+(counters|environment|diagnostic|interfaces?\s+transceiver|interface\s+transceiver)\b|^\s*\/\S+.*\b(print|monitor)\b/i
const ASSIST_DENY_RE = /\b(config|conf\b|undo|reset|reboot|reload|save|delete|copy|debug|clear|shutdown|restore|upgrade|erase|write|system-view|patch|license)\b|^no\s/i
const isReadonlyCommand = (cmd) => ASSIST_ALLOW_RE.test(cmd) && !ASSIST_DENY_RE.test(cmd)

// 建议命令点击：只读命令直接代发执行；非只读仅插入终端（人工回车）
async function runSuggested(cmd) {
  const blocked = autoRunCommand(cmd)
  if (blocked) { ElMessage.warning('非只读命令已插入终端，请人工回车执行'); return }
  pushMsg({ role: 'step', text: cmd })
  await waitTermQuiet()
}
// 设备侧命令报错标记：命中则该命令视为失败，传给 AI 避免反复执行不存在/错误的命令
const CMD_ERROR_RE = /% (unrecognized|invalid|wrong) |unrecognized command found|invalid input|incomplete command|too many parameters|命令不存在|不存在或参数|参数错误|Error: /i

// 失败命令跨会话记忆：按厂商存 localStorage，AI 永不再尝试已证明不存在的命令
function failedStoreKey() {
  return `sfa_failed_cmds_${(consoleDevice.value?.vendor || 'generic').toLowerCase()}`
}
function loadFailedStore() {
  try { return JSON.parse(localStorage.getItem(failedStoreKey()) || '[]') } catch { return [] }
}
function rememberFailed(cmd) {
  try {
    const arr = loadFailedStore()
    if (!arr.includes(cmd)) localStorage.setItem(failedStoreKey(), JSON.stringify([...arr, cmd].slice(-30)))
  } catch { /* 存储异常不影响流程 */ }
}
// 只读取新增行（避免把上一条命令残留的错误文本误判为本次失败）
function readLinesSince(baseLine) {
  return outLines.slice(Math.max(0, baseLine)).join(String.fromCharCode(10))
}

// ---- 回显环形缓冲：跨重连保留全部输出（AI 上下文不因断连丢失） ----
const outLines = []
let _linePartial = ''

function pushOutRing(text) {
  _linePartial += text
  const parts = _linePartial.split('\n')
  _linePartial = parts.pop()          // 末行可能不完整，留到下一段
  for (const ln of parts) {
    outLines.push(ln)
    if (outLines.length > 600) outLines.shift()
  }
}

function tailLines(n) {
  return outLines.slice(-Math.max(1, n)).join('\n')
}

// ---- 连接会话：可重复调用（重连复用现有终端，仅重建 WS） ----
function connectSession() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/netdev/ws/${consoleDevice.value.id}`)
  consoleWs = ws
  ws.onmessage = ev => {
    let msg
    try { msg = JSON.parse(ev.data) } catch { return }
    if (msg.type === 'data') {
      if (!consoleWsOk.value) consoleWsOk.value = true
      pushOutRing(msg.text)
      consoleTerm?.write(msg.text)
    } else if (msg.type === 'error') {
      consoleTerm?.write(`\r\n\x1b[31m${msg.text}\x1b[0m\r\n`)
      ws.close()
    }
  }
  ws.onopen = () => { consoleReconnects = 0; consoleTerm?.emitResize?.(); consoleTerm?.focus?.() }
  ws.onclose = () => {
    consoleTerm?.write('\r\n\x1b[33m— 会话已断开 —\x1b[0m\r\n')
    // 闪断自动重连（最多 3 次；手动关闭弹窗时 consoleVisible 已为 false）
    if (consoleVisible.value && consoleReconnects < 3) {
      consoleReconnects += 1
      consoleTerm?.write('\x1b[33m正在自动重连…\x1b[0m\r\n')
      setTimeout(() => { try { connectSession() } catch { /* 忽略 */ } }, 1500)
    }
  }
  ws.onerror = () => consoleTerm?.write('\r\n\x1b[31m— WebSocket 连接失败 —\x1b[0m\r\n')
}

async function waitWsOpen(timeoutMs = 8000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    if (consoleWs && consoleWs.readyState === WebSocket.OPEN) return true
    await new Promise(r => setTimeout(r, 250))
  }
  return !!(consoleWs && consoleWs.readyState === WebSocket.OPEN)
}

// 返回 true = 非只读被拦截（已插入终端未回车）；false = 已代发执行
function autoRunCommand(cmd) {
  if (!consoleWs || consoleWs.readyState !== WebSocket.OPEN) return true
  if (!isReadonlyCommand(cmd)) {
    consoleTerm?.write(cmd)
    return true
  }
  consoleWs.send(JSON.stringify({ type: 'data', text: cmd + '\n' }))
  return false
}

// 等待终端回显趋于静止：xterm buffer 行数不再变化 quietMs 视为本条命令输出完成
function waitTermQuiet(quietMs = 700, maxMs = 9000) {
  return new Promise(resolve => {
    let lastLen = -1
    let lastChange = Date.now()
    const t0 = Date.now()
    const timer = setInterval(() => {
      let len = -1
      try { len = consoleTerm?.buffer.active.length ?? -1 } catch { /* 终端已释放 */ }
      if (len !== lastLen) { lastLen = len; lastChange = Date.now() }
      if (Date.now() - lastChange >= quietMs || Date.now() - t0 >= maxMs) {
        clearInterval(timer)
        resolve()
      }
    }, 120)
  })
}

async function sendAssist(textRaw) {
  const text = (textRaw ?? aiInput.value).trim()
  if (!text || aiBusy.value || !consoleDevice.value) return
  if (!consoleWsOk.value) { ElMessage.warning('控制台尚未连接，待连接建立后再使用 AI 助手'); return }
  if (textRaw === undefined) aiInput.value = ''
  pushMsg({ role: 'user', text })
  sideTab.value = 'ai'
  aiBusy.value = true
  const done = [], failed = [], manual = []
let emptyRetried = false
for (const c of loadFailedStore()) if (!failed.includes(c)) failed.push(c)
if (failed.length) pushMsg({ role: 'step', tone: 'warn',
  text: `已载入该厂商历史失败命令 ${failed.length} 条（AI 不会再尝试）：${failed.join('；')}` })
  // 轮次上限内每轮执行后把新回显喂回模型；末轮 force_answer 让模型基于已采集回显收尾，
  // 避免「巡检类」多命令请求把轮次耗尽后只得到一句上限提示。
  const MAX_ROUNDS = 6
  try {
    for (let round = 0; round < MAX_ROUNDS; round++) {
      aiStep.value = round === 0 ? '正在思考下一步…' : `正在汇总分析（第 ${round + 1}/${MAX_ROUNDS} 步）…`
    // 会话自愈：WS 断开先重连（回显在环形缓冲中保留，AI 上下文不丢）
    if (!consoleWs || consoleWs.readyState !== WebSocket.OPEN) {
      aiStep.value = '控制台连接断开，正在重连…'
      connectSession()
      if (!(await waitWsOpen(8000))) {
        pushMsg({ role: 'step', tone: 'err', text: '控制台重连失败，请检查设备网络后重试' })
        break
      }
    }
      const r = await NetDev.consoleAssist({
        device_id: consoleDevice.value.id, request: text,
        output: readTermTail(150), done_commands: done, failed_commands: failed,
        force_answer: round === MAX_ROUNDS - 1,
      })
      // 模型偶发空响应：自动重试一次（重连刚恢复时首调易失败）
      if (r?.action === 'answer' && String(r.answer || '').startsWith('（AI 本轮未返回有效内容')
          && !emptyRetried) {
        emptyRetried = true
        round -= 1
        await new Promise(rs => setTimeout(rs, 900))
        continue
      }
      if (r.action === 'execute' && r.commands?.length) {
        for (const cmd of r.commands) {
          aiStep.value = `正在执行 ${cmd}`
          // 基线：只扫描本条命令执行后的新增行（旧回显里的错误文本会造成误判）
          const base = outLines.length
          const blocked = autoRunCommand(cmd)
          if (blocked) {
            pushMsg({ role: 'step', tone: 'warn', text: `非只读命令「${cmd}」已插入终端，请人工回车执行` })
            manual.push(cmd)
            continue
          }
          done.push(cmd)
          pushMsg({ role: 'step', text: cmd, note: r.note })
          await waitTermQuiet()
          if (CMD_ERROR_RE.test(readLinesSince(base))) {
            failed.push(cmd)
            rememberFailed(cmd)
            pushMsg({ role: 'step', tone: 'warn',
                      text: `「${cmd}」设备报语法错误或不存在，已记忆并告知 AI 不再重试` })
          }
        }
        continue   // 带着新回显再问一轮
      }
      pushMsg({ role: 'assistant', text: r.answer || '（无有效回答）', commands: r.commands || [] })
      if (r.manual_commands?.length) {
        pushMsg({ role: 'step', tone: 'warn',
                  text: `非只读命令需人工执行（已插入终端不回车）：${r.manual_commands.join('；')}` })
        r.manual_commands.forEach(c => insertToTerm(c))
      }
      return
    }
    pushMsg({ role: 'assistant',
              text: '自动执行已到上限。请基于当前回显继续人工排查，或给出更具体的请求。',
              commands: [] })
  } catch (e) {
    pushMsg({ role: 'step', tone: 'err', text: String(e.message || e) })
  } finally {
    aiBusy.value = false
    aiStep.value = ''
  }
}

async function runAiAnalysis() {
  if (!consoleDevice.value || aiBusy.value) return
  sideTab.value = 'ai'
  const output = readTermTail(150)
  if (!output.trim()) {
    pushMsg({ role: 'step', tone: 'warn', text: '终端回显为空：请先执行一条命令再分析' })
    return
  }
  pushMsg({ role: 'user', text: '分析最近回显' })
  aiBusy.value = true
  try {
    aiStep.value = '正在分析最近回显…'
    const r = await NetDev.consoleAnalyze(consoleDevice.value.id, output, aiCurrentCmd.value)
    pushMsg({ role: 'assistant', text: r.analysis, commands: r.commands || [] })
  } catch (e) {
    pushMsg({ role: 'step', tone: 'err', text: String(e.message || e) })
  } finally {
    aiBusy.value = false
    aiStep.value = ''
  }
}
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

const maxConcurrency = 20   // 与后端 BATCH_CONCURRENCY 保持一致（仅用于提示文案）
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
  // ElMessageBox.confirm 在用户点「取消」时 reject 'cancel'，必须捕获，否则产生未捕获 rejection
  try {
    await ElMessageBox.confirm(`确定删除设备「${row.name}」吗？`, '删除设备', { type: 'warning' })
  } catch {
    return   // 用户取消，静默返回
  }
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
  let ticks = 0   // 连续轮询计数：间隔指数退避 1.2s→2.4s→4s（长任务降低空转频率）
  const tick = async () => {
    try {
      // 页面隐藏时低频待机（不递增 ticks，回前台后恢复基础频率）
      if (document.hidden) { pollTimer.value = setTimeout(tick, 2000); return }
      const t = await NetDev.task(taskId)
      task.value = t
      if (t.status === 'running') {
        ticks++
        pollTimer.value = setTimeout(tick, Math.min(1200 * 2 ** ticks, 4000))
        return
      }
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
  // AI 助手线程按会话重置：历史残留会误导下一台设备的排障
  aiMessages.value = []
  aiInput.value = ''
  sideTab.value = 'ai'
  consoleVisible.value = true
}

// 拓扑节点回调：按设备 id 定位完整设备记录后走既有编辑/控制台入口
function editById(deviceId) {
  const row = devices.value.find(d => d.id === deviceId)
  if (!row) return ElMessage.warning('设备不存在或已被删除')
  openEdit(row)
}

function consoleById(deviceId) {
  const row = devices.value.find(d => d.id === deviceId)
  if (!row) return ElMessage.warning('设备不存在或已被删除')
  openConsole(row)
}

async function initConsole() {
  try {
    await nextTick()
    if (!termEl.value || !consoleDevice.value) return
    termEl.value.innerHTML = ''   // 重连重建终端：清理旧画布，避免 DOM 堆叠
    if (lastConsoleDeviceId !== consoleDevice.value.id) {
      outLines.length = 0; _linePartial = ''   // 换设备：清空回显缓冲，避免上下文串台
      lastConsoleDeviceId = consoleDevice.value.id
    }   // 重连重建终端：清理旧画布，避免 DOM 堆叠
    const [{ Terminal }, { FitAddon }] = await Promise.all([
      import('@xterm/xterm'), import('@xterm/addon-fit')])
    const term = new Terminal({
      fontSize: 14, fontFamily: 'Consolas, "Courier New", monospace',
      theme: { background: '#0D1424', foreground: '#d6e2f0', cursor: '#4fc3f7' },
      cursorBlink: true, scrollback: 5000,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(termEl.value)
    fit.fit()

    // 复制/粘贴支持：选中即复制（回退 execCommand 兼容非安全上下文）；Ctrl+Shift+C 显式复制；
    // Ctrl+V 粘贴由 xterm 的 textarea paste 事件原生处理
    const copySelection = (t) => {
      const text = t.getSelection()
      if (!text) return
      const fallback = () => {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        try { document.execCommand('copy') } catch { /* 忽略 */ }
        document.body.removeChild(ta)
      }
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).catch(fallback)
      } else fallback()
    }
    term.attachCustomKeyEventHandler(ev => {
      if (ev.type === 'keydown' && ev.ctrlKey && ev.shiftKey && !ev.altKey && !ev.metaKey
          && (ev.key === 'C' || ev.key === 'c')) {
        copySelection(term)
        return false
      }
      return true
    })
    term.onSelectionChange(() => copySelection(term))

    term.onData(text => {
      // Backspace：xterm 默认发 （DEL），部分网络设备 CLI（Comware/VRP）只认 （BS）
      const out = text === '' ? '' : text
      consoleWs?.send(JSON.stringify({ type: 'data', text: out }))
    })
    term.onResize(({ cols, rows }) =>
      consoleWs?.send(JSON.stringify({ type: 'resize', cols, rows })))
    consoleTerm = term
    connectSession()
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
.nd-page { display: flex; flex-direction: column; gap: 14px; height: 100%; overflow: auto; animation: sfa-fade-up .3s var(--ease-out); }

/* 顶部统计 */
.nd-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.sfa-stat.nd-ok .num { color: var(--sfa-ok-ink); }
.nd-sub { color: var(--sfa-text-3); font-size: 12px; }

/* 筛选行 */
.nd-filter { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }

/* 设备表 */
.nd-vendor-tag { border: none; color: #fff; }
.nd-dev-name { font-weight: 600; }
.nd-mono { font-family: var(--sfa-mono); font-size: 12px; font-feature-settings: "tnum" 1; }

/* 批量执行 */
.nd-exec { align-items: stretch; }
.nd-col-card { display: flex; flex-direction: column; }
.nd-cmd-input :deep(textarea) { font-family: var(--sfa-mono); font-size: 12px; }
.nd-results { border: none; }
.nd-result-head { display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; }
.nd-result-head .nd-dev-name { flex-shrink: 0; }
.nd-err { color: var(--sfa-danger); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nd-output { margin: 0; padding: 12px 14px; background: #0D1424; color: #C6D2EC;
  border: 1px solid #1C2742;
  font-family: var(--sfa-mono); font-size: 12px; line-height: 1.6;
  max-height: 340px; overflow: auto; white-space: pre-wrap; word-break: break-all; border-radius: 10px; }
.nd-empty { padding: 28px 0; text-align: center; color: var(--sfa-text-3); font-size: 13px; line-height: 1.8; }

/* 操作列：2×2 网格对齐（每行两个操作） */
.nd-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 0 6px; }
.nd-ops :deep(.el-button) { margin: 0; padding: 5px 0; justify-content: center; }

/* 控制台：左终端 + 右 AI 助手双栏（PC 端尽量占满可视区，避免拥挤局促） */
.nd-console { display: flex; gap: 14px; align-items: stretch;
  height: clamp(620px, calc(100vh - 170px), 960px); }
.nd-console-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.nd-term { flex: 1; min-height: 0; background: #0D1424; border: 1px solid #1C2742; border-radius: var(--sfa-r-md); overflow: hidden; }
.nd-console-status { display: flex; align-items: center; gap: 7px; color: var(--sfa-text-3); font-size: 12px; flex-wrap: wrap; }
.nd-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.nd-dot-ok { background: #0FB9A4; box-shadow: 0 0 6px rgba(15, 185, 164, .8); }
.nd-dot-bad { background: #F5A90B; }
.nd-copy-hint { color: var(--sfa-text-4); font-size: 11px; margin-left: auto; }

.nd-side { width: 400px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px;
  border: 1px solid var(--sfa-border-soft); border-radius: var(--sfa-r-md);
  background: var(--sfa-surface); padding: 12px; min-height: 0; }
.nd-side-head { display: flex; align-items: center; justify-content: space-between; }

.nd-quick { display: flex; flex-wrap: wrap; gap: 5px; }
.nd-quick-chip { cursor: pointer; border: 1px solid var(--sfa-border-soft); background: var(--sfa-tint-primary, #F4F7FD);
  border-radius: 999px; padding: 3px 10px; font-size: 12px; color: var(--sfa-text-2);
  transition: all var(--dur-1) var(--ease-out); }
.nd-quick-chip:hover:not(:disabled) { border-color: var(--sfa-primary); color: var(--sfa-primary); }
.nd-quick-chip:disabled { opacity: .55; cursor: not-allowed; }
.nd-quick-chip.is-analyze { border-color: rgba(15, 185, 164, .4); background: rgba(15, 185, 164, .08); color: var(--sfa-ok-ink); }

.nd-thread { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding: 2px; }
.nd-thread-empty { margin: auto 0; text-align: center; color: var(--sfa-text-4); font-size: 12px; line-height: 1.9; padding: 0 8px; }
.nd-msg { font-size: 12.5px; line-height: 1.7; border-radius: 10px; padding: 7px 10px; max-width: 96%; word-break: break-word; }
.nd-msg-user { align-self: flex-end; background: var(--sfa-tint-primary, rgba(59, 99, 255, .08));
  border: 1px solid rgba(59, 99, 255, .18); }
.nd-msg-ai { align-self: flex-start; background: var(--sfa-bg-deep, #F6F8FC); border: 1px solid var(--sfa-border-soft); }
.nd-msg-ai .md-body { font-size: 12.5px; }
.nd-msg-ai .md-body :deep(h1), .nd-msg-ai .md-body :deep(h2),
.nd-msg-ai .md-body :deep(h3), .nd-msg-ai .md-body :deep(h4) {
  margin: 8px 0 4px; font-size: 13px; font-weight: 700; line-height: 1.5; }
.nd-msg-ai .md-body :deep(h1):first-child, .nd-msg-ai .md-body :deep(h2):first-child,
.nd-msg-ai .md-body :deep(h3):first-child, .nd-msg-ai .md-body :deep(p):first-child { margin-top: 0; }
.nd-msg-ai .md-body :deep(p) { margin: 4px 0; }
.nd-msg-ai .md-body :deep(ul), .nd-msg-ai .md-body :deep(ol) { margin: 4px 0; padding-left: 18px; }
.nd-msg-ai .md-body :deep(li) { margin: 2.5px 0; }
.nd-msg-ai .md-body :deep(table) { border-collapse: collapse; margin: 6px 0; width: 100%; font-size: 12px; }
.nd-msg-ai .md-body :deep(th), .nd-msg-ai .md-body :deep(td) {
  border: 1px solid var(--sfa-border-soft); padding: 4px 8px; text-align: left; }
.nd-msg-ai .md-body :deep(th) { background: var(--sfa-tint-primary, rgba(59, 99, 255, .06)); font-weight: 650; }
.nd-msg-ai .md-body :deep(blockquote) { margin: 6px 0; padding: 2px 10px; border-left: 3px solid var(--sfa-primary);
  color: var(--sfa-text-3); }
.nd-msg-ai .md-body :deep(hr) { border: none; border-top: 1px solid var(--sfa-border-soft); margin: 8px 0; }
.nd-msg-ai .md-body :deep(pre) { background: #0D1424; color: #C6D2EC; padding: 8px 10px;
  border-radius: 8px; overflow-x: auto; font-size: 11.5px; margin: 6px 0; }
.nd-msg-ai .md-body :deep(pre code) { background: transparent; padding: 0; color: inherit; }
.nd-msg-ai .md-body :deep(code) { background: var(--sfa-code-bg, rgba(148,163,199,.15)); padding: 0 4px; border-radius: 4px; font-size: 11.5px; }
.nd-msg-cmds { margin-top: 7px; display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.nd-msg-cmds-label { font-size: 11px; color: var(--sfa-text-4); width: 100%; }
.nd-cmd-chip { cursor: pointer; border: 1px solid rgba(15, 185, 164, .4); background: rgba(15, 185, 164, .08);
  color: var(--sfa-ok-ink); border-radius: 6px; padding: 2px 8px; font-size: 11.5px; }
.nd-cmd-chip:hover { background: rgba(15, 185, 164, .16); }
.nd-msg-step { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--sfa-text-3);
  padding: 2px 4px; flex-wrap: wrap; }
.nd-msg-step .el-icon { color: var(--sfa-primary); flex-shrink: 0; }
.nd-msg-step.is-warn { color: var(--sfa-warn-ink, #B7791F); }
.nd-msg-step.is-warn .el-icon { color: var(--sfa-warn-ink, #B7791F); }
.nd-msg-step.is-err { color: var(--sfa-danger); }
.nd-msg-step.is-err .el-icon { color: var(--sfa-danger); }
.nd-msg-note { font-size: 10.5px; color: var(--sfa-text-4); }
.nd-thinking { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--sfa-primary); padding: 2px 4px; }

.nd-input { display: flex; gap: 6px; align-items: flex-end; }
.nd-input :deep(.el-textarea__inner) { font-size: 12.5px; }

.nd-cmdlist { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.nd-cmd-row { cursor: pointer; display: flex; align-items: center; justify-content: space-between; gap: 8px;
  border: 1px solid var(--sfa-border-soft); background: transparent; border-radius: 8px;
  padding: 5px 9px; font-size: 12px; text-align: left; transition: all var(--dur-1) var(--ease-out); }
.nd-cmd-row:hover { border-color: var(--sfa-primary); background: var(--sfa-tint-primary, #F4F7FD); }
.nd-cmd-desc { opacity: .65; font-size: 11px; flex-shrink: 0; }
.nd-cmd-tip { font-size: 11px; color: var(--sfa-text-4); }

@media (max-width: 980px) {
  .nd-console { flex-direction: column; height: auto; }
  .nd-term { min-height: 380px; }
  .nd-side { width: 100%; }
  .nd-thread { max-height: 320px; }
}
</style>
