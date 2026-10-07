import { describe, it, expect } from 'vitest'
import { isNearBottom, distanceToBottom, BOTTOM_THRESHOLD } from '../scrollGuard'

/**
 * 回归测试：聊天区滚动保护
 *
 * 对应缺陷：ChatView.scrollBottom() 原实现无条件滚底，
 * 用户上滚查看历史时会被流式输出持续拽回底部。
 */
describe('isNearBottom 上滚保护判定', () => {
  it('完全贴底时返回 true（允许自动跟随）', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 500, clientHeight: 500 })).toBe(true)
  })

  it('距底在阈值内仍视为贴底', () => {
    // 距底 79px < 80px
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 421, clientHeight: 500 })).toBe(true)
  })

  it('距底超过阈值时返回 false（用户已上滚，停止跟随）', () => {
    // 距底 300px
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 200, clientHeight: 500 })).toBe(false)
  })

  it('阈值边界：恰好等于阈值不视为贴底', () => {
    // 距底恰为 80px，条件为 < 80，故 false
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 420, clientHeight: 500 })).toBe(false)
  })

  it('容器未挂载（null）时按贴底处理，保证首次渲染能滚到底', () => {
    expect(isNearBottom(null)).toBe(true)
  })

  it('支持自定义阈值', () => {
    const el = { scrollHeight: 1000, scrollTop: 300, clientHeight: 500 }  // 距底 200
    expect(isNearBottom(el, 250)).toBe(true)
    expect(isNearBottom(el, 150)).toBe(false)
  })

  it('默认阈值常量为 80', () => {
    expect(BOTTOM_THRESHOLD).toBe(80)
  })

  it('内容未超出容器（无滚动条）视为贴底', () => {
    expect(isNearBottom({ scrollHeight: 400, scrollTop: 0, clientHeight: 500 })).toBe(true)
  })
})

describe('distanceToBottom 距底距离', () => {
  it('正确计算距底距离', () => {
    expect(distanceToBottom({ scrollHeight: 1000, scrollTop: 200, clientHeight: 500 })).toBe(300)
  })

  it('内容未超容器时返回 0 而非负数', () => {
    expect(distanceToBottom({ scrollHeight: 400, scrollTop: 0, clientHeight: 500 })).toBe(0)
  })

  it('null 容器返回 0', () => {
    expect(distanceToBottom(null)).toBe(0)
  })
})
