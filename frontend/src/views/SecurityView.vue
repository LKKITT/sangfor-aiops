<template>
  <div class="sec-page">
    <div class="page-head">
      <div>
        <h2 class="ph-title">安全设备管理</h2>
        <p class="ph-desc">深信服设备（AF 防火墙 / AC 上网行为管理 / SCP 云计算平台）的接入、编辑与删除 · 设备切换请在各工作台页头进行</p>
      </div>
      <div class="ph-actions">
        <el-button @click="refresh" :loading="loading">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button type="primary" @click="openAdd">
          <el-icon><Plus /></el-icon>&nbsp;添加设备
        </el-button>
      </div>
    </div>

    <div class="sec-stats">
      <div class="sfa-stat"><div class="num">{{ devices.length }}</div><div class="lbl">设备总数</div></div>
      <div class="sfa-stat"><div class="num">{{ countByType('af') }}</div><div class="lbl">AF 防火墙</div></div>
      <div class="sfa-stat"><div class="num">{{ countByType('ac') }}</div><div class="lbl">AC 上网行为管理</div></div>
      <div class="sfa-stat"><div class="num">{{ countByType('scp') }}</div><div class="lbl">SCP 云计算平台</div></div>
    </div>

    <div class="page-card">
      <div class="sec-filter">
        <el-select v-model="filterGroup" size="small" clearable placeholder="全部分组" style="width: 150px">
          <el-option v-for="g in groupOptions" :key="g" :label="g" :value="g" />
        </el-select>
        <span class="sec-dim">匹配 {{ filteredDevices.length }} / {{ devices.length }} 台</span>
      </div>
      <el-table :data="filteredDevices" size="small" v-loading="loading"
                :empty-text="loading ? '加载中…' : '暂无深信服设备，点击右上角「添加设备」开始接入'">
        <el-table-column prop="name" label="设备名称" min-width="140" show-overflow-tooltip>
          <template #default="{ row }"><span class="sec-name">{{ row.name }}</span></template>
        </el-table-column>
        <el-table-column label="类型" width="140">
          <template #default="{ row }">
            <el-tag size="small" :type="TYPE_META[row.type]?.tag || 'info'" effect="light">
              {{ TYPE_META[row.type]?.label || row.type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分组" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.group_name || '默认分组' }}</template>
        </el-table-column>
        <el-table-column label="设备地址" min-width="190">
          <template #default="{ row }"><span class="sec-mono">{{ row.base_url || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="账号" width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.username || '—' }}</template>
        </el-table-column>
        <el-table-column label="只读模式" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.readonly" size="small" type="warning" effect="light">只读</el-tag>
            <span v-else class="sec-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="连通性" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="testState[row.id]?.ok" size="small" type="success" effect="light" round>● 正常</el-tag>
            <el-tooltip v-else-if="testState[row.id]" :content="testState[row.id].error" placement="top">
              <el-tag size="small" type="danger" effect="light" round>✕ 失败</el-tag>
            </el-tooltip>
            <span v-else class="sec-dim">未测试</span>
          </template>
        </el-table-column>
        <el-table-column label="添加时间" width="150">
          <template #default="{ row }">{{ (row.created_at || '').slice(0, 16).replace('T', ' ') || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <div class="sec-ops">
              <el-button size="small" text type="primary" :loading="testingId === row.id"
                         @click="testSaved(row)">测试</el-button>
              <el-button size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button size="small" text type="danger" @click="removeOne(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 添加设备 -->
    <el-dialog v-model="showAdd" title="添加安全设备" width="480px" append-to-body>
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
        <el-form-item label="分组">
          <el-input v-model="form.group_name" placeholder="自定义分组，留空归入默认分组" />
        </el-form-item>
        <el-form-item v-if="testResult" label="测连接">
          <span :style="{ color: testResult.ok ? '#0E9F6E' : '#E5484D', fontSize: '12px' }">
            {{ testResult.message || testResult.error }}
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="testing" @click="testNew">测试连接</el-button>
        <el-button @click="showAdd = false; testResult = null">取消</el-button>
        <el-button type="primary" @click="addDevice">确定</el-button>
      </template>
    </el-dialog>

    <!-- 编辑设备 -->
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
        <el-form-item label="分组">
          <el-input v-model="editForm.group_name" placeholder="自定义分组，留空归入默认分组" />
        </el-form-item>
        <el-form-item v-if="editTestResult" label="测连接">
          <span :style="{ color: editTestResult.ok ? '#0E9F6E' : '#E5484D', fontSize: '12px' }">
            {{ editTestResult.message || editTestResult.error }}
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="editTesting" @click="testEdit">测试连接</el-button>
        <el-button @click="showEdit = false; editTestResult = null">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { Devices } from '../api.js'
import { store, loadDevices } from '../store.js'

const TYPE_META = {
  af: { label: 'AF 防火墙', tag: 'danger' },
  ac: { label: 'AC 行为管理', tag: 'warning' },
  scp: { label: 'SCP 云平台', tag: 'primary' },
}

const devices = computed(() => store.devices)
const loading = ref(false)
const countByType = (t) => devices.value.filter(d => d.type === t).length

// 分组筛选（设备 group_name 留空视为默认分组）
const filterGroup = ref('')
const groupOptions = computed(() =>
  [...new Set(devices.value.map(d => (d.group_name || '').trim() || '默认分组'))].sort())
const filteredDevices = computed(() =>
  !filterGroup.value ? devices.value
    : devices.value.filter(d => ((d.group_name || '').trim() || '默认分组') === filterGroup.value))

async function refresh() {
  loading.value = true
  try { await loadDevices() } catch (e) { ElMessage.error(String(e.message || e)) }
  loading.value = false
}

// AC 设备：IP → http://{ip}:9999；SCP：IP → https://{ip}
function normalizeBaseUrl(payload) {
  if (payload.type === 'ac' && payload.device_ip) payload.base_url = `http://${payload.device_ip}:9999`
  if (payload.type === 'scp' && payload.device_ip) payload.base_url = `https://${payload.device_ip}`
  return payload
}

// ---------- 添加 ----------
const showAdd = ref(false)
const form = ref({})
const testing = ref(false)
const testResult = ref(null)

function openAdd() {
  form.value = { name: '', type: 'af', mode: 'real', base_url: '', device_ip: '', username: '', password: '', readonly: false, group_name: '' }
  testResult.value = null
  showAdd.value = true
}

function preparePayload(f) {
  const payload = normalizeBaseUrl({ ...f })
  if (!payload.name) { ElMessage.warning('请填写设备名称'); return null }
  if (!payload.base_url) { ElMessage.warning('请填写设备地址'); return null }
  return payload
}

async function testNew() {
  const payload = preparePayload(form.value)
  if (!payload) return
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

async function addDevice() {
  const payload = preparePayload(form.value)
  if (!payload) return
  try {
    await Devices.add({ ...payload, group_name: (payload.group_name || '').trim() })
    await loadDevices()
    showAdd.value = false
    testResult.value = null
    ElMessage.success('设备已添加')
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

// ---------- 编辑 ----------
const showEdit = ref(false)
const editForm = ref({})
const editTesting = ref(false)
const editTestResult = ref(null)

function openEdit(row) {
  // AC/SCP 设备：从 base_url 中提取 IP 回填
  const editIp = (row.type === 'ac' || row.type === 'scp') && row.base_url
    ? row.base_url.replace(/^https?:\/\//, '').replace(/:9999$/, '') : ''
  editForm.value = {
    id: row.id, name: row.name, type: row.type, mode: row.mode || 'real',
    base_url: row.base_url || '', device_ip: editIp, username: row.username || '',
    password: '', readonly: !!row.readonly,
    group_name: (row.group_name || '').trim() || '',
  }
  editTestResult.value = null
  showEdit.value = true
}

async function testEdit() {
  const f = editForm.value
  const payload = normalizeBaseUrl({
    name: f.name, type: f.type, mode: f.mode,
    base_url: (f.type === 'af') ? f.base_url : '', device_ip: f.device_ip,
    username: f.username, password: f.password, readonly: f.readonly,
  })
  if (!payload.name) return ElMessage.warning('请填写设备名称')
  if (!payload.base_url) return ElMessage.warning('请填写设备地址')
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

async function saveEdit() {
  const f = editForm.value
  if (!f.name) return ElMessage.warning('设备名称不能为空')
  try {
    const payload = { name: f.name, type: f.type, mode: f.mode, readonly: f.readonly }
    if (f.type === 'ac' && f.device_ip) payload.base_url = `http://${f.device_ip}:9999`
    else if (f.type === 'scp' && f.device_ip) payload.base_url = `https://${f.device_ip}`
    else if (f.base_url) payload.base_url = f.base_url
    if (f.username) payload.username = f.username
    if (f.password) payload.password = f.password
    payload.group_name = (f.group_name || '').trim()
    await Devices.patch(f.id, payload)
    await loadDevices()
    showEdit.value = false
    editTestResult.value = null
    ElMessage.success('设备信息已更新')
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

// ---------- 测试（已保存设备） / 删除 ----------
const testingId = ref('')
const testState = ref({})

async function testSaved(row) {
  testingId.value = row.id
  try {
    const r = await Devices.test(row.id)
    testState.value[row.id] = r.ok ? { ok: true } : { ok: false, error: r.error || '连接失败' }
    if (r.ok) ElMessage.success(`${row.name} 连接正常`)
  } catch (e) {
    testState.value[row.id] = { ok: false, error: String(e.message || e) }
  }
  testingId.value = ''
}

async function removeOne(row) {
  try {
    await ElMessageBox.confirm(
      `确定要删除设备「${row.name}」吗？\n此操作不可恢复，关联的备份数据也将被删除。`,
      '删除设备',
      { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }   // 用户取消
  try {
    await Devices.remove(row.id)
    await loadDevices()
    ElMessage.success(`设备「${row.name}」已删除`)
  } catch (e) { ElMessage.error(String(e.message || e)) }
}

onMounted(refresh)
</script>

<style scoped>
.sec-page { display: flex; flex-direction: column; gap: 14px; animation: sfa-fade-up .3s var(--ease-out); }
.sec-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.sec-filter { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.sec-name { font-weight: 600; }
.sec-mono { font-family: var(--sfa-mono); font-size: 12px; font-feature-settings: "tnum" 1; }
.sec-dim { color: var(--sfa-text-4); font-size: 12px; }
.sec-ops { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0 4px; }
.sec-ops :deep(.el-button) { margin: 0; padding: 5px 0; justify-content: center; }
</style>
