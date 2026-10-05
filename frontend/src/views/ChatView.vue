<template>
  <div class="chat-page">
    <!-- 无设备时的引导 -->
    <div v-if="!hasAnyDevice" class="no-device-hero">
      <div class="hero-orb orb-a"></div>
      <div class="hero-orb orb-b"></div>
      <div class="hero-grid"></div>
      <svg class="hero-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id="sfaChatGrad" x1="6" y1="4" x2="34" y2="36" gradientUnits="userSpaceOnUse">
            <stop offset="0" stop-color="#4A70FF" /><stop offset="1" stop-color="#0FB9A4" />
          </linearGradient>
        </defs>
        <path d="M20 3.5 34.3 11.8v16.4L20 36.5 5.7 28.2V11.8L20 3.5Z" stroke="url(#sfaChatGrad)" stroke-width="2.5" stroke-linejoin="round" />
        <path d="M20 13.2v3.4M23 21.5l3.4 2M17 21.5l-3.4 2" stroke="#B9C4E4" stroke-width="1.4" stroke-linecap="round" />
        <circle cx="20" cy="20" r="3.4" fill="url(#sfaChatGrad)" />
        <circle cx="20" cy="10.6" r="2" fill="#4A70FF" />
        <circle cx="28.6" cy="25" r="2" fill="#0FB9A4" />
        <circle cx="11.4" cy="25" r="2" fill="#4A70FF" />
      </svg>
      <h2 class="hero-title">接入你的第一台设备</h2>
      <p class="hero-desc">
        添加一台深信服设备（AF 防火墙 / AC 上网行为管理 / SCP 云计算平台），<br />
        或在「网络设备管理」页添加华为 / H3C / 锐捷交换机路由器，<br />
        即可通过自然语言完成配置管理、体检、终端定位与升级建议。
      </p>
      <el-button type="primary" size="large" round @click="goToDevices">
        <el-icon><Plus /></el-icon>&nbsp;添加设备
      </el-button>
    </div>

    <template v-else>
      <!-- 对话区 -->
      <div class="chat-scroll" ref="scrollRef">
        <div class="chat-col">
          <div class="chat-hero" v-if="messages.length <= 1">
            <h2 class="ch-title">全局运维 AI 助手</h2>
            <p class="ch-desc">统一对话管理深信服设备（AF/AC/SCP）与网络设备（华为/H3C/锐捷）· 查询与配置变更 · 批量操作 · 体检备份 · 终端定位 · 升级建议，修改类操作会先生成确认卡片。</p>
          </div>

          <div v-for="(m, i) in messages" :key="i" class="chat-row" :class="m.role">
            <div v-if="m.role === 'assistant'" class="chat-avatar ai" title="SFA Agent">
              <svg viewBox="0 0 40 40" fill="none" aria-hidden="true">
                <path d="M20 5.5 32.6 12.8v14.4L20 34.5 7.4 27.2V12.8L20 5.5Z" stroke="#5F79E8" stroke-width="2.6" stroke-linejoin="round" />
                <circle cx="20" cy="20" r="4" fill="#0FB9A4" />
              </svg>
            </div>

            <div v-if="m.role === 'assistant'" class="bubble-ai">
              <div v-if="m.text" class="bubble-actions">
                <button class="ba-btn" title="复制全文" @click="copyText(m.text)">
                  <el-icon><CopyDocument /></el-icon><span class="ba-tip">复制</span>
                </button>
              </div>
              <div v-if="m.trace?.length" class="trace">
                <span v-for="(t, j) in m.trace" :key="j" class="tool-chip">
                  <el-icon v-if="t.endsWith('✓')" class="chip-ok"><CircleCheckFilled /></el-icon>
                  <span v-else class="chip-dot"></span>
                  {{ t.replace(' …', '').replace(' ✓', '') }}
                </span>
              </div>
              <div class="md-body" v-html="render(m.text)"></div>

              <!-- 变更确认卡片（组件化：表单校验/高危二次确认内聚在 ConfirmCard） -->
              <ConfirmCard v-if="m.confirm" :confirm="m.confirm" :confirming="confirming"
                           @decide="(approved, edited) => confirmAction(m, approved, edited)" />

              <!-- 失败重试：仅最后一条助手消息且保留有用户输入时 -->
              <div v-if="m.failed && i === messages.length - 1 && lastUserText" class="retry-row">
                <el-button size="small" type="warning" plain @click="retryLast">
                  <el-icon><RefreshRight /></el-icon>&nbsp;重试上一条提问
                </el-button>
              </div>
            </div>

            <div v-else class="bubble-user">{{ m.text }}</div>
          </div>

          <div v-if="streaming" class="chat-row assistant">
            <div class="chat-avatar ai">
              <svg viewBox="0 0 40 40" fill="none" aria-hidden="true">
                <path d="M20 5.5 32.6 12.8v14.4L20 34.5 7.4 27.2V12.8L20 5.5Z" stroke="#5F79E8" stroke-width="2.6" stroke-linejoin="round" />
                <circle cx="20" cy="20" r="4" fill="#0FB9A4" />
              </svg>
            </div>
            <div class="bubble-ai"><span class="typing-dots"><i></i><i></i><i></i></span></div>
          </div>
        </div>
      </div>

      <!-- 输入台 -->
      <div class="composer-wrap">
        <div class="chat-composer" :class="{ 'is-streaming': streaming }">
          <div v-if="messages.length <= 1 && quickPrompts.length" class="quick">
            <button v-for="q in quickPrompts" :key="q" class="quick-chip" :disabled="streaming" @click="send(q)">{{ q }}</button>
          </div>
          <div class="input-row">
            <el-input v-model="input" :placeholder="inputPlaceholder" size="large" @keyup.enter="send()" :disabled="streaming" />
            <button v-if="!streaming" class="send-btn" :class="{ ready: !!input.trim() }" @click="send()" title="发送（Enter）">
              <el-icon :size="17"><Promotion /></el-icon>
            </button>
            <el-button v-else type="danger" size="large" @click="stopChat">
              <el-icon><CircleCloseFilled /></el-icon>&nbsp;终止
            </el-button>
          </div>
          <div class="composer-toolbar">
            <div class="ct-left">
              <button class="kb-toggle" :class="{ on: useKnowledge }" @click="useKnowledge = !useKnowledge"
                      title="勾选后，对话可检索深信服官方知识库（诸葛小T），回答将附带官方引用来源；对话还会沉淀到个人知识库">
                <el-icon><Search /></el-icon>查询知识库
              </button>
              <button class="ghost-act" @click="newConversation"
                      title="开启新会话：清空当前上下文，避免话题混淆与 token 浪费；设备信息与长期记忆会自动带入新会话">
                <el-icon><CirclePlus /></el-icon>新会话
              </button>
            </div>
            <div class="device-selector">
              <el-icon class="ds-ico"><Monitor /></el-icon>
              <el-select v-model="store.currentDeviceId" size="small" class="ds-select"
                         @change="onDeviceChange" placeholder="选择目标设备">
                <el-option :value="GLOBAL_DEVICE_ID" label="全局（所有设备）">
                  <span>全局（所有设备）</span>
                  <span class="ds-opt-meta">跨设备 · 批量</span>
                </el-option>
                <el-option-group label="深信服设备">
                  <el-option v-for="d in store.devices" :key="d.id" :value="d.id"
                             :label="`${d.name}（${{ af: '防火墙', scp: '云计算平台', ac: '上网行为管理' }[d.type] || d.type}）`">
                    <span>{{ d.name }}</span>
                    <span class="ds-opt-meta">
                      {{ { af: 'AF', ac: 'AC', scp: 'SCP' }[d.type] || d.type }} | 真实设备
                    </span>
                  </el-option>
                </el-option-group>
                <el-option-group v-if="netdevTargets.length" label="网络设备（华为/H3C/锐捷）">
                  <el-option v-for="d in netdevTargets" :key="d.id" :value="d.id"
                             :label="`${d.name}（网络设备·${vendorName(d.vendor)}）`">
                    <span>{{ d.name }}</span>
                    <span class="ds-opt-meta">{{ vendorName(d.vendor) }} | {{ d.host }}</span>
                  </el-option>
                </el-option-group>
              </el-select>
              <button class="icon-mini" @click="refreshDevices" title="刷新设备列表">
                <el-icon><Refresh /></el-icon>
              </button>
            </div>
          </div>
        </div>
        <div class="composer-foot">修改类操作将生成确认卡片，确认后才会下发设备 · 支持深信服设备与网络设备（华为/H3C/锐捷）批量操作 · 结果请复核</div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { store, currentDevice, loadDevices, isNetDev, isGlobal, GLOBAL_DEVICE_ID, aiNetdevs, NETDEV_VENDOR_NAMES } from '../store.js'
