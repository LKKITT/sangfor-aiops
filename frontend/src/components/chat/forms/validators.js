// 确认卡片表单校验规则：与后端 guardrails/写翻译的口径保持一致——
// 形似 IP/网段/范围/端口的项做严格校验，纯文本（地址组名/服务名）放行。

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/

export const isIPv4 = (s) => IPV4.test(String(s || '').trim())

const CIDR_PART = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}\/(\d{1,2})$/
const RANGE_PART = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}-(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/
const IP_LIKE = /^\d{1,3}(\.\d{1,3}){0,3}$/

export function splitField(value) {
  return String(value || '').replace(/，/g, ',').split(',').map(p => p.trim()).filter(Boolean)
}

// 地址类字段（src_addr/dst_addr）：形似 IP/CIDR/范围的项严格校验，其余视为地址组名放行
export function validAddrField(rule, value, cb) {
  for (const p of splitField(value)) {
    if (CIDR_PART.test(p)) {
      const bits = Number(p.split('/')[1])
      if (bits > 32) return cb(new Error(`「${p}」网段前缀长度不合法（0-32）`))
    } else if (RANGE_PART.test(p)) {
      const [a, b] = p.split('-')
      if (!isIPv4(a) || !isIPv4(b)) return cb(new Error(`「${p}」不是合法的 IP 范围`))
    } else if (IP_LIKE.test(p)) {
      if (!isIPv4(p)) return cb(new Error(`「${p}」不是合法的 IP 地址`))
    }
  }
  cb()
}

// 对象成员字段（members）：必须全部为 IP/网段/范围（不允许组名）
export function validMembersField(rule, value, cb) {
  const parts = splitField(value)
  if (!parts.length) return cb(new Error('请填写成员地址'))
  for (const p of parts) {
    if (!(CIDR_PART.test(p) || RANGE_PART.test(p) || isIPv4(p))) {
      return cb(new Error(`「${p}」不是合法的 IP/网段/范围（如 10.0.0.0/24、192.168.1.5、10.0.0.1-10.0.0.10）`))
    }
  }
  cb()
}

// 端口字段：80 / 8000-9000 / 80,443 / 8000-9000,9090（1-65535 且起始≤结束）
export function validPortField(rule, value, cb) {
  for (const p of splitField(value)) {
    const m = p.match(/^(\d{1,5})(?:-(\d{1,5}))?$/)
    if (!m) return cb(new Error(`「${p}」端口格式应为 80 或 8000-9000`))
    const a = Number(m[1]), b = Number(m[2] ?? m[1])
    if (a < 1 || b > 65535 || a > b) return cb(new Error(`「${p}」端口范围不合法（1-65535 且起始≤结束）`))
  }
  cb()
}

// MAC 地址：11-22-33-44-55-66 或 11:22:33:44:55:66
export function validMac(rule, value, cb) {
  const v = String(value || '').trim()
  if (v && !/^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$/.test(v)) {
    return cb(new Error('MAC 格式应为 11-22-33-44-55-66'))
  }
  cb()
}

export const required = (msg) => ({ required: true, message: msg, trigger: 'blur' })
