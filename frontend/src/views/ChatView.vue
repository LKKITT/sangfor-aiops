<template>
  <div class="chat-page">
    <div class="chat-header page-card">
      <b>AI 对话</b>
      <span class="hint">支持自然语言查询/修改配置、配置体检、备份恢复、软件升级建议。修改类操作会先生成确认卡片。</span>
    </div>

    <div class="chat-body page-card" ref="scrollRef">
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

            <!-- AC 绑定创建：交互式表单（用户名/IP/MAC/免认证/限制登录，默认永久有效） -->
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

            <!-- 规则变更 before/after -->
            <div v-if="!isBindingCreate(m.confirm) && (m.confirm.before || m.confirm.after)" class="mono" style="font-size: 12px; background:#fff; border-radius:6px; padding:8px; border:1px solid #f3d19e;">
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
        </div>

        <div v-else class="bubble-user">{{ m.text }}</div>
      </div>

      <div v-if="streaming" class="chat-row assistant">
        <div class="bubble-ai"><span class="typing">AI 正在处理<span class="dots">…</span></span></div>
      </div>
    </div>

    <div class="chat-input page-card">
      <div class="quick">
        <el-button v-for="q in quickPrompts" :key="q" size="small" round @click="send(q)" :disabled="streaming">{{ q }}</el-button>
      </div>
      <div style="display: flex; gap: 8px">
        <el-input v-model="input" placeholder="例如：帮我看一下外网接口流量，再把 3389 对公网暴露的策略收紧" @keyup.enter="send()" :disabled="streaming" />
        <el-button type="primary" @click="send()" :loading="streaming" style="width: 90px">发送</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, nextTick, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { store, currentDevice } from '../store.js'
import { chatStream } from '../api.js'

const md = new MarkdownIt({ breaks: true })
const render = (text) => md.render(text || '')
const input = ref('')
const streaming = ref(false)
const confirming = ref(false)
const messages = ref([])
const scrollRef = ref(null)
const quickPrompts = [
  '查看设备运行状态', '体检一下设备配置有哪些风险', '看看 NAT 策略',
  '把 445 端口对公网暴露的策略停用', '立即创建一次备份', '有新版本可以升级吗？'
]

const TOOL_NAMES = {
  get_device_status: '查询设备状态', get_interfaces: '查询接口', get_nat_rules: '查询 NAT',
  get_acl_rules: '查询访问控制策略', get_user_bindings: '查询用户绑定', get_static_routes: '查询路由',
  get_network_objects: '查询网络对象', get_services: '查询自定义服务',
  run_config_checkup: '运行配置体检', create_backup: '创建备份', list_backups: '查询备份列表',
  diff_backups: '对比备份差异', get_software_updates: '获取软件更新信息', get_upgrade_advice: '生成升级建议',
  get_audit_logs: '查询审计日志', restore_backup: '生成恢复计划', execute_restore: '执行恢复',
  create_nat_rule: '新建 NAT', update_nat_rule: '修改 NAT', delete_nat_rule: '删除 NAT',
  create_acl_rule: '新建策略', update_acl_rule: '修改策略', delete_acl_rule: '删除策略',
  create_user_binding: '新建绑定', update_user_binding: '修改绑定', delete_user_binding: '删除绑定',
  create_network_object: '新建网络对象', update_network_object: '修改网络对象', delete_network_object: '删除网络对象',
  create_service: '新建自定义服务', update_service: '修改自定义服务', delete_service: '删除自定义服务'
}

onMounted(() => { if (!messages.value.length) pushHello() })
watch(() => store.currentDeviceId, pushHello)

function pushHello() {
  const dev = currentDevice()
  if (!dev) return
  messages.value = [{
    role: 'assistant',
    text: `您好！我是深信服售后技术支持 Agent，当前目标设备：**${dev.name}**。\n\n可以试试：\n- "查看 NAT 策略"\n- "体检一下配置有哪些风险"\n- "把 445 端口对公网暴露的策略停用"\n- "马上要变更了，先备份一下"\n- "有新版本可以升级吗？"\n\n修改类操作我会先生成变更计划卡片，确认后才会下发设备。`,
    trace: [], confirm: null
  }]
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

  chatStream('/api/chat', { message: text, device_id: dev.id }, ev => handleEvent(ev, aiMsg))
    .catch(e => { aiMsg.text += `\n\n**连接失败**：${e.message}` })
    .finally(() => { streaming.value = false; scrollBottom() })
}

function reactiveMsg() {
  // Vue reactive：流式 token 逐段触发重渲染（自定义 Proxy 会绕过响应式导致一次性出现）
  return reactive({ role: 'assistant', text: '', trace: [], confirm: null, _currentTool: null })
}

function handleEvent(ev, aiMsg) {
  if (ev.type === 'meta') return
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

function confirmAction(msg, approved) {
  const dev = currentDevice()
  confirming.value = true
  const contMsg = reactiveMsg()
  messages.value.push(contMsg)
  let edited = null
  if (approved && isBindingCreate(msg.confirm)) {
    edited = { data: { user: bindForm.user, ip: bindForm.ip, mac: bindForm.mac,
                       noauth: bindForm.noauth, limitlogon: bindForm.limitlogon,
                       comment: bindForm.comment } }
  }
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
.chat-header .hint { color: #909399; font-size: 12px; margin-left: 10px; }
.chat-body { flex: 1; overflow-y: auto; padding: 16px; }
.chat-input { padding: 10px 14px; }
.quick { margin-bottom: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.trace { margin-bottom: 6px; }
.bind-note { font-size: 12px; color: #909399; margin-left: 8px; }
.typing { color: #909399; font-size: 13px; }
.dots { animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0.2; } }
</style>