import { apiGet, chatStream, Devices } from '../api.js'
import ConfirmCard from '../components/chat/ConfirmCard.vue'
import { applyEvent, toolLabel } from '../chat/agentStream'

// 会话缓存（模块级）：切视图/切设备不丢；页面刷新后由后端 last-conversation 接口兜底恢复
const convCache = {}
const md = new MarkdownIt({ breaks: true })
const render = (text) => md.render(text || '')
const input = ref('')
const streaming = ref(false)
const confirming = ref(false)
// 知识库检索开关：勾选后对话可调用官方知识库工具
const useKnowledge = ref(false)

const vendorName = (v) => NETDEV_VENDOR_NAMES[v] || v
const hasAnyDevice = computed(() => !!(store.devices.length || aiNetdevs().length))
const netdevTargets = computed(() => aiNetdevs())

// 对话状态（组件级，缓存与持久化见模块级 convCache + 后端接口）
const messages = ref([])
// 当前对话ID（续接对话时使用）
let currentConvId = null
// 终止对话：AbortController
let abortController = null

const scrollRef = ref(null)
const quickPrompts = computed(() => {
  const dev = currentDevice()
  if (!dev) return []
  if (isGlobal(dev)) {
    return [
      '查看所有设备的运行状态', '把所有深信服设备都体检一遍',
      '看看网络设备的健康状态', '查一下核心交换机的 ARP 表',
      '定位终端 192.168.1.100 接在哪台交换机哪个口', '有新版本可以升级吗？'
    ]
  }
  if (isNetDev(dev)) {
    return [
      '看看设备健康状态（CPU/内存/温度）', '查看接口概览', '查看路由表',
      '查一下 ARP 表', '看看最近的日志有没有异常', '定位一下终端 192.168.1.100 接在哪台交换机哪个口'
    ]
  }
  if (dev.type === 'ac') {
    return [
      '查看设备运行状态', '体检一下设备配置有哪些风险', '看看在线用户',
      '查看上网策略', '查看用户绑定', '立即创建一次备份', '有新版本可以升级吗？'
    ]
  }
  if (dev.type === 'scp') {
    return [
      '查看平台版本和集群状态', '看看集群的计算和存储资源使用情况',
      '列出物理机及资源详情', '查看虚拟机列表', '立即创建一次备份', '有新版本可以升级吗？'
    ]
  }
  return [
    '查看设备运行状态', '体检一下设备配置有哪些风险', '看看 NAT 策略',
    '把 445 端口对公网暴露的策略停用', '立即创建一次备份', '有新版本可以升级吗？'
  ]
})

