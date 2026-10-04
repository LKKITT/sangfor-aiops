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

              <!-- 变更确认卡片 -->
              <div v-if="m.confirm" class="confirm-card">
                <div class="cf-head">
                  <el-icon class="cf-ico"><WarningFilled /></el-icon>
                  <span class="cf-title">{{ m.confirm.title }}</span>
                </div>
                <div v-if="m.confirm.warning" class="cf-warning">⚠ {{ m.confirm.warning }}</div>

                <!-- 高危操作标识 -->
                <div v-if="isHighRisk(m.confirm)" class="cf-highrisk">
                  <el-icon><WarningFilled /></el-icon> <b>高危操作</b>：{{ highRiskReason(m.confirm) }}
                </div>

                <!-- 定向冲突核实（只针对本配置） -->
                <div v-if="m.confirm.conflicts?.length" class="cf-conflicts">
                  <div class="cf-sec-label">
                    定向核实：本配置与现有配置的冲突/重叠（{{ m.confirm.conflicts.length }} 项，不含无关配置）
                  </div>
                  <div v-for="(cf, ci) in m.confirm.conflicts" :key="ci" class="cf-conflict-item">
                    <el-tag :type="cf.level === 'high' ? 'danger' : cf.level === 'medium' ? 'warning' : 'info'" size="small">
                      {{ cf.level === 'high' ? '冲突' : cf.level === 'medium' ? '重叠' : '冗余' }}
                    </el-tag>
                    <span class="cf-conflict-text">{{ cf.text }}</span>
                    <div class="cf-conflict-sug">{{ cf.suggestion }}</div>
                  </div>
                </div>
                <div v-else-if="m.confirm.conflicts && m.confirm.op !== 'delete'" class="cf-noconflict">
                  ✓ 定向核实：与现有配置无冲突、无重叠
                </div>

                <!-- AC 绑定创建：交互式表单 -->
                <div v-if="isBindingCreate(m.confirm)" class="cf-form">
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

                <!-- NAT 创建/修改：交互式表单 -->
                <div v-if="isResourceEdit(m.confirm, 'nat')" class="cf-form">
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
                <div v-if="isResourceEdit(m.confirm, 'acl')" class="cf-form">
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
                <div v-if="isResourceEdit(m.confirm, 'object')" class="cf-form">
                  <el-form label-width="100px" size="small" style="max-width: 480px">
                    <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
                    <el-form-item label="成员地址"><el-input v-model="editForm.members" placeholder="如：10.0.0.0/24, 192.168.1.5" /></el-form-item>
                    <el-form-item label="备注"><el-input v-model="editForm.comment" /></el-form-item>
                  </el-form>
                </div>

                <!-- 自定义服务 创建/修改：交互式表单 -->
                <div v-if="isResourceEdit(m.confirm, 'service')" class="cf-form">
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
                <div v-if="!isBindingCreate(m.confirm) && !isResourceEdit(m.confirm) && (m.confirm.before || m.confirm.after)"
                     class="cf-diff mono">
                  <div v-if="m.confirm.before" class="diff-removed">- {{ fmtRule(m.confirm.before) }}</div>
                  <div v-if="m.confirm.after" class="diff-added">+ {{ fmtRule(m.confirm.after) }}</div>
                </div>

                <!-- 批量变更：逐台设备计划 -->
                <div v-if="m.confirm.batch_devices?.length" class="cf-batch">
                  <div class="cf-sec-label">批量下发（{{ m.confirm.batch_devices.length }} 台设备，确认后逐台执行）</div>
                  <div v-for="(b, bi) in m.confirm.batch_devices" :key="bi" class="cf-batch-item">
                    <el-tag size="small" type="info">{{ b.device }}</el-tag>
                    <span class="cf-batch-title">{{ b.title }}</span>
                    <div v-if="b.warning" class="cf-batch-warn">{{ b.warning }}</div>
                  </div>
                </div>

                <!-- 恢复计划 -->
                <div v-if="m.confirm.plan" class="cf-plan">
                  <div class="cf-plan-line">
                    共 <b>{{ m.confirm.plan.total }}</b> 项变更：
                    <el-tag v-for="g in ['delete','update','create']" :key="g" size="small" class="cf-plan-tag"
                            :type="g === 'delete' ? 'danger' : g === 'update' ? 'warning' : 'success'">
                      {{ { delete: '删除', update: '修改', create: '重建' }[g] }} {{ m.confirm.plan[g]?.length || 0 }} 项
                    </el-tag>
                  </div>
                  <el-collapse class="cf-plan-detail">
                    <el-collapse-item title="查看详细变更清单">
                      <div v-for="(it, k) in allPlanItems(m.confirm.plan)" :key="k" class="mono cf-plan-item">
                        {{ it }}
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </div>

                <div v-if="m.confirm.status === 'pending'" class="cf-actions">
                  <el-button type="primary" size="small" :loading="confirming" @click="confirmAction(m, true)">
                    <el-icon><Check /></el-icon>&nbsp;确认执行
                  </el-button>
                  <el-button type="danger" plain size="small" :disabled="confirming" @click="confirmAction(m, false)">
                    <el-icon><Close /></el-icon>&nbsp;拒绝
                  </el-button>
                </div>
                <div v-else-if="m.confirm.status === 'executed'" class="cf-status ok">
                  <el-icon><CircleCheckFilled /></el-icon> 已执行
                </div>
                <div v-else-if="m.confirm.status === 'rejected'" class="cf-status dim">已拒绝</div>
                <div v-else class="cf-status dim">{{ m.confirm.status }}</div>
              </div>

              <!-- 高危操作二次确认弹窗 -->
              <el-dialog v-model="showSecondConfirm" title="二次确认" width="420px" :close-on-click-modal="false" append-to-body>
                <div class="second-confirm">
                  <div class="sc-ico"><el-icon :size="30"><WarningFilled /></el-icon></div>
                  <div class="sc-title">高危操作确认</div>
                  <div class="sc-reason">{{ secondConfirmReason }}</div>
                  <div class="sc-note">此操作可能影响业务，请确认已充分评估风险</div>
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

