<template>
  <div class="chat-page">
    <div class="chat-header page-card">
      <b>AI 对话</b>
      <span class="hint" style="margin-left: 10px">支持自然语言查询/修改配置、配置体检、备份恢复、软件升级建议；修改类操作会先生成确认卡片。</span>
    </div>

    <!-- 添加设备卡片 -->
    <div v-if="showAddCard" class="page-card add-device-card">
      <div class="add-device-header">
        <b>添加设备</b>
        <el-button size="small" text @click="showAddCard = false"><el-icon><Close /></el-icon></el-button>
      </div>
      <el-form label-width="90px" size="small">
        <el-form-item label="名称"><el-input v-model="addForm.name" placeholder="如：AF-办公网防火墙" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="addForm.type">
            <el-radio value="af">下一代防火墙 AF</el-radio>
            <el-radio value="ac">上网行为管理 AC</el-radio>
            <el-radio value="scp">云计算平台 SCP</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="接入方式">
          <el-radio-group v-model="addForm.mode">
            <el-radio value="simulator">内置模拟器</el-radio>
            <el-radio value="real">真实设备</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="addForm.mode === 'real' && addForm.type === 'af'">
          <el-form-item label="设备地址"><el-input v-model="addForm.base_url" placeholder="https://192.168.1.1" /></el-form-item>
          <el-form-item label="API 账号"><el-input v-model="addForm.username" /></el-form-item>
          <el-form-item label="API 密码"><el-input v-model="addForm.password" type="password" show-password /></el-form-item>
        </template>
        <template v-if="addForm.mode === 'real' && addForm.type === 'ac'">
          <el-form-item label="设备 IP"><el-input v-model="addForm.device_ip" placeholder="192.168.1.1" /></el-form-item>
          <el-form-item label="共享密钥"><el-input v-model="addForm.password" type="password" show-password /></el-form-item>
        </template>
        <el-form-item label="只读模式"><el-switch v-model="addForm.readonly" /></el-form-item>
        <el-form-item v-if="addTestResult" label="测连接">
          <span :style="{ color: addTestResult.ok ? '#67c23a' : '#f56c6c', fontSize: '12px' }">
            {{ addTestResult.message || addTestResult.error }}
          </span>
        </el-form-item>
      </el-form>
      <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px">
        <el-button size="small" :loading="addTesting" @click="testConnection">测试连接</el-button>
        <el-button size="small" @click="showAddCard = false; addTestResult = null">取消</el-button>
        <el-button size="small" type="primary" @click="addDevice">确定添加</el-button>
      </div>
    </div>

    <!-- 无设备时的引导 -->
    <div v-if="!store.devices.length" class="page-card no-device-card">
      <el-empty description="暂无设备">
        <template #image>
          <el-icon style="font-size: 64px; color: #c0c4cc"><Monitor /></el-icon>
        </template>
        <p>请先添加一台深信服设备（AF 防火墙 / AC 上网行为管理 / SCP 云计算平台），然后即可通过自然语言进行配置管理和查询。</p>
        <el-button type="primary" @click="goToDevices">
          <el-icon><Plus /></el-icon> 添加设备
        </el-button>
      </el-empty>
    </div>

    <div v-if="store.devices.length" class="chat-body page-card" ref="scrollRef">
      <div v-for="(m, i) in messages" :key="i" class="chat-row" :class="m.role">
        <div v-if="m.role === 'assistant'" class="bubble-ai">
          <div v-if="m.trace?.length" class="trace">
            <span v-for="(t, j) in m.trace" :key="j" class="tool-chip">
              <el-icon><Cpu /></el-icon>{{ t }}
            </span>
          </div>
          <div class="md-body" v-html="render(m.text)"></div>

          <!-- 变更确认卡片 -->
          <div v-if="m.confirm" class="confirm-card">
            <div style="font-weight: 600; margin-bottom: 6px">
              <el-icon style="vertical-align: -2px"><WarningFilled /></el-icon>
              {{ m.confirm.title }}
            </div>
            <div v-if="m.confirm.warning" style="color: #b88230; margin-bottom: 6px">⚠ {{ m.confirm.warning }}</div>

            <!-- 高危操作标识 -->
            <div v-if="isHighRisk(m.confirm)" style="margin-bottom: 8px; padding: 6px 10px; background: #fef0f0; border-radius: 4px; border: 1px solid #fde2e2; font-size: 12px; color: #f56c6c">
              <el-icon><WarningFilled /></el-icon> <b>高危操作</b>：{{ highRiskReason(m.confirm) }}
            </div>

            <!-- 定向冲突核实（只针对本配置） -->
            <div v-if="m.confirm.conflicts?.length" style="margin-bottom: 8px">
              <div style="font-size: 12px; color: #909399; margin-bottom: 4px">
                定向核实：本配置与现有配置的冲突/重叠（{{ m.confirm.conflicts.length }} 项，不含无关配置）
              </div>
              <div v-for="(cf, ci) in m.confirm.conflicts" :key="ci"
                   style="font-size: 12px; background: #fff; border-left: 3px solid #e6a23c; padding: 4px 8px; margin-bottom: 4px">
                <el-tag :type="cf.level === 'high' ? 'danger' : cf.level === 'medium' ? 'warning' : 'info'" size="small">
                  {{ cf.level === 'high' ? '冲突' : cf.level === 'medium' ? '重叠' : '冗余' }}
                </el-tag>
                {{ cf.text }}
                <div style="color: #909399; margin-top: 2px">{{ cf.suggestion }}</div>
              </div>
            </div>
            <div v-else-if="m.confirm.conflicts && m.confirm.op !== 'delete'" style="font-size: 12px; color: #67c23a; margin-bottom: 6px">
              ✓ 定向核实：与现有配置无冲突、无重叠
            </div>

            <!-- AC 绑定创建：交互式表单 -->
            <div v-if="isBindingCreate(m.confirm)" style="background:#fff; border-radius:6px; padding:10px; border:1px solid #f3d19e; margin-bottom: 6px">
              <el-form label-width="92px" size="small" style="max-width: 460px">
                <el-form-item label="用户名">
                  <el-input v-model="bindForm.user" placeholder="用户名，如：张三" />
                </el-form-item>
                <el-form-item label="IP 地址">
                  <el-input v-model="bindForm.ip" placeholder="如：192.168.1.1" />
                </el-form-item>
                <el-form-item label="MAC 地址">
                  <el-input v-model="bindForm.mac" placeholder="如：11-22-33-44-55-66" />
                </el-form-item>
                <el-form-item label="免认证">
                  <el-switch v-model="bindForm.noauth" />
                  <span class="bind-note">开启后该用户流量不经认证直接放行</span>
                </el-form-item>
                <el-form-item label="限制登录">
                  <el-switch v-model="bindForm.limitlogon" />
                  <span class="bind-note">开启后限制该绑定登录</span>
                </el-form-item>
                <el-form-item label="有效期">
                  <el-tag size="small" type="success">永久有效（noauth.expire_time=0）</el-tag>
                </el-form-item>
                <el-form-item label="描述">
                  <el-input v-model="bindForm.comment" placeholder="选填" />
                </el-form-item>
              </el-form>
            </div>

            <!-- NAT/ACL 创建/修改：交互式表单 -->
            <div v-if="isResourceEdit(m.confirm, 'nat')" style="background:#fff; border-radius:6px; padding:10px; border:1px solid #f3d19e; margin-bottom: 6px">
              <el-form label-width="100px" size="small" style="max-width: 480px">
                <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
                <el-form-item label="源区域"><el-input v-model="editForm.src_zone" placeholder="trust / untrust / dmz" /></el-form-item>
                <el-form-item label="目的区域"><el-input v-model="editForm.dst_zone" placeholder="trust / untrust / dmz" /></el-form-item>
                <el-form-item label="源地址"><el-input v-model="editForm.src_addr" /></el-form-item>
                <el-form-item label="目的地址"><el-input v-model="editForm.dst_addr" /></el-form-item>
                <el-form-item label="服务"><el-input v-model="editForm.service" /></el-form-item>
                <el-form-item label="转换地址"><el-input v-model="editForm.translated_addr" /></el-form-item>
                <el-form-item label="启用"><el-switch v-model="editForm.enabled" /></el-form-item>
                <el-form-item label="备注"><el-input v-model="editForm.comment" /></el-form-item>
              </el-form>
            </div>

            <!-- ACL 创建/修改：交互式表单 -->
            <div v-if="isResourceEdit(m.confirm, 'acl')" style="background:#fff; border-radius:6px; padding:10px; border:1px solid #f3d19e; margin-bottom: 6px">
              <el-form label-width="100px" size="small" style="max-width: 480px">
                <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
                <el-form-item label="源区域"><el-input v-model="editForm.src_zone" placeholder="trust / untrust / dmz" /></el-form-item>
                <el-form-item label="目的区域"><el-input v-model="editForm.dst_zone" placeholder="trust / untrust / dmz" /></el-form-item>
                <el-form-item label="源地址"><el-input v-model="editForm.src_addr" /></el-form-item>
                <el-form-item label="目的地址"><el-input v-model="editForm.dst_addr" /></el-form-item>
                <el-form-item label="服务"><el-input v-model="editForm.service" /></el-form-item>
                <el-form-item label="动作">
                  <el-radio-group v-model="editForm.action">
                    <el-radio value="allow">允许</el-radio>
                    <el-radio value="deny">拒绝</el-radio>
                  </el-radio-group>
                </el-form-item>
                <el-form-item label="启用"><el-switch v-model="editForm.enabled" /></el-form-item>
                <el-form-item label="备注"><el-input v-model="editForm.comment" /></el-form-item>
              </el-form>
            </div>

            <!-- 网络对象 创建/修改：交互式表单 -->
            <div v-if="isResourceEdit(m.confirm, 'object')" style="background:#fff; border-radius:6px; padding:10px; border:1px solid #f3d19e; margin-bottom: 6px">
              <el-form label-width="100px" size="small" style="max-width: 480px">
                <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
                <el-form-item label="成员地址"><el-input v-model="editForm.members" placeholder="如：10.0.0.0/24, 192.168.1.5" /></el-form-item>
                <el-form-item label="备注"><el-input v-model="editForm.comment" /></el-form-item>
              </el-form>
            </div>

            <!-- 自定义服务 创建/修改：交互式表单 -->
            <div v-if="isResourceEdit(m.confirm, 'service')" style="background:#fff; border-radius:6px; padding:10px; border:1px solid #f3d19e; margin-bottom: 6px">
              <el-form label-width="100px" size="small" style="max-width: 480px">
                <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
                <el-form-item label="协议">
                  <el-radio-group v-model="editForm.protocol">
                    <el-radio value="TCP">TCP</el-radio>
                    <el-radio value="UDP">UDP</el-radio>
                    <el-radio value="TCP/UDP">TCP/UDP</el-radio>
                  </el-radio-group>
                </el-form-item>
                <el-form-item label="端口"><el-input v-model="editForm.ports" placeholder="如：80,443 或 8000-9000" /></el-form-item>
                <el-form-item label="备注"><el-input v-model="editForm.comment" /></el-form-item>
              </el-form>
            </div>

            <!-- 规则变更 before/after -->
            <div v-if="!isBindingCreate(m.confirm) && !isResourceEdit(m.confirm) && (m.confirm.before || m.confirm.after)" class="mono" style="font-size: 12px; background:#fff; border-radius:6px; padding:8px; border:1px solid #f3d19e;">
              <div v-if="m.confirm.before" class="diff-removed">- {{ fmtRule(m.confirm.before) }}</div>
              <div v-if="m.confirm.after" class="diff-added">+ {{ fmtRule(m.confirm.after) }}</div>
            </div>

            <!-- 恢复计划 -->
            <div v-if="m.confirm.plan" style="font-size: 12px">
              共 <b>{{ m.confirm.plan.total }}</b> 项变更：
              <el-tag v-for="g in ['delete','update','create']" :key="g" size="small" style="margin: 2px"
                      :type="g === 'delete' ? 'danger' : g === 'update' ? 'warning' : 'success'">
                {{ { delete: '删除', update: '修改', create: '重建' }[g] }} {{ m.confirm.plan[g]?.length || 0 }} 项
              </el-tag>
              <el-collapse style="margin-top: 6px">
                <el-collapse-item title="查看详细变更清单">
                  <div v-for="(it, k) in allPlanItems(m.confirm.plan)" :key="k" class="mono" style="font-size:12px; padding:2px 0">
                    {{ it }}
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>

            <div v-if="m.confirm.status === 'pending'" style="margin-top: 10px; display: flex; gap: 8px">
              <el-button type="primary" size="small" :loading="confirming" @click="confirmAction(m, true)">
                <el-icon><Check /></el-icon> 确认执行
              </el-button>
              <el-button type="danger" plain size="small" :disabled="confirming" @click="confirmAction(m, false)">
                <el-icon><Close /></el-icon> 拒绝
              </el-button>
            </div>
            <el-tag v-else-if="m.confirm.status === 'executed'" type="success" size="small">已执行</el-tag>
            <el-tag v-else-if="m.confirm.status === 'rejected'" type="info" size="small">已拒绝</el-tag>
            <el-tag v-else size="small">{{ m.confirm.status }}</el-tag>
          </div>

          <!-- 高危操作二次确认弹窗 -->
          <el-dialog v-model="showSecondConfirm" title="二次确认" width="420px" :close-on-click-modal="false">
            <div style="padding: 10px 0">
              <div style="font-size: 24px; text-align: center; color: #f56c6c; margin-bottom: 12px">
                <el-icon style="font-size: 48px"><WarningFilled /></el-icon>
              </div>
              <div style="text-align: center; font-weight: 600; margin-bottom: 8px">高危操作确认</div>
              <div style="color: #606266; font-size: 13px; text-align: center; margin-bottom: 16px">{{ secondConfirmReason }}</div>
              <div style="color: #f56c6c; font-size: 12px; text-align: center; background: #fef0f0; padding: 8px; border-radius: 4px">
                此操作可能影响业务，请确认已充分评估风险
              </div>
            </div>
            <template #footer>
              <el-button @click="showSecondConfirm = false; secondConfirmCallback = null">取消</el-button>
              <el-button type="danger" @click="doSecondConfirm">确认执行高危操作</el-button>
            </template>
          </el-dialog>
        </div>

        <div v-else class="bubble-user">{{ m.text }}</div>
      </div>

      <div v-if="streaming" class="chat-row assistant">
        <div class="bubble-ai"><span class="typing">AI 正在处理<span class="dots">…</span></span></div>
      </div>
    </div>

    <div v-if="store.devices.length" class="chat-input page-card">
      <div class="input-toolbar">
        <div class="toolbar-left">
          <el-checkbox v-model="useKnowledge" size="small"
                       title="勾选后，对话可检索深信服官方知识库（诸葛小T），回答将附带官方引用来源；对话还会沉淀到个人知识库">
            <span style="font-size: 13px"><el-icon style="vertical-align: -2px"><Search /></el-icon> 查询知识库</span>
          </el-checkbox>
          <el-button size="small" @click="newConversation"
                     title="开启新会话：清空当前上下文，避免话题混淆与 token 浪费；设备信息与长期记忆会自动带入新会话">
            <el-icon><CirclePlus /></el-icon> 新会话
          </el-button>
        </div>
        <div class="device-selector">
          <el-icon style="color: #909399"><Monitor /></el-icon>
          <el-select v-model="store.currentDeviceId" size="small" style="width: 250px"
                     @change="onDeviceChange" placeholder="选择目标设备">
            <el-option v-for="d in store.devices" :key="d.id" :value="d.id"
                       :label="`${d.name}（${d.type === 'af' ? '防火墙' : d.type === 'scp' ? '云计算平台' : '上网行为管理'}）`">
              <span>{{ d.name }}</span>
              <span style="float: right; color: #909399; font-size: 12px">
                {{ { af: 'AF', ac: 'AC', scp: 'SCP' }[d.type] || d.type }} | {{ d.mode === 'simulator' ? '模拟器' : '真实设备' }}
              </span>
            </el-option>
          </el-select>
          <el-button size="small" text @click="refreshDevices" title="刷新设备列表">
            <el-icon><Refresh /></el-icon>
          </el-button>
        </div>
      </div>
      <div class="quick">
        <el-button v-for="q in quickPrompts" :key="q" size="small" round @click="send(q)" :disabled="streaming">{{ q }}</el-button>
      </div>
      <div style="display: flex; gap: 8px">
        <el-input v-model="input" :placeholder="inputPlaceholder" @keyup.enter="send()" :disabled="streaming" />
        <el-button type="primary" @click="send()" :loading="streaming" style="width: 90px">发送</el-button>
        <el-button v-if="streaming" type="danger" @click="stopChat" style="margin-left: 6px">
          <el-icon><CircleCloseFilled /></el-icon> 终止
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { store, currentDevice, loadDevices } from '../store.js'
import { apiGet, chatStream, Devices } from '../api.js'