function cachePut(devId) {
  if (!devId) return
  convCache[devId] = { list: JSON.parse(JSON.stringify(messages.value)), convId: currentConvId }
}

async function loadMessages(devId) {
  if (!devId) return
  const cached = convCache[devId]
  if (cached) {
    messages.value = cached.list
    currentConvId = cached.convId
    return
  }
  messages.value = []
  currentConvId = null
  pushHello(devId)
  await restoreFromBackend(devId)
}

// 把后端消息历史映射回前端消息模型（工具调用合成轨迹芯片，tool 消息并入 trace）
function mapHistoryMessages(rawMsgs) {
  const doneCalls = new Set()
  for (const m of rawMsgs) {
    if (m.role === 'tool') doneCalls.add(m.content?.tool_call_id)
  }
  const out = []
  for (const m of rawMsgs) {
    const c = m.content || {}
    if (m.role === 'user') {
      out.push({ role: 'user', text: c.text || '' })
    } else if (m.role === 'assistant' && (c.text || c.tool_calls?.length)) {
      const trace = (c.tool_calls || []).map(tc => {
        const cn = toolLabel(tc.name)
        return doneCalls.has(tc.id) ? `${cn} ✓` : `${cn} …`
      })
      out.push({ role: 'assistant', text: c.text || '', trace, confirm: null })
    }
  }
  return out
}

