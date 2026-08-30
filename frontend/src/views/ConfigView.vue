<template>
  <div class="config-page">
    <el-tabs v-model="tab" class="page-card">
      <el-tab-pane label="设备状态" name="status">
        <div v-if="status" class="status-grid">
          <div class="stat"><div class="stat-label">软件版本</div><div class="stat-value">{{ status.sw_version }}</div></div>
          <div class="stat"><div class="stat-label">型号</div><div class="stat-value">{{ status.model }}</div></div>
          <div class="stat"><div class="stat-label">运行时间</div><div class="stat-value sm">{{ status.uptime }}</div></div>
          <div class="stat"><div class="stat-label">HA 状态</div><div class="stat-value">{{ status.ha_status }}</div></div>
          <div class="stat" v-for="m in meters" :key="m.key">
            <div class="stat-label">{{ m.label }}</div>
            <el-progress :percentage="status[m.key]" :color="meterColor(status[m.key])" :stroke-width="14" />
          </div>
          <div class="stat">
            <div class="stat-label">会话数</div>
            <div class="stat-value">{{ status.session_count.toLocaleString() }} / {{ status.session_capacity.toLocaleString() }}</div>
          </div>
        </div>
        <el-empty v-else description="加载中" />
      </el-tab-pane>

      <el-tab-pane label="网络接口" name="interfaces">
        <el-table :data="interfaces" size="small" border stripe>
          <el-table-column prop="name" label="接口" width="90" />
          <el-table-column prop="zone" label="区域" width="90">
            <template #default="{ row }"><el-tag size="small" :type="row.zone === 'untrust' ? 'danger' : row.zone === 'trust' ? 'success' : 'info'">{{ row.zone || '未划分' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="IP 地址">
            <template #default="{ row }"><span class="mono">{{ row.ip || '-' }}{{ row.netmask ? '/' + row.netmask : '' }}</span></template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="80">
            <template #default="{ row }"><el-tag size="small" :type="row.status === 'up' ? 'success' : 'info'">{{ row.status }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="speed" label="速率" width="80" />
          <el-table-column label="收 / 发 (kbps)" min-width="160">
            <template #default="{ row }">
              <span class="mono">{{ row.rx_kbps }} / {{ row.tx_kbps }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="comment" label="备注" min-width="140" />
        </el-table>
        <div ref="trafficChart" style="height: 260px; margin-top: 12px"></div>
      </el-tab-pane>

      <el-tab-pane :label="`NAT 策略（${nat.length}）`" name="nat">
        <el-table :data="nat" size="small" border stripe>
          <el-table-column prop="id" label="ID" width="90" />
          <el-table-column prop="name" label="名称" min-width="150" />
          <el-table-column prop="type" label="类型" width="80">
            <template #default="{ row }"><el-tag size="small" :type="row.type === 'SNAT' ? 'primary' : 'warning'">{{ row.type }}</el-tag></template>
          </el-table-column>
          <el-table-column label="源区域/地址" min-width="150">
            <template #default="{ row }"><span class="mono">{{ row.src_zone }}：{{ row.src_addr }}</span></template>
          </el-table-column>
          <el-table-column label="目的" min-width="130">
            <template #default="{ row }"><span class="mono">{{ row.dst_zone }}：{{ row.dst_addr }}</span></template>
          </el-table-column>
          <el-table-column label="转换后" min-width="140">
            <template #default="{ row }"><span class="mono">{{ row.translated_addr }}{{ row.translated_port ? ':' + row.translated_port : '' }}</span></template>
          </el-table-column>
          <el-table-column prop="hit_count" label="命中" width="90" />
          <el-table-column label="启用" width="70">
            <template #default="{ row }"><el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '是' : '否' }}</el-tag></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`访问控制策略（${acl.length}）`" name="acl">
        <el-table :data="acl" size="small" border stripe>
          <el-table-column type="index" label="#" width="50" />
          <el-table-column prop="id" label="ID" width="90" />
          <el-table-column prop="name" label="名称" min-width="160" />
          <el-table-column label="源" min-width="130">
            <template #default="{ row }"><span class="mono">{{ row.src_zone }}：{{ row.src_addr }}</span></template>
          </el-table-column>
          <el-table-column label="目的" min-width="130">
            <template #default="{ row }"><span class="mono">{{ row.dst_zone }}：{{ row.dst_addr }}</span></template>
          </el-table-column>
          <el-table-column prop="service" label="服务" min-width="110" />
          <el-table-column prop="action" label="动作" width="80">
            <template #default="{ row }"><el-tag size="small" :type="row.action === 'allow' ? 'success' : 'danger'">{{ row.action }}</el-tag></template>
          </el-table-column>
          <el-table-column label="日志" width="70">
            <template #default="{ row }">{{ row.log ? '✓' : '✗' }}</template>
          </el-table-column>
          <el-table-column prop="hit_count" label="命中" width="90" />
          <el-table-column label="启用" width="70">
            <template #default="{ row }"><el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '是' : '否' }}</el-tag></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`用户绑定（${bindings.length}）`" name="bindings">
        <el-table :data="bindings" size="small" border stripe>
          <el-table-column prop="user" label="用户" min-width="140" />
          <el-table-column prop="ip" label="IP" min-width="130" />
          <el-table-column prop="mac" label="MAC" min-width="150" />
          <el-table-column prop="binding_type" label="类型" width="90" />
          <el-table-column label="启用" width="70">
            <template #default="{ row }"><el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '是' : '否' }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="comment" label="备注" min-width="120" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { store, currentDevice } from '../store.js'
import { Devices } from '../api.js'

const tab = ref('status')
const status = ref(null)
const interfaces = ref([])
const nat = ref([])
const acl = ref([])
const bindings = ref([])
const trafficChart = ref(null)
let chart = null
let timer = null

const meters = [
  { key: 'cpu_usage', label: 'CPU 使用率' },
  { key: 'memory_usage', label: '内存使用率' },
  { key: 'disk_usage', label: '磁盘使用率' },
  { key: 'mbuf_usage', label: 'mbuf 缓冲池' }
]
const meterColor = v => (v >= 85 ? '#f56c6c' : v >= 70 ? '#e6a23c' : '#67c23a')

async function load() {
  const dev = currentDevice()
  if (!dev) return
  try {
    const [s, itf, n, a, b] = await Promise.all([
      Devices.status(dev.id), Devices.interfaces(dev.id),
      Devices.nat(dev.id), Devices.acl(dev.id), Devices.bindings(dev.id)
    ])
    status.value = s
    interfaces.value = itf
    nat.value = n
    acl.value = a
    bindings.value = b
    if (tab.value === 'interfaces') await renderTraffic()
  } catch (e) { console.error(e) }
}

async function renderTraffic() {
  await nextTick()
  if (!trafficChart.value) return
  chart = chart || echarts.init(trafficChart.value)
  const names = interfaces.value.map(i => i.name)
  chart.setOption({
    title: { text: '接口实时流量', left: 'center', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0 },
    grid: { left: 60, right: 20, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: names },
    yAxis: { type: 'value', name: 'kbps' },
    series: [
      { name: '收流量', type: 'bar', data: interfaces.value.map(i => i.rx_kbps), itemStyle: { color: '#409eff' } },
      { name: '发流量', type: 'bar', data: interfaces.value.map(i => i.tx_kbps), itemStyle: { color: '#67c23a' } }
    ]
  })
}

watch(tab, v => { if (v === 'interfaces') renderTraffic() })
watch(() => store.currentDeviceId, load)
onMounted(() => { load(); timer = setInterval(() => { if (tab.value === 'status' || tab.value === 'interfaces') load() }, 8000) })
onUnmounted(() => { clearInterval(timer); chart?.dispose() })
</script>

<style scoped>
.status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
.stat { background: #f8f9fb; border-radius: 8px; padding: 12px 14px; }
.stat-label { color: #909399; font-size: 12px; margin-bottom: 6px; }
.stat-value { font-size: 18px; font-weight: 600; }
.stat-value.sm { font-size: 13px; }
</style>