// 会话缓存（模块级）：切视图/切设备不丢；页面刷新后由后端 last-conversation 接口兜底恢复
const convCache = {}
const md = new MarkdownIt({ breaks: true })
const render = (text) => md.render(text || '')
const input = ref('')
const streaming = ref(false)
const confirming = ref(false)
// 知识库检索开关：勾选后对话可调用官方知识库工具
const useKnowledge = ref(false)

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

const TOOL_NAMES = {
  get_device_status: '查询设备状态', get_interfaces: '查询接口', get_nat_rules: '查询 NAT',
  get_acl_rules: '查询访问控制策略', get_user_bindings: '查询用户绑定', get_static_routes: '查询路由',
  get_network_objects: '查询网络对象', get_services: '查询自定义服务',
  run_config_checkup: '运行配置体检', create_backup: '创建备份', list_backups: '查询备份列表',
  diff_backups: '对比备份差异', get_software_updates: '获取软件更新信息', get_upgrade_advice: '生成升级建议',
  get_audit_logs: '查询审计日志', restore_backup: '生成恢复计划', execute_restore: '执行恢复',
  search_official_knowledge: '查询官方知识库',
  get_scp_clusters: '查询 SCP 集群', get_scp_hosts: '查询 SCP 物理机',
  get_scp_host_interfaces: '查询物理机网口', get_scp_vms: '查询 SCP 虚拟机',
  get_scp_vm_detail: '查询虚拟机详情', get_scp_storages: '查询 SCP 存储',
  record_to_kb: '沉淀对话到知识库', ingest_url_to_kb: '沉淀链接到知识库',
  list_available_devices: '查询设备列表', add_device: '添加设备',
  create_nat_rule: '新建 NAT', update_nat_rule: '修改 NAT', delete_nat_rule: '删除 NAT',
  create_acl_rule: '新建策略', update_acl_rule: '修改策略', delete_acl_rule: '删除策略',
  create_user_binding: '新建绑定', update_user_binding: '修改绑定', delete_user_binding: '删除绑定',
  create_network_object: '新建网络对象', update_network_object: '修改网络对象', delete_network_object: '删除网络对象',
  create_service: '新建自定义服务', update_service: '修改自定义服务', delete_service: '删除自定义服务',
  get_whiteblacklist: '查询黑白名单',
  create_whiteblacklist: '添加黑白名单', update_whiteblacklist: '修改黑白名单', delete_whiteblacklist: '删除黑白名单'
}

