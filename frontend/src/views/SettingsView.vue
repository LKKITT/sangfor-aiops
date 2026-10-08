<template>
  <div class="settings-page">
    <div class="page-head">
      <div>
        <h2 class="ph-title">平台设置</h2>
        <p class="ph-desc">基本配置 · MCP 服务 · Agent Skills —— 配置仅保存在本机数据库，敏感信息不回显，保存后立即生效、无需重启</p>
      </div>
      <div class="ph-actions" v-if="activeTab === 'basic'">
        <el-button type="primary" :loading="savingSettings" @click="saveSettings">
          <el-icon><Check /></el-icon>&nbsp;保存设置
        </el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="page-card settings-tabs">
      <!-- ================= 基本配置（原有） ================= -->
      <el-tab-pane label="基本配置" name="basic">
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
      </el-tab-pane>

      <!-- ================= MCP 配置 ================= -->
      <el-tab-pane label="MCP 配置" name="mcp">
        <el-alert type="info" :closable="false" style="margin-bottom: 12px; font-size: 12px"
                  title="接入 MCP（Model Context Protocol）服务后，其原生工具自动注入 AI 对话（工具名前缀 mcp_）。stdio 方式在本机拉起子进程（需已安装 npx/uvx 等运行时）；http 方式直连远程端点。开关与配置保存即生效。" />

        <div class="mcp-toolbar">
          <el-button type="primary" size="small" @click="openMcpEditor()">
            <el-icon><Plus /></el-icon>&nbsp;自定义新增
          </el-button>
          <el-button size="small" @click="showMcpRegistry = true">
            <el-icon><Search /></el-icon>&nbsp;从注册表安装
          </el-button>
          <el-button size="small" @click="showMcpImport = true">
            <el-icon><Document /></el-icon>&nbsp;粘贴 JSON 导入
          </el-button>
        </div>

        <el-empty v-if="!mcpServers.length" description="尚未配置 MCP 服务" :image-size="70" />
        <div v-for="s in mcpServers" :key="s.id" class="mcp-card">
          <div class="mcp-main">
            <div class="mcp-name">
              <el-switch v-model="s.enabled" @change="toggleMcp(s)" style="margin-right: 10px" />
              <b>{{ s.name }}</b>
              <el-tag size="small" :type="s.transport === 'http' ? 'warning' : 'info'" effect="plain">
                {{ s.transport === 'http' ? 'HTTP 远端' : 'stdio 本机' }}
              </el-tag>
            </div>
            <div class="mcp-target mono">{{ s.transport === 'http' ? s.url : s.command + ' ' + (s.args || []).join(' ') }}</div>
          </div>
          <div class="mcp-ops">
            <el-button size="small" text type="primary" :loading="testingId === s.id" @click="testMcp(s)">测试</el-button>
            <el-button size="small" text @click="openMcpEditor(s)">编辑</el-button>
            <el-button size="small" text type="danger" @click="removeMcp(s)">删除</el-button>
          </div>
          <div v-if="testResults[s.id]" class="mcp-test-result mono"
               :class="{ err: testResults[s.id].err }">{{ testResults[s.id].text }}</div>
        </div>

        <!-- 新增/编辑对话框 -->
        <el-dialog v-model="mcpEditor" :title="mcpForm.id ? '编辑 MCP 服务' : '自定义新增 MCP 服务'" width="560px" append-to-body>
          <el-form label-width="92px">
            <el-form-item label="名称"><el-input v-model="mcpForm.name" placeholder="如 fetch / 公司内部工具" /></el-form-item>
            <el-form-item label="接入方式">
              <el-radio-group v-model="mcpForm.transport">
                <el-radio value="stdio">stdio（本机子进程）</el-radio>
                <el-radio value="http">http（远程端点）</el-radio>
              </el-radio-group>
            </el-form-item>
            <template v-if="mcpForm.transport === 'stdio'">
              <el-form-item label="启动命令">
                <el-input v-model="mcpForm.command" placeholder="如 npx / uvx / python" />
              </el-form-item>
              <el-form-item label="参数">
                <el-input v-model="mcpForm.argsText" type="textarea" :rows="2"
                          placeholder="每行一个参数，如：&#10;-y&#10;mcp-server-fetch" />
              </el-form-item>
              <el-form-item label="环境变量">
                <el-input v-model="mcpForm.envText" type="textarea" :rows="2"
                          placeholder='JSON，如 {"API_KEY": "xxx"}（选填）' />
              </el-form-item>
            </template>
            <template v-else>
              <el-form-item label="端点 URL">
                <el-input v-model="mcpForm.url" placeholder="https://host/mcp" />
              </el-form-item>
              <el-form-item label="请求头">
                <el-input v-model="mcpForm.headersText" type="textarea" :rows="2"
                          placeholder='JSON，如 {"Authorization": "Bearer xxx"}（选填）' />
              </el-form-item>
            </template>
            <el-form-item label="启用">
              <el-switch v-model="mcpForm.enabled" />
            </el-form-item>
            <el-form-item v-if="mcpTestEditor" label="测试">
              <span class="mono" :style="{ color: mcpTestEditor.err ? '#E5484D' : '#0E9F6E', fontSize: '12px' }">{{ mcpTestEditor.text }}</span>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="testEditor" :loading="testingEditor">测试连接</el-button>
            <el-button @click="mcpEditor = false">取消</el-button>
            <el-button type="primary" @click="saveMcp">保存</el-button>
          </template>
        </el-dialog>

        <!-- 注册表安装 -->
        <el-dialog v-model="showMcpRegistry" title="从 MCP 注册表安装（registry.modelcontextprotocol.io）" width="640px" top="5vh" append-to-body>
          <div class="mcp-toolbar">
            <el-input v-model="registryKeyword" size="small" clearable placeholder="搜索关键词，如 fetch / filesystem / github"
                      @keyup.enter="searchRegistry" />
            <el-button size="small" type="primary" :loading="searching" @click="searchRegistry">搜索</el-button>
          </div>
          <div v-if="registryError" class="ndc-trunc">{{ registryError }}</div>
          <el-empty v-if="!registryResults.length && !searching" description="输入关键词搜索官方注册表" :image-size="60" />
          <div v-for="it in registryResults" :key="it.registry_name" class="mcp-card">
            <div class="mcp-main">
              <div class="mcp-name"><b>{{ it.display }}</b>
                <el-tag size="small" type="info" effect="plain">{{ it.status || 'active' }}</el-tag>
              </div>
              <div class="mcp-desc">{{ it.description }}</div>
              <div class="mcp-target mono" v-if="it.remotes.length">HTTP {{ it.remotes[0].url }}</div>
              <div class="mcp-target mono" v-else-if="it.packages.length">
                {{ it.packages[0].command }} {{ (it.packages[0].args || []).join(' ') }}
              </div>
            </div>
            <div class="mcp-ops">
              <el-button size="small" type="primary" plain @click="installRegistry(it)">安装</el-button>
            </div>
          </div>
          <div class="ndc-trunc" style="margin-top: 8px">
            stdio 方式的服务在本机执行启动命令，需已安装对应运行时（npx / uvx）；安装后默认启用。
          </div>
        </el-dialog>

        <!-- 粘贴 JSON 导入 -->
        <el-dialog v-model="showMcpImport" title="粘贴 JSON 导入（Claude Desktop / Cursor 格式）" width="600px" append-to-body>
          <el-input v-model="mcpImportText" type="textarea" :rows="8"
                    placeholder='{"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}' />
          <el-checkbox v-model="mcpImportEnabled" style="margin-top: 8px">导入后立即启用</el-checkbox>
          <template #footer>
            <el-button @click="showMcpImport = false">取消</el-button>
            <el-button type="primary" :loading="importing" @click="doImport">导入</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- ================= Skills 配置 ================= -->
      <el-tab-pane label="Skills 配置" name="skills">
        <el-alert type="info" :closable="false" style="margin-bottom: 12px; font-size: 12px"
                  title="Agent Skills（SKILL.md 规范）：启用后注入 AI 对话，模型按需加载技能说明并遵循执行。自动扫描本机 ~/.agents/skills、~/.claude/skills 与本应用导入目录；也可从 GitHub 仓库文件夹导入。" />
        <div class="mcp-toolbar">
          <el-button size="small" :loading="loadingSkills" @click="loadSkills">
            <el-icon><Refresh /></el-icon>&nbsp;重新扫描本机
          </el-button>
          <el-input v-model="skillImportUrl" size="small" clearable
                    placeholder="GitHub 文件夹链接（…/tree/main/skills/xxx）" style="width: 340px" />
          <el-checkbox v-model="skillImportForce" size="small">覆盖</el-checkbox>
          <el-button size="small" type="primary" :loading="importingSkill" @click="importSkill">
            <el-icon><Download /></el-icon>&nbsp;从 GitHub 导入
          </el-button>
        </div>

        <el-empty v-if="!agentSkills.length && !loadingSkills" description="未发现已安装的 Agent Skills" :image-size="70" />
        <div v-for="s in agentSkills" :key="s.source + s.folder" class="mcp-card">
          <div class="mcp-main">
            <div class="mcp-name">
              <el-switch v-model="s.enabled" @change="toggleSkill(s)" style="margin-right: 10px" />
              <b>{{ s.name }}</b>
              <el-tag size="small" :type="s.source === 'imported' ? 'success' : 'info'" effect="plain">
                {{ { 'user-agents': '~/.agents', 'user-claude': '~/.claude', imported: '已导入' }[s.source] || s.source }}
              </el-tag>
            </div>
            <div class="mcp-desc">{{ s.description || '（无描述）' }}</div>
            <div class="mcp-target mono">{{ s.path }}</div>
          </div>
          <div class="mcp-ops">
            <el-button v-if="s.deletable" size="small" text type="danger" @click="removeSkill(s)">删除</el-button>
          </div>
        </div>
        <div class="ndc-trunc" style="margin-top: 8px">
          启用的技能会在 AI 对话中以清单注入，模型按需加载说明后遵循执行；删除仅限本应用导入目录内的技能。
        </div>
      </el-tab-pane>

      <!-- ================= 客户管理 ================= -->
      <el-tab-pane label="客户管理" name="customers">
        <el-alert type="info" :closable="false" style="margin-bottom: 12px; font-size: 12px"
                  title="客户即工作区（租户）：编码用于隔离设备、会话等数据（侧栏「客户」切换器选择）；名称/联系人是展示档案。内置「默认客户」可编辑、不可删除；新客户保存后即可在侧栏切换。" />
        <div class="mcp-toolbar">
          <el-button type="primary" size="small" @click="openCustomerEditor()">
            <el-icon><Plus /></el-icon>&nbsp;添加客户
          </el-button>
          <el-button size="small" :loading="loadingCustomers" @click="loadCustomers">
            <el-icon><Refresh /></el-icon>&nbsp;刷新
          </el-button>
        </div>
        <el-table :data="customers" size="small" v-loading="loadingCustomers"
                  :empty-text="loadingCustomers ? '加载中…' : '尚未登记客户，点击「添加客户」创建；未登记的租户仍可继续使用'">
          <el-table-column prop="name" label="客户名称" min-width="140" show-overflow-tooltip />
          <el-table-column prop="code" label="编码" width="130" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.code }}</span></template>
          </el-table-column>
          <el-table-column prop="contact" label="联系人" width="100" show-overflow-tooltip>
            <template #default="{ row }">{{ row.contact || '—' }}</template>
          </el-table-column>
          <el-table-column prop="phone" label="电话" width="130" show-overflow-tooltip>
            <template #default="{ row }">{{ row.phone || '—' }}</template>
          </el-table-column>
          <el-table-column prop="email" label="邮箱" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">{{ row.email || '—' }}</template>
          </el-table-column>
          <el-table-column label="关联数据" width="150">
            <template #default="{ row }">
              <span class="cust-usage">设备 {{ row.usage.devices }} · 网络设备 {{ row.usage.netdev_devices }} · 会话 {{ row.usage.conversations }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.note || '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="110" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="openCustomerEditor(row)">编辑</el-button>
              <el-button v-if="row.code !== 'default'" size="small" text type="danger" @click="removeCustomer(row)">删除</el-button>
              <el-tooltip v-else content="内置默认客户不可删除，名称等信息可编辑" placement="top">
                <span class="cust-default-tag">内置</span>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>

        <el-dialog v-model="customerEditor" :title="customerForm.id ? '编辑客户' : '添加客户'" width="480px" append-to-body>
          <el-form label-width="86px">
            <el-form-item label="客户名称" required>
              <el-input v-model="customerForm.name" placeholder="如：某某科技有限公司" />
            </el-form-item>
            <el-form-item label="编码" required>
              <el-input v-model="customerForm.code" :disabled="!!customerForm.id"
                        placeholder="字母/数字/下划线/中划线，如 cust_acme（创建后不可改）" />
            </el-form-item>
            <el-form-item label="联系人"><el-input v-model="customerForm.contact" /></el-form-item>
            <el-form-item label="电话"><el-input v-model="customerForm.phone" /></el-form-item>
            <el-form-item label="邮箱"><el-input v-model="customerForm.email" /></el-form-item>
            <el-form-item label="备注"><el-input v-model="customerForm.note" type="textarea" :rows="2" /></el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="customerEditor = false">取消</el-button>
            <el-button type="primary" :loading="savingCustomer" @click="saveCustomer">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Settings, Customers } from '../api.js'
