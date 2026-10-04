<template>
  <div class="settings-page">
    <div class="page-head">
      <div>
        <h2 class="ph-title">平台设置</h2>
        <p class="ph-desc">知识库社区账号（BBS）· 大模型（LLM）接入 · 企业微信机器人 —— 配置仅保存在本机数据库，敏感信息不回显，保存后立即生效、无需重启</p>
      </div>
      <div class="ph-actions">
        <el-button type="primary" :loading="savingSettings" @click="saveSettings">
          <el-icon><Check /></el-icon>&nbsp;保存设置
        </el-button>
      </div>
    </div>

    <div class="page-card settings-card">
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
        <el-collapse-item name="wecom">
          <template #title><b>企业微信机器人</b></template>
          <el-alert type="info" :closable="false" style="margin-bottom: 12px; font-size: 12px"
                    title="在企业微信管理后台创建智能机器人（开启「API 模式」并选择「长连接」，建议由超级管理员创建以保证成员 ID 明文）后启用，即可在企微 App 内单聊机器人使用 AI 对话。保存后立即生效，长连接自动重连。" />
          <el-form label-width="110px">
            <el-form-item label="启用渠道">
              <el-switch v-model="settingsForm.wecom_aibot_enabled" />
            </el-form-item>
            <el-form-item label="BotID">
              <el-input v-model="settingsForm.wecom_aibot_id" :disabled="!settingsForm.wecom_aibot_enabled"
                        placeholder="企微后台机器人的 BotID" />
            </el-form-item>
            <el-form-item label="Secret">
              <el-input v-model="settingsForm.wecom_aibot_secret" type="password" show-password
                        :disabled="!settingsForm.wecom_aibot_enabled"
                        :placeholder="settings.wecom_secret_set ? '已配置（留空保持不变，输入新值覆盖）' : '企微后台机器人的 Secret'" />
            </el-form-item>
            <el-form-item label="当前状态">
              <el-tag :type="wecomStatusType" size="small">{{ wecomStatusText }}</el-tag>
            </el-form-item>
          </el-form>
        </el-collapse-item>
      </el-collapse>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Settings } from '../api.js'
import { store, loadHealth } from '../store.js'

const savingSettings = ref(false)
const settingsTabs = ref(['platform', 'llm', 'wecom'])
const settings = ref({ zhuge_username: '', zhuge_password_set: false, zhuge_account_source: 'none', llm_base_url: '', llm_model: '', llm_api_key_set: false, llm_source: 'none', wecom_enabled: false, wecom_bot_id: '', wecom_secret_set: false, wecom_source: 'none', wecom_conn_status: 'disabled', wecom_conn_error: '' })
const settingsForm = ref({ zhuge_bbs_username: '', zhuge_bbs_password: '', llm_base_url: '', llm_api_key: '', llm_model: '', wecom_aibot_enabled: false, wecom_aibot_id: '', wecom_aibot_secret: '' })

const bbsStatusText = computed(() => ({
  database: '已配置（界面保存）',
  env: '已配置（.env）',
  builtin: '使用技能内置账号（建议配置为自己的社区账号）',
  none: '未配置（知识库查询不可用）'
}[settings.value.zhuge_account_source] || '未配置'))

const wecomStatusText = computed(() => {
  const s = settings.value
  if (!s.wecom_enabled) return '未启用'
  const src = { database: '界面保存', env: '.env' }[s.wecom_source] || '未配置'
  if (s.wecom_conn_status === 'connected') return `已连接（${src}）`
  if (s.wecom_conn_status === 'connecting') return '连接中…'
  if (s.wecom_conn_status === 'reconnecting') return '连接断开，重连中…'
  if (s.wecom_conn_status === 'error') return `异常：${s.wecom_conn_error || '请检查 BotID / Secret'}`
  return `已启用（${src}），等待连接`
})
const wecomStatusType = computed(() =>
  ({ connected: 'success', connecting: 'warning', reconnecting: 'warning', error: 'danger' }[settings.value.wecom_conn_status] || 'info'))

async function loadSettings() {
  settings.value = await Settings.get().catch(() => settings.value)
  settingsForm.value = {
    zhuge_bbs_username: settings.value.zhuge_username || '',
    zhuge_bbs_password: '',
    llm_base_url: settings.value.llm_base_url || '',
    llm_api_key: '',
    llm_model: settings.value.llm_model || '',
    wecom_aibot_enabled: !!settings.value.wecom_enabled,
    wecom_aibot_id: settings.value.wecom_bot_id || '',
    wecom_aibot_secret: ''
  }
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
    // 企微渠道：开关始终提交；BotID 有值才覆盖（Secret 留空保持不变）
    payload.wecom_aibot_enabled = !!settingsForm.value.wecom_aibot_enabled
    if (settingsForm.value.wecom_aibot_id) payload.wecom_aibot_id = settingsForm.value.wecom_aibot_id
    if (settingsForm.value.wecom_aibot_secret) payload.wecom_aibot_secret = settingsForm.value.wecom_aibot_secret
    settings.value = await Settings.save(payload)
    await loadHealth()
    ElMessage.success('已保存并即时生效')
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { savingSettings.value = false }
}

onMounted(loadSettings)
</script>

<style scoped>
.settings-page { max-width: 860px; margin: 0 auto; animation: sfa-fade-up .3s var(--ease-out); }
.settings-card { padding: 6px 18px 10px; }
.settings-card :deep(.el-collapse-item__header) { font-size: 14px; }
.settings-card :deep(.el-collapse) { border-top: none; }
.settings-card :deep(.el-collapse-item__content) { padding-bottom: 18px; }
</style>
