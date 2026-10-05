import { describe, it, expect, vi } from 'vitest'
import { applyEvent, createAssistantMsg, toolLabel, TOOL_NAMES } from '../agentStream'

// SSE 事件归约回归（对应后端 orchestrator 事件契约）：
// 重点守护"并行轮次 = 先全部 tool_call 后全部 tool_result"的按名配对时序。

const msg = () => createAssistantMsg()

describe('toolLabel', () => {
  it('已知工具给中文名，未知工具回退原名', () => {
    expect(toolLabel('get_device_status')).toBe('查询设备状态')
    expect(toolLabel('unknown_tool')).toBe('unknown_tool')
    expect(TOOL_NAMES.get_device_status).toBeTruthy()
  })
})

describe('applyEvent: token / offline / error', () => {
  it('token 逐段追加', () => {
    const m = msg()
    applyEvent(m, { type: 'token', text: '你好' })
    applyEvent(m, { type: 'token', text: '，世界' })
    expect(m.text).toBe('你好，世界')
  })

  it('offline_notice 以引用块追加', () => {
    const m = msg()
    applyEvent(m, { type: 'offline_notice', text: '离线兜底模式' })
    expect(m.text).toContain('> 离线兜底模式')
  })

  it('error 标记 failed 并追加文案（视图据此渲染重试入口）', () => {
    const m = msg()
    applyEvent(m, { type: 'error', text: '模型调用失败' })
    expect(m.failed).toBe('模型调用失败')
    expect(m.text).toContain('**出错了**：模型调用失败')
  })

  it('done 无副作用', () => {
    const m = msg()
    applyEvent(m, { type: 'done' })
    expect(m).toEqual({ role: 'assistant', text: '', trace: [], confirm: null, _currentTool: null, failed: null })
  })
})

describe('applyEvent: tool trace 配对', () => {
  it('串行轮次：call → result 一一配对', () => {
    const m = msg()
    applyEvent(m, { type: 'tool_call', name: 'get_device_status' })
    expect(m.trace).toEqual(['查询设备状态 …'])
    applyEvent(m, { type: 'tool_result', name: 'get_device_status' })
    expect(m.trace).toEqual(['查询设备状态 ✓'])
  })

  it('并行轮次：先全部 call 后乱序 result，按工具名配对', () => {
    const m = msg()
    for (const n of ['get_device_status', 'get_zones', 'get_interfaces']) {
      applyEvent(m, { type: 'tool_call', name: n })
    }
    expect(m.trace).toEqual(['查询设备状态 …', 'get_zones …', '查询接口 …'])   // get_zones 未注册中文名，回退原名
    applyEvent(m, { type: 'tool_result', name: 'get_interfaces' })   // 乱序：最后调用的先回
    applyEvent(m, { type: 'tool_result', name: 'get_device_status' })
    applyEvent(m, { type: 'tool_result', name: 'get_zones' })
    expect(m.trace.every(t => t.endsWith('✓'))).toBe(true)
    expect(m._currentTool).toBeNull()
  })

  it('同一工具多实例配对：lastIndexOf 收尾最后一个未完成项', () => {
    const m = msg()
    applyEvent(m, { type: 'tool_call', name: 'get_nat_rules' })
    applyEvent(m, { type: 'tool_call', name: 'get_nat_rules' })
    applyEvent(m, { type: 'tool_result', name: 'get_nat_rules' })
    applyEvent(m, { type: 'tool_result', name: 'get_nat_rules' })
    expect(m.trace).toEqual(['查询 NAT ✓', '查询 NAT ✓'])
  })

  it('无对应 call 的 result 直接以完成态入 trace（恢复历史的兜底形态）', () => {
    const m = msg()
    applyEvent(m, { type: 'tool_result', name: 'get_status' })
    expect(m.trace).toEqual(['get_status ✓'])
  })
})

describe('applyEvent: confirm / meta', () => {
  it('confirm_required 挂起卡片并触发 onConfirm 副作用', () => {
    const m = msg()
    const onConfirm = vi.fn()
    const action = { action_id: 'act_1', title: '修改 NAT' }
    applyEvent(m, { type: 'confirm_required', action }, { onConfirm })
    expect(m.confirm).toEqual({ ...action, status: 'pending' })
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('confirm_result 经 onConfirmResult 回传回退点 id（不改动消息本体）', () => {
    const m = msg()
    const onConfirmResult = vi.fn()
    applyEvent(m, { type: 'confirm_result', action_id: 'act_1', approved: true,
                    safety_backup_id: 'bk_abc' }, { onConfirmResult })
    expect(onConfirmResult).toHaveBeenCalledWith(expect.objectContaining({
      action_id: 'act_1', safety_backup_id: 'bk_abc',
    }))
    expect(m.text).toBe('')
  })

  it('meta 经 onMeta 回调回写会话 ID', () => {
    const onMeta = vi.fn()
    applyEvent(msg(), { type: 'meta', conv_id: 'conv_x' }, { onMeta })
    expect(onMeta).toHaveBeenCalledWith('conv_x')
  })
})
