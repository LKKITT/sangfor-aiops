<template>
  <div class="confirm-card">
    <div class="cf-head">
      <el-icon class="cf-ico"><WarningFilled /></el-icon>
      <span class="cf-title">{{ confirm.title }}</span>
    </div>
    <div v-if="confirm.warning" class="cf-warning">⚠ {{ confirm.warning }}</div>

    <!-- 高危操作标识 -->
    <div v-if="isHighRisk" class="cf-highrisk">
      <el-icon><WarningFilled /></el-icon> <b>高危操作</b>：{{ highRiskReason }}
    </div>

    <!-- 定向冲突核实（只针对本配置） -->
    <div v-if="confirm.conflicts?.length" class="cf-conflicts">
      <div class="cf-sec-label">
        定向核实：本配置与现有配置的冲突/重叠（{{ confirm.conflicts.length }} 项，不含无关配置）
      </div>
      <div v-for="(cf, ci) in confirm.conflicts" :key="ci" class="cf-conflict-item">
        <el-tag :type="cf.level === 'high' ? 'danger' : cf.level === 'medium' ? 'warning' : 'info'" size="small">
          {{ cf.level === 'high' ? '冲突' : cf.level === 'medium' ? '重叠' : '冗余' }}
        </el-tag>
        <span class="cf-conflict-text">{{ cf.text }}</span>
        <div class="cf-conflict-sug">{{ cf.suggestion }}</div>
      </div>
    </div>
    <div v-else-if="confirm.conflicts && confirm.op !== 'delete'" class="cf-noconflict">
      ✓ 定向核实：与现有配置无冲突、无重叠
    </div>

    <!-- 交互式表单（按变更类型选择，带提交前校验） -->
    <div v-if="formType" class="cf-form">
      <BindingForm v-if="formType === 'binding'" ref="formRef" :action="confirm" />
      <RuleForm v-else-if="formType === 'nat'" ref="formRef" :action="confirm" resource="nat" />
      <RuleForm v-else-if="formType === 'acl'" ref="formRef" :action="confirm" resource="acl" />
      <ObjectForm v-else-if="formType === 'object'" ref="formRef" :action="confirm" />
      <ServiceForm v-else ref="formRef" :action="confirm" />
    </div>

    <!-- 规则变更 before/after -->
    <div v-if="!formType && (confirm.before || confirm.after)" class="cf-diff mono">
      <div v-if="confirm.before" class="diff-removed">- {{ fmtRule(confirm.before) }}</div>
      <div v-if="confirm.after" class="diff-added">+ {{ fmtRule(confirm.after) }}</div>
    </div>

    <!-- 批量变更：逐台设备计划 -->
    <div v-if="confirm.batch_devices?.length" class="cf-batch">
      <div class="cf-sec-label">批量下发（{{ confirm.batch_devices.length }} 台设备，确认后逐台执行）</div>
      <div v-for="(b, bi) in confirm.batch_devices" :key="bi" class="cf-batch-item">
        <el-tag size="small" type="info">{{ b.device }}</el-tag>
        <span class="cf-batch-title">{{ b.title }}</span>
        <div v-if="b.warning" class="cf-batch-warn">{{ b.warning }}</div>
      </div>
    </div>

    <!-- 恢复计划 -->
    <div v-if="confirm.plan" class="cf-plan">
      <div class="cf-plan-line">
        共 <b>{{ confirm.plan.total }}</b> 项变更：
        <el-tag v-for="g in ['delete','update','create']" :key="g" size="small" class="cf-plan-tag"
                :type="g === 'delete' ? 'danger' : g === 'update' ? 'warning' : 'success'">
          {{ { delete: '删除', update: '修改', create: '重建' }[g] }} {{ confirm.plan[g]?.length || 0 }} 项
        </el-tag>
      </div>
      <el-collapse class="cf-plan-detail">
        <el-collapse-item title="查看详细变更清单">
          <div v-for="(it, k) in allPlanItems" :key="k" class="mono cf-plan-item">
            {{ it }}
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>

    <div v-if="confirm.status === 'pending'" class="cf-actions">
      <el-button type="primary" size="small" :loading="confirming" @click="decide(true)">
        <el-icon><Check /></el-icon>&nbsp;确认执行
      </el-button>
      <el-button type="danger" plain size="small" :disabled="confirming" @click="decide(false)">
        <el-icon><Close /></el-icon>&nbsp;拒绝
      </el-button>
    </div>
    <div v-else-if="confirm.status === 'executed'" class="cf-status ok">
      <el-icon><CircleCheckFilled /></el-icon> 已执行
      <el-button v-if="confirm.safety_backup_id" size="small" text type="primary"
                 @click="router.push({ path: '/backup', query: { highlight: confirm.safety_backup_id } })">
        查看回退点（变更前备份 {{ confirm.safety_backup_id.slice(0, 12) }}…）
      </el-button>
    </div>
    <div v-else-if="confirm.status === 'rejected'" class="cf-status dim">已拒绝</div>
    <div v-else class="cf-status dim">{{ confirm.status }}</div>

    <!-- 高危操作二次确认弹窗 -->
    <el-dialog v-model="showSecondConfirm" title="二次确认" width="420px" :close-on-click-modal="false" append-to-body>
      <div class="second-confirm">
        <div class="sc-ico"><el-icon :size="30"><WarningFilled /></el-icon></div>
        <div class="sc-title">高危操作确认</div>
        <div class="sc-reason">{{ secondConfirmReason }}</div>
        <div class="sc-note">此操作可能影响业务，请确认已充分评估风险</div>
      </div>
      <template #footer>
        <el-button @click="showSecondConfirm = false">取消</el-button>
        <el-button type="danger" @click="doSecondConfirm">确认执行高危操作</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