// 从后端恢复该设备最近一次会话（文本+工具轨迹+待确认卡片基础形态）
async function restoreFromBackend(devId) {
  try {
    const data = await apiGet(`/api/chat/last-conversation/${devId}`)
    if (!data.conv_id || !data.messages?.length) return
    if (messages.value.length > 1) return   // 用户已在恢复期间交互，放弃覆盖
    currentConvId = data.conv_id
    const restored = mapHistoryMessages(data.messages)
    if (data.pending_action) {
      restored.push({
        role: 'assistant', text: '', trace: [],
        confirm: {
          action_id: data.pending_action.action_id,
          title: data.pending_action.summary,
          status: 'pending',
          warning: '该变更卡片由历史会话恢复：确认/拒绝可直接执行，参数与风险详情以原对话为准'
        }
      })
    }
    if (restored.length) {
      messages.value = restored
      scrollBottom()
    }
  } catch { /* 恢复失败保持欢迎语，不影响新对话 */ }
}

onMounted(async () => {
  await loadDevices()
  loadMessages(store.currentDeviceId)
  consumeChatSeed()
})
watch(() => store.chatSeed?.tick, () => consumeChatSeed())
watch(() => store.currentDeviceId, (newId, oldId) => {
  if (newId && newId !== oldId) {
    cachePut(oldId)
    loadMessages(newId)
  }
})

function onDeviceChange() {
  // 设备切换由 watch 自动触发
}

// 空状态引导：触发 App 外壳的「添加设备」弹窗
function goToDevices() {
  store.uiAddDeviceTick++
}

// 消费跨视图种子（知识库引用「去问 Agent」）：勾选知识库、预填问题并自动发送
function consumeChatSeed() {
  const seed = store.chatSeed
  if (!seed?.text) return
  store.chatSeed = null
  useKnowledge.value = seed.useKnowledge !== false
  input.value = seed.text
  nextTick(() => send())
}

function refreshDevices() {
  loadDevices()
  ElMessage.success('设备列表已刷新')
}

// 新会话：清空上下文（设备信息与长期记忆仍会自动注入，不会丢失设备基本情况）
function newConversation() {
  if (streaming.value) return ElMessage.warning('请先终止当前对话')
  const dev = currentDevice()
  if (!dev) return
  currentConvId = null
  pushHello(dev.id)
  cachePut(dev.id)
  ElMessage.success('已开启新会话，设备信息与历史记忆会自动带入')
}

