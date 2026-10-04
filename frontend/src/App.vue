<template>
  <div class="app-shell" :class="{ 'side-open': mobileOpen }">
    <div class="side-scrim" @click="mobileOpen = false"></div>

    <aside class="side">
      <header class="brand">
        <svg class="brand-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true">
          <defs>
            <linearGradient id="sfaBrandGrad" x1="6" y1="4" x2="34" y2="36" gradientUnits="userSpaceOnUse">
              <stop offset="0" stop-color="#4A70FF" />
              <stop offset="1" stop-color="#0FB9A4" />
            </linearGradient>
          </defs>
          <path d="M20 3.5 34.3 11.8v16.4L20 36.5 5.7 28.2V11.8L20 3.5Z"
                stroke="url(#sfaBrandGrad)" stroke-width="2.5" stroke-linejoin="round" />
          <path d="M20 13.2v3.4M23 21.5l3.4 2M17 21.5l-3.4 2" stroke="#4E5B7E" stroke-width="1.4" stroke-linecap="round" />
          <circle cx="20" cy="20" r="3.4" fill="url(#sfaBrandGrad)" />
          <circle cx="20" cy="10.6" r="2" fill="#4A70FF" />
          <circle cx="28.6" cy="25" r="2" fill="#0FB9A4" />
          <circle cx="11.4" cy="25" r="2" fill="#4A70FF" />
        </svg>
        <div class="brand-text">
          <div class="brand-name">SFA&nbsp;Agent</div>
          <div class="brand-tag">深信服售后智能体</div>
        </div>
      </header>

      <div class="device-panel">
        <div class="device-panel-head">
          <span class="device-label">目标设备</span>
          <span class="device-ops" v-if="device && !isNetDevDevice && !isGlobalDevice">
            <button class="icon-ghost" title="编辑设备连接信息" aria-label="编辑设备连接信息" @click="openEditDevice"><el-icon><Edit /></el-icon></button>
            <button class="icon-ghost is-danger" title="删除设备" aria-label="删除设备" @click="confirmDeleteDevice"><el-icon><Delete /></el-icon></button>
          </span>
          <span class="device-ops" v-else-if="isNetDevDevice">
            <span class="nd-hint" title="网络设备请在「网络设备管理」页维护">网络设备</span>
          </span>
        </div>
        <el-select v-model="store.currentDeviceId" placeholder="选择设备" class="device-select" popper-class="sfa-device-popper">
          <el-option :value="GLOBAL_DEVICE_ID" label="全局（所有设备）">
            <span>全局（所有设备）</span>
            <span class="dev-opt-meta">全部深信服 + 网络设备</span>
          </el-option>
          <el-option-group label="深信服设备">
            <el-option v-for="d in store.devices" :key="d.id" :value="d.id"
                       :label="`${d.name}（${deviceTypeName(d)}）`">
              <span>{{ d.name }}</span>
              <span class="dev-opt-meta">{{ deviceTypeName(d) }} · 真实设备</span>
            </el-option>
          </el-option-group>
          <el-option-group v-if="aiNetdevs().length" label="网络设备（华为/H3C/锐捷）">
            <el-option v-for="d in aiNetdevs()" :key="d.id" :value="d.id"
                       :label="`${d.name}（网络设备·${vendorName(d.vendor)}）`">
              <span>{{ d.name }}</span>
              <span class="dev-opt-meta">{{ vendorName(d.vendor) }} · {{ d.host }}</span>
            </el-option>
          </el-option-group>
        </el-select>
        <div class="device-tags">
          <span v-if="device && !isNetDevDevice && device.readonly" class="mini-chip warn"><i class="mc-dot"></i>只读</span>
          <span class="mini-chip" :class="llmChip.cls"><i class="mc-dot"></i>{{ llmChip.text }}</span>
          <span v-if="store.health.readonly_mode" class="mini-chip danger"><i class="mc-dot"></i>全局只读</span>
        </div>
      </div>

      <nav class="nav">
        <template v-for="group in navGroups" :key="group.label">
          <div class="nav-group-label">{{ group.label }}</div>
          <button v-for="item in group.items" :key="item.key" class="nav-item"
                  :class="{ active: store.view === item.key }" :title="item.label"
                  @click="store.view = item.key; mobileOpen = false">
            <span class="nav-bar"></span>
            <el-icon class="nav-ico"><component :is="item.icon" /></el-icon>
            <span class="nav-label">{{ item.label }}</span>
          </button>
        </template>
      </nav>

      <footer class="side-foot">
        <div class="health-line" :title="store.health.llm_configured ? '大模型已接入' : '未配置 LLM，使用离线兜底模式'">
          <span class="pulse-dot" :class="store.health.llm_configured ? 'ok' : 'warn'"></span>
          <span class="health-text">{{ store.health.llm_configured ? '智能体在线' : '离线兜底模式' }}</span>
        </div>
        <div class="foot-btns">
          <button class="foot-btn" @click="showAdd = true"><el-icon><Plus /></el-icon><span>添加设备</span></button>
        </div>
      </footer>
    </aside>

    <div class="workspace">
      <div class="mobile-topbar">
        <button class="hamburger" @click="mobileOpen = true" aria-label="打开菜单">
          <span></span><span></span><span></span>
        </button>
        <span class="mt-title">深信服售后 Agent</span>
        <span class="mt-dot"></span>
      </div>

      <main class="main">
        <transition name="view">
          <component :is="views[store.view]" :key="store.view" />
        </transition>
      </main>
    </div>

    <el-dialog v-model="showAdd" title="添加设备" width="480px" append-to-body>
      <el-form label-width="90px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如：总部-AF-01" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="form.type">
            <el-radio value="af">下一代防火墙 AF</el-radio>
            <el-radio value="ac">上网行为管理 AC</el-radio>
            <el-radio value="scp">云计算平台 SCP</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="form.type === 'af'">
          <el-form-item label="设备地址"><el-input v-model="form.base_url" placeholder="https://192.168.1.1" /></el-form-item>
          <el-form-item label="API 账号"><el-input v-model="form.username" /></el-form-item>
          <el-form-item label="API 密码"><el-input v-model="form.password" type="password" show-password /></el-form-item>
        </template>
        <template v-if="form.type === 'scp'">
          <el-form-item label="平台地址">
            <el-input v-model="form.device_ip" placeholder="SCP 平台 IP，如 10.134.85.140（可带端口 10.1.1.1:4430）" />
          </el-form-item>
          <el-form-item label="AccessKey"><el-input v-model="form.username" placeholder="SCP 平台申请的 AccessKey" /></el-form-item>
          <el-form-item label="SecretKey"><el-input v-model="form.password" type="password" show-password placeholder="SCP 平台申请的 SecretKey" /></el-form-item>
          <el-form-item label=" ">
            <span style="font-size: 12px; color: #909399">
              SCP 走 OpenAPI（EC2 AK/SK 签名）只读接入：查询集群/物理机/虚拟机/存储，不支持任何变更
            </span>
          </el-form-item>
        </template>
        <template v-if="form.type === 'ac'">
          <el-form-item label="设备 IP">
            <el-input v-model="form.device_ip" placeholder="192.168.1.1" />
          </el-form-item>
          <el-form-item label="共享密钥">
            <el-input v-model="form.password" type="password" show-password
                      placeholder="开放接口共享密钥（无需账号密码）" />
          </el-form-item>
          <el-form-item label=" ">
            <span style="font-size: 12px; color: #909399">
              AC 走开放接口（md5 共享密钥签名），需在设备上启用开放接口并将本机 IP 加入允许列表
            </span>
          </el-form-item>
        </template>
        <el-form-item label="只读模式"><el-switch v-model="form.readonly" /></el-form-item>
        <el-form-item v-if="testResult" label="测连接">
          <span :style="{ color: testResult.ok ? '#0E9F6E' : '#E5484D', fontSize: '12px' }">
            {{ testResult.message || testResult.error }}
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="testing" @click="testConnection">测试连接</el-button>
        <el-button @click="showAdd = false; testResult = null">取消</el-button>
        <el-button type="primary" @click="addDevice">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showEdit" title="编辑设备连接信息" width="480px" append-to-body>
      <el-form label-width="90px">
        <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="editForm.type">
            <el-radio value="af">下一代防火墙 AF</el-radio>
            <el-radio value="ac">上网行为管理 AC</el-radio>
            <el-radio value="scp">云计算平台 SCP</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="editForm.type === 'scp'">
          <el-form-item label="平台地址">
            <el-input v-model="editForm.device_ip" placeholder="SCP 平台 IP（可带端口）" />
          </el-form-item>
          <el-form-item label="AccessKey"><el-input v-model="editForm.username" /></el-form-item>
          <el-form-item label="SecretKey"><el-input v-model="editForm.password" type="password" show-password placeholder="留空不修改" /></el-form-item>
        </template>
        <template v-if="editForm.type === 'af'">
          <el-form-item label="设备地址"><el-input v-model="editForm.base_url" placeholder="https://192.168.1.1" /></el-form-item>
          <el-form-item label="API 账号"><el-input v-model="editForm.username" /></el-form-item>
          <el-form-item label="API 密码"><el-input v-model="editForm.password" type="password" show-password placeholder="留空不修改" /></el-form-item>
        </template>
        <template v-if="editForm.type === 'ac'">
          <el-form-item label="设备 IP">
            <el-input v-model="editForm.device_ip" placeholder="192.168.1.1" />
          </el-form-item>
          <el-form-item label="共享密钥">
            <el-input v-model="editForm.password" type="password" show-password placeholder="留空不修改" />
          </el-form-item>
        </template>
        <el-form-item label="只读模式"><el-switch v-model="editForm.readonly" /></el-form-item>
        <el-form-item v-if="editTestResult" label="测连接">
          <span :style="{ color: editTestResult.ok ? '#0E9F6E' : '#E5484D', fontSize: '12px' }">
            {{ editTestResult.message || editTestResult.error }}
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="editTesting" @click="testEditConnection">测试连接</el-button>
        <el-button @click="showEdit = false; editTestResult = null">取消</el-button>
        <el-button type="primary" @click="saveEditDevice">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, markRaw, watch, defineAsyncComponent } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { store, loadDevices, loadHealth, currentDevice, isNetDev, isGlobal, GLOBAL_DEVICE_ID, aiNetdevs, NETDEV_VENDOR_NAMES } from './store.js'
