import { reactive } from 'vue'
import { Devices, Health, NetDev } from './api.js'

// 全局模式：不绑定单一设备，AI 对话覆盖全部深信服 + 网络设备
export const GLOBAL_DEVICE_ID = 'global'
export const GLOBAL_DEVICE = Object.freeze({
  id: GLOBAL_DEVICE_ID, name: '全局（所有设备）', type: 'global',
})

// AI 对话支持的网络设备厂家（华为/H3C/锐捷）
export const AI_NETDEV_VENDORS = ['huawei', 'h3c', 'ruijie']
export const NETDEV_VENDOR_NAMES = { huawei: '华为', h3c: 'H3C', ruijie: '锐捷', cisco: '思科', zte: '中兴' }

// 全局状态：当前设备（全局/深信服/网络设备）、健康信息
export const store = reactive({
  devices: [],
  netdevDevices: [],
  currentDeviceId: GLOBAL_DEVICE_ID,
  health: { llm_configured: false, readonly_mode: false },
  uiAddDeviceTick: 0,   // 触发 App 外壳打开「添加设备」弹窗（自增计数）
  chatSeed: null,       // { text, useKnowledge, tick } 跨视图预填 AI 对话（知识库引用「去问 Agent」）
})

export function isGlobal(dev) {
  return !!dev && dev.id === GLOBAL_DEVICE_ID
}

export function isNetDev(dev) {
  return !!dev && String(dev.id || '').startsWith('nd_')
}

export async function loadDevices() {
  store.devices = await Devices.list()
  try {
    store.netdevDevices = await NetDev.devices()
  } catch { /* 网络设备接口异常不阻塞主列表 */ store.netdevDevices = [] }
  // 当前选择失效（设备被删除）时回退全局；首次进入默认全局模式
  const exists = store.currentDeviceId === GLOBAL_DEVICE_ID
      || store.devices.some(d => d.id === store.currentDeviceId)
      || store.netdevDevices.some(d => d.id === store.currentDeviceId)
  if (!exists) store.currentDeviceId = GLOBAL_DEVICE_ID
}

export async function loadHealth() {
  try { store.health = await Health.get() } catch { /* 后端未启动时静默 */ }
}

export function currentDevice() {
  if (store.currentDeviceId === GLOBAL_DEVICE_ID) return GLOBAL_DEVICE
  return store.devices.find(d => d.id === store.currentDeviceId)
      || store.netdevDevices.find(d => d.id === store.currentDeviceId) || null
}

// AI 对话可选的网络设备（限华为/H3C/锐捷；其他厂家在网络设备管理页操作）
export function aiNetdevs() {
  return store.netdevDevices.filter(d => AI_NETDEV_VENDORS.includes(d.vendor))
}