// 添加设备相关
const showAddCard = ref(false)
const addForm = ref({ name: '', type: 'af', mode: 'real', base_url: '', device_ip: '', username: '', password: '', readonly: false })
const addTesting = ref(false)
const addTestResult = ref(null)

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
        const cn = TOOL_NAMES[tc.name] || tc.name
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
})
watch(() => store.currentDeviceId, (newId, oldId) => {
  if (newId && newId !== oldId) {
    cachePut(oldId)
    loadMessages(newId)
  }
})

function onDeviceChange() {
  // 设备切换由 watch 自动触发
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
  }
}

function pushHello(devId) {
  const dev = devId ? store.devices.find(d => d.id === devId) : currentDevice()
  if (!dev) return
  const h = HELLO_BY_TYPE[dev.type] || HELLO_BY_TYPE.af
  messages.value = [{
    role: 'assistant',
    text: `您好！我是深信服售后技术支持 Agent，当前目标设备：**${dev.name}**（${h.label}）。\n\n可以试试：\n- ${h.tips.join('\n- ')}\n\n${h.note}`,
    trace: [], confirm: null
  }]
}

const inputPlaceholder = computed(() => {
  const dev = currentDevice()
  if (!dev) return '请先选择设备'
  if (dev.type === 'scp') return '例如：看看集群的计算和存储资源使用情况，再列出内存使用率高的虚拟机'
  if (dev.type === 'ac') return '例如：看看在线用户，再把 192.168.1.100 做个 IP-MAC 绑定'
  return '例如：帮我看一下外网接口流量，再把 3389 对公网暴露的策略收紧'
})

