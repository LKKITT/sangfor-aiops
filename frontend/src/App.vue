<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <el-icon size="22"><Monitor /></el-icon>
        <div>
          <div class="logo-title">深信服售后 Agent</div>
          <div class="logo-sub">配置管理 · 更新建议</div>
        </div>
      </div>

      <div class="device-box">
        <div class="device-label">
          目标设备
          <el-button v-if="device" size="small" text style="float: right; color: #8a9099; height: 20px; padding: 0 4px" @click="openEditDevice" title="编辑设备连接信息">
            <el-icon><Edit /></el-icon>
          </el-button>
          <el-button v-if="device" size="small" text style="float: right; color: #f56c6c; height: 20px; padding: 0 4px; margin-right: 2px" @click="confirmDeleteDevice" title="删除设备">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
        <el-select v-model="store.currentDeviceId" placeholder="选择设备" style="width: 100%">
          <el-option v-for="d in store.devices" :key="d.id" :value="d.id"
                     :label="`${d.name}（${d.mode === 'simulator' ? '模拟器' : '真实'}）`" />
        </el-select>
        <div class="device-tags">
          <el-tag v-if="device && device.readonly" type="warning" size="small">只读</el-tag>
          <el-tag :type="store.health.llm_configured ? 'success' : 'info'" size="small">
            {{ store.health.llm_configured ? `LLM ${store.health.llm_configured}` : 'LLM 未配置(离线兜底)' }}
          </el-tag>
          <el-tag v-if="store.health.readonly_mode" type="danger" size="small">全局只读</el-tag>
        </div>
      </div>

      <el-menu :default-active="store.view" class="menu" @select="v => store.view = v">
        <el-menu-item index="chat"><el-icon><ChatDotRound /></el-icon>AI 对话</el-menu-item>
        <el-menu-item index="config"><el-icon><SetUp /></el-icon>配置可视化</el-menu-item>
        <el-menu-item index="backup"><el-icon><CopyDocument /></el-icon>备份与恢复</el-menu-item>
        <el-menu-item index="checkup"><el-icon><Odometer /></el-icon>配置体检</el-menu-item>
        <el-menu-item index="updates"><el-icon><Download /></el-icon>软件更新建议</el-menu-item>
        <el-menu-item index="knowledge"><el-icon><Collection /></el-icon>个人知识库</el-menu-item>
        <el-menu-item index="chatlog"><el-icon><Notebook /></el-icon>对话日志</el-menu-item>
      </el-menu>

      <div class="aside-footer">
        <el-button size="small" text @click="showAdd = true">+ 添加设备</el-button>
        <el-button size="small" text @click="openSettings">
          <el-icon><Setting /></el-icon> 平台设置
        </el-button>
      </div>
    </el-aside>

    <el-main class="main">
      <component :is="views[store.view]" :key="store.view" />
    </el-main>

    <el-dialog v-model="showSettings" title="平台设置" width="600px">
      <el-collapse v-model="settingsTabs">
        <el-collapse-item name="platform">
          <template #title><b>知识库社区账号（BBS）</b></template>
          <el-alert type="info" :closable="false" style="margin-bottom: 12px; font-size: 12px"
                    title="用于 AI 对话勾选「查询知识库」时，通过深信服社区（bbs.sangfor.com.cn）SSO 登录接入官方诸葛智能知识库。密码仅保存在本机数据库，不会回显。" />
          <el-form label-width="110px">
            <el-form-item label="社区账号">
              <el-input v-model="settingsForm.zhuge_bbs_username" placeholder="深信服社区账号（手机号）" />
            </el-form-item>
            <el-form-item label="社区密码">
              <el-input v-model="settingsForm.zhuge_bbs_password" type="password" show-password
                        :placeholder="settings.zhuge_password_set ? '已配置（留空保持不变，输入新值覆盖）' : '社区登录密码'" />
            </el-form-item>
            <el-form-item label="当前状态">
              <el-tag :type="settings.zhuge_account_source === 'none' ? 'info' : 'success'" size="small">
                {{ bbsStatusText }}
              </el-tag>
            </el-form-item>
          </el-form>
        </el-collapse-item>

        <el-collapse-item name="llm">
          <template #title><b>大模型（LLM）接入</b></template>
          <el-alert type="info" :closable="false" style="margin-bottom: 12px; font-size: 12px"
                    title="任意 OpenAI 兼容接口均可（智谱 GLM / DeepSeek / 通义 等）。保存后立即生效，无需重启；不配置则使用离线兜底模式。" />
          <el-form label-width="110px">
            <el-form-item label="API 地址">
              <el-input v-model="settingsForm.llm_base_url" placeholder="https://open.bigmodel.cn/api/paas/v4" />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input v-model="settingsForm.llm_api_key" type="password" show-password
                        :placeholder="settings.llm_api_key_set ? '已配置（留空保持不变，输入新值覆盖）' : 'sk-xxx 或厂商 API Key'" />
            </el-form-item>
            <el-form-item label="模型">
              <el-input v-model="settingsForm.llm_model" placeholder="glm-4-flash / deepseek-chat 等" />
            </el-form-item>
            <el-form-item label="当前状态">
              <el-tag :type="settings.llm_api_key_set ? 'success' : 'info'" size="small">
                {{ settings.llm_api_key_set ? `已配置（${settings.llm_source === 'database' ? '界面保存' : '.env'}）· ${settings.llm_model}` : '未配置（离线兜底模式）' }}
              </el-tag>
            </el-form-item>
          </el-form>
        </el-collapse-item>
      </el-collapse>
      <template #footer>
        <el-button @click="showSettings = false">取消</el-button>
        <el-button type="primary" :loading="savingSettings" @click="saveSettings">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showAdd" title="添加设备" width="480px">
      <el-form label-width="90px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如：总部-AF-01" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="form.type">
            <el-radio value="af">下一代防火墙 AF</el-radio>
            <el-radio value="ac">上网行为管理 AC</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="接入方式">
          <el-radio-group v-model="form.mode">
            <el-radio value="simulator">内置模拟器</el-radio>
            <el-radio value="real">真实设备</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="form.mode === 'real' && form.type === 'af'">
          <el-form-item label="设备地址"><el-input v-model="form.base_url" placeholder="https://192.168.1.1" /></el-form-item>
          <el-form-item label="API 账号"><el-input v-model="form.username" /></el-form-item>
          <el-form-item label="API 密码"><el-input v-model="form.password" type="password" show-password /></el-form-item>
        </template>
        <template v-if="form.mode === 'real' && form.type === 'ac'">
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
          <span :style="{ color: testResult.ok ? '#67c23a' : '#f56c6c', fontSize: '12px' }">
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

    <el-dialog v-model="showEdit" title="编辑设备连接信息" width="480px">
      <el-form label-width="90px">
        <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="editForm.type">
            <el-radio value="af">下一代防火墙 AF</el-radio>
            <el-radio value="ac">上网行为管理 AC</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="接入方式">
          <el-radio-group v-model="editForm.mode">
            <el-radio value="simulator">内置模拟器</el-radio>
            <el-radio value="real">真实设备</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="editForm.mode === 'real' && editForm.type === 'af'">
          <el-form-item label="设备地址"><el-input v-model="editForm.base_url" placeholder="https://192.168.1.1" /></el-form-item>
          <el-form-item label="API 账号"><el-input v-model="editForm.username" /></el-form-item>
          <el-form-item label="API 密码"><el-input v-model="editForm.password" type="password" show-password placeholder="留空不修改" /></el-form-item>
        </template>
        <template v-if="editForm.mode === 'real' && editForm.type === 'ac'">
          <el-form-item label="设备 IP">
            <el-input v-model="editForm.device_ip" placeholder="192.168.1.1" />
          </el-form-item>
          <el-form-item label="共享密钥">
            <el-input v-model="editForm.password" type="password" show-password placeholder="留空不修改" />
          </el-form-item>
        </template>
        <el-form-item label="只读模式"><el-switch v-model="editForm.readonly" /></el-form-item>
        <el-form-item v-if="editTestResult" label="测连接">
          <span :style="{ color: editTestResult.ok ? '#67c23a' : '#f56c6c', fontSize: '12px' }">
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
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, markRaw } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { store, loadDevices, loadHealth, currentDevice } from './store.js'
import { Devices, Settings } from './api.js'
import ChatView from './views/ChatView.vue'
import ConfigView from './views/ConfigView.vue'
import BackupView from './views/BackupView.vue'
import CheckupView from './views/CheckupView.vue'
import UpdatesView from './views/UpdatesView.vue'
import KnowledgeView from './views/KnowledgeView.vue'
import ChatLogView from './views/ChatLogView.vue'

