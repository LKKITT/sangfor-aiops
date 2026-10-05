<template>
  <el-form ref="formRef" :model="form" :rules="rules" label-width="100px" size="small" style="max-width: 480px">
    <el-form-item label="名称" prop="name">
      <el-input v-model="form.name" />
    </el-form-item>
    <el-form-item label="成员地址" prop="members">
      <el-input v-model="form.members" placeholder="如：10.0.0.0/24, 192.168.1.5" />
    </el-form-item>
    <el-form-item label="备注">
      <el-input v-model="form.comment" />
    </el-form-item>
  </el-form>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { required, validMembersField } from './validators'

const props = defineProps({ action: { type: Object, required: true } })

const formRef = ref(null)
const form = reactive({ name: '', members: '', comment: '' })

const rules = {
  name: [required('请填写名称')],
  members: [required('请填写成员地址'), { validator: validMembersField, trigger: 'blur' }],
}

watch(() => props.action, (a) => {
  const after = a?.after || a?.data || {}
  form.name = after.name || ''
  form.members = after.members || ''
  form.comment = after.comment || after.desc || ''
}, { immediate: true })

async function validate() {
  await formRef.value.validate()
}

function getData() {
  return { name: form.name, members: form.members, comment: form.comment }
}

defineExpose({ validate, getData })
</script>