async function testConnection() {
  if (addForm.value.mode === 'simulator') {
    addTestResult.value = { ok: true, message: '模拟器设备，无需测试连接' }
    return
  }
  // AC 设备：将 IP 转为 http://{ip}:9999
  const payload = { ...addForm.value }
  if (payload.type === 'ac' && payload.device_ip) {
    payload.base_url = `http://${payload.device_ip}:9999`
  }
  if (!payload.base_url) return ElMessage.warning('请填写设备地址')
  addTesting.value = true
  addTestResult.value = null
  try {
    addTestResult.value = await Devices.testConnection(payload)
  } catch (e) {
    addTestResult.value = { ok: false, error: String(e.message || e) }
  } finally {
    addTesting.value = false
  }
}

async function addDevice() {
  if (!addForm.value.name) return ElMessage.warning('设备名称不能为空')
  // AC 设备：将 IP 转为 http://{ip}:9999
  const payload = { ...addForm.value }
  if (payload.type === 'ac' && payload.device_ip) {
    payload.base_url = `http://${payload.device_ip}:9999`
  }
  if (payload.mode === 'real' && !payload.base_url) return ElMessage.warning('请填写设备地址')
  try {
    await Devices.add(payload)
    await loadDevices()
    showAddCard.value = false
    addTestResult.value = null
    addForm.value = { name: '', type: 'af', mode: 'real', base_url: '', device_ip: '', username: '', password: '', readonly: false }
    ElMessage.success('设备添加成功')
  } catch (e) {
    ElMessage.error(String(e.message || e))
  }
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
  return reactive({ role: 'assistant', text: '', trace: [], confirm: null, _currentTool: null })
}

