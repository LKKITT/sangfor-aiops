<template>
  <div class="config-page">
    <div class="config-toolbar">
      <span class="config-toolbar-title">配置数据缓存于页面，点击刷新重新获取</span>
      <el-button size="small" :loading="refreshing" @click="load" icon="Refresh">刷新</el-button>
    </div>
    <el-tabs v-model="tab" class="page-card">
      <!-- ========== 设备状态（AF / AC 通用） ========== -->
      <el-tab-pane label="设备状态" name="status">
        <div v-if="status" class="status-wrap">
          <!-- 系统信息 -->
          <div class="status-section">
            <div class="status-section-title">系统信息</div>
            <div class="status-grid">
              <div class="stat"><div class="stat-label">软件版本</div><div class="stat-value">{{ status.sw_version }}</div></div>
              <div class="stat"><div class="stat-label">型号</div><div class="stat-value">{{ status.model }}</div></div>
              <div class="stat"><div class="stat-label">运行时间</div><div class="stat-value sm">{{ status.uptime }}</div></div>
              <div class="stat" v-if="isAF"><div class="stat-label">HA 状态</div><div class="stat-value">{{ status.ha_status }}</div></div>
            </div>
          </div>
          <!-- 资源与用户 -->
          <div class="status-section">
            <div class="status-section-title">资源与用户</div>
            <div class="status-grid">
              <div class="stat" v-for="m in meters" :key="m.key">
                <div class="stat-label">{{ m.label }}</div>
                <el-progress :percentage="status[m.key]" :color="meterColor(status[m.key])" :stroke-width="14" />
              </div>
              <div class="stat">
                <div class="stat-label">会话数</div>
                <div class="stat-value">{{ status.session_count?.toLocaleString() || 0 }} / {{ status.session_capacity?.toLocaleString() || 'N/A' }}</div>
              </div>
              <div class="stat" v-if="isAC && status.extra?.online_users != null">
                <div class="stat-label">在线用户</div>
                <div class="stat-value">{{ status.extra.online_users?.toLocaleString() || 0 }}</div>
              </div>
            </div>
          </div>
          <!-- 流量概况（AC） -->
          <div class="status-section" v-if="isAC">
            <div class="status-section-title">流量概况</div>
            <div class="status-grid">
              <div class="stat">
                <div class="stat-label">上行吞吐量</div>
                <div class="stat-value">{{ fmtThroughput(acThroughput.up_throughput || acThroughput.upstream || 0) }}</div>
              </div>
              <div class="stat">
                <div class="stat-label">下行吞吐量</div>
                <div class="stat-value">{{ fmtThroughput(acThroughput.down_throughput || acThroughput.downstream || 0) }}</div>
              </div>
              <div class="stat">
                <div class="stat-label">总吞吐量</div>
                <div class="stat-value">{{ fmtThroughput(acThroughput.total || (acThroughput.up_throughput || 0) + (acThroughput.down_throughput || 0)) }}</div>
              </div>
              <div class="stat" v-if="status.extra?.bandwidth_usage != null">
                <div class="stat-label">带宽使用率</div>
                <el-progress :percentage="Math.min(100, Math.round(status.extra.bandwidth_usage))" :color="meterColor(status.extra.bandwidth_usage)" :stroke-width="14" />
              </div>
            </div>
          </div>
        </div>
        <el-alert v-else-if="loadErrors.status" type="error" :closable="false"
                  :title="`状态读取失败：${loadErrors.status}`" />
        <el-empty v-else description="加载中" />
      </el-tab-pane>

      <!-- ========== AF 特有 tabs ========== -->
      <template v-if="isAF">
        <el-tab-pane label="网络接口" name="interfaces">
          <el-alert v-if="interfaces.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                    :title="emptyHint('interfaces')" />
          <el-table :data="interfaces" size="small" border stripe>
            <el-table-column prop="name" label="接口" width="90" />
            <el-table-column prop="zone" label="区域" width="90">
              <template #default="{ row }"><el-tag size="small" :type="row.zone === 'untrust' ? 'danger' : row.zone === 'trust' ? 'success' : 'info'">{{ row.zone || '未划分' }}</el-tag></template>
            </el-table-column>
            <el-table-column label="IP 地址" min-width="200">
              <template #default="{ row }">
                <span class="mono">{{ row.ip || '-' }}{{ row.netmask ? '/' + row.netmask : '' }}</span>
                <template v-if="row.extra_ips">
                  <br><span style="color: #909399; font-size: 12px">附加：</span><span class="mono" style="font-size: 12px">{{ row.extra_ips }}</span>
                </template>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'up' ? 'success' : row.status === 'enabled' ? '' : 'info'">
                  {{ row.status === 'up' ? '运行中' : row.status === 'enabled' ? '已启用' : '未运行' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="speed" label="速率" width="80" />
            <el-table-column prop="comment" label="备注" min-width="140" />
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`安全区域（${zones.length}）`" name="zones">
          <el-alert v-if="zones.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                    :title="emptyHint('zones')" />
          <el-table :data="zones" size="small" border stripe>
            <el-table-column prop="name" label="区域名称" width="120">
              <template #default="{ row }"><el-tag size="small" :type="row.name === 'untrust' ? 'danger' : row.name === 'trust' ? 'success' : 'info'">{{ row.name }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="type" label="类型" width="90">
              <template #default="{ row }">{{ row.type || row.securityZoneType || 'L3' }}</template>
            </el-table-column>
            <el-table-column label="绑定接口" min-width="200">
              <template #default="{ row }">
                <el-tag v-for="iface in (row.interfaces || [])" :key="iface" size="small" style="margin: 2px">{{ iface }}</el-tag>
                <span v-if="!row.interfaces?.length" style="color: #909399">无绑定接口</span>
              </template>
            </el-table-column>
            <el-table-column prop="member" label="成员" min-width="160">
              <template #default="{ row }">
                <span class="mono">{{ (row.members || []).join(', ') || row.member || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="描述" min-width="140">
              <template #default="{ row }">{{ row.description || row.desc || '-' }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`NAT 策略（${nat.length}）`" name="nat">
          <el-alert v-if="nat.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                    :title="emptyHint('nat')" />
          <el-table :data="nat" size="small" border stripe>
            <el-table-column prop="id" label="ID" width="90" />
            <el-table-column prop="name" label="名称" min-width="140" />
            <el-table-column prop="type" label="类型" width="80">
              <template #default="{ row }"><el-tag size="small" :type="row.type === 'SNAT' ? 'primary' : row.type === 'BNAT' ? 'danger' : 'warning'">{{ row.type }}</el-tag></template>
            </el-table-column>
            <el-table-column label="源区域/地址" min-width="140">
              <template #default="{ row }"><span class="mono">{{ row.src_zone }}：{{ row.src_addr }}</span></template>
            </el-table-column>
            <el-table-column label="目的" min-width="130">
              <template #default="{ row }"><span class="mono">{{ row.dst_zone }}：{{ row.dst_addr }}</span></template>
            </el-table-column>
            <el-table-column label="映射服务" min-width="130">
              <template #default="{ row }">
                <el-tag v-if="row.service && row.service !== 'any'" size="small" type="warning" style="margin: 1px">{{ row.service }}</el-tag>
                <span v-else style="color: #909399">-</span>
              </template>
            </el-table-column>
            <el-table-column label="转换后" min-width="140">
              <template #default="{ row }"><span class="mono">{{ row.translated_addr }}{{ row.translated_port ? ':' + row.translated_port : '' }}</span></template>
            </el-table-column>
            <el-table-column prop="hit_count" label="命中" width="80" />
            <el-table-column label="启用" width="60">
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
      </template>

      <!-- ========== AC 特有 tabs ========== -->
      <template v-if="isAC">
        <el-tab-pane :label="`在线用户（${acOnlineUsers.length}）`" name="ac-online-users">
          <el-alert v-if="acOnlineUsers.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                    title="暂无在线用户数据" />
          <el-table :data="acOnlineUsers" size="small" border stripe>
            <el-table-column type="index" label="#" width="50" />
            <el-table-column prop="name" label="用户" min-width="120" />
            <el-table-column prop="ip" label="IP 地址" min-width="130">
              <template #default="{ row }"><span class="mono">{{ row.ip || row.addr || '-' }}</span></template>
            </el-table-column>
            <el-table-column prop="mac" label="MAC" min-width="150">
              <template #default="{ row }"><span class="mono">{{ row.mac || '-' }}</span></template>
            </el-table-column>
            <el-table-column label="上线时间" min-width="160">
              <template #default="{ row }"><span class="mono">{{ row.login_time || row.online_time || row.time || '-' }}</span></template>
            </el-table-column>
            <el-table-column label="流量 (KB)" min-width="110">
              <template #default="{ row }"><span class="mono">{{ row.flow || row.traffic || '-' }}</span></template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="应用流量排行" name="ac-app-rank">
          <el-alert v-if="acAppRank.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                    title="暂无应用流量排行数据" />
          <div v-if="acAppRank.length" class="rank-grid">
            <div class="rank-card" v-for="(app, i) in acAppRank" :key="i">
              <div class="rank-num">#{{ i + 1 }}</div>
              <div class="rank-body">
                <div class="rank-name">{{ app.name || app.app || app.app_name || '未知' }}</div>
                <div class="rank-bar-wrap">
                  <div class="rank-bar" :style="{ width: rankBarWidth(app, acAppRank) + '%' }"></div>
                </div>
                <div class="rank-stat">
                  <span>上行：{{ fmtBytes(app.up_bytes || app.up_flow || 0) }}</span>
                  <span>下行：{{ fmtBytes(app.down_bytes || app.down_flow || 0) }}</span>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="用户流量排行" name="ac-user-rank">
          <el-alert v-if="acUserRank.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                    title="暂无用户流量排行数据" />
          <div v-if="acUserRank.length" class="rank-grid">
            <div class="rank-card" v-for="(u, i) in acUserRank" :key="i">
              <div class="rank-num">#{{ i + 1 }}</div>
              <div class="rank-body">
                <div class="rank-name">{{ u.name || u.user || u.username || '未知' }}</div>
                <div class="rank-sub">{{ u.ip || u.addr || '' }}</div>
                <div class="rank-bar-wrap">
                  <div class="rank-bar" :style="{ width: rankBarWidth(u, acUserRank) + '%' }"></div>
                </div>
                <div class="rank-stat">
                  <span>上行：{{ fmtBytes(u.up_bytes || u.up_flow || 0) }}</span>
                  <span>下行：{{ fmtBytes(u.down_bytes || u.down_flow || 0) }}</span>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </template>

      <!-- ========== 用户绑定（仅 AC） ========== -->
      <template v-if="isAC">
        <el-tab-pane :label="`用户绑定（${bindings.length}）`" name="bindings">
        <div style="display: flex; gap: 8px; margin-bottom: 8px">
          <el-input v-model="bindKeyword" placeholder="按用户名 / IP / MAC 搜索"
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
      </template>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { store, currentDevice } from '../store.js'

const tab = ref('status')
const status = ref(null)
const interfaces = ref([])
const nat = ref([])
const zones = ref([])     // AF 安全区域
const acl = ref([])
const bindings = ref([])
const objects = ref([])
const services = ref([])
const routes = ref([])
const loadErrors = ref({})
const bindKeyword = ref('')
const bindSearching = ref(false)
const refreshing = ref(false)

// AC 特有数据（只取 STATUS 范畴：在线用户/吞吐量/流量排行）
const acOnlineUsers = ref([])
const acThroughput = ref({})
const acAppRank = ref([])
const acUserRank = ref([])

const isAF = computed(() => {
  const dev = currentDevice()
  return dev && dev.type === 'af'
})
const isAC = computed(() => {
  const dev = currentDevice()
  return dev && dev.type === 'ac'
})

const meters = computed(() => {
  const m = [
    { key: 'cpu_usage', label: 'CPU 使用率' },
    { key: 'memory_usage', label: '内存使用率' },
    { key: 'disk_usage', label: '磁盘使用率' },
  ]
  return m
})
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
  refreshing.value = true
  loadErrors.value = {}
  const fetches = [
    fetchOne(dev, 'status', 'status', r => { status.value = r }),
  ]
  if (dev.type === 'af') {
    fetches.push(
      fetchOne(dev, 'interfaces', 'interfaces', r => { interfaces.value = r }),
      fetchOne(dev, 'zones', 'zones', r => { zones.value = r }),
      fetchOne(dev, 'nat', 'nat', r => { nat.value = r }),
      fetchOne(dev, 'acl', 'acl', r => { acl.value = r }),
      fetchOne(dev, 'objects', 'objects', r => { objects.value = r }),
      fetchOne(dev, 'services', 'services', r => { services.value = r }),
      fetchOne(dev, 'routes', 'routes', r => { routes.value = r }),
    )
  } else {
    // AC：只取 STATUS 范畴数据（在线用户/吞吐量/用户流量排行/应用流量排行）
    fetches.push(
      fetchOne(dev, 'bindings', 'bindings', r => { bindings.value = r }),
      fetchOne(dev, 'ac_online_users', 'ac/online-users', r => { acOnlineUsers.value = r }),
      fetchOne(dev, 'ac_throughput', 'ac/throughput', r => { acThroughput.value = r }),
      fetchOne(dev, 'ac_app_rank', 'ac/app-rank?top=10', r => { acAppRank.value = r }),
      fetchOne(dev, 'ac_user_rank', 'ac/user-rank?top=10', r => { acUserRank.value = r }),
    )
  }
  await Promise.allSettled(fetches)
  refreshing.value = false
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
  if (loadErrors.value[section]) return `读取失败：${loadErrors.value[section]}`
  const dev = currentDevice()
  if (dev && dev.type === 'ac') {
    return 'AC 经开放接口仅提供 状态 / 用户绑定 / 策略 / 在线用户 数据，此项配置不在开放接口范围内'
  }
  return '该设备当前无此配置数据'
}

const bindHint = computed(() => {
  const dev = currentDevice()
  if (bindKeyword.value) return `未找到与「${bindKeyword.value}」匹配的绑定记录`
  if (dev && dev.type === 'ac') return 'AC 开放接口按用户名/IP/MAC 搜索绑定关系，请在上方输入关键词查询'
  if (loadErrors.value.bindings) return `读取失败：${loadErrors.value.bindings}`
  return '该设备当前无绑定数据'
})

function rankBarWidth(item, list) {
  const max = Math.max(...list.map(x => {
    const total = (x.up_bytes || x.up_flow || 0) + (x.down_bytes || x.down_flow || 0)
    return total
  }), 1)
  const cur = (item.up_bytes || item.up_flow || 0) + (item.down_bytes || item.down_flow || 0)
  return (cur / max) * 100
}

function fmtBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B'
  const b = Number(bytes)
  if (b < 1024) return b + ' B'
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB'
  if (b < 1024 * 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB'
  return (b / 1024 / 1024 / 1024).toFixed(2) + ' GB'
}

function fmtThroughput(val) {
  if (!val || val === 0) return '0 bps'
  const v = Number(val)
  if (v < 1000) return v.toFixed(1) + ' bps'
  if (v < 1000 * 1000) return (v / 1000).toFixed(1) + ' Kbps'
  if (v < 1000 * 1000 * 1000) return (v / 1000 / 1000).toFixed(1) + ' Mbps'
  return (v / 1000 / 1000 / 1000).toFixed(2) + ' Gbps'
}

watch(() => store.currentDeviceId, load)
onMounted(() => { load() })
</script>

<style scoped>
.config-toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-bottom: 8px; }
.config-toolbar-title { flex: 1; font-size: 12px; color: #909399; }
.status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
.status-section { margin-bottom: 18px; }
.status-section-title { font-size: 13px; font-weight: 600; color: #606266; margin-bottom: 10px; padding-left: 8px; border-left: 3px solid #409eff; }
.stat { background: #f8f9fb; border-radius: 8px; padding: 12px 14px; }
.stat-label { color: #909399; font-size: 12px; margin-bottom: 6px; }
.stat-value { font-size: 18px; font-weight: 600; }
.stat-value.sm { font-size: 13px; }

/* AC 排行卡片 */
.rank-grid { display: flex; flex-direction: column; gap: 10px; }
.rank-card { display: flex; align-items: flex-start; gap: 12px; background: #f8f9fb; border-radius: 8px; padding: 12px 14px; }
.rank-num { font-size: 20px; font-weight: 700; color: #409eff; min-width: 36px; line-height: 1.4; }
.rank-body { flex: 1; min-width: 0; }
.rank-name { font-weight: 600; font-size: 14px; }
.rank-sub { color: #909399; font-size: 12px; margin-bottom: 4px; }
.rank-bar-wrap { height: 8px; background: #e4e7ed; border-radius: 4px; margin: 6px 0; overflow: hidden; }
.rank-bar { height: 100%; background: linear-gradient(90deg, #409eff, #79bbff); border-radius: 4px; transition: width 0.3s; }
.rank-stat { display: flex; gap: 16px; font-size: 12px; color: #606266; }

/* AC 吞吐量 */
.throughput-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
</style>