// AI 对话 SSE 事件归约（纯逻辑，与视图解耦，供 ChatView 与单测共用）。
// 事件契约单一来源：docs/sse-events.schema.json（后端 app/agent/events.py 导出，
// 测试守卫漂移）。修改事件字段需同步两端。
// 归约只操作传入的 aiMsg 对象，不依赖 Vue 响应式——reactive 与否由调用方决定。

export const TOOL_NAMES = {
  get_device_status: '查询设备状态', get_interfaces: '查询接口', get_nat_rules: '查询 NAT',
  get_acl_rules: '查询访问控制策略', get_user_bindings: '查询用户绑定', get_static_routes: '查询路由',
  get_network_objects: '查询网络对象', get_services: '查询自定义服务',
  run_config_checkup: '运行配置体检', create_backup: '创建备份', list_backups: '查询备份列表',
  diff_backups: '对比备份差异', get_software_updates: '获取软件更新信息', get_upgrade_advice: '生成升级建议',
  get_audit_logs: '查询审计日志', restore_backup: '生成恢复计划', execute_restore: '执行恢复',
  search_official_knowledge: '查询官方知识库', search_personal_kb: '检索本地知识库',
  get_scp_clusters: '查询 SCP 集群', get_scp_hosts: '查询 SCP 物理机',
  get_scp_host_interfaces: '查询物理机网口', get_scp_vms: '查询 SCP 虚拟机',
  get_scp_vm_detail: '查询虚拟机详情', get_scp_storages: '查询 SCP 存储',
  record_to_kb: '沉淀对话到知识库', ingest_url_to_kb: '沉淀链接到知识库',
  list_available_devices: '查询设备列表', add_device: '添加设备',
  create_nat_rule: '新建 NAT', update_nat_rule: '修改 NAT', delete_nat_rule: '删除 NAT',
  create_acl_rule: '新建策略', update_acl_rule: '修改策略', delete_acl_rule: '删除策略',
  create_user_binding: '新建绑定', update_user_binding: '修改绑定', delete_user_binding: '删除绑定',
  create_network_object: '新建网络对象', update_network_object: '修改网络对象', delete_network_object: '删除网络对象',
  create_service: '新建自定义服务', update_service: '修改自定义服务', delete_service: '删除自定义服务',
  get_whiteblacklist: '查询黑白名单',
  create_whiteblacklist: '添加黑白名单', update_whiteblacklist: '修改黑白名单', delete_whiteblacklist: '删除黑白名单',
  netdev_list_devices: '查询网络设备列表', netdev_get_status: '网络设备健康查询',
  netdev_get_config: '网络设备配置查询', netdev_get_interfaces: '网络设备接口查询',
  netdev_get_routes: '网络设备路由查询', netdev_get_arp: 'ARP 表项查询',
  netdev_get_logs: '网络设备日志分析', netdev_locate_terminal: '终端定位',
  netdev_apply_config: '下发网络设备配置', netdev_run_commands: '执行网络设备命令'
}

export function toolLabel(name) {
  return TOOL_NAMES[name] || name
}

export function createAssistantMsg() {
  return { role: 'assistant', text: '', trace: [], confirm: null, _currentTool: null, failed: null }
}

/**
 * 把一条 SSE 事件归并进助手消息（与后端 orchestrator 事件序契约一致）：
 * - token：追加正文
 * - tool_call / tool_result：trace 按工具名配对收尾（并行轮次"先全部 call 后全部 result"同样兼容）
 * - confirm_required：挂起确认卡片（handlers.onConfirm 供视图滚动等副作用）
 * - error：标记 failed（视图据此渲染重试入口）并追加文案
 * handlers: { onMeta(convId), onConfirm() }
 */
export function applyEvent(aiMsg, ev, handlers = {}) {
  switch (ev.type) {
    case 'meta':
      handlers.onMeta?.(ev.conv_id)
      break
    case 'token':
      aiMsg.text += ev.text
      break
    case 'tool_call': {
      const label = toolLabel(ev.name)
      aiMsg._currentTool = label
      aiMsg.trace.push(`${label} …`)
      break
    }
    case 'tool_result': {
      const label = toolLabel(ev.name)
      const idx = aiMsg.trace.lastIndexOf(`${label} …`)
      if (idx >= 0) aiMsg.trace[idx] = `${label} ✓`
      else aiMsg.trace.push(`${label} ✓`)
      aiMsg._currentTool = null
      break
    }
    case 'confirm_required':
      aiMsg.confirm = { ...ev.action, status: 'pending' }
      handlers.onConfirm?.()
      break
    case 'confirm_result':
      handlers.onConfirmResult?.(ev)
      break
    case 'offline_notice':
      aiMsg.text += `\n\n> ${ev.text}`
      break
    case 'error':
      aiMsg.failed = ev.text
      aiMsg.text += `\n\n**出错了**：${ev.text}`
      break
    case 'done':
      break
  }
  return aiMsg
}
