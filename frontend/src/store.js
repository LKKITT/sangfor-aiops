import { reactive } from 'vue'
import { Devices, Health } from './api.js'

// 全局状态：当前设备、健康信息
export const store = reactive({
  devices: [],
  currentDeviceId: '',
  health: { llm_configured: false, readonly_mode: false },
  view: 'chat'
})

export async function loadDevices() {
  store.devices = await Devices.list()
  if (!store.currentDeviceId && store.devices.length) {
    store.currentDeviceId = store.devices[0].id
  }
}

export async function loadHealth() {
  try { store.health = await Health.get() } catch { /* 后端未启动时静默 */ }
}

export function currentDevice() {
  return store.devices.find(d => d.id === store.currentDeviceId) || null
}
