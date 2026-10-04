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
          <el-table-column prop="msg_count" label="消息" width="60" align="center" />
          <el-table-column prop="last_message" label="最后提问" min-width="200" show-overflow-tooltip />
          <el-table-column label="AI 摘要" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.summary" class="muted">{{ row.summary }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
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
                         :current-page="page" :page-size="pageSize"
                         :page-sizes="[10, 20, 50]" background
                         @current-change="p => { page = p; loadLogs() }"
                         @size-change="s => { pageSize = s; page = 1; loadLogs() }" />
        </div>
      </template>
    </div>

    <!-- 对话详情弹窗 -->
    <el-dialog v-model="showDetailDialog" :title="detailTitle" width="700px" top="5vh" append-to-body>
      <div v-if="loadingDetail" style="text-align: center; padding: 20px">
        <el-icon class="is-loading" style="font-size: 24px"><Loading /></el-icon>
      </div>
      <div v-else class="detail-messages">
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
import MarkdownIt from 'markdown-it'
import { apiGet } from '../api.js'
import { store, loadDevices, NETDEV_VENDOR_NAMES } from '../store.js'

const md = new MarkdownIt({ breaks: true })
const render = (text) => md.render(text || '')

const loading = ref(false)
const conversations = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = reactive({ keyword: '', device_id: '', range: null })
const showDetailDialog = ref(false)
const detailTitle = ref('')
const detailMessages = ref([])
const loadingDetail = ref(false)

async function loadLogs() {
  loading.value = true
  try {
    const params = new URLSearchParams({
      page: String(page.value), page_size: String(pageSize.value),
      keyword: filters.keyword || '', device_id: filters.device_id || '',
      start: filters.range?.[0] || '', end: filters.range?.[1] || ''
    })
    const data = await apiGet(`/api/chat/conversations/detail?${params}`)
    conversations.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    conversations.value = []
    total.value = 0
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
    detailMessages.value = await apiGet(`/api/chat/conversations/${convId}`)
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
.state-mark { width: 52px; height: 52px; margin-bottom: 12px; }

.detail-messages { max-height: 65vh; overflow-y: auto; padding-right: 6px; }
.detail-row { display: flex; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--sfa-border-soft); }
.detail-role-tag { flex-shrink: 0; width: 52px; }
.detail-content { flex: 1; font-size: 13px; line-height: 1.7; }
.tool-content { font-size: 12px; }
.tool-content pre { background: #0D1424; color: #BFC9E4; padding: 10px 12px; border-radius: 9px; white-space: pre-wrap; word-break: break-all; margin-top: 6px; font-family: var(--sfa-mono); }
.md-body { line-height: 1.7; }
.md-body :deep(p) { margin: 4px 0; }
.md-body :deep(code) { background: #EEF1FA; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
</style>