const HELLO_BY_TYPE = {
  global: {
    label: '全局模式（所有设备）',
    tips: ['"查看所有设备的运行状态"', '"把所有深信服设备都体检一遍"', '"看看网络设备的健康状态"',
           '"查一下核心交换机的 ARP 表"', '"定位终端 192.168.1.100 接在哪"',
           '"把这条策略在总部-AF 和分支-AC 上都停用"'],
    note: '全局模式下我覆盖全部已添加设备：点名设备（或说"所有设备"）即可跨深信服设备与网络设备（华为/H3C/锐捷）查询与批量变更；也可以在下方选择器切换到具体设备。修改类操作会先生成确认卡片。'
  },
  af: {
    label: '下一代防火墙 AF',
    tips: ['"查看 NAT 策略"', '"体检一下配置有哪些风险"', '"把 445 端口对公网暴露的策略停用"',
           '"马上要变更了，先备份一下"', '"有新版本可以升级吗？"'],
    note: '修改类操作我会先生成变更计划卡片，确认后才会下发设备。'
  },
  ac: {
    label: '上网行为管理 AC',
    tips: ['"看看在线用户"', '"查看用户绑定"', '"体检一下配置有哪些风险"',
           '"马上要变更了，先备份一下"', '"有新版本可以升级吗？"'],
    note: 'AC 通过开放接口接入，支持状态/绑定/策略查询与绑定变更（走确认卡片）。'
  },
  scp: {
    label: '云计算平台 SCP（只读）',
    tips: ['"查看平台版本和集群状态"', '"看看集群的计算和存储资源使用情况"', '"列出物理机及资源详情"',
           '"查询内存使用率超过 80% 的虚拟机"', '"立即创建一次备份"', '"有新版本可以升级吗？"'],
    note: 'SCP 为只读接入：可查询集群/物理机/虚拟机/存储与网口端口组信息，不支持配置变更。'
  },
  netdev: {
    label: '网络设备（华为/H3C/锐捷）',
    tips: ['"看看设备健康状态"', '"查看接口概览"', '"查看路由表"', '"查一下 ARP 表"',
           '"看看最近日志有没有异常"', '"定位终端 192.168.1.100 接在哪"'],
    note: '支持配置/健康/路由/接口/ARP 查询、终端定位与日志分析；接口等配置下发会先生成确认卡片，默认不保存配置。多台设备可直接说"在 A 和 B 上都查一下"。'
  }
}

function pushHello(devId) {
  const dev = devId ? [...store.devices, ...store.netdevDevices, { id: GLOBAL_DEVICE_ID, name: '全局（所有设备）', type: 'global' }]
    .find(d => d.id === devId) : currentDevice()
  if (!dev) return
  const h = isGlobal(dev) ? HELLO_BY_TYPE.global
    : isNetDev(dev) ? HELLO_BY_TYPE.netdev
      : (HELLO_BY_TYPE[dev.type] || HELLO_BY_TYPE.af)
  const vendorText = isNetDev(dev) ? `，${vendorName(dev.vendor)} ${dev.host}` : ''
  messages.value = [{
    role: 'assistant',
    text: `您好！我是全局运维 AI 助手，当前目标：**${dev.name}**（${h.label}${vendorText}）。\n\n可以试试：\n- ${h.tips.join('\n- ')}\n\n${h.note}`,
    trace: [], confirm: null
  }]
}

const inputPlaceholder = computed(() => {
  const dev = currentDevice()
  if (!dev) return '请先选择设备'
  if (isGlobal(dev)) return '全局模式：例如"查看所有设备的运行状态"，或点名任意一台设备/交换机'
  if (isNetDev(dev)) return '例如：看看接口流量和最近日志，再把 GE1/0/1 口的终端定位出来'
  if (dev.type === 'scp') return '例如：看看集群的计算和存储资源使用情况，再列出内存使用率高的虚拟机'
  if (dev.type === 'ac') return '例如：看看在线用户，再把 192.168.1.100 做个 IP-MAC 绑定'
  return '例如：帮我看一下外网接口流量，再把 3389 对公网暴露的策略收紧'
})

const lastUserText = ref('')

async function retryLast() {
  if (streaming.value) return ElMessage.warning('当前仍有对话在进行')
  if (lastUserText.value) send(lastUserText.value)
}

async function scrollBottom() {
  await nextTick()
  if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
}