function handleEvent(ev, aiMsg) {
  if (ev.type === 'meta') {
    currentConvId = ev.conv_id || currentConvId
    return
  }
  if (ev.type === 'token') { aiMsg.text += ev.text; return }
  if (ev.type === 'tool_call') {
    aiMsg._currentTool = TOOL_NAMES[ev.name] || ev.name
    aiMsg.trace.push(`${TOOL_NAMES[ev.name] || ev.name} …`)
    return
  }
  if (ev.type === 'tool_result') {
    if (aiMsg._currentTool && aiMsg.trace.length) {
      aiMsg.trace[aiMsg.trace.length - 1] = `${aiMsg._currentTool} ✓`
    } else {
      aiMsg.trace.push(`${TOOL_NAMES[ev.name] || ev.name} ✓`)
    }
    aiMsg._currentTool = null
    return
  }
  if (ev.type === 'confirm_required') {
    aiMsg.confirm = { ...ev.action, status: 'pending' }
    if (isBindingCreate(ev.action)) syncBindForm(ev.action)
    if (isResourceEdit(ev.action, 'nat') || isResourceEdit(ev.action, 'acl') ||
        isResourceEdit(ev.action, 'object') || isResourceEdit(ev.action, 'service')) {
      syncEditForm(ev.action)
    }
    scrollBottom()
    return
  }
  if (ev.type === 'offline_notice') {
    aiMsg.text += `\n\n> ${ev.text}`
    return
  }
  if (ev.type === 'error') {
    aiMsg.text += `\n\n**出错了**：${ev.text}`
    return
  }
  if (ev.type === 'done') return
}

