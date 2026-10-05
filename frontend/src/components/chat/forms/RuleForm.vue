<template>
  <el-form ref="formRef" :model="form" :rules="rules" label-width="100px" size="small" style="max-width: 480px">
    <el-form-item label="名称" prop="name">
      <el-input v-model="form.name" />
    </el-form-item>
    <el-form-item label="源区域" prop="src_zone">
      <el-input v-model="form.src_zone" placeholder="trust / untrust / dmz" />
    </el-form-item>
    <el-form-item label="目的区域" prop="dst_zone">
      <el-input v-model="form.dst_zone" placeholder="trust / untrust / dmz" />
    </el-form-item>
    <el-form-item label="源地址" prop="src_addr">
      <el-input v-model="form.src_addr" placeholder="IP/网段/范围或地址组名，逗号分隔" />
    </el-form-item>
    <el-form-item label="目的地址" prop="dst_addr">
      <el-input v-model="form.dst_addr" placeholder="IP/网段/范围或地址组名，逗号分隔" />
    </el-form-item>
    <el-form-item label="服务" prop="service">
      <el-input v-model="form.service" placeholder="服务名或端口，如 https / 443 / TCP5211" />
    </el-form-item>
    <el-form-item v-if="resource === 'nat'" label="转换地址" prop="translated_addr">
      <el-input v-model="form.translated_addr" placeholder="如 10.0.0.1 / 10.0.0.1-10.0.0.9" />
    </el-form-item>
    <el-form-item v-if="resource === 'acl'" label="动作">
      <el-radio-group v-model="form.action">
        <el-radio value="allow">允许</el-radio>
        <el-radio value="deny">拒绝</el-radio>
      </el-radio-group>
    </el-form-item>
    <el-form-item label="启用">
      <el-switch v-model="form.enabled" />
    </el-form-item>
    <el-form-item label="备注">
      <el-input v-model="form.comment" />
    </el-form-item>
  </el-form>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { required, validAddrField } from './validators'

const props = defineProps({
  action: { type: Object, required: true },
  resource: { type: String, required: true },   // 'nat' | 'acl'
})

const formRef = ref(null)
const form = reactive({
  name: '', src_zone: '', dst_zone: '', src_addr: '', dst_addr: '',
  service: '', translated_addr: '', enabled: true, action: 'allow', comment: ''
})

const rules = {
  name: [required('请填写名称')],
  src_addr: [{ validator: validAddrField, trigger: 'blur' }],
  dst_addr: [{ validator: validAddrField, trigger: 'blur' }],
}

watch(() => props.action, (a) => {
  const after = a?.after || a?.data || {}
  form.name = after.name || ''
  form.src_zone = after.src_zone || ''
  form.dst_zone = after.dst_zone || ''
  form.src_addr = after.src_addr || ''
  form.dst_addr = after.dst_addr || ''
  form.service = after.service || ''
  form.translated_addr = after.translated_addr || ''
  form.enabled = after.enabled !== false
  form.action = after.action || 'allow'
  form.comment = after.comment || after.desc || ''
}, { immediate: true })

async function validate() {
  await formRef.value.validate()
}

function getData() {
  const data = { name: form.name, src_zone: form.src_zone, dst_zone: form.dst_zone,
                 src_addr: form.src_addr, dst_addr: form.dst_addr, service: form.service,
                 enabled: form.enabled, comment: form.comment }
  if (props.resource === 'nat') data.translated_addr = form.translated_addr
  if (props.resource === 'acl') data.action = form.action
  return data
}

defineExpose({ validate, getData })
</script>