const router = useRouter()
import BindingForm from './forms/BindingForm.vue'
import RuleForm from './forms/RuleForm.vue'
import ObjectForm from './forms/ObjectForm.vue'
import ServiceForm from './forms/ServiceForm.vue'

const props = defineProps({
  confirm: { type: Object, required: true },
  confirming: { type: Boolean, default: false },
})
const emit = defineEmits(['decide'])

const formRef = ref(null)
const showSecondConfirm = ref(false)
const secondConfirmReason = ref('')

// 表单类型判定（迁移自 ChatView：绑定创建 / NAT / ACL / 网络对象 / 自定义服务 编辑）
const isBindingCreate = computed(() =>
  props.confirm?.op === 'create' && (props.confirm?.resource === 'binding' || props.confirm?.tool_name === 'create_user_binding'))

function isResourceEdit(type) {
  const c = props.confirm
  if (!c) return false
  const op = c.op || ''
  if (op !== 'create' && op !== 'update') return false
  return (c.resource || c.tool_name || '').toLowerCase().includes(type)
}

const formType = computed(() => {
  const c = props.confirm
  if (isBindingCreate.value) return 'binding'
  if (isResourceEdit('nat')) return 'nat'
  if (isResourceEdit('acl')) return 'acl'
  if (isResourceEdit('object')) return 'object'
  if (isResourceEdit('service')) return 'service'
  return null
})

// ---- 高危判定（迁移自 ChatView，口径不变） ----
const HIGH_RISK_PORTS = ['445', '139', '135', '3389', '22', '23', '2049', '6379', '27017', '3306', '1433']

const isHighRisk = computed(() => {
  const c = props.confirm
  if (!c) return false
  if (c.op === 'delete') return true
  const service = (c.after?.service || c.data?.service || '').toLowerCase()
  if (HIGH_RISK_PORTS.some(p => service.includes(p))) return true
  if (c.after?.action === 'allow' && (c.after?.dst_zone === 'untrust' || c.data?.dst_zone === 'untrust')) {
    const src = c.after?.src_addr || ''
    if (src === 'any' || src === '0.0.0.0/0') return true
  }
  if (c.plan) return true
  return false
})

const highRiskReason = computed(() => {
  const c = props.confirm
  if (c?.op === 'delete') return `将要删除 ${c.resource || '配置'}，删除后相关业务将受影响`
  const service = (c?.after?.service || c?.data?.service || '').toLowerCase()
  const matched = HIGH_RISK_PORTS.filter(p => service.includes(p))
  if (matched.length) return `操作涉及高危端口 ${matched.join(', ')}，可能被利用进行远程攻击`
  if (c?.after?.action === 'allow' && (c?.after?.dst_zone === 'untrust' || c?.data?.dst_zone === 'untrust')) {
    return '该策略允许任意地址访问公网，可能造成数据泄露或资源滥用'
  }
  if (c?.plan) return '恢复操作将覆盖当前配置，请确认备份文件正确'
  return '该操作涉及业务配置变更，请确认风险'
})