const bindForm = reactive({ user: '', ip: '', mac: '', noauth: false, limitlogon: false, comment: '' })

// 资源编辑表单（NAT / ACL / 网络对象 / 自定义服务）
const editForm = reactive({
  name: '', src_zone: '', dst_zone: '', src_addr: '', dst_addr: '',
  service: '', translated_addr: '', enabled: true, action: 'allow',
  protocol: 'TCP', ports: '', members: '', comment: ''
})

// 高危操作二次确认
const showSecondConfirm = ref(false)
const secondConfirmReason = ref('')
let secondConfirmCallback = null

function isBindingCreate(confirm) {
  return confirm?.op === 'create' && (confirm?.resource === 'binding' || confirm?.tool_name === 'create_user_binding')
}

function syncBindForm(confirm) {
  const a = confirm?.after || {}
  bindForm.user = a.user || ''
  bindForm.ip = a.ip || ''
  bindForm.mac = a.mac || ''
  bindForm.noauth = !!a.noauth
  bindForm.limitlogon = !!a.limitlogon
  bindForm.comment = a.comment || a.desc || ''
}

function isHighRisk(confirm) {
  if (!confirm) return false
  // 删除操作
  if (confirm.op === 'delete') return true
  // 高危端口暴露
  const service = (confirm.after?.service || confirm.data?.service || '').toLowerCase()
  const highRiskPorts = ['445', '139', '135', '3389', '22', '23', '2049', '6379', '27017', '3306', '1433']
  if (highRiskPorts.some(p => service.includes(p))) return true
  // 开放 any 到 untrust
  if (confirm.after?.action === 'allow' && (confirm.after?.dst_zone === 'untrust' || confirm.data?.dst_zone === 'untrust')) {
    const src = confirm.after?.src_addr || ''
    if (src === 'any' || src === '0.0.0.0/0') return true
  }
  // 恢复操作
  if (confirm.plan) return true
  return false
}

