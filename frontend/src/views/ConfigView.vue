<template>
  <div class="config-page">
    <ContextBar />
    <div class="page-head">
      <div>
        <h2 class="ph-title">配置可视化</h2>
        <p class="ph-desc">设备状态、接口、安全区域与策略全景视图 · 数据缓存于页面，点击刷新重新获取</p>
      </div>
      <div class="ph-actions">
        <span v-if="fetchedAtText" class="cfg-fetched">{{ fetchedAtText }}</span>
        <el-button :loading="refreshing" @click="load(true)"><el-icon><Refresh /></el-icon>&nbsp;刷新数据</el-button>
      </div>
    </div>
    <NetDevConfigPanel v-if="netdevMode" :device="currentDevice()" />
    <el-alert v-if="globalMode" type="info" :closable="false" show-icon
              title="当前为全局模式：下方为纳管设备总览；选择具体设备后显示该设备的配置全景" />
      <div v-if="globalMode" class="page-card" style="margin-bottom: 12px">
        <div class="col-title" style="margin-bottom: 8px"><el-icon><DataLine /></el-icon> 纳管设备总览</div>
        <div v-if="globalStats" style="display: flex; gap: 24px; flex-wrap: wrap">
          <div class="sfa-stat"><div class="num">{{ globalStats.sangfor }}</div><div class="lbl">深信服设备</div></div>
          <div class="sfa-stat"><div class="num">{{ globalStats.netdev }}</div><div class="lbl">网络设备</div></div>
          <div v-for="(v, k) in globalStats.byType" :key="k" class="sfa-stat">
            <div class="num">{{ v }}</div><div class="lbl">{{ k.toUpperCase() }}</div></div>
          <div v-for="(v, k) in globalStats.byVendor" :key="'nd-' + k" class="sfa-stat">
            <div class="num">{{ v }}</div><div class="lbl">{{ k }}</div></div>
        </div>
        <div v-else style="opacity: .7">加载中…</div>
      </div>
    <el-tabs v-if="!netdevMode && !globalMode" v-model="tab" class="page-card config-tabs">
      <!-- ========== 设备状态（AF / AC 通用） ========== -->
      <el-tab-pane label="设备状态" name="status">
        <div v-if="status" class="status-wrap">
          <!-- 系统信息 -->
          <div class="status-section">
            <div class="status-section-title">系统信息</div>
            <div class="status-grid">
              <div class="stat"><div class="stat-label">软件版本</div><div class="stat-value">{{ status.sw_version }}</div></div>
              <div class="stat"><div class="stat-label">型号</div><div class="stat-value">{{ status.model }}</div></div>
              <div class="stat" v-if="!isSCP"><div class="stat-label">运行时间</div><div class="stat-value sm">{{ status.uptime }}</div></div>
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
              <div class="stat" v-if="!isSCP">
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
          <el-input v-model="acUserFilter" size="small" clearable placeholder="筛选：IP / MAC / 用户名（支持部分字符，不区分大小写）"
                    style="max-width: 340px; margin-bottom: 8px" aria-label="筛选在线用户" />
          <el-alert v-if="acOnlineUsers.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                    title="暂无在线用户数据" />
          <el-table :data="acOnlineUsersFiltered" size="small" border stripe>
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

      <!-- ========== SCP 特有 tabs（只读查询） ========== -->
      <template v-if="isSCP">
        <el-tab-pane label="平台概况" name="scp-platform">
          <div v-if="scpPlatform && Object.keys(scpPlatform).length" class="status-wrap">
            <div class="status-section">
              <div class="status-section-title">平台信息</div>
              <div class="status-grid">
                <div class="stat"><div class="stat-label">SCP 版本</div><div class="stat-value sm">{{ scpPlatform.version || status?.sw_version }}</div></div>
                <div class="stat"><div class="stat-label">部署模式</div><div class="stat-value">{{ status?.extra?.manage_mode === 'managed_cloud' ? '托管云' : status?.extra?.manage_mode === 'private_cloud' ? '私有云' : (scpPlatform.manage_mode || '未知') }}</div></div>
                <div class="stat"><div class="stat-label">集群 IP</div><div class="stat-value sm">{{ scpPlatform.dcluster_info?.cluster_ip || '-' }}</div></div>
                <div class="stat"><div class="stat-label">维护模式</div><div class="stat-value">{{ scpPlatform.maintain_mode ? '维护中' : '正常' }}</div></div>
              </div>
            </div>
            <div class="status-section">
              <div class="status-section-title">资源使用（物理资源）</div>
              <div class="status-grid">
                <div class="stat"><div class="stat-label">CPU</div><el-progress :percentage="status?.cpu_usage || 0" :color="meterColor(status?.cpu_usage)" :stroke-width="14" /></div>
                <div class="stat"><div class="stat-label">内存</div><el-progress :percentage="status?.memory_usage || 0" :color="meterColor(status?.memory_usage)" :stroke-width="14" /></div>
                <div class="stat"><div class="stat-label">存储</div><el-progress :percentage="status?.extra?.storage_ratio || 0" :color="meterColor(status?.extra?.storage_ratio)" :stroke-width="14" /></div>
              </div>
            </div>
            <div class="status-section">
              <div class="status-section-title">主机与虚拟机</div>
              <div class="status-grid">
                <div class="stat"><div class="stat-label">物理机</div><div class="stat-value">{{ status?.extra?.hosts_online || 0 }} 在线 / {{ status?.extra?.hosts_total || 0 }}（告警 {{ status?.extra?.hosts_alarm || 0 }}）</div></div>
                <div class="stat"><div class="stat-label">虚拟机</div><div class="stat-value">{{ status?.extra?.servers_running || 0 }} 运行 / {{ status?.extra?.servers_total || 0 }}（告警 {{ status?.extra?.servers_alarm || 0 }}）</div></div>
              </div>
            </div>
          </div>
          <el-alert v-else-if="loadErrors.scp_platform" type="error" :closable="false" :title="`平台信息读取失败：${loadErrors.scp_platform}`" />
          <el-empty v-else description="加载中" />
        </el-tab-pane>

        <el-tab-pane :label="`集群（${scpClusters.length}）`" name="scp-clusters">
          <el-table :data="scpClusters" size="small" border stripe>
            <el-table-column prop="name" label="集群名称" min-width="140" />
            <el-table-column prop="version" label="HCI 版本" width="100" />
            <el-table-column prop="type" label="类型" width="80">
              <template #default="{ row }"><el-tag size="small">{{ row.type === 'hci' ? 'HCI' : row.type || '-' }}</el-tag></template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'normal' ? 'success' : 'danger'">{{ row.status === 'normal' ? '正常' : row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="CPU 使用率" width="140">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.res?.cpu?.ratio || 0)" :color="meterColor(row.res?.cpu?.ratio)" :stroke-width="10" /></template>
            </el-table-column>
            <el-table-column label="内存使用率" width="140">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.res?.memory?.ratio || 0)" :color="meterColor(row.res?.memory?.ratio)" :stroke-width="10" /></template>
            </el-table-column>
            <el-table-column label="存储使用率" width="140">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.res?.storage?.ratio || 0)" :color="meterColor(row.res?.storage?.ratio)" :stroke-width="10" /></template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`物理机（${scpHosts.length}）`" name="scp-hosts">
          <el-table :data="scpHosts" size="small" border stripe max-height="480">
            <el-table-column prop="name" label="名称/IP" min-width="120" />
            <el-table-column prop="cluster_name" label="集群" min-width="110" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'running' ? 'success' : 'danger'">{{ row.status === 'running' ? '在线' : row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="CPU" min-width="130">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.res?.cpu?.ratio || 0)" :color="meterColor(row.res?.cpu?.ratio)" :stroke-width="10" /></template>
            </el-table-column>
            <el-table-column label="内存" min-width="130">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.res?.memory?.ratio || 0)" :color="meterColor(row.res?.memory?.ratio)" :stroke-width="10" /></template>
            </el-table-column>
            <el-table-column label="存储总量" width="100">
              <template #default="{ row }">{{ fmtBytes((row.res?.storage?.total || row.storage?.total_mb || 0) * 1024 * 1024) }}</template>
            </el-table-column>
            <el-table-column label="告警" width="70" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.alarm_count" size="small" type="danger">{{ row.alarm_count }}</el-tag>
                <span v-else>0</span>
              </template>
            </el-table-column>
            <el-table-column prop="cpu.type" label="CPU 型号" min-width="150" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`虚拟机（${scpVms.length}）`" name="scp-vms">
          <el-table :data="scpVms" size="small" border stripe max-height="480" @row-click="openVmDetail" style="cursor: pointer">
            <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'running' ? 'success' : 'info'">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="IP" min-width="130">
              <template #default="{ row }"><span class="mono">{{ (row.ips || []).join(', ') || (row.networks || []).map(n => n.ip_address).filter(Boolean).join(', ') || '-' }}</span></template>
            </el-table-column>
            <el-table-column prop="host_name" label="所在物理机" min-width="110" show-overflow-tooltip />
            <el-table-column label="CPU" width="110">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.res?.cpu?.ratio || 0)" :color="meterColor(row.res?.cpu?.ratio)" :stroke-width="10" /></template>
            </el-table-column>
            <el-table-column label="内存" width="110">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.res?.memory?.ratio || 0)" :color="meterColor(row.res?.memory?.ratio)" :stroke-width="10" /></template>
            </el-table-column>
            <el-table-column label="操作系统" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.os_display || row.os_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="配置" width="100">
              <template #default="{ row }">{{ row.cores }}核 / {{ Math.round((row.memory_mb || 0) / 1024 * 10) / 10 }}G</template>
            </el-table-column>
          </el-table>
          <div class="hint" style="margin-top: 6px">点击行查看虚拟机详情（网卡/端口组/磁盘）</div>
        </el-tab-pane>

        <el-tab-pane :label="`存储（${scpStorages.length}）`" name="scp-storages">
          <el-table :data="scpStorages" size="small" border stripe>
            <el-table-column prop="name" label="存储名称" min-width="140" />
            <el-table-column prop="type" label="类型" width="100" />
            <el-table-column prop="status" label="状态" width="90" />
            <el-table-column label="容量" width="100">
              <template #default="{ row }">{{ fmtBytes((row.total_mb || 0) * 1024 * 1024) }}</template>
            </el-table-column>
            <el-table-column label="使用率" min-width="140">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.ratio || 0)" :color="meterColor(row.ratio)" :stroke-width="10" /></template>
            </el-table-column>
            <el-table-column prop="cluster_name" label="所属集群" min-width="110" show-overflow-tooltip />
          </el-table>
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
        <el-input v-model="bindingFilter" size="small" clearable placeholder="即时筛选已加载结果：IP / MAC / 用户名"
                  style="max-width: 340px; margin-bottom: 8px" aria-label="即时筛选用户绑定" />
        <el-alert v-if="bindings.length === 0" type="info" :closable="false" style="margin-bottom: 8px"
                  :title="bindHint" />
        <el-table :data="bindingsFiltered" size="small" border stripe>
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

    <!-- SCP 虚拟机详情抽屉 -->
    <el-drawer v-model="showVmDetail" :title="vmDetail?.name || '虚拟机详情'" size="520px" append-to-body>
      <template v-if="vmDetail">
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="状态">{{ vmDetail.status }}</el-descriptions-item>
          <el-descriptions-item label="电源">{{ vmDetail.power_state }}</el-descriptions-item>
          <el-descriptions-item label="vCPU">{{ vmDetail.cores }} 核</el-descriptions-item>
          <el-descriptions-item label="内存">{{ vmDetail.memory_mb }} MB</el-descriptions-item>
          <el-descriptions-item label="磁盘">{{ Math.round((vmDetail.storage_mb || 0) / 1024 * 10) / 10 }} GB</el-descriptions-item>
          <el-descriptions-item label="操作系统">{{ vmDetail.os_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="所在物理机" :span="2">{{ vmDetail.host_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="所属租户" :span="2">{{ vmDetail.project_name || '-' }}</el-descriptions-item>
        </el-descriptions>
        <div class="status-section-title" style="margin-top: 14px">网卡 / IP / 端口组</div>
        <el-table :data="vmDetail.networks || []" size="small" border>
          <el-table-column label="网卡" width="80"><template #default="{ row }">{{ row.vif_id || row.name || '-' }}</template></el-table-column>
          <el-table-column label="MAC" min-width="130"><template #default="{ row }"><span class="mono">{{ row.mac_address || '-' }}</span></template></el-table-column>
          <el-table-column label="IP" min-width="120"><template #default="{ row }"><span class="mono">{{ row.ip_address || (row.ip_info && row.ip_info.ip_address) || '-' }}</span></template></el-table-column>
          <el-table-column label="网络/端口组" min-width="140">
            <template #default="{ row }">
              {{ row.subnet_name || row.vpc_name || row.name || '-' }}
              <span v-if="row.network_type">（{{ row.network_type }}）</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="status-section-title" style="margin-top: 14px">磁盘</div>
        <el-table :data="vmDetail.disks || []" size="small" border>
          <el-table-column label="磁盘" width="90"><template #default="{ row }">{{ row.id || '-' }}</template></el-table-column>
          <el-table-column label="容量"><template #default="{ row }">{{ Math.round((row.size_mb || 0) / 1024 * 10) / 10 }} GB</template></el-table-column>
          <el-table-column label="存储" min-width="140"><template #default="{ row }">{{ row.storage_name || '-' }}</template></el-table-column>
        </el-table>
      </template>
      <el-empty v-else description="加载中" />
    </el-drawer>
  </div>
</template>

<script>
// 模块级数据缓存（跨组件实例共享）：进入页面先回放缓存数据，不触发设备采集；
// 仅点击「刷新数据」才实拉最新数据并更新缓存。
const configDataCache = new Map()   // device_id -> {各数据源快照, fetchedAt}
</script>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { store, currentDevice, isNetDev, isGlobal } from '../store.js'
import { apiGet, NetDev } from '../api.js'
import NetDevConfigPanel from '../components/NetDevConfigPanel.vue'
import ContextBar from '../components/ContextBar.vue'

const tab = ref('status')
const status = ref(null)
const interfaces = ref([])
const nat = ref([])
const zones = ref([])     // AF 安全区域
const acl = ref([])
const bindings = ref([])
// 绑定/在线用户表格即时筛选（部分字符、不区分大小写）
const bindingFilter = ref('')
const acUserFilter = ref('')
const _fuzzy = (row, q, fields) => !q || fields.some(f =>
  String(row[f] ?? '').toLowerCase().includes(q.toLowerCase()))
const bindingsFiltered = computed(() =>
  bindings.value.filter(r => _fuzzy(r, bindingFilter.value, ['user', 'ip', 'mac', 'comment'])))
const acOnlineUsersFiltered = computed(() =>
  acOnlineUsers.value.filter(r => _fuzzy(r, acUserFilter.value, ['name', 'ip', 'mac', 'addr'])))
// 全局模式：纳管设备总览看板
const objects = ref([])
const services = ref([])
const routes = ref([])
const loadErrors = ref({})
const bindKeyword = ref('')
const bindSearching = ref(false)
const refreshing = ref(false)
const fetchedAt = ref('')   // 当前数据的获取时间（缓存回放时同步回放）

const fetchedAtText = computed(() => {
  if (!fetchedAt.value) return ''
  return `数据获取于 ${fetchedAt.value}`
})

// SCP 特有数据（只读查询：平台/集群/物理机/虚拟机/存储）
const scpPlatform = ref({})
const scpClusters = ref([])
const scpHosts = ref([])
const scpVms = ref([])
const scpStorages = ref([])
const showVmDetail = ref(false)
const vmDetail = ref(null)

// AC 特有数据（只取 STATUS 范畴：在线用户/吞吐量/流量排行）
const acOnlineUsers = ref([])
const acThroughput = ref({})
const acAppRank = ref([])
const acUserRank = ref([])

const netdevMode = computed(() => isNetDev(currentDevice()))
const currentDeviceRef = currentDevice
const globalMode = computed(() => isGlobal(currentDevice()))

// 全局模式：纳管设备总览看板
const globalStats = ref(null)
async function loadGlobalStats() {
  try {
    const [devs, nds] = await Promise.all([apiGet('/api/devices'), NetDev.devices()])
    const byType = {}
    for (const d of devs) byType[d.type] = (byType[d.type] || 0) + 1
    const byVendor = {}
    for (const d of nds) byVendor[d.vendor] = (byVendor[d.vendor] || 0) + 1
    globalStats.value = { sangfor: devs.length, netdev: nds.length, byType, byVendor }
  } catch { globalStats.value = null }
}
watch(globalMode, v => { if (v) loadGlobalStats() }, { immediate: true })
const guardText = computed(() => netdevMode.value
  ? '当前选中的是网络设备：请在「AI 对话」中用自然语言查询/配置，或在「网络设备管理」页批量执行与控制台操作'
  : '当前为全局模式：本页面需要指定具体设备，请在页顶设备切换器中选择一台深信服设备')
const isAF = computed(() => {
  const dev = currentDevice()
  return dev && dev.type === 'af'
})
const isAC = computed(() => {
  const dev = currentDevice()
  return dev && dev.type === 'ac'
})
const isSCP = computed(() => {
  const dev = currentDevice()
  return dev && dev.type === 'scp'
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

// 各数据源 → 页面 ref 的绑定表（缓存回放与采集写回共用）
function dataRefs() {
  return {
    status, interfaces, nat, zones, acl, bindings, objects, services, routes,
    scpPlatform, scpClusters, scpHosts, scpVms, scpStorages,
    acOnlineUsers, acThroughput, acAppRank, acUserRank,
  }
}

function snapshotData() {
  const out = { loadErrors: { ...loadErrors.value }, fetchedAt: fetchedAt.value }
  for (const [k, r] of Object.entries(dataRefs())) out[k] = r.value
  return out
}

function hydrateData(c) {
  for (const [k, r] of Object.entries(dataRefs())) r.value = c[k]
  loadErrors.value = c.loadErrors || {}
  fetchedAt.value = c.fetchedAt || ''
}

async function load(force = false) {
  const dev = currentDevice()
  if (!dev || isNetDev(dev) || isGlobal(dev)) return
  // 有缓存且非手动刷新：直接回放，不触发采集（进入页面不再实拉设备）
  const cached = configDataCache.get(dev.id)
  if (!force && cached) { hydrateData(cached); return }
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
  } else if (dev.type === 'scp') {
    // SCP：平台/集群/物理机/虚拟机/存储（只读查询）
    fetches.push(
      fetchOne(dev, 'scp_platform', 'scp/platform', r => { scpPlatform.value = r || {} }),
      fetchOne(dev, 'scp_clusters', 'scp/clusters', r => { scpClusters.value = r || [] }),
      fetchOne(dev, 'scp_hosts', 'scp/hosts', r => { scpHosts.value = r || [] }),
      fetchOne(dev, 'scp_vms', 'scp/vms?limit=200', r => { scpVms.value = r || [] }),
      fetchOne(dev, 'scp_storages', 'scp/storages', r => { scpStorages.value = r || [] }),
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
  fetchedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
  configDataCache.set(dev.id, snapshotData())
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

async function openVmDetail(row) {
  const dev = currentDevice()
  if (!dev || !row || !row.id) return
  vmDetail.value = null
  showVmDetail.value = true
  try {
    const r = await fetch(`/api/devices/${dev.id}/scp/vms/${row.id}`).then(resp => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      return resp.json()
    })
    vmDetail.value = r
  } catch (e) { vmDetail.value = row }   // 失败时退回列表行数据
}

watch(() => store.currentDeviceId, () => load(false))   // 切换设备：缓存回放优先，不实拉
onMounted(() => { load() })
</script>

<style scoped>
.config-page { animation: sfa-fade-up .3s var(--ease-out); }
.cfg-fetched { color: var(--sfa-text-4); font-size: 11.5px; margin-right: 4px; }
.config-tabs :deep(.el-tabs__header) { margin-bottom: 14px; }
.config-tabs :deep(.el-tabs__item) { font-weight: 550; }
.config-tabs :deep(.el-tabs__item.is-active) { font-weight: 700; }

.status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
.status-section { margin-bottom: 20px; animation: sfa-fade-up .35s var(--ease-out) both; }
.status-section-title {
  font-size: 13px; font-weight: 650; color: var(--sfa-text-2);
  margin-bottom: 10px; padding-left: 9px;
  border-left: 3px solid transparent;
  border-image: linear-gradient(180deg, var(--sfa-primary), #8FA9FF) 1;
}
.stat {
  background: linear-gradient(180deg, var(--sfa-panel-soft), var(--sfa-panel-soft));
  border: 1px solid var(--sfa-border-soft); border-radius: var(--sfa-r-md);
  padding: 13px 15px; min-height: 92px;
  transition: transform var(--dur-2) var(--ease-out), box-shadow var(--dur-2);
}
.stat:hover { transform: translateY(-2px); box-shadow: var(--sfa-shadow-2); }
.stat-label { color: var(--sfa-text-3); font-size: 11.5px; margin-bottom: 7px; letter-spacing: .03em; }
.stat-value { font-size: 19px; font-weight: 700; letter-spacing: -.01em; font-feature-settings: "tnum" 1; }
.stat-value.sm { font-size: 12.5px; font-family: var(--sfa-mono); font-weight: 600; }

/* AC 排行卡片 */
.rank-grid { display: flex; flex-direction: column; gap: 10px; }
.rank-card {
  display: flex; align-items: flex-start; gap: 13px;
  background: linear-gradient(180deg, var(--sfa-panel-soft), var(--sfa-panel-soft));
  border: 1px solid var(--sfa-border-soft); border-radius: var(--sfa-r-md);
  padding: 12px 15px; transition: transform var(--dur-2) var(--ease-out), box-shadow var(--dur-2);
}
.rank-card:hover { transform: translateX(2px); box-shadow: var(--sfa-shadow-2); }
.rank-num {
  font-size: 17px; font-weight: 800; min-width: 34px; line-height: 1.5;
  color: var(--sfa-text-4); font-family: var(--sfa-mono); font-feature-settings: "tnum" 1;
}
.rank-card:first-child .rank-num { color: var(--sfa-primary); }
.rank-body { flex: 1; min-width: 0; }
.rank-name { font-weight: 650; font-size: 13.5px; }
.rank-sub { color: var(--sfa-text-4); font-size: 11.5px; margin-bottom: 4px; font-family: var(--sfa-mono); }
.rank-bar-wrap { height: 7px; background: var(--sfa-bar-track); border-radius: 999px; margin: 7px 0; overflow: hidden; }
.rank-bar {
  height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, #3B63FF, #6A87FF 60%, #0FB9A4);
  transition: width .8s var(--ease-out);
}
.rank-stat { display: flex; gap: 18px; font-size: 11.5px; color: var(--sfa-text-3); font-family: var(--sfa-mono); font-feature-settings: "tnum" 1; }
</style>