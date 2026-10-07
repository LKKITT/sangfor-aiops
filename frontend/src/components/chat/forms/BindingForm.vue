<template>
  <el-form ref="formRef" :model="form" :rules="rules" label-width="92px" size="small" style="max-width: 460px">
    <el-form-item label="用户名" prop="user">
      <el-input v-model="form.user" placeholder="用户名，如：张三" />
    </el-form-item>
    <el-form-item label="IP 地址" prop="ip">
      <el-input v-model="form.ip" placeholder="如：192.168.1.1" />
    </el-form-item>
    <el-form-item label="MAC 地址" prop="mac">
      <el-input v-model="form.mac" placeholder="如：11-22-33-44-55-66" />
    </el-form-item>
    <el-form-item label="免认证">
      <el-switch v-model="form.noauth" />
      <span class="bind-note">开启后该用户流量不经认证直接放行</span>
    </el-form-item>
    <el-form-item label="限制登录">
      <el-switch v-model="form.limitlogon" />
      <span class="bind-note">开启后限制该绑定登录</span>
    </el-form-item>
    <el-form-item label="有效期">
      <el-tag size="small" type="success">永久有效（noauth.expire_time=0）</el-tag>
    </el-form-item>
    <el-form-item label="描述">
      <el-input v-model="form.comment" placeholder="选填" />
    </el-form-item>
    <el-form-item label="绑定目的">
      <el-input v-model="form.purpose" placeholder="选填，如：办公终端准入" />
    </el-form-item>
  </el-form>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { required, validAddrField, validMac } from './validators'

const props = defineProps({ action: { type: Object, required: true } })

const formRef = ref(null)
const form = reactive({ user: '', ip: '', mac: '', noauth: false, limitlogon: false, comment: '', purpose: '' })

const rules = {
  user: [required('请填写用户名')],
  ip: [required('请填写 IP 地址'), { validator: validAddrField, trigger: 'blur' }],
  mac: [required('请填写 MAC 地址'), { validator: validMac, trigger: 'blur' }],
}

// 以确认计划的 after 字段初始化表单（用户可改，确认时提交 edited）
watch(() => props.action, (a) => {
  const after = a?.after || {}
  form.user = after.user || ''
  form.ip = after.ip || ''
  form.mac = after.mac || ''
  form.noauth = !!after.noauth
  form.limitlogon = !!after.limitlogon
  form.comment = after.comment || after.desc || ''
  form.purpose = after.purpose || ''
}, { immediate: true })

async function validate() {
  await formRef.value.validate()
}

function getData() {
  return { user: form.user, ip: form.ip, mac: form.mac, noauth: form.noauth,
           limitlogon: form.limitlogon, comment: form.comment, purpose: form.purpose }
}

defineExpose({ validate, getData })
</script>

<style scoped>
.bind-note { margin-left: 10px; font-size: 12px; color: var(--sfa-text-3, #909399); }
</style>