import { store, loadHealth, refreshTenants } from '../store.js'

const activeTab = ref('basic')

// ================= 基本配置（原有逻辑原样保留） =================
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

// ================= MCP 配置 =================
const mcpServers = ref([])
const mcpEditor = ref(false)
const mcpForm = ref({})
const testingEditor = ref(false)
const mcpTestEditor = ref(null)
const testingId = ref('')
const testResults = ref({})
const showMcpRegistry = ref(false)
const registryKeyword = ref('')
const registryResults = ref([])
const searching = ref(false)
const registryError = ref('')
const showMcpImport = ref(false)
const mcpImportText = ref('')
const mcpImportEnabled = ref(false)
const importing = ref(false)

function parseLines(t) {
  return String(t || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean)
}
function parseJsonLoose(t, fallback) {
  try { return JSON.parse(t || '') || fallback } catch { return null }
}

async function loadMcp() {
  try {
    mcpServers.value = (await Settings.mcpList()).servers || []
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

function openMcpEditor(s = null) {
  mcpTestEditor.value = null
  mcpForm.value = s ? {
    ...s,
    argsText: (s.args || []).join('\n'),
    envText: JSON.stringify(s.env || {}, null, 0),
    headersText: JSON.stringify(s.headers || {}, null, 0)
  } : { name: '', transport: 'stdio', command: '', argsText: '', envText: '{}', url: '', headersText: '{}', enabled: true }
  mcpEditor.value = true
}

function buildFormPayload() {
  const f = mcpForm.value
  const payload = { id: f.id, name: f.name, transport: f.transport, enabled: f.enabled }
  if (f.transport === 'stdio') {
    payload.command = f.command
    payload.args = parseLines(f.argsText)
    const env = parseJsonLoose(f.envText, null)
    if (env === null) { ElMessage.warning('环境变量不是合法 JSON'); return null }
    payload.env = env
  } else {
    payload.url = f.url
    const headers = parseJsonLoose(f.headersText, null)
    if (headers === null) { ElMessage.warning('请求头不是合法 JSON'); return null }
    payload.headers = headers
  }
  return payload
}

async function testEditor() {
  const payload = buildFormPayload()
  if (!payload) return
  testingEditor.value = true
  mcpTestEditor.value = null
  try {
    const r = await Settings.mcpTest(payload)
    const names = (r.tools || []).map(t => t.name).join('、') || '（无工具）'
    mcpTestEditor.value = { err: false, text: `连接成功，发现 ${r.tools.length} 个工具：${names}` }
  } catch (e) {
    mcpTestEditor.value = { err: true, text: String(e.message || e) }
  } finally { testingEditor.value = false }
}

async function saveMcp() {
  const payload = buildFormPayload()
  if (!payload) return
  if (!payload.name) return ElMessage.warning('请填写名称')
  try {
    await Settings.mcpSave(payload)
    ElMessage.success('已保存并即时生效')
    mcpEditor.value = false
    await loadMcp()
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

async function toggleMcp(s) {
  try {
    await Settings.mcpSave({ ...s })
    ElMessage.success(`${s.name} 已${s.enabled ? '启用' : '停用'}`)
  } catch (e) {
    s.enabled = !s.enabled
    ElMessage.error(String(e.message || e))
  }
}

async function removeMcp(s) {
  await ElMessageBox.confirm(`删除 MCP 服务「${s.name}」？`, '确认删除', { type: 'warning' })
  await Settings.mcpDelete(s.id)
  ElMessage.success('已删除')
  await loadMcp()
}

async function testMcp(s) {
  testingId.value = s.id
  testResults.value[s.id] = null
  try {
    const r = await Settings.mcpTest(s)
    testResults.value[s.id] = { err: false, text: `连接成功：${(r.tools || []).length} 个工具（${r.tools.map(t => t.name).join('、')}）` }
  } catch (e) {
    testResults.value[s.id] = { err: true, text: String(e.message || e) }
  } finally { testingId.value = '' }
}

async function searchRegistry() {
  searching.value = true
  registryError.value = ''
  try {
    const r = await Settings.mcpRegistry(registryKeyword.value.trim())
    registryResults.value = r.servers || []
    if (!registryResults.value.length) registryError.value = '没有匹配的结果'
  } catch (e) {
    registryError.value = String(e.message || e)
  } finally { searching.value = false }
}

async function installRegistry(it) {
  try {
    await Settings.mcpRegistryInstall(it)
    ElMessage.success(`「${it.display}」已安装并启用`)
    showMcpRegistry.value = false
    await loadMcp()
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

async function doImport() {
  importing.value = true
  try {
    const r = await Settings.mcpImport(mcpImportText.value, mcpImportEnabled.value)
    ElMessage.success(`导入 ${r.imported} 个服务${r.errors.length ? `，${r.errors.length} 条失败：${r.errors[0]}` : ''}`)
    showMcpImport.value = false
    mcpImportText.value = ''
    await loadMcp()
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { importing.value = false }
}

// ================= Agent Skills =================
const agentSkills = ref([])
const loadingSkills = ref(false)
const skillImportUrl = ref('')
const skillImportForce = ref(false)
const importingSkill = ref(false)

async function loadSkills() {
  loadingSkills.value = true
  try {
    agentSkills.value = (await Settings.skillsList()).skills || []
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { loadingSkills.value = false }
}

async function toggleSkill(s) {
  try {
    await Settings.skillsToggle(s.folder, s.enabled)
    ElMessage.success(`${s.name} 已${s.enabled ? '启用' : '停用'}`)
  } catch (e) {
    s.enabled = !s.enabled
    ElMessage.error(String(e.message || e))
  }
}

async function importSkill() {
  if (!skillImportUrl.value.trim()) return ElMessage.warning('请粘贴 GitHub 文件夹链接')
  importingSkill.value = true
  try {
    const r = await Settings.skillsImport(skillImportUrl.value.trim(), skillImportForce.value)
    ElMessage.success(`「${r.folder}」导入成功（${r.files.length} 个文件）`)
    skillImportUrl.value = ''
    skillImportForce.value = false
    await loadSkills()
  } catch (e) { ElMessage.error(String(e.message || e)) } finally { importingSkill.value = false }
}

async function removeSkill(s) {
  await ElMessageBox.confirm(`删除技能「${s.name}」的文件夹？该操作不可恢复`, '确认删除', { type: 'warning' })
  await Settings.skillsDelete(s.folder)
  ElMessage.success('已删除')
  await loadSkills()
}

// ================= 客户管理 =================
const customers = ref([])
const loadingCustomers = ref(false)
const customerEditor = ref(false)
const customerForm = ref({})
const savingCustomer = ref(false)

async function loadCustomers() {
  loadingCustomers.value = true
  try {
    customers.value = (await Customers.list()).customers || []
  } catch (e) { ElMessage.error(String(e.message || e)) }
  loadingCustomers.value = false
}

function openCustomerEditor(row = null) {
  customerForm.value = row
    ? { id: row.id, code: row.code, name: row.name, contact: row.contact || '',
        phone: row.phone || '', email: row.email || '', note: row.note || '' }
    : { id: '', code: '', name: '', contact: '', phone: '', email: '', note: '' }
  customerEditor.value = true
}

async function saveCustomer() {
  const f = customerForm.value
  if (!f.name?.trim()) return ElMessage.warning('请填写客户名称')
  if (!f.id && !f.code?.trim()) return ElMessage.warning('请填写客户编码')
  savingCustomer.value = true
  try {
    if (f.id) {
      await Customers.update(f.id, { name: f.name, contact: f.contact, phone: f.phone, email: f.email, note: f.note })
      ElMessage.success('客户信息已更新')
    } else {
      await Customers.create(f)
      ElMessage.success('客户已创建，可在左上角「客户」切换器中选择')
    }
    customerEditor.value = false
    await loadCustomers()
    await refreshTenants()   // 侧栏客户切换器同步新客户
  } catch (e) { ElMessage.error(String(e.message || e)) }
  savingCustomer.value = false
}

async function removeCustomer(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除客户「${row.name}」吗？\n仅删除客户档案，关联数据（设备/会话）不会被删除，但删除后侧栏将不再显示该客户显示名。`,
      '删除客户', { type: 'warning' })
  } catch { return }   // 用户取消
  try {
    await Customers.remove(row.id)
    ElMessage.success('客户已删除')
    await loadCustomers()
    await refreshTenants()
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

onMounted(() => {
  loadSettings()
  loadMcp()
  loadSkills()
  loadCustomers()
})
</script>

<style scoped>
.settings-page { max-width: 900px; margin: 0 auto; animation: sfa-fade-up .3s var(--ease-out); }
.settings-tabs { padding: 6px 18px 14px; }
.settings-tabs :deep(.el-collapse-item__header) { font-size: 14px; }
.settings-tabs :deep(.el-collapse) { border-top: none; }
.settings-tabs :deep(.el-collapse-item__content) { padding-bottom: 18px; }

.mcp-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.mcp-card {
  display: flex; align-items: flex-start; gap: 10px;
  border: 1px solid var(--sfa-border-soft); border-radius: 10px;
  padding: 10px 13px; margin-bottom: 9px; background: var(--sfa-surface);
}
.mcp-main { flex: 1; min-width: 0; }
.mcp-name { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.mcp-desc { font-size: 12px; color: var(--sfa-text-3); margin-top: 3px; line-height: 1.6; }
.mcp-target { font-size: 11.5px; color: var(--sfa-text-4); margin-top: 4px; overflow: hidden;
              text-overflow: ellipsis; white-space: nowrap; }
.mcp-ops { display: flex; flex-direction: column; gap: 0; align-items: flex-end; }
.mcp-test-result { width: 100%; margin-top: 7px; font-size: 11.5px;
                   color: var(--sfa-ok-ink); word-break: break-all; }
.mcp-test-result.err { color: var(--sfa-danger); }
.mono { font-family: var(--sfa-mono); }
.ndc-trunc { font-size: 12px; color: var(--sfa-warning, var(--sfa-warn-ink)); }
.cust-usage { font-size: 11.5px; color: var(--sfa-text-3); font-feature-settings: "tnum" 1; }
.cust-default-tag { font-size: 11px; color: var(--sfa-text-4); }
</style>
