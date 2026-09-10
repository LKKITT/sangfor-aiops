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

      <div v-if="loading" style="text-align: center; padding: 40px; color: #909399">
        <el-icon class="is-loading" style="font-size: 24px"><Loading /></el-icon>
        <div style="margin-top: 8px">加载对话日志…</div>
      </div>

      <div v-else-if="!conversations.length" style="text-align: center; padding: 40px; color: #909399">
        <el-icon style="font-size: 48px; color: #c0c4cc"><ChatDotRound /></el-icon>
        <p style="margin-top: 12px">暂无对话日志，开始使用 AI 对话后会自动记录</p>
      </div>

      <el-timeline v-else>
        <el-timeline-item
          v-for="conv in conversations"
          :key="conv.id"
          :timestamp="conv.updated_at"
          placement="top"
          :type="conv.msg_count > 0 ? 'primary' : 'info'"
        >
          <div class="conv-item" @click="showDetail(conv.id)">
            <div class="conv-title">
              <el-tag size="small" :type="conv.msg_count > 1 ? 'success' : 'info'" style="margin-right: 6px">
                {{ conv.msg_count }} 条消息
              </el-tag>
              {{ conv.title }}
              <span v-if="conv.device_name" class="conv-device">
                <el-icon><Monitor /></el-icon> {{ conv.device_name }}
              </span>
            </div>
            <div v-if="conv.last_message" class="conv-preview">{{ conv.last_message }}</div>
            <div v-if="conv.summary" class="conv-summary">
              <el-icon><Memo /></el-icon> {{ conv.summary }}
            </div>
            <div class="conv-meta">
              {{ conv.created_at }}
            </div>
          </div>
        </el-timeline-item>
      </el-timeline>
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
import { ref, onMounted } from 'vue'
import MarkdownIt from 'markdown-it'
import { apiGet } from '../api.js'

const md = new MarkdownIt({ breaks: true })
const render = (text) => md.render(text || '')

const loading = ref(false)
const conversations = ref([])
const showDetailDialog = ref(false)
const detailTitle = ref('')
const detailMessages = ref([])
const loadingDetail = ref(false)

async function loadLogs() {
  loading.value = true
  try {
    conversations.value = await apiGet('/api/chat/conversations/detail')
  } catch (e) {
    conversations.value = []
  } finally {
    loading.value = false
  }
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
.chatlog-page { max-width: 900px; margin: 0 auto; }
.header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.subtitle { font-size: 12px; color: #909399; }
.conv-item { cursor: pointer; padding: 8px 12px; border-radius: 6px; transition: background 0.2s; }
.conv-item:hover { background: #f5f7fa; }
.conv-title { font-weight: 500; margin-bottom: 4px; }
.conv-device { margin-left: 8px; font-size: 12px; color: #909399; }
.conv-preview { font-size: 12px; color: #606266; margin-bottom: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conv-summary { font-size: 12px; color: #909399; margin-top: 2px; }
.conv-meta { font-size: 11px; color: #c0c4cc; margin-top: 4px; }
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