function highRiskReason(confirm) {
  if (!confirm) return ''
  if (confirm.op === 'delete') return `将要删除 ${confirm.resource || '配置'}，删除后相关业务将受影响`
  const service = (confirm.after?.service || confirm.data?.service || '').toLowerCase()
  const highRiskPorts = ['445', '139', '135', '3389', '22', '23', '2049', '6379', '27017', '3306', '1433']
  const matched = highRiskPorts.filter(p => service.includes(p))
  if (matched.length) return `操作涉及高危端口 ${matched.join(', ')}，可能被利用进行远程攻击`
  if (confirm.after?.action === 'allow' && (confirm.after?.dst_zone === 'untrust' || confirm.data?.dst_zone === 'untrust')) {
    return '该策略允许任意地址访问公网，可能造成数据泄露或资源滥用'
  }
  if (confirm.plan) return '恢复操作将覆盖当前配置，请确认备份文件正确'
  return '该操作涉及业务配置变更，请确认风险'
}

function isResourceEdit(confirm, type) {
  if (!confirm) return false
  const resourceMap = { nat: 'nat', acl: 'acl', object: 'object', service: 'service' }
  const expected = resourceMap[type]
  if (!expected) return false
  const op = confirm.op || ''
  if (op !== 'create' && op !== 'update') return false
  const resource = (confirm.resource || confirm.tool_name || '').toLowerCase()
  return resource.includes(expected)
}

