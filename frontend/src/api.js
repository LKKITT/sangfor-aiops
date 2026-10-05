// 后端 API 封装
const BASE = ''

// 请求超时：手写 AbortController（兼容性优于 AbortSignal.timeout），超时抛中文错误
function timeoutSignal(ms, reason) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(reason || new Error(`请求超时（${Math.round(ms / 1000)}s），请稍后重试`)), ms)
  return { signal: ctrl.signal, done: () => clearTimeout(timer) }
}

async function readError(resp) {
  return (await resp.json().catch(() => ({}))).detail || `HTTP ${resp.status}`
}

// 请求内核：超时注册 + !ok 抛错（readError 优先取后端 detail）+ JSON 解析
async function request(method, path, body, timeout = 45000) {
  const { signal, done } = timeoutSignal(timeout)
  try {
    const resp = await fetch(BASE + path, {
      method,
      ...(body !== undefined
        ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
        : {}),
      signal
    })
    if (!resp.ok) throw new Error(await readError(resp))
    return resp.json()
  } finally {
    done()
  }
}

export const apiGet = (path, timeout = 45000) => request('GET', path, undefined, timeout)
export const apiPost = (path, body = {}, timeout = 45000) => request('POST', path, body, timeout)
export const apiDelete = (path, timeout = 45000) => request('DELETE', path, undefined, timeout)

/**
 * SSE 流式对话。onEvent(event) 回调每个事件对象。
 */