import { Devices } from './api.js'

// 视图按需加载：异步组件让 Vite 自动按视图分片，首屏只携带当前视图，其余首次切换时拉取
const ChatView = defineAsyncComponent(() => import('./views/ChatView.vue'))
const ConfigView = defineAsyncComponent(() => import('./views/ConfigView.vue'))
const BackupView = defineAsyncComponent(() => import('./views/BackupView.vue'))
const CheckupView = defineAsyncComponent(() => import('./views/CheckupView.vue'))
const UpdatesView = defineAsyncComponent(() => import('./views/UpdatesView.vue'))
const KnowledgeView = defineAsyncComponent(() => import('./views/KnowledgeView.vue'))
const ChatLogView = defineAsyncComponent(() => import('./views/ChatLogView.vue'))
const NetDevView = defineAsyncComponent(() => import('./views/NetDevView.vue'))
const SettingsView = defineAsyncComponent(() => import('./views/SettingsView.vue'))

const views = {
  chat: markRaw(ChatView),
  config: markRaw(ConfigView),
  backup: markRaw(BackupView),
  checkup: markRaw(CheckupView),
  updates: markRaw(UpdatesView),
  knowledge: markRaw(KnowledgeView),
  netdev: markRaw(NetDevView),
  chatlog: markRaw(ChatLogView),
  settings: markRaw(SettingsView)
}

