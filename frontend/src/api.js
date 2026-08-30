// 后端 API 封装
const BASE = ''

export async function apiGet(path) {
  const resp = await fetch(BASE + path)
  if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || `HTTP ${resp.status}`)
  return resp.json()
}

export async function apiPost(path, body = {}) {
  const resp = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || `HTTP ${resp.status}`)
  return resp.json()
}

export async function apiDelete(path) {
  const resp = await fetch(BASE + path, { method: 'DELETE' })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

/**
 * SSE 流式对话。onEvent(event) 回调每个事件对象。
 */
export async function chatStream(path, body, onEvent) {
  const resp = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
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

export const Health = { get: () => apiGet('/api/health') }
export const Devices = {
  list: () => apiGet('/api/devices'),
  add: (d) => apiPost('/api/devices', d),
  patch: (id, d) => fetch(`/api/devices/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) }).then(r => r.json()),
  remove: (id) => apiDelete(`/api/devices/${id}`),
  test: (id) => apiPost(`/api/devices/${id}/test`),
  status: (id) => apiGet(`/api/devices/${id}/status`),
  interfaces: (id) => apiGet(`/api/devices/${id}/interfaces`),
  nat: (id) => apiGet(`/api/devices/${id}/nat`),
  acl: (id) => apiGet(`/api/devices/${id}/acl`),
  bindings: (id) => apiGet(`/api/devices/${id}/bindings`),
  snapshot: (id) => apiGet(`/api/devices/${id}/snapshot`),
  checkup: (id) => apiPost(`/api/devices/${id}/checkup`),
  lastCheckup: (id) => apiGet(`/api/devices/${id}/checkup/last`)
}
export const Backups = {
  list: (dev) => apiGet(`/api/devices/${dev}/backups`),
  create: (dev, label) => apiPost(`/api/devices/${dev}/backups`, { label }),
  remove: (dev, id) => apiDelete(`/api/devices/${dev}/backups/${id}`),
  downloadUrl: (dev, id) => `/api/devices/${dev}/backups/${id}/file`,
  diff: (a, b) => apiGet(`/api/backups/diff?a=${a}&b=${b}`),
  restorePreview: (dev, id) => apiPost(`/api/devices/${dev}/backups/${id}/restore/preview`),
  restoreApply: (dev, id) => apiPost(`/api/devices/${dev}/backups/${id}/restore/apply`, { confirm: true })
}
export const Updates = {
  overview: (dev) => apiGet(`/api/devices/${dev}/updates`),
  advice: (dev) => apiGet(`/api/devices/${dev}/upgrade-advice`),
  refresh: () => apiPost('/api/updates/refresh')
}
export const Audit = { list: () => apiGet('/api/chat/audit') }