export async function chatStream(path, body, onEvent, signal) {
  const resp = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal
  })
  if (!resp.ok || !resp.body) throw new Error(`连接失败 HTTP ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      for (const line of raw.split('\n')) {
        if (line.startsWith('data: ')) {
          try { onEvent(JSON.parse(line.slice(6))) } catch { /* 忽略坏帧 */ }
        }
      }
    }
  }
}

// ---------- 业务 API ----------

export const apiPut = (path, body = {}, timeout = 45000) => request('PUT', path, body, timeout)
export const apiPatch = (path, body = {}, timeout = 45000) => request('PATCH', path, body, timeout)

export const Health = { get: () => apiGet('/api/health') }
export const Settings = {
  get: () => apiGet('/api/settings'),
  save: (d) => apiPost('/api/settings', d),
  // ---- MCP 服务管理 ----
  mcpList: () => apiGet('/api/settings/mcp'),
  mcpSave: (server) => apiPut('/api/settings/mcp', server),
  mcpDelete: (id) => apiDelete(`/api/settings/mcp/${id}`),
  mcpTest: (server) => apiPost('/api/settings/mcp/test', server, 60000),
  mcpImport: (text, enabled = false) => apiPost('/api/settings/mcp/import', { text, enabled }),
  mcpRegistry: (search) => apiGet(`/api/settings/mcp/registry?search=${encodeURIComponent(search)}`, 30000),
  mcpRegistryInstall: (item, enabled = true) =>
    apiPost('/api/settings/mcp/registry/install', { item, enabled }),
  // ---- Agent Skills 管理 ----
  skillsList: () => apiGet('/api/settings/agent-skills'),
  skillsToggle: (folder, enabled) =>
    apiPost('/api/settings/agent-skills/toggle', { folder, enabled }),
  skillsImport: (url, force = false) =>
    apiPost('/api/settings/agent-skills/import', { url, force }, 60000),
  skillsDelete: (folder) => apiDelete(`/api/settings/agent-skills/${folder}`)
}
export const Devices = {
  list: () => apiGet('/api/devices'),
  add: (d) => apiPost('/api/devices', d),
  patch: (id, d) => apiPatch(`/api/devices/${id}`, d),
  remove: (id) => apiDelete(`/api/devices/${id}`),
  test: (id) => apiPost(`/api/devices/${id}/test`),
  testConnection: (d) => apiPost('/api/devices/test-connection', d),
  status: (id) => apiGet(`/api/devices/${id}/status`),
  interfaces: (id) => apiGet(`/api/devices/${id}/interfaces`),
  nat: (id) => apiGet(`/api/devices/${id}/nat`),
  acl: (id) => apiGet(`/api/devices/${id}/acl`),
  bindings: (id) => apiGet(`/api/devices/${id}/bindings`),
  objects: (id) => apiGet(`/api/devices/${id}/objects`),
  services: (id) => apiGet(`/api/devices/${id}/services`),
  snapshot: (id) => apiGet(`/api/devices/${id}/snapshot`),
  checkup: (id) => apiPost(`/api/devices/${id}/checkup`),
  lastCheckup: (id) => apiGet(`/api/devices/${id}/checkup/last`)
}
export const Backups = {
  list: (dev) => apiGet(`/api/devices/${dev}/backups`),
  create: (dev, label) => apiPost(`/api/devices/${dev}/backups`, { label }),
  remove: (dev, id) => apiDelete(`/api/devices/${dev}/backups/${id}`),
  downloadUrl: (dev, id) => `/api/devices/${dev}/backups/${id}/file`,
  reportUrl: (dev, id) => `/api/devices/${dev}/backups/${id}/report`,
  exportUrl: (dev, id) => `/api/devices/${dev}/backups/${id}/snapshot/export`,
  diff: (a, b) => apiGet(`/api/backups/diff?a=${a}&b=${b}`),
  restorePreview: (dev, id) => apiPost(`/api/devices/${dev}/backups/${id}/restore/preview`),
  restoreApply: (dev, id) => apiPost(`/api/devices/${dev}/backups/${id}/restore/apply`, { confirm: true })
}
export const Updates = {
  overview: (dev) => apiGet(`/api/devices/${dev}/updates`),
  advice: (dev) => apiGet(`/api/devices/${dev}/upgrade-advice`),
  refresh: () => apiPost('/api/updates/refresh'),
  softwareList: (product, force = false) => apiGet(`/api/software-list?product=${product}&force=${force}`)
}
export const Audit = { list: () => apiGet('/api/chat/audit') }

export const KB = {
  stats: () => apiGet('/api/kb/stats'),
  entries: (category = '', keyword = '', order = 'created') =>
    apiGet(`/api/kb/entries?category=${encodeURIComponent(category)}&keyword=${encodeURIComponent(keyword)}&order=${encodeURIComponent(order)}`),
  entry: (id) => apiGet(`/api/kb/entries/${id}`),
  removeEntry: (id) => apiDelete(`/api/kb/entries/${id}`),
  pending: () => apiGet('/api/kb/pending'),
  dismissPending: (id) => apiDelete(`/api/kb/pending/${id}`),
  // 沉淀/反思涉及多轮 LLM 调用，超时放宽到 300s
  process: (limit = 10, convs = []) => apiPost('/api/kb/process', { limit, convs }, 300000),
  reflection: (start = '', end = '') => apiPost('/api/kb/reflection', { start, end }, 300000),
  deleteReflection: (id) => apiDelete(`/api/kb/reflections/${id}`),
  reflections: () => apiGet('/api/kb/reflections')
}

// 网络设备管理（SSH 交换机/路由器）
export const NetDev = {
  devices: (group = '') => apiGet(`/api/netdev/devices${group ? `?group=${encodeURIComponent(group)}` : ''}`),
  vendors: () => apiGet('/api/netdev/vendors'),
  add: (dev) => apiPost('/api/netdev/devices', dev, 30000),
  addBatch: (devices) => apiPost('/api/netdev/devices/batch', { devices }, 60000),
  remove: (id) => apiDelete(`/api/netdev/devices/${id}`),
  exportDevices: () => apiGet('/api/netdev/devices/export', 30000),
  test: (deviceId, timeout = 15) => apiPost('/api/netdev/devices/test', { device_id: deviceId, timeout }, 60000),
  execute: (deviceIds, commands, name = '', timeout = 30) =>
    apiPost('/api/netdev/execute', { device_ids: deviceIds, commands, name, timeout }, 30000),
  tasks: (limit = 20) => apiGet(`/api/netdev/tasks?limit=${limit}`),
  task: (id) => apiGet(`/api/netdev/tasks/${id}`),
  // 网络拓扑：LLDP/ARP 自动发现（force=1 强制重新采集，采集耗时较长）
  topology: (group = '', force = false) =>
    apiGet(`/api/netdev/topology?group=${encodeURIComponent(group)}&force=${force ? 1 : 0}`, 180000),
  topologySearch: (group = '', q = '') =>
    apiGet(`/api/netdev/topology/search?group=${encodeURIComponent(group)}&q=${encodeURIComponent(q)}`),
  saveTopologyPositions: (group = '', positions = {}) =>
    apiPost('/api/netdev/topology/positions', { group, positions }, 30000),
  // ---- 工作台运维（配置可视化/体检/备份；全部只读，无恢复） ----
  snapshot: (deviceId, force = false) =>
    apiGet(`/api/netdev/${deviceId}/snapshot${force ? '?force=1' : ''}`, 120000),
  checkup: (deviceId, force = false) =>
    apiPost(`/api/netdev/${deviceId}/checkup?force=${force ? 1 : 0}`, {}, 90000),
  backups: (deviceId) => apiGet(`/api/netdev/${deviceId}/backups`),
  createBackup: (deviceId, label) =>
    apiPost(`/api/netdev/${deviceId}/backups`, { label }, 90000),
  backupDiff: (deviceId, a, b) =>
    apiGet(`/api/netdev/${deviceId}/backups/diff?a=${a}&b=${b}`, 30000),
  backupFileUrl: (deviceId, backupId) => `/api/netdev/${deviceId}/backups/${backupId}/file`,
  deleteBackup: (deviceId, backupId) =>
    apiDelete(`/api/netdev/${deviceId}/backups/${backupId}`),
}
