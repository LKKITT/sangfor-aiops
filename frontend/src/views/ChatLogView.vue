<template>
  <div class="chatlog-page">
    <div class="page-card">
      <div class="header">
        <b>对话日志</b>
        <span class="subtitle">记录所有对话内容及结果，用于审计与经验回顾</span>
        <el-button size="small" style="margin-left: auto" @click="loadLogs" :loading="loading">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
      </div>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <el-input v-model="filters.keyword" size="small" clearable placeholder="搜索标题/消息内容"
                  style="width: 220px" @keyup.enter="applyFilters" @clear="applyFilters" />
        <el-select v-model="filters.device_id" size="small" clearable placeholder="全部设备"
                   style="width: 170px" @change="applyFilters">
          <el-option v-for="d in store.devices" :key="d.id" :label="d.name" :value="d.id" />
        </el-select>
        <el-date-picker v-model="filters.range" type="daterange" value-format="YYYY-MM-DD" size="small"
                        start-placeholder="开始日期" end-placeholder="结束日期"
                        style="width: 240px" @change="applyFilters" />
        <el-button size="small" type="primary" @click="applyFilters">查询</el-button>
        <el-button size="small" @click="resetFilters">重置</el-button>
      </div>

      <div v-if="loading" style="text-align: center; padding: 40px; color: #909399">
        <el-icon class="is-loading" style="font-size: 24px"><Loading /></el-icon>
        <div style="margin-top: 8px">加载对话日志…</div>
      </div>

      <div v-else-if="!conversations.length" style="text-align: center; padding: 40px; color: #909399">
        <el-icon style="font-size: 48px; color: #c0c4cc"><ChatDotRound /></el-icon>
        <p style="margin-top: 12px">没有符合条件的对话日志</p>
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
    <el-dialog v-model="showDetailDialog" :title="detailTitle" width="700px" top="5vh">
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
import MarkdownIt from 'markdown-it'
import { apiGet } from '../api.js'
import { store } from '../store.js'

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

onMounted(loadLogs)
</script>

<style scoped>
.chatlog-page { max-width: 1000px; margin: 0 auto; }
.header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.subtitle { font-size: 12px; color: #909399; }
.filter-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
.conv-link { cursor: pointer; color: #409eff; }
.conv-link:hover { text-decoration: underline; }
.muted { color: #909399; font-size: 12px; }
.detail-messages { max-height: 65vh; overflow-y: auto; }
.detail-row { display: flex; gap: 10px; padding: 10px 0; border-bottom: 1px solid #f0f0f0; }
.detail-role-tag { flex-shrink: 0; width: 50px; }
.detail-content { flex: 1; font-size: 13px; line-height: 1.6; }
.tool-content { font-size: 12px; }
.tool-content pre { background: #f5f7fa; padding: 6px; border-radius: 4px; white-space: pre-wrap; word-break: break-all; margin-top: 4px; }
.md-body { line-height: 1.7; }
.md-body :deep(p) { margin: 4px 0; }
.md-body :deep(code) { background: #f0f2f5; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
</style>