const allPlanItems = computed(() => {
  const plan = props.confirm?.plan
  if (!plan) return []
  return [...(plan.delete || []), ...(plan.update || []), ...(plan.create || [])]
})

function fmtRule(r) {
  return JSON.stringify(r)
}

// ---- 确认/拒绝：表单校验 → 高危二次确认 → 上抛 ----
async function decide(approved) {
  let edited = null
  if (approved && formType.value && formRef.value) {
    try {
      await formRef.value.validate()
    } catch {
      ElMessage.warning('请先修正表单中标红的字段')
      return
    }
    edited = { data: formRef.value.getData() }
  }
  if (approved && isHighRisk.value) {
    secondConfirmReason.value = highRiskReason.value
    pendingApproved = true
    showSecondConfirm.value = true
    return
  }
  emit('decide', approved, edited)
}

let pendingApproved = true
function doSecondConfirm() {
  showSecondConfirm.value = false
  if (pendingApproved) {
    let edited = null
    if (formType.value && formRef.value) edited = { data: formRef.value.getData() }
    emit('decide', true, edited)
  }
}
</script>

<style scoped>
.confirm-card { margin-top: 10px; }
/* ===== 确认卡片内部 ===== */
.cf-head { display: flex; align-items: center; gap: 7px; font-weight: 650; margin-bottom: 8px; font-size: 13.5px; }
.cf-ico { color: #D08700; font-size: 16px; }
.cf-warning { color: var(--sfa-warn-ink); margin-bottom: 8px; font-size: 12.5px; }
.cf-highrisk {
  margin-bottom: 10px; padding: 7px 11px; border-radius: 8px;
  background: #FDEEEF; border: 1px solid #FBD8D9; font-size: 12px; color: #D33A40;
  display: flex; align-items: center; gap: 4px;
}
.cf-conflicts { margin-bottom: 10px; }
.cf-sec-label { font-size: 12px; color: var(--sfa-text-3); margin-bottom: 6px; }
.cf-conflict-item {
  font-size: 12px; background: var(--sfa-surface); border-left: 3px solid var(--sfa-warning);
  padding: 5px 9px; margin-bottom: 5px; border-radius: 0 7px 7px 0;
}
.cf-conflict-text { margin-left: 4px; }
.cf-conflict-sug { color: var(--sfa-text-3); margin-top: 3px; padding-left: 2px; }
.cf-noconflict { font-size: 12px; color: #0E9F6E; margin-bottom: 8px; }
.cf-form { background: var(--sfa-surface); border-radius: 9px; padding: 12px; border: 1px solid var(--sfa-warn-border); margin-bottom: 8px; }
.cf-diff { font-size: 12px; background: var(--sfa-surface); border-radius: 9px; padding: 9px 11px; border: 1px solid var(--sfa-warn-border); }
.cf-batch { margin-bottom: 10px; }
.cf-batch-item {
  font-size: 12px; background: var(--sfa-surface); border: 1px solid var(--sfa-border-soft);
  border-radius: 8px; padding: 6px 9px; margin-bottom: 5px;
}
.cf-batch-title { margin-left: 6px; }
.cf-batch-warn { color: var(--sfa-warn-ink); margin-top: 3px; }
.cf-plan { font-size: 12.5px; }
.cf-plan-tag { margin: 2px 3px; }
.cf-plan-detail { margin-top: 6px; }
.cf-plan-item { font-size: 12px; padding: 2.5px 0; }
.cf-actions { margin-top: 12px; display: flex; gap: 8px; }
.cf-status { display: inline-flex; align-items: center; gap: 5px; margin-top: 10px; font-size: 12.5px; font-weight: 600; }
.cf-status.ok { color: #0E9F6E; }
.cf-status.dim { color: var(--sfa-text-4); }
.bind-note { font-size: 12px; color: var(--sfa-text-3); margin-left: 8px; }

/* 二次确认弹窗 */
.second-confirm { padding: 8px 0 4px; text-align: center; }
.sc-ico { color: var(--sfa-danger); margin-bottom: 10px; }
.sc-title { font-weight: 700; font-size: 15px; margin-bottom: 8px; }
.sc-reason { color: var(--sfa-text-2); font-size: 13px; margin-bottom: 14px; }
.sc-note { color: var(--sfa-danger); font-size: 12px; background: #FDEEEF; padding: 9px; border-radius: 8px; }
</style>
