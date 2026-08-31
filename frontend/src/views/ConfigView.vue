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
        <el-alert v-else-if="loadErrors.status" type="error" :closable="false"
                  :title="`状态读取失败：${loadErrors.status}`" />
        <el-empty v-else description="加载中" />
      </el-tab-pane>

      <el-tab-pane label="网络接口" name="interfaces">
        <el-alert v-if="interfaces.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                  :title="emptyHint('interfaces')" />
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
        <el-alert v-if="nat.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                  :title="emptyHint('nat')" />
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
        <el-alert v-if="acl.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                  :title="emptyHint('acl')" />
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

      <el-tab-pane :label="`静态路由（${routes.length}）`" name="routes">
        <el-alert v-if="routes.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                  :title="emptyHint('routes')" />
        <el-table :data="routes" size="small" border stripe>
          <el-table-column prop="id" label="ID" width="110" />
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column label="目的网段" min-width="150">
            <template #default="{ row }"><span class="mono">{{ row.dst }}</span></template>
          </el-table-column>
          <el-table-column label="下一跳" min-width="130">
            <template #default="{ row }"><span class="mono">{{ row.next_hop }}</span></template>
          </el-table-column>
          <el-table-column prop="interface" label="出接口" width="100" />
          <el-table-column prop="distance" label="优先级" width="80" />
          <el-table-column label="启用" width="70">
            <template #default="{ row }"><el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '是' : '否' }}</el-tag></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`网络对象（${objects.length}）`" name="objects">
        <el-alert v-if="objects.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                  :title="emptyHint('objects')" />
        <el-table :data="objects" size="small" border stripe>
          <el-table-column prop="id" label="ID" width="90" />
          <el-table-column prop="name" label="对象名称" min-width="140" />
          <el-table-column prop="type" label="类型" width="90" />
          <el-table-column label="成员地址" min-width="220">
            <template #default="{ row }"><span class="mono">{{ row.members }}</span></template>
          </el-table-column>
          <el-table-column prop="comment" label="备注" min-width="150" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`自定义服务（${services.length}）`" name="services">
        <el-alert v-if="services.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                  :title="emptyHint('services')" />
        <el-table :data="services" size="small" border stripe>
          <el-table-column prop="id" label="ID" width="90" />
          <el-table-column prop="name" label="服务名称" min-width="140" />
          <el-table-column prop="protocol" label="协议" width="90">
            <template #default="{ row }"><el-tag size="small">{{ row.protocol }}</el-tag></template>
          </el-table-column>
          <el-table-column label="端口" min-width="160">
            <template #default="{ row }"><span class="mono">{{ row.ports }}</span></template>
          </el-table-column>
          <el-table-column prop="comment" label="备注" min-width="150" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`用户绑定（${bindings.length}）`" name="bindings">
        <div style="display: flex; gap: 8px; margin-bottom: 8px">
          <el-input v-model="bindKeyword" placeholder="按用户名 / IP / MAC 搜索（AC 开放接口需指定搜索词）"
                    size="small" clearable style="max-width: 420px" @keyup.enter="searchBindings" />
          <el-button size="small" type="primary" :loading="bindSearching" @click="searchBindings">搜索</el-button>
          <el-button v-if="bindKeyword" size="small" text @click="clearBindSearch">清除</el-button>
        </div>
        <el-alert v-if="bindings.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                  :title="bindHint" />
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
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { store, currentDevice } from '../store.js'
import { Devices } from '../api.js'

const tab = ref('status')
const status = ref(null)
const interfaces = ref([])
const nat = ref([])
const acl = ref([])
const bindings = ref([])
const objects = ref([])
const services = ref([])
const routes = ref([])
const loadErrors = ref({})
const bindKeyword = ref('')
const bindSearching = ref(false)
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

// 单个数据源失败不影响其它面板；错误分别展示
async function fetchOne(dev, label, path, assign) {
  try {
    const r = await fetch(`/api/devices/${dev.id}/${path}`).then(resp => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      return resp.json()
    })
    assign(r)
    delete loadErrors.value[label]
  } catch (e) {
    loadErrors.value = { ...loadErrors.value, [label]: String(e.message || e) }
  }
}

async function load() {
  const dev = currentDevice()
  if (!dev) return
  loadErrors.value = {}
  await Promise.allSettled([
    fetchOne(dev, 'status', 'status', r => { status.value = r }),
    fetchOne(dev, 'interfaces', 'interfaces', r => { interfaces.value = r }),
    fetchOne(dev, 'nat', 'nat', r => { nat.value = r }),
    fetchOne(dev, 'acl', 'acl', r => { acl.value = r }),
    fetchOne(dev, 'bindings', 'bindings', r => { bindings.value = r }),
    fetchOne(dev, 'objects', 'objects', r => { objects.value = r }),
    fetchOne(dev, 'services', 'services', r => { services.value = r }),
    fetchOne(dev, 'routes', 'routes', r => { routes.value = r })
  ])
  if (tab.value === 'interfaces') await renderTraffic()
}

async function searchBindings() {
  const dev = currentDevice()
  if (!dev) return
  bindSearching.value = true
  await fetchOne(dev, 'bindings', `bindings?keyword=${encodeURIComponent(bindKeyword.value)}`,
                 r => { bindings.value = r })
  bindSearching.value = false
}

function clearBindSearch() {
  bindKeyword.value = ''
  load()
}

function emptyHint(section) {
  const dev = currentDevice()
  if (dev && dev.type === 'ac') {
    return 'AC 经开放接口仅提供 状态 / 用户绑定 / 策略 / 在线用户 数据，此项配置不在开放接口范围内'
  }
  if (loadErrors.value[section]) return `读取失败：${loadErrors.value[section]}`
  return '该设备当前无此配置数据'
}

const bindHint = computed(() => {
  const dev = currentDevice()
  if (bindKeyword.value) return `未找到与「${bindKeyword.value}」匹配的绑定记录`
  if (dev && dev.type === 'ac') return 'AC 开放接口按用户名/IP/MAC 搜索绑定关系，请在上方输入关键词查询'
  if (loadErrors.value.bindings) return `读取失败：${loadErrors.value.bindings}`
  return '该设备当前无绑定数据'
})

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