const TOOL_NAMES = {
  get_device_status: '查询设备状态', get_interfaces: '查询接口', get_nat_rules: '查询 NAT',
  get_acl_rules: '查询访问控制策略', get_user_bindings: '查询用户绑定', get_static_routes: '查询路由',
  get_network_objects: '查询网络对象', get_services: '查询自定义服务',
  run_config_checkup: '运行配置体检', create_backup: '创建备份', list_backups: '查询备份列表',
  diff_backups: '对比备份差异', get_software_updates: '获取软件更新信息', get_upgrade_advice: '生成升级建议',
  get_audit_logs: '查询审计日志', restore_backup: '生成恢复计划', execute_restore: '执行恢复',
  search_official_knowledge: '查询官方知识库', search_personal_kb: '检索本地知识库',
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
  create_whiteblacklist: '添加黑白名单', update_whiteblacklist: '修改黑白名单', delete_whiteblacklist: '删除黑白名单',
  netdev_list_devices: '查询网络设备列表', netdev_get_status: '网络设备健康查询',
  netdev_get_config: '网络设备配置查询', netdev_get_interfaces: '网络设备接口查询',
  netdev_get_routes: '网络设备路由查询', netdev_get_arp: 'ARP 表项查询',
  netdev_get_logs: '网络设备日志分析', netdev_locate_terminal: '终端定位',
  netdev_apply_config: '下发网络设备配置', netdev_run_commands: '执行网络设备命令'
}

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

/* ===== 确认卡片内部 ===== */
.cf-head { display: flex; align-items: center; gap: 7px; font-weight: 650; margin-bottom: 8px; font-size: 13.5px; }
.cf-ico { color: #D08700; font-size: 16px; }
.cf-warning { color: #B88230; margin-bottom: 8px; font-size: 12.5px; }
.cf-highrisk {
  margin-bottom: 10px; padding: 7px 11px; border-radius: 8px;
  background: #FDEEEF; border: 1px solid #FBD8D9; font-size: 12px; color: #D33A40;
  display: flex; align-items: center; gap: 4px;
}
.cf-conflicts { margin-bottom: 10px; }
.cf-sec-label { font-size: 12px; color: var(--sfa-text-3); margin-bottom: 6px; }
.cf-conflict-item {
  font-size: 12px; background: #fff; border-left: 3px solid var(--sfa-warning);
  padding: 5px 9px; margin-bottom: 5px; border-radius: 0 7px 7px 0;
}
.cf-conflict-text { margin-left: 4px; }
.cf-conflict-sug { color: var(--sfa-text-3); margin-top: 3px; padding-left: 2px; }
.cf-noconflict { font-size: 12px; color: #0E9F6E; margin-bottom: 8px; }
.cf-form { background: #fff; border-radius: 9px; padding: 12px; border: 1px solid #F3DFB2; margin-bottom: 8px; }
.cf-diff { font-size: 12px; background: #fff; border-radius: 9px; padding: 9px 11px; border: 1px solid #F3DFB2; }
.cf-batch { margin-bottom: 10px; }
.cf-batch-item {
  font-size: 12px; background: #fff; border: 1px solid var(--sfa-border-soft);
  border-radius: 8px; padding: 6px 9px; margin-bottom: 5px;
}
.cf-batch-title { margin-left: 6px; }
.cf-batch-warn { color: #B88230; margin-top: 3px; }
.cf-plan { font-size: 12.5px; }
.cf-plan-tag { margin: 2px 3px; }
.cf-plan-detail { margin-top: 6px; }
.cf-plan-item { font-size: 12px; padding: 2.5px 0; }
.cf-actions { margin-top: 12px; display: flex; gap: 8px; }
.cf-status { display: inline-flex; align-items: center; gap: 5px; margin-top: 10px; font-size: 12.5px; font-weight: 600; }
.cf-status.ok { color: #0E9F6E; }
.cf-status.dim { color: var(--sfa-text-4); }
.bind-note { font-size: 12px; color: var(--sfa-text-3); margin-left: 8px; }

/* 二次确认弹窗 */
.second-confirm { padding: 8px 0 4px; text-align: center; }
.sc-ico { color: var(--sfa-danger); margin-bottom: 10px; }
.sc-title { font-weight: 700; font-size: 15px; margin-bottom: 8px; }
.sc-reason { color: var(--sfa-text-2); font-size: 13px; margin-bottom: 14px; }
.sc-note { color: var(--sfa-danger); font-size: 12px; background: #FDEEEF; padding: 9px; border-radius: 8px; }

@media (max-width: 1240px) { .chat-page { height: calc(100vh - 36px); } }
@media (max-width: 820px) {
  .chat-page { height: calc(100vh - 28px - 56px); }
  .ds-select { width: 160px; }
  .chat-col { padding: 6px 2px 14px; }
  .bubble-user, .bubble-ai { max-width: 92%; }
}
</style>