const views = {
  chat: markRaw(ChatView),
  config: markRaw(ConfigView),
  backup: markRaw(BackupView),
  checkup: markRaw(CheckupView),
  updates: markRaw(UpdatesView),
  knowledge: markRaw(KnowledgeView),
  chatlog: markRaw(ChatLogView)
}

const showAdd = ref(false)
const form = ref({ name: '', type: 'af', mode: 'real', base_url: '', device_ip: '', username: '', password: '', readonly: false })
const device = computed(currentDevice)
const testing = ref(false)
const testResult = ref(null)

// 编辑设备
const showEdit = ref(false)
const editForm = ref({ id: '', name: '', type: 'af', mode: 'real', base_url: '', device_ip: '', username: '', password: '', readonly: false })
const editTesting = ref(false)
const editTestResult = ref(null)

// 平台设置（BBS 社区账号 + LLM）
const showSettings = ref(false)
const savingSettings = ref(false)
const settingsTabs = ref(['platform', 'llm'])
const settings = ref({ zhuge_username: '', zhuge_password_set: false, zhuge_account_source: 'none', llm_base_url: '', llm_model: '', llm_api_key_set: false, llm_source: 'none' })
const settingsForm = ref({ zhuge_bbs_username: '', zhuge_bbs_password: '', llm_base_url: '', llm_api_key: '', llm_model: '' })
const bbsStatusText = computed(() => ({
  database: '已配置（界面保存）',
  env: '已配置（.env）',
  builtin: '使用技能内置账号（建议配置为自己的社区账号）',
  none: '未配置（知识库查询不可用）'
}[settings.value.zhuge_account_source] || '未配置'))

