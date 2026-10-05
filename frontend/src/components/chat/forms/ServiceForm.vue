<template>
  <el-form ref="formRef" :model="form" :rules="rules" label-width="100px" size="small" style="max-width: 480px">
    <el-form-item label="名称" prop="name">
      <el-input v-model="form.name" />
    </el-form-item>
    <el-form-item label="协议">
      <el-radio-group v-model="form.protocol">
        <el-radio value="TCP">TCP</el-radio>
        <el-radio value="UDP">UDP</el-radio>
        <el-radio value="TCP/UDP">TCP/UDP</el-radio>
      </el-radio-group>
    </el-form-item>
    <el-form-item label="端口" prop="ports">
      <el-input v-model="form.ports" placeholder="如：80,443 或 8000-9000" />
    </el-form-item>
    <el-form-item label="备注">
      <el-input v-model="form.comment" />
    </el-form-item>
  </el-form>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { required, validPortField } from './validators'

const props = defineProps({ action: { type: Object, required: true } })

const formRef = ref(null)
const form = reactive({ name: '', protocol: 'TCP', ports: '', comment: '' })

const rules = {
  name: [required('请填写名称')],
  ports: [required('请填写端口'), { validator: validPortField, trigger: 'blur' }],
}

watch(() => props.action, (a) => {
  const after = a?.after || a?.data || {}
  form.name = after.name || ''
  form.protocol = after.protocol || 'TCP'
  form.ports = after.ports || ''
  form.comment = after.comment || after.desc || ''
}, { immediate: true })

async function validate() {
  await formRef.value.validate()
}

function getData() {
  return { name: form.name, protocol: form.protocol, ports: form.ports, comment: form.comment }
}

defineExpose({ validate, getData })
</script>