function send(preset) {
  const text = (preset || input.value || '').trim()
  if (!text) return
  const dev = currentDevice()
  if (!dev) return ElMessage.warning('请先选择设备')
  if (preset) input.value = ''
  else input.value = ''
  lastUserText.value = text
  messages.value.push({ role: 'user', text })
  streaming.value = true
  const aiMsg = reactiveMsg()
  messages.value.push(aiMsg)
  scrollBottom()

  abortController = new AbortController()
  const body = { message: text, device_id: dev.id, use_knowledge: useKnowledge.value }
  if (currentConvId) body.conv_id = currentConvId
  chatStream('/api/chat', body, ev => handleEvent(ev, aiMsg), abortController.signal)
    .catch(e => {
      if (e.name === 'AbortError') return
      aiMsg.failed = e.message
      aiMsg.text += `\n\n**连接失败**：${e.message}`
    })
    .finally(() => { streaming.value = false; abortController = null; scrollBottom() })
}

async function stopChat() {
  if (abortController) {
    // 先通知后端取消
    if (currentConvId) {
      try {
        await fetch(`/api/chat/${currentConvId}/cancel`, { method: 'POST' })
      } catch (_) { /* ignore */ }
    }
    abortController.abort()
    streaming.value = false
    // 添加取消消息
    const msg = messages.value[messages.value.length - 1]
    if (msg && msg.role === 'assistant') {
      msg.text += '\n\n*对话已终止*'
    }
  }
}

function reactiveMsg() {
  // Vue reactive：流式 token 逐段触发重渲染（自定义 Proxy 会绕过响应式导致一次性出现）
  return reactive({ role: 'assistant', text: '', trace: [], confirm: null, _currentTool: null, failed: null })
}

function handleEvent(ev, aiMsg) {
  applyEvent(aiMsg, ev, {
    onMeta: (convId) => { if (convId) currentConvId = convId },
    onConfirm: () => scrollBottom(),
  })
}

// edited 由 ConfirmCard 校验并收集（表单组件 validate + getData）
async function confirmAction(msg, approved, edited) {
  const dev = currentDevice()
  confirming.value = true
  const contMsg = reactiveMsg()
  messages.value.push(contMsg)
  chatStream('/api/chat/confirm', {
    action_id: msg.confirm.action_id, device_id: dev.id, approved, edited: approved ? edited : null
  }, ev => applyEvent(contMsg, ev, {
    onMeta: (convId) => { if (convId) currentConvId = convId },
    onConfirm: () => scrollBottom(),
  }))
    .catch(e => { contMsg.failed = e.message; contMsg.text += `\n\n**连接失败**：${e.message}` })
    .finally(() => {
      confirming.value = false
      msg.confirm.status = approved ? 'executed' : 'rejected'
      scrollBottom()
    })
}

// 复制 AI 回复全文（clipboard API 失败时回退 execCommand）
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy'); ElMessage.success('已复制到剪贴板') }
    catch { ElMessage.error('复制失败，请手动选择复制') }
    document.body.removeChild(ta)
  }
}

function fmtRule(r) {
  if (!r) return ''
  return Object.entries(r).filter(([k]) => !['comment'].includes(k))
    .map(([k, v]) => `${k}=${v === null ? '' : v}`).join('  ')
}

function allPlanItems(plan) {
  const out = []
  for (const g of ['delete', 'update', 'create']) {
    for (const it of plan[g] || []) {
      out.push(`[${{ delete: '删除', update: '修改', create: '重建' }[g]}] ${it.resource_cn} ${it.name}（${it.target_id}）`)
    }
  }
  return out
}
</script>

<style scoped>
.chat-page { display: flex; flex-direction: column; height: calc(100vh - 44px); }