async function openSettings() {
  settings.value = await Settings.get().catch(() => settings.value)
  settingsForm.value = {
    zhuge_bbs_username: settings.value.zhuge_username || '',
    zhuge_bbs_password: '',
    llm_base_url: settings.value.llm_base_url || '',
    llm_api_key: '',
    llm_model: settings.value.llm_model || ''
  }
  showSettings.value = true
}

async function saveSettings() {
  savingSettings.value = true
  try {
    const payload = {}
    if (settingsForm.value.zhuge_bbs_username) payload.zhuge_bbs_username = settingsForm.value.zhuge_bbs_username
    if (settingsForm.value.zhuge_bbs_password) payload.zhuge_bbs_password = settingsForm.value.zhuge_bbs_password
    if (settingsForm.value.llm_base_url) payload.llm_base_url = settingsForm.value.llm_base_url
    if (settingsForm.value.llm_api_key) payload.llm_api_key = settingsForm.value.llm_api_key
    if (settingsForm.value.llm_model) payload.llm_model = settingsForm.value.llm_model
    settings.value = await Settings.save(payload)
    await loadHealth()
    ElMessage.success('已保存并即时生效')
    showSettings.value = false
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { savingSettings.value = false }
}

onMounted(async () => {
  await Promise.all([loadDevices(), loadHealth()])
})

async function addDevice() {
  if (!form.value.name) return ElMessage.warning('请填写设备名称')
  // AC 设备：将 IP 转为 http://{ip}:9999
  const payload = { ...form.value }
  if (payload.type === 'ac' && payload.device_ip) {
    payload.base_url = `http://${payload.device_ip}:9999`
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
  // 对 AC 设备，从 base_url 中提取 IP
  const editIp = d.type === 'ac' && d.base_url ? d.base_url.replace(/^https?:\/\//, '').replace(/:9999$/, '') : ''
  editForm.value = {
    id: d.id, name: d.name, type: d.type, mode: d.mode,
    base_url: d.base_url, device_ip: editIp, username: d.username || '',
    password: '', readonly: !!d.readonly
  }
  editTestResult.value = null
  showEdit.value = true
}

async function testEditConnection() {
  // AC 设备：将 IP 转为 http://{ip}:9999
  const payload = { ...editForm.value }
  if (payload.type === 'ac' && payload.device_ip) {
    payload.base_url = `http://${payload.device_ip}:9999`
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
    // AC 设备：将 IP 转为 http://{ip}:9999
    if (payload.type === 'ac' && editForm.value.device_ip) {
      payload.base_url = `http://${editForm.value.device_ip}:9999`
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
.layout { height: 100vh; }
.aside { background: #001529; color: #cfd3dc; display: flex; flex-direction: column; }
.logo { display: flex; gap: 10px; align-items: center; padding: 18px 16px; color: #fff; }
.logo-title { font-weight: 600; font-size: 15px; }
.logo-sub { font-size: 12px; color: #8a9099; }
.device-box { padding: 0 14px 10px; }
.device-label { font-size: 12px; color: #8a9099; margin-bottom: 6px; }
.device-tags { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.menu { border-right: none; background: transparent; flex: 1; }
.menu :deep(.el-menu-item) { color: #cfd3dc; }
.menu :deep(.el-menu-item.is-active) { color: #409eff; background: #11263f; }
.menu :deep(.el-menu-item:hover) { background: #11263f; }
.aside-footer { padding: 12px; }
.main { padding: 12px; overflow: auto; }
</style>
