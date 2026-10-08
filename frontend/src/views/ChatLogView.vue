<template>
  <div class="chatlog-page">
    <div class="page-head">
      <div>
        <h2 class="ph-title">对话日志</h2>
        <p class="ph-desc">记录所有对话内容及结果，用于审计与经验回顾</p>
      </div>
      <div class="ph-actions">
        <el-button @click="loadLogs" :loading="loading"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>
    </div>
    <div class="page-card">
      <!-- 筛选栏 -->
      <div class="filter-bar">
        <el-input v-model="filters.keyword" clearable placeholder="搜索标题 / 消息内容"
                  class="fb-input" :prefix-icon="Search" @keyup.enter="applyFilters" @clear="applyFilters" />
        <el-select v-model="tenantFilter" clearable placeholder="全部客户" style="width: 130px"
                   @change="loadLogs" aria-label="按客户筛选">
          <el-option v-for="t in tenants" :key="t" :value="t"
                     :label="t === 'default' ? '默认客户' : t" />
        </el-select>
        <el-select v-model="filters.device_id" clearable placeholder="全部设备（含全局会话）"
                   style="width: 190px" @change="applyFilters">
          <el-option value="global" label="全局会话（未绑定设备）" />
          <el-option-group label="深信服设备">
            <el-option v-for="d in store.devices" :key="d.id" :label="d.name" :value="d.id" />
          </el-option-group>
          <el-option-group v-if="store.netdevDevices.length" label="网络设备">
            <el-option v-for="d in store.netdevDevices" :key="d.id"
                       :label="`${d.name}（${NETDEV_VENDOR_NAMES[d.vendor] || d.vendor}）`" :value="d.id" />
          </el-option-group>
        </el-select>
        <el-date-picker v-model="filters.range" type="daterange" value-format="YYYY-MM-DD"
                        start-placeholder="开始日期" end-placeholder="结束日期"
                        style="width: 250px" @change="applyFilters" />
        <el-button type="primary" @click="applyFilters">查询</el-button>
        <el-button text @click="resetFilters">重置</el-button>
      </div>

      <div v-if="loading" class="state-block">
        <el-icon class="is-loading state-ico"><Loading /></el-icon>
        <div>加载对话日志…</div>
      </div>

      <div v-else-if="loadError" class="state-block">
        <el-icon class="state-ico is-error" :size="30"><WarningFilled /></el-icon>
        <p>{{ loadError }}</p>
        <el-button size="small" @click="loadLogs">重新加载</el-button>
      </div>

      <div v-else-if="!conversations.length" class="state-block">
        <svg viewBox="0 0 40 40" fill="none" class="state-mark" aria-hidden="true">
          <path d="M20 3.5 34.3 11.8v16.4L20 36.5 5.7 28.2V11.8L20 3.5Z" stroke="#C6CEDD" stroke-width="2.5" stroke-linejoin="round" />
          <circle cx="20" cy="20" r="3.4" fill="#C6CEDD" />
        </svg>
        <p>没有符合条件的对话日志</p>
      </div>

      <template v-else>
        <el-table :data="conversations" size="small" style="width: 100%">
          <el-table-column prop="title" label="对话标题" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="conv-link" @click="showDetail(row.id)">{{ row.title }}</span>
            </template>
          </el-table-column>
          <el-table-column label="设备" width="140">
            <template #default="{ row }">
              <span v-if="row.device_name"><el-icon style="vertical-align: -2px"><Monitor /></el-icon> {{ row.device_name }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="客户" width="100">
            <template #default="{ row }">{{ row.tenant_id === 'default' ? '默认客户' : (row.tenant_id || '—') }}</template>
          </el-table-column>
          <el-table-column prop="msg_count" label="消息" width="60" align="center" />
          <el-table-column prop="last_message" label="最后提问" min-width="200" show-overflow-tooltip />
          <!-- AI 摘要列已按需求隐藏：后端摘要沉淀功能保留，仅不在列表中展示 -->
          <el-table-column label="最后活跃" width="140">
            <template #default="{ row }">{{ (row.updated_at || '').slice(0, 16) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="70">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="showDetail(row.id)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div style="display: flex; justify-content: flex-end; margin-top: 10px">
          <el-pagination layout="total, prev, pager, next, sizes" :total="total"
                         v-model:current-page="page" v-model:page-size="pageSize"
                         :page-sizes="[10, 20, 50]" background
                         @current-change="loadLogs"
                         @size-change="() => { page = 1; loadLogs() }" />
        </div>
      </template>
    </div>

    <!-- 对话详情弹窗 -->
    <el-dialog v-model="showDetailDialog" :title="detailTitle" width="700px" top="5vh" append-to-body>
      <div v-if="loadingDetail" style="text-align: center; padding: 20px">
        <el-icon class="is-loading" style="font-size: 24px"><Loading /></el-icon>
      </div>
      <div v-else class="detail-messages">
        <div v-if="detailHasMore" style="text-align: center; padding: 8px 0">
          <el-button size="small" text type="primary" :loading="loadingMore" @click="loadEarlier">
            加载更早消息
          </el-button>
        </div>
        <div v-for="(m, i) in detailMessages" :key="i" class="detail-row" :class="m.role">
          <div class="detail-role-tag">
            <el-tag :type="m.role === 'user' ? '' : 'success'" size="small" effect="plain">
              {{ m.role === 'user' ? '用户' : m.role === 'assistant' ? '助手' : '工具' }}
            </el-tag>
          </div>
          <div class="detail-content">
            <div v-if="m.role === 'user'">{{ m.content?.text }}</div>
            <div v-else-if="m.role === 'assistant'" class="md-body" v-html="render(m.content?.text || '')"></div>
            <div v-else-if="m.role === 'tool'" class="tool-content">
              <el-tag size="small" type="warning">{{ m.content?.name || '工具' }}</el-tag>
              <pre>{{ (m.content?.content || '').slice(0, 500) }}{{ (m.content?.content || '').length > 500 ? '…' : '' }}</pre>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { apiGet } from '../api.js'
const tenants = ref(['default'])
const tenantFilter = ref('')
apiGet('/api/tenants').then(d => { tenants.value = d.tenants || ['default'] }).catch(() => {})
import { renderMarkdown } from '../chat/markdown'
import { store, loadDevices, NETDEV_VENDOR_NAMES } from '../store.js'

// 统一走 chat/markdown：含代码高亮 + DOMPurify 净化 + 代码块复制按钮
const render = renderMarkdown

const loading = ref(false)
const conversations = ref([])
const total = ref(0)
const loadError = ref('')   // 区分「加载失败」与「确实没有数据」
const page = ref(1)
const pageSize = ref(20)
const filters = reactive({ keyword: '', device_id: '', range: null })
const showDetailDialog = ref(false)
const detailTitle = ref('')
const detailMessages = ref([])
const loadingDetail = ref(false)
const DETAIL_PAGE = 200   // 详情消息分页：默认最近 200 条，更早内容点击按需加载
const detailConvId = ref('')
const detailOldestId = ref(null)
const detailHasMore = ref(false)
const loadingMore = ref(false)

async function fetchDetailMessages(convId, beforeId = null) {
  const rows = await apiGet(`/api/chat/conversations/${convId}?limit=${DETAIL_PAGE + 1}`
    + (beforeId ? `&before_id=${beforeId}` : ''))
  const hasMore = rows.length > DETAIL_PAGE
  return { msgs: hasMore ? rows.slice(rows.length - DETAIL_PAGE) : rows, hasMore }
}

async function loadEarlier() {
  if (!detailOldestId.value || loadingMore.value) return
  loadingMore.value = true
  try {
    const { msgs, hasMore } = await fetchDetailMessages(detailConvId.value, detailOldestId.value)
    if (msgs.length) detailOldestId.value = msgs[0].id
    detailHasMore.value = hasMore
    detailMessages.value = [...msgs, ...detailMessages.value]
  } catch { /* 静默 */ } finally {
    loadingMore.value = false
  }
}

async function loadLogs() {
  loading.value = true
  try {
    const params = new URLSearchParams({
      page: String(page.value), page_size: String(pageSize.value),
      keyword: filters.keyword || '', device_id: filters.device_id || '',
      start: filters.range?.[0] || '', end: filters.range?.[1] || '',
      all_tenants: '1'   // 会话日志全局共享：跨客户查看，再按客户前端过滤
    })
    const data = await apiGet(`/api/chat/conversations/detail?${params}`)
    conversations.value = (data.items || [])
      .filter(c => !tenantFilter.value || c.tenant_id === tenantFilter.value)
    total.value = data.total || 0
    loadError.value = ''
  } catch (e) {
    // 原实现静默清空 → 后端故障被伪装成「没有符合条件的对话日志」，误导排查方向
    conversations.value = []
    total.value = 0
    loadError.value = e?.message || '加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadLogs()
}

function resetFilters() {
  filters.keyword = ''
  filters.device_id = ''
  filters.range = null
  applyFilters()
}

async function showDetail(convId) {
  const conv = conversations.value.find(c => c.id === convId)
  detailTitle.value = conv?.title || '对话详情'
  loadingDetail.value = true
  showDetailDialog.value = true
  try {
    detailConvId.value = convId
    const { msgs, hasMore } = await fetchDetailMessages(convId)
    detailMessages.value = msgs
    detailOldestId.value = msgs.length ? msgs[0].id : null
    detailHasMore.value = hasMore
  } catch (e) {
    detailMessages.value = []
  } finally {
    loadingDetail.value = false
  }
}

onMounted(() => { if (!store.netdevDevices.length) loadDevices(); loadLogs() })
</script>

<style scoped>
.chatlog-page { max-width: 1080px; margin: 0 auto; animation: sfa-fade-up .3s var(--ease-out); }
.filter-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
.fb-input { width: 240px; }
.conv-link { cursor: pointer; color: var(--sfa-primary); font-weight: 550; }
.conv-link:hover { text-decoration: underline; }
.muted { color: var(--sfa-text-4); font-size: 12px; }

.state-block { text-align: center; padding: 48px 0; color: var(--sfa-text-3); font-size: 13px; }
.state-ico { font-size: 26px; color: var(--sfa-primary); }
.state-ico.is-error { color: var(--sfa-danger-ink); }
.state-mark { width: 52px; height: 52px; margin-bottom: 12px; }

.detail-messages { max-height: 65vh; overflow-y: auto; padding-right: 6px; }
.detail-row { display: flex; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--sfa-border-soft); }
.detail-role-tag { flex-shrink: 0; width: 52px; }
.detail-content { flex: 1; font-size: 13px; line-height: 1.7; }
.tool-content { font-size: 12px; }
.tool-content pre { background: #0D1424; color: #BFC9E4; padding: 10px 12px; border-radius: 9px; white-space: pre-wrap; word-break: break-all; margin-top: 6px; font-family: var(--sfa-mono); }
.md-body { line-height: 1.7; }
.md-body :deep(p) { margin: 4px 0; }
.md-body :deep(code) { background: var(--sfa-code-bg); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
</style>
