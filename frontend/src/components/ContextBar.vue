<template>
  <div class="ctx-bar">
    <el-icon class="ctx-ico"><Monitor /></el-icon>
    <span class="ctx-name">{{ device?.name || '未选择设备' }}</span>
    <el-tag size="small" :type="badgeType" effect="plain">{{ badgeText }}</el-tag>
    <el-tag v-if="showReadonly" size="small" type="warning" effect="plain">只读模式</el-tag>
    <el-select v-model="store.currentDeviceId" size="small" class="ctx-select" placeholder="切换目标设备">
      <el-option :value="GLOBAL_DEVICE_ID" label="全局（所有设备）" />
      <el-option-group label="深信服设备">
        <el-option v-for="d in store.devices" :key="d.id" :value="d.id"
                   :label="d.name" />
      </el-option-group>
      <el-option-group v-if="aiNetdevs().length" label="网络设备（华为/H3C/锐捷）">
        <el-option v-for="d in aiNetdevs()" :key="d.id" :value="d.id"
                   :label="`${d.name}（${vendorName(d.vendor)}）`" />
      </el-option-group>
    </el-select>
  </div>
</template>

<script setup>
// 设备上下文条：工作台各页页头的"我在看谁"强提示，内联切换与侧栏/输入台三方同步。
import { computed } from 'vue'
import { store, currentDevice, isGlobal, isNetDev, GLOBAL_DEVICE_ID, aiNetdevs, NETDEV_VENDOR_NAMES } from '../store.js'

const vendorName = (v) => NETDEV_VENDOR_NAMES[v] || v
const device = computed(currentDevice)

const isNetdevCtx = computed(() => isNetDev(device.value))
const badgeText = computed(() => {
  if (isGlobal(device.value)) return '全局模式'
  if (isNetdevCtx.value) return `网络设备 · ${vendorName(device.value.vendor)}`
  return { af: '防火墙', scp: '云计算平台', ac: '上网行为管理' }[device.value?.type] || device.value?.type
})
const badgeType = computed(() => (isGlobal(device.value) ? 'success' : isNetdevCtx.value ? 'warning' : 'primary'))
const showReadonly = computed(() => device.value && !isGlobal(device.value) && !isNetdevCtx.value && !!device.value.readonly)
</script>

<style scoped>
.ctx-bar {
  display: flex; align-items: center; gap: 8px; margin-bottom: 10px;
  padding: 7px 11px; border-radius: 10px;
  background: var(--sfa-surface); border: 1px solid var(--sfa-border-soft, #E6E9F2);
}
.ctx-ico { color: var(--sfa-primary, #4A70FF); }
.ctx-name { font-weight: 650; font-size: 13.5px; }
.ctx-select { margin-left: auto; width: 220px; }
</style>
