/**
 * 网络接口名简写：把各厂商全称/长写压缩成紧凑展示形式。
 *
 * 目标（用户明确要求）：
 *   GE1/0/11   → g0/0/11      （千兆以太网，首段槽位省略、前缀小写单字母）
 *   XGE1/1     → XG0/0/1      （万兆，保留 XG 大写，槽位补齐为 0/0/N）
 *   GE1/0/24   → g0/0/24
 *   XGE1/1/1   → XG0/0/1/1
 *
 * 设计要点：
 * 1) 前缀映射成短码（GIGABITETHERNET→g、10GIGABITETHERNET→XG …），
 *    其中万兆及以上保留大写以区分于千兆（g vs XG 视觉上能一眼分开）。
 * 2) 数字部分统一为「槽位/子槽位/端口」三段：不足补 0，多余的保留。
 *    首段（通常是机箱/槽位）在单机设备上恒为 1，展示时省略。
 * 3) 原样是纯数字端口（如 `1`、`22`）时不做处理，避免把 VLAN 口等误改。
 */

// 全称 → 短码。顺序重要：必须先匹配更长的前缀（100G 早于 10G 早于 GE）。
// 注意：数字部分已被前置正则剥离，这里的前缀**不含数字**，
// 因此不能用 `(?=\d)` 前瞻（那样永远匹配不上）。
const ABBR = [
  [/^XGIGABITETHERNET/i, 'XG'],
  [/^TENGIGABITETHERNET/i, 'XG'],
  [/^XGE/i, 'XG'],
  [/^(100|40|25|10)GIGABITETHERNET/i, (m) => `${m[1]}G`],
  [/^(100|40|25|10)GE/i, (m) => `${m[1]}G`],
  [/^GIGABITETHERNET/i, 'g'],
  [/^GE/i, 'g'],
  [/^FASTETHERNET/i, 'f'],
  [/^FE/i, 'f'],
  [/^MGIGABITETHERNET/i, 'mG'],
  [/^MGE/i, 'mG'],
  [/^ETHERNET/i, 'e'],
  [/^ETH/i, 'e'],
  [/^TUNNEL/i, 'tun'],
  [/^VLANIF/i, 'vlan'],
  [/^LOOPBACK/i, 'lo'],
  [/^NULL/i, 'null'],
  [/^PORTCHANNEL/i, 'Po'],
  [/^ETHTRUNK/i, 'Eth-Trunk'],
  [/^BAGG/i, 'bagg'],
  [/^AGG/i, 'agg'],
]

/**
 * 把接口名转为紧凑展示形式。无法识别时原样返回（保证不丢信息）。
 */
export function shortPort(name) {
  const raw = String(name || '').trim()
  if (!raw) return ''
  // 去掉厂商全称里的空格与连字符（`Ten-GigabitEthernet` → `TenGigabitEthernet`）
  const s = raw.replace(/[\s-]+/g, '')
  // 直接从整串剥离尾部数字段：`100GE1/1` → 前缀 `100GE`、数字 `1/1`。
  // 注意不能用 `^[A-Za-z]` 起头——`100GE` 以数字开头会被误拒。
  const m = s.match(/^(.+?)(\d+(?:\/\d+)*)$/)
  if (!m) return raw
  const prefix = m[1]
  const nums = m[2]
  // 前缀必须含字母（纯数字串如 `22` 不是接口名，原样返回）
  if (!/[A-Za-z]/.test(prefix)) return raw

  // 前缀归一
  let short = null
  for (const [re, to] of ABBR) {
    if (re.test(prefix)) {
      short = typeof to === 'function' ? to(prefix.match(re)) : to
      break
    }
  }
  if (!short) return raw

  // 数字段归一：目标统一为 `0/子槽位/端口` 的展示形式。
  //  - `GE1/0/11`（三段）→ 首段是机箱号（单机恒为 1），省略 → 0/0/11
  //  - `XGE1/1`（两段）→ 视作「槽位/端口」，槽位补 0 → 0/0/1
  //  - `GE1/1/1/1`（四段）→ 省略首段 → 0/1/1/1
  const parts = nums.split('/').filter(p => p !== '')
  if (!parts.length) return raw
  if (parts.some(p => !/^\d+$/.test(p))) return raw
  const segs = parts.map(p => String(parseInt(p, 10)))
  let tail
  if (segs.length === 1) {
    // GE5 → 只有一个端口号
    tail = `0/${segs[0]}`
  } else {
    // 两段及以上：丢弃首段机箱号，其余按原层级展示，
    // 两段情形需补齐子槽位 0（XGE1/1 → 0/0/1）
    const rest = segs.slice(1)
    tail = rest.length === 1 ? `0/${rest[0]}` : rest.join('/')
  }
  return `${short}0/${tail}`
}

/**
 * 接口对的展示文本：`g0/0/11 ↔ g0/0/24`
 * 用于链路 hover 时在连线中点浮出。
 */
export function shortPortPair(from, to) {
  const a = shortPort(from)
  const b = shortPort(to)
  if (!a && !b) return ''
  if (!a) return b
  if (!b) return a
  return a === b ? a : `${a} ↔ ${b}`
}