/* ===== 无设备引导 ===== */
.no-device-hero {
  position: relative; flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  text-align: center; gap: 6px; padding: 40px 20px; overflow: hidden;
}
.hero-orb { position: absolute; border-radius: 50%; filter: blur(70px); opacity: .5; pointer-events: none; }
.orb-a { width: 420px; height: 420px; background: rgba(59, 99, 255, .16); top: -6%; left: 6%; animation: hero-float 14s ease-in-out infinite alternate; }
.orb-b { width: 380px; height: 380px; background: rgba(15, 185, 164, .13); bottom: -4%; right: 4%; animation: hero-float 17s ease-in-out infinite alternate-reverse; }
.hero-grid {
  position: absolute; inset: 0; pointer-events: none;
  background-image: radial-gradient(rgba(16, 24, 40, .06) 1px, transparent 1px);
  background-size: 24px 24px;
  -webkit-mask-image: radial-gradient(ellipse 70% 60% at 50% 45%, rgba(0, 0, 0, .9), transparent 75%);
  mask-image: radial-gradient(ellipse 70% 60% at 50% 45%, rgba(0, 0, 0, .9), transparent 75%);
}
@keyframes hero-float { from { transform: translate(0, 0) scale(1); } to { transform: translate(36px, 26px) scale(1.08); } }
.hero-mark { width: 84px; height: 84px; margin-bottom: 20px; filter: drop-shadow(0 16px 32px rgba(59, 99, 255, .3)); animation: sfa-fade-up .5s var(--ease-out) both; }
.hero-title { font-size: 24px; font-weight: 750; letter-spacing: -.02em; margin: 0 0 10px; animation: sfa-fade-up .5s .06s var(--ease-out) both; }
.hero-desc { color: var(--sfa-text-3); font-size: 13.5px; line-height: 1.9; margin: 0 0 24px; animation: sfa-fade-up .5s .12s var(--ease-out) both; }
.no-device-hero .el-button { animation: sfa-fade-up .5s .18s var(--ease-out) both; }

/* ===== 对话滚动区 ===== */
.chat-scroll { flex: 1; overflow-y: auto; overscroll-behavior: contain; }
.chat-col { max-width: 920px; margin: 0 auto; padding: 10px 4px 18px; }

.chat-hero { padding: 14px 2px 18px; }
.ch-title { font-size: 22px; font-weight: 750; letter-spacing: -.02em; margin: 0 0 6px; }
.ch-desc { color: var(--sfa-text-3); font-size: 13px; margin: 0; line-height: 1.7; }

.chat-avatar.ai svg { width: 100%; height: 100%; padding: 5.5px; }