// 侧栏导航分组（图标为全局注册的 Element 图标组件名）
const navGroups = [
  { label: '工作台', items: [
    { key: 'chat', label: 'AI 对话', icon: 'ChatDotRound' },
    { key: 'config', label: '配置可视化', icon: 'SetUp' },
    { key: 'checkup', label: '配置体检', icon: 'Odometer' },
    { key: 'backup', label: '备份与恢复', icon: 'CopyDocument' },
  ]},
  { label: '资产运营', items: [
    { key: 'netdev', label: '网络设备管理', icon: 'Cpu' },
    { key: 'updates', label: '软件更新建议', icon: 'Download' },
  ]},
  { label: '知识沉淀', items: [
    { key: 'knowledge', label: '个人知识库', icon: 'Collection' },
    { key: 'chatlog', label: '对话日志', icon: 'Notebook' },
  ]},
  { label: '系统', items: [
    { key: 'settings', label: '平台设置', icon: 'Setting' },
  ]},
]

const mobileOpen = ref(false)
const showAdd = ref(false)
const form = ref({ name: '', type: 'af', mode: 'real', base_url: '', device_ip: '', username: '', password: '', readonly: false })
const device = computed(currentDevice)
const isNetDevDevice = computed(() => isNetDev(device.value))
const isGlobalDevice = computed(() => isGlobal(device.value))
const vendorName = (v) => NETDEV_VENDOR_NAMES[v] || v
// LLM 徽章：后端返回 false（未配置）/ true（已接入无模型名）/ 模型名字符串
const llmChip = computed(() => {
  const v = store.health.llm_configured
  if (!v) return { text: 'LLM 未配置', cls: '' }
  if (v === true) return { text: 'LLM 已接入', cls: 'ok' }
  return { text: `LLM ${v}`, cls: 'ok' }
})
// 设备类型中文名（与 ChatView 输入台的设备选择器保持同一文案口径）
const deviceTypeName = (d) => {
  if (isGlobal(d)) return '全局模式'
  if (isNetDev(d)) return `网络设备·${vendorName(d.vendor)}`
  return d.type === 'af' ? '防火墙' : d.type === 'scp' ? '云计算平台' : '上网行为管理'
}
const testing = ref(false)
const testResult = ref(null)

