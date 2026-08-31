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
        <div class="device-label">目标设备</div>
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
          <template #title><b>深信服技术支持平台</b></template>
          <el-alert type="info" :closable="false" style="margin-bottom: 12px; font-size: 12px"
                    title="发布说明为免认证抓取；软件下载列表与部分正文需深信服客户/伙伴身份认证。在浏览器登录 support.sangfor.com.cn 后，复制请求 Cookie 粘贴到此处即可抓取认证内容。" />
          <el-form label-width="110px">
            <el-form-item label="平台 Cookie">
              <el-input v-model="settingsForm.support_cookie" type="textarea" :rows="4"
                        placeholder="粘贴浏览器登录 support.sangfor.com.cn 后的 Cookie（如 SF_COOKIE=xxx; SESSION=yyy）" />
            </el-form-item>
            <el-form-item label="当前状态">
              <el-tag :type="settings.support_cookie_source === 'none' ? 'info' : 'success'" size="small">
                {{ cookieStatusText }}
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
          <el-form-item label="设备地址">
            <el-input v-model="form.base_url" placeholder="http://10.68.5.1:9999（开放接口端口 9999）" />
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
      </el-form>
      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" @click="addDevice">确定</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, markRaw } from 'vue'
import { ElMessage } from 'element-plus'
import { store, loadDevices, loadHealth, currentDevice } from './store.js'
import { Devices, Settings } from './api.js'
import ChatView from './views/ChatView.vue'
import ConfigView from './views/ConfigView.vue'
import BackupView from './views/BackupView.vue'
import CheckupView from './views/CheckupView.vue'
import UpdatesView from './views/UpdatesView.vue'

const views = {
  chat: markRaw(ChatView),
  config: markRaw(ConfigView),
  backup: markRaw(BackupView),
  checkup: markRaw(CheckupView),
  updates: markRaw(UpdatesView)
}

const showAdd = ref(false)
const form = ref({ name: '', type: 'af', mode: 'real', base_url: '', username: '', password: '', readonly: false })
const device = computed(currentDevice)

// 平台设置（Cookie + LLM）
const showSettings = ref(false)
const savingSettings = ref(false)
const settingsTabs = ref(['platform', 'llm'])
const settings = ref({ support_cookie: '', support_cookie_source: 'none', llm_base_url: '', llm_model: '', llm_api_key_set: false, llm_source: 'none' })
const settingsForm = ref({ support_cookie: '', llm_base_url: '', llm_api_key: '', llm_model: '' })
const cookieStatusText = computed(() => ({
  database: '已配置（界面保存，抓取认证内容）',
  env: '已配置（.env）',
  none: '未配置（使用公开来源 + 内置知识库）'
}[settings.value.support_cookie_source] || '未配置'))

async function openSettings() {
  settings.value = await Settings.get().catch(() => settings.value)
  settingsForm.value = {
    support_cookie: settings.value.support_cookie || '',
    llm_base_url: settings.value.llm_base_url || '',
    llm_api_key: '',
    llm_model: settings.value.llm_model || ''
  }
  showSettings.value = true
}

async function saveSettings() {
  savingSettings.value = true
  try {
    const payload = { support_cookie: settingsForm.value.support_cookie }
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
  try {
    await Devices.add(form.value)
    await loadDevices()
    showAdd.value = false
    ElMessage.success('设备已添加')
  } catch (e) { ElMessage.error(String(e.message || e)) }
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