/* AI 气泡悬浮操作（复制） */
.bubble-ai { position: relative; }
.bubble-actions {
  position: absolute; top: 8px; right: 8px;
  opacity: 0; transform: translateY(-2px);
  transition: opacity var(--dur-1) var(--ease-out), transform var(--dur-1) var(--ease-out);
}
.bubble-ai:hover .bubble-actions { opacity: 1; transform: none; }
.ba-btn {
  display: inline-flex; align-items: center; gap: 4px;
  border: 1px solid var(--sfa-border); background: rgba(255, 255, 255, .92);
  color: var(--sfa-text-3); font-size: 11px; font-family: var(--sfa-font);
  border-radius: 7px; padding: 3px 8px; cursor: pointer;
  backdrop-filter: blur(4px);
  transition: all var(--dur-1) var(--ease-out);
}
.ba-btn .el-icon { font-size: 12px; }
.ba-btn:hover { color: var(--sfa-primary); border-color: #B9C7FF; background: #fff; }
.ba-btn:active { transform: scale(.95); }

/* ===== 输入台 ===== */
.composer-wrap {
  position: relative; max-width: 920px; margin: 0 auto; width: 100%;
  padding-bottom: 2px;
}
.composer-wrap::before {
  content: ""; position: absolute; left: -30px; right: -30px; bottom: 100%; height: 34px;
  background: linear-gradient(180deg, transparent, var(--sfa-bg));
  pointer-events: none;
}
.chat-composer {
  background: var(--sfa-surface);
  border: 1px solid var(--sfa-border);
  border-radius: 18px;
  padding: 12px 14px 10px;
  box-shadow: 0 4px 24px -8px rgba(16, 24, 40, .12);
  transition: border-color var(--dur-2) var(--ease-out), box-shadow var(--dur-2);
}
.chat-composer:focus-within {
  border-color: #B9C7FF;
  box-shadow: 0 0 0 4px rgba(59, 99, 255, .09), 0 8px 32px -8px rgba(16, 24, 40, .16);
}

.input-row { display: flex; gap: 10px; align-items: center; }
.input-row .el-input :deep(.el-input__wrapper) { box-shadow: none; background: transparent; padding: 4px 4px; font-size: 14px; }
.input-row .el-input :deep(.el-input__wrapper.is-focus) { box-shadow: none !important; }

.send-btn {
  width: 40px; height: 40px; border-radius: 12px; border: none; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: var(--sfa-bg-deep); color: #A8B0C4; cursor: default;
  transition: all var(--dur-2) var(--ease-spring);
}
.send-btn.ready {
  background: linear-gradient(135deg, #4A70FF, var(--sfa-primary) 70%, #3355EE);
  color: #fff;
  box-shadow: 0 6px 16px -4px rgba(59, 99, 255, .5);
}
.send-btn.ready:hover { transform: translateY(-1px) scale(1.04); }
.send-btn.ready:active { transform: scale(.94); }

.quick { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 10px; }
.quick-chip {
  border: 1px solid var(--sfa-border); background: #FAFBFD; color: var(--sfa-text-2);
  font-size: 12px; font-family: var(--sfa-font);
  padding: 5px 12px; border-radius: 999px; cursor: pointer;
  transition: all var(--dur-1) var(--ease-out);
}
.quick-chip:hover:not(:disabled) { border-color: #B9C7FF; color: var(--sfa-primary); background: #F3F6FF; transform: translateY(-1px); }
.quick-chip:active:not(:disabled) { transform: scale(.97); }
.quick-chip:disabled { opacity: .5; cursor: not-allowed; }

.composer-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 9px; padding-top: 10px; border-top: 1px solid var(--sfa-border-soft); }
.ct-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.kb-toggle, .ghost-act {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; font-family: var(--sfa-font); cursor: pointer;
  padding: 4.5px 11px; border-radius: 999px;
  border: 1px solid var(--sfa-border); background: #fff; color: var(--sfa-text-3);
  transition: all var(--dur-1) var(--ease-out);
}
.kb-toggle .el-icon, .ghost-act .el-icon { font-size: 12.5px; }
.kb-toggle:hover, .ghost-act:hover { color: var(--sfa-primary); border-color: #B9C7FF; background: #F6F8FF; }
.kb-toggle.on {
  background: #EEF1FF; border-color: #B9C7FF; color: var(--sfa-primary); font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(59, 99, 255, .12);
}

.device-selector {
  display: flex; align-items: center; gap: 5px;
  padding-left: 14px; margin-left: auto;
  border-left: 1px solid var(--sfa-border-soft);
}
.ds-ico { color: var(--sfa-text-4); font-size: 14px; }
.ds-select { width: 250px; }
.ds-select :deep(.el-select__wrapper) { background: #FAFBFD; }
.ds-opt-meta { float: right; color: var(--sfa-text-4); font-size: 11.5px; font-family: var(--sfa-mono); }
.icon-mini {
  width: 26px; height: 26px; border-radius: 7px; border: none; cursor: pointer;
  background: transparent; color: var(--sfa-text-4);
  display: inline-flex; align-items: center; justify-content: center;
  transition: all var(--dur-1) var(--ease-out);
}
.icon-mini:hover { background: #EEF1FA; color: var(--sfa-primary); }

.composer-foot {
  text-align: center; font-size: 10.5px; color: #A5ADC0;
  letter-spacing: .04em; margin-top: 11px;
}

.retry-row { margin-top: 8px; }

@media (max-width: 1240px) { .chat-page { height: calc(100vh - 36px); } }
@media (max-width: 820px) {
  .chat-page { height: calc(100vh - 28px - 56px); }
  .ds-select { width: 160px; }
  .chat-col { padding: 6px 2px 14px; }
  .bubble-user, .bubble-ai { max-width: 92%; }
}
</style>