// 编辑设备
const showEdit = ref(false)
const editForm = ref({ id: '', name: '', type: 'af', mode: 'real', base_url: '', device_ip: '', username: '', password: '', readonly: false })
const editTesting = ref(false)
const editTestResult = ref(null)

onMounted(async () => {
  await Promise.all([loadDevices(), loadHealth()])
})

// 其他视图（如空状态引导）请求打开「添加设备」弹窗
watch(() => store.uiAddDeviceTick, v => { if (v > 0) showAdd.value = true })

async function addDevice() {
  if (!form.value.name) return ElMessage.warning('请填写设备名称')
  // AC 设备：将 IP 转为 http://{ip}:9999
  const payload = { ...form.value }
  if (payload.type === 'ac' && payload.device_ip) {
    payload.base_url = `http://${payload.device_ip}:9999`
  }
  if (payload.type === 'scp' && payload.device_ip) {
    payload.base_url = `https://${payload.device_ip}`
  }
  if (payload.mode === 'real' && !payload.base_url) return ElMessage.warning('请填写设备地址')
  try {
    await Devices.add(payload)
    await loadDevices()
    showAdd.value = false
    testResult.value = null
    form.value = { name: '', type: 'af', mode: 'real', base_url: '', device_ip: '', username: '', password: '', readonly: false }
    ElMessage.success('设备已添加')
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

async function testConnection() {
  if (!form.value.name) return ElMessage.warning('请先填写设备名称')
  // AC 设备：将 IP 转为 http://{ip}:9999
  const payload = { ...form.value }
  if (payload.type === 'ac' && payload.device_ip) {
    payload.base_url = `http://${payload.device_ip}:9999`
  }
  if (payload.type === 'scp' && payload.device_ip) {
    payload.base_url = `https://${payload.device_ip}`
  }
  if (payload.mode === 'real' && !payload.base_url) return ElMessage.warning('请填写设备地址')
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await Devices.testConnection(payload)
  } catch (e) {
    testResult.value = { ok: false, error: String(e.message || e) }
  } finally {
    testing.value = false
  }
}

function openEditDevice() {
  const d = currentDevice()
  if (!d) return
  // 对 AC/SCP 设备，从 base_url 中提取 IP
  const editIp = (d.type === 'ac' || d.type === 'scp') && d.base_url
    ? d.base_url.replace(/^https?:\/\//, '').replace(/:9999$/, '') : ''
  editForm.value = {
    id: d.id, name: d.name, type: d.type, mode: d.mode,
    base_url: d.base_url, device_ip: editIp, username: d.username || '',
    password: '', readonly: !!d.readonly
  }
  editTestResult.value = null
  showEdit.value = true
}

async function testEditConnection() {
  // AC/SCP 设备：将 IP 转为对应 base_url
  const payload = { ...editForm.value }
  if (payload.type === 'ac' && payload.device_ip) {
    payload.base_url = `http://${payload.device_ip}:9999`
  }
  if (payload.type === 'scp' && payload.device_ip) {
    payload.base_url = `https://${payload.device_ip}`
  }
  if (payload.mode === 'real' && !payload.base_url) return ElMessage.warning('请填写设备地址')
  editTesting.value = true
  editTestResult.value = null
  try {
    editTestResult.value = await Devices.testConnection(payload)
  } catch (e) {
    editTestResult.value = { ok: false, error: String(e.message || e) }
  } finally {
    editTesting.value = false
  }
}

async function saveEditDevice() {
  if (!editForm.value.name) return ElMessage.warning('设备名称不能为空')
  try {
    const payload = { name: editForm.value.name, type: editForm.value.type, mode: editForm.value.mode, readonly: editForm.value.readonly }
    // AC/SCP 设备：将 IP 转为对应 base_url
    if (payload.type === 'ac' && editForm.value.device_ip) {
      payload.base_url = `http://${editForm.value.device_ip}:9999`
    } else if (payload.type === 'scp' && editForm.value.device_ip) {
      payload.base_url = `https://${editForm.value.device_ip}`
    } else if (editForm.value.base_url) {
      payload.base_url = editForm.value.base_url
    }
    if (editForm.value.username) payload.username = editForm.value.username
    if (editForm.value.password) payload.password = editForm.value.password
    await Devices.patch(editForm.value.id, payload)
    await loadDevices()
    showEdit.value = false
    editTestResult.value = null
    ElMessage.success('设备信息已更新')
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

async function confirmDeleteDevice() {
  const d = currentDevice()
  if (!d) return
  try {
    await ElMessageBox.confirm(
      `确定要删除设备「${d.name}」吗？\n此操作不可恢复，关联的备份数据也将被删除。`,
      '删除设备',
      { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' }
    )
    await Devices.remove(d.id)
    await loadDevices()
    if (store.currentDeviceId === d.id) {
      const remaining = store.devices.filter(x => x.id !== d.id)
      store.currentDeviceId = remaining.length > 0 ? remaining[0].id : ''
    }
    ElMessage.success(`设备「${d.name}」已删除`)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(String(e.message || e))
  }
}
</script>

<style scoped>
.app-shell { display: flex; height: 100vh; overflow: hidden; }

/* ================= 侧栏 ================= */
.side {
  width: 252px; flex-shrink: 0;
  display: flex; flex-direction: column;
  background:
    radial-gradient(420px 300px at -60px -40px, rgba(59, 99, 255, .16), transparent 60%),
    radial-gradient(360px 280px at 110% 108%, rgba(15, 185, 164, .1), transparent 62%),
    var(--side-bg);
  border-right: 1px solid var(--side-line);
  position: relative; z-index: 40;
}

.brand { display: flex; align-items: center; gap: 11px; padding: 20px 18px 16px; }
.brand-mark { width: 36px; height: 36px; flex-shrink: 0; filter: drop-shadow(0 2px 8px rgba(59, 99, 255, .35)); }
.brand-name {
  font-size: 15.5px; font-weight: 750; letter-spacing: .01em;
  color: #F2F5FF; line-height: 1.2;
  font-family: var(--sfa-font);
}
.brand-tag { font-size: 11px; color: #99A5C4; margin-top: 3px; letter-spacing: .07em; }

/* 设备面板 */
.device-panel { margin: 2px 14px 12px; padding: 12px; border-radius: 12px; background: rgba(148, 163, 199, .07); border: 1px solid var(--side-line); }
.device-panel-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.device-label { font-size: 10.5px; letter-spacing: .14em; color: #8A96B8; font-weight: 650; }
.nd-hint { font-size: 10.5px; color: #8A96B8; letter-spacing: .04em; }
.device-ops { display: inline-flex; gap: 2px; }
.icon-ghost {
  width: 24px; height: 24px; border-radius: 7px; border: none; cursor: pointer;
  background: transparent; color: #8A96B8;
  display: inline-flex; align-items: center; justify-content: center;
  transition: all var(--dur-1) var(--ease-out);
}
.icon-ghost .el-icon { font-size: 14px; }
.icon-ghost:hover { background: rgba(148, 163, 199, .14); color: #DCE3F5; }
.icon-ghost.is-danger:hover { background: rgba(229, 72, 77, .16); color: #FF7A80; }

.device-select :deep(.el-select__wrapper) {
  background: rgba(10, 15, 30, .6);
  box-shadow: 0 0 0 1px rgba(148, 163, 199, .2) inset;
  border-radius: 8px; min-height: 30px;
}
.device-select :deep(.el-select__wrapper.is-hovering) { box-shadow: 0 0 0 1px rgba(120, 145, 255, .55) inset; }
.device-select :deep(.el-select__wrapper.is-focused) { box-shadow: 0 0 0 1px var(--sfa-primary) inset, 0 0 0 3px rgba(59, 99, 255, .2) !important; }
.device-select :deep(.el-select__placeholder), .device-select :deep(.el-select__selected-item) { color: #D6DDF0; font-size: 12.5px; }
.device-select :deep(.el-select__caret) { color: var(--side-text-dim); }

.device-tags { margin-top: 9px; display: flex; gap: 5px; flex-wrap: wrap; }
.mini-chip {
  font-size: 11px; padding: 2px 8px; border-radius: 999px; letter-spacing: .02em;
  background: rgba(148, 163, 199, .12); color: #C9D1E6;
  border: 1px solid rgba(148, 163, 199, .14);
  display: inline-flex; align-items: center; gap: 5px;
}
.mc-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.mini-chip.ok { background: rgba(15, 185, 164, .13); color: #52D6C4; border-color: rgba(15, 185, 164, .25); }
.mini-chip.warn { background: rgba(245, 169, 11, .13); color: #FFC65C; border-color: rgba(245, 169, 11, .25); }
.mini-chip.danger { background: rgba(229, 72, 77, .14); color: #FF8085; border-color: rgba(229, 72, 77, .28); }

/* 导航 */
.nav { flex: 1; overflow-y: auto; padding: 2px 12px 12px; display: flex; flex-direction: column; gap: 2px; }
.nav::-webkit-scrollbar { width: 4px; }
.nav-group-label {
  font-size: 10.5px; letter-spacing: .18em; color: #A6B1CE;
  padding: 16px 10px 7px; font-weight: 700;
  border-top: 1px solid rgba(148, 163, 199, .1);
  margin-top: 10px;
}
.nav > :first-child.nav-group-label { border-top: none; margin-top: 0; padding-top: 6px; }
.dev-opt-meta { float: right; color: var(--sfa-text-4); font-size: 11.5px; }
.nav-item {
  position: relative; display: flex; align-items: center; gap: 10px;
  width: 100%; padding: 8.5px 10px; border: none; cursor: pointer;
  background: transparent; border-radius: 9px;
  color: var(--side-text); font-size: 13px; font-family: var(--sfa-font);
  transition: background-color var(--dur-1) var(--ease-out), color var(--dur-1), transform var(--dur-1) var(--ease-out);
}
.nav-item:hover { background: rgba(148, 163, 199, .09); color: #E6EBF8; }
.nav-item:hover .nav-ico { transform: translateX(1px); }
.nav-item:active { transform: scale(.98); }
.nav-item .nav-ico { font-size: 15.5px; transition: transform var(--dur-1) var(--ease-out); }
.nav-item .nav-bar {
  position: absolute; left: -12px; top: 50%; transform: translateY(-50%);
  width: 3px; height: 0; border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, #6A87FF, #0FB9A4);
  transition: height var(--dur-2) var(--ease-out), box-shadow var(--dur-2);
}
.nav-item.active { background: linear-gradient(90deg, rgba(59, 99, 255, .22), rgba(59, 99, 255, .07) 75%, transparent); color: #fff; font-weight: 620; }
.nav-item.active .nav-ico { color: #7D96FF; }
.nav-item.active .nav-bar { height: 18px; box-shadow: 0 0 12px rgba(93, 126, 255, .8); }

/* 侧栏底栏 */
.side-foot { padding: 14px 14px 14px; border-top: 1px solid var(--side-line); }
.health-line { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; padding: 0 2px; }
.health-text { font-size: 11px; color: #8A96B8; letter-spacing: .04em; }
.foot-btns { display: flex; gap: 7px; }
.foot-btn {
  flex: 1; display: flex; align-items: center; justify-content: center; gap: 5px;
  padding: 7px 0; border-radius: 8px; cursor: pointer;
  background: rgba(148, 163, 199, .08); border: 1px solid var(--side-line);
  color: var(--side-text); font-size: 12px; font-family: var(--sfa-font);
  transition: all var(--dur-1) var(--ease-out);
}
.foot-btn .el-icon { font-size: 13px; }
.foot-btn:hover { background: rgba(148, 163, 199, .16); color: #fff; border-color: rgba(148, 163, 199, .28); }
.foot-btn:active { transform: scale(.97); }

/* ================= 工作区 ================= */
.workspace { flex: 1; min-width: 0; display: flex; flex-direction: column; position: relative; }
.main { flex: 1; overflow: auto; padding: 22px 26px; position: relative; }

/* 顶部微点阵：工作区的精密质感 */
.workspace::before {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background-image: radial-gradient(rgba(16, 24, 40, .05) 1px, transparent 1px);
  background-size: 22px 22px;
  -webkit-mask-image: linear-gradient(180deg, rgba(0, 0, 0, .8), transparent 220px);
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, .8), transparent 220px);
}

/* 视图转场：旧视图立即卸载（防快速切换卡死），新视图淡入上浮 */
.view-enter-active { transition: opacity .24s var(--ease-out), transform .3s var(--ease-out); }
.view-leave-active { display: none; }
.view-enter-from { opacity: 0; transform: translateY(10px); }

/* ================= 移动端顶栏（默认隐藏） ================= */
.side-scrim { display: none; }
.mobile-topbar { display: none; }

@media (max-width: 1240px) {
  .side { width: 224px; }
  .main { padding: 18px 20px; }
}

/* 中屏：图标轨道 */
@media (max-width: 1060px) {
  .side { width: 68px; }
  .brand { justify-content: center; padding: 18px 0 12px; }
  .brand-text, .device-panel, .nav-group-label, .nav-label, .health-text, .foot-btn span, .side-foot { display: none; }
  .nav { padding: 4px 10px; align-items: center; }
  .nav-item { justify-content: center; padding: 11px 0; width: 46px; }
  .nav-item .nav-bar { left: -10px; }
  .foot-btns { flex-direction: column; gap: 7px; }
  .foot-btn { padding: 8px 0; }
  .side-foot { border-top: 1px solid var(--side-line); padding: 10px; }
}

/* 小屏：抽屉式侧栏 + 顶栏 */
@media (max-width: 820px) {
  .side {
    position: fixed; inset: 0 auto 0 0; width: 252px;
    transform: translateX(-102%);
    transition: transform var(--dur-2) var(--ease-out);
    box-shadow: none;
  }
  .app-shell.side-open .side { transform: none; box-shadow: var(--sfa-shadow-3); }
  .brand-text, .device-panel, .nav-group-label, .nav-label, .health-text, .foot-btn span { display: initial; }
  .nav { align-items: stretch; }
  .nav-item { justify-content: flex-start; width: 100%; padding: 8.5px 10px; }
  .foot-btns { flex-direction: row; }
  .side-foot { display: block; }
  .side-scrim {
    display: block; position: fixed; inset: 0; z-index: 35;
    background: rgba(12, 18, 34, .5); backdrop-filter: blur(3px);
    opacity: 0; pointer-events: none; transition: opacity var(--dur-2);
  }
  .app-shell.side-open .side-scrim { opacity: 1; pointer-events: auto; }
  .mobile-topbar {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 16px; z-index: 30;
    background: rgba(244, 246, 251, .82); backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--sfa-border);
  }
  .mt-title { font-weight: 700; font-size: 14.5px; letter-spacing: -.01em; }
  .mt-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--sfa-accent); margin-left: auto; }
  .hamburger {
    width: 34px; height: 34px; border-radius: 9px; border: 1px solid var(--sfa-border);
    background: #fff; cursor: pointer; padding: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
  }
  .hamburger span { width: 15px; height: 1.8px; border-radius: 2px; background: var(--sfa-text-2); }
  .main { padding: 14px; }
}
</style>

<style>
/* 设备下拉（深色面板配套，非 scoped） */
.sfa-device-popper .el-select-dropdown__item { font-size: 12.5px; }
</style>