function syncEditForm(confirm) {
  const a = confirm?.after || confirm?.data || {}
  editForm.name = a.name || ''
  editForm.src_zone = a.src_zone || ''
  editForm.dst_zone = a.dst_zone || ''
  editForm.src_addr = a.src_addr || ''
  editForm.dst_addr = a.dst_addr || ''
  editForm.service = a.service || ''
  editForm.translated_addr = a.translated_addr || ''
  editForm.enabled = a.enabled !== false
  editForm.action = a.action || 'allow'
  editForm.protocol = a.protocol || 'TCP'
  editForm.ports = a.ports || ''
  editForm.members = a.members || ''
  editForm.comment = a.comment || a.desc || ''
}

function doSecondConfirm() {
  showSecondConfirm.value = false
  if (secondConfirmCallback) {
    secondConfirmCallback()
    secondConfirmCallback = null
  }
}

function buildEditedData(confirm) {
  if (isBindingCreate(confirm)) {
    return { data: { user: bindForm.user, ip: bindForm.ip, mac: bindForm.mac,
                     noauth: bindForm.noauth, limitlogon: bindForm.limitlogon,
                     comment: bindForm.comment } }
  }
  if (isResourceEdit(confirm, 'nat') || isResourceEdit(confirm, 'acl')) {
    return { data: { name: editForm.name, src_zone: editForm.src_zone, dst_zone: editForm.dst_zone,
                     src_addr: editForm.src_addr, dst_addr: editForm.dst_addr,
                     service: editForm.service, translated_addr: editForm.translated_addr,
                     enabled: editForm.enabled, action: editForm.action,
                     comment: editForm.comment } }
  }
  if (isResourceEdit(confirm, 'object')) {
    return { data: { name: editForm.name, members: editForm.members, comment: editForm.comment } }
  }
  if (isResourceEdit(confirm, 'service')) {
    return { data: { name: editForm.name, protocol: editForm.protocol, ports: editForm.ports,
                     comment: editForm.comment } }
  }
  return null
}

function confirmAction(msg, approved) {
  const dev = currentDevice()
  if (approved && isHighRisk(msg.confirm)) {
    // 高危操作：先弹出二次确认
    secondConfirmReason.value = highRiskReason(msg.confirm)
    secondConfirmCallback = () => doConfirmAction(msg, true)
    showSecondConfirm.value = true
    return
  }
  doConfirmAction(msg, approved)
}

async function doConfirmAction(msg, approved) {
  const dev = currentDevice()
  confirming.value = true
  const contMsg = reactiveMsg()
  messages.value.push(contMsg)
  const edited = approved ? buildEditedData(msg.confirm) : null
  chatStream('/api/chat/confirm', {
    action_id: msg.confirm.action_id, device_id: dev.id, approved, edited
  }, ev => handleEvent(ev, contMsg))
    .catch(e => { contMsg.text += `\n\n**连接失败**：${e.message}` })
    .finally(() => {
      confirming.value = false
      msg.confirm.status = approved ? 'executed' : 'rejected'
      scrollBottom()
    })
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
.chat-page { display: flex; flex-direction: column; height: calc(100vh - 24px); gap: 10px; }
.chat-header { padding: 12px 16px; }
.chat-header-top { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.chat-header .hint { color: #909399; font-size: 12px; margin-top: 8px; display: block; }
.device-selector { display: flex; align-items: center; gap: 4px; }
.no-device-card { padding: 40px 20px; text-align: center; }
.no-device-card p { color: #909399; font-size: 13px; margin: 12px 0 16px; }
.chat-body { flex: 1; overflow-y: auto; padding: 16px; }
.chat-input { padding: 10px 14px; }
.input-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; padding: 6px 10px; background: #f5f7fa; border-radius: 8px; }
.device-selector { display: flex; align-items: center; gap: 4px; }
.toolbar-left { display: flex; align-items: center; gap: 12px; }
.quick { margin-bottom: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.trace { margin-bottom: 6px; }
.bind-note { font-size: 12px; color: #909399; margin-left: 8px; }
.typing { color: #909399; font-size: 13px; }
.dots { animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0.2; } }
.add-device-card { padding: 16px; margin-bottom: 10px; border: 1px solid #ebeef5; }
.add-device-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
</style>
