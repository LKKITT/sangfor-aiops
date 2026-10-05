import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { apiGet, apiPatch } from '../api'

// 统一请求封装回归：超时注册、!resp.ok 抛错（readError 优先取后端 detail）、JSON 解析。

describe('apiGet / apiPatch', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('2xx：解析 JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({ hello: 'world' }),
    })))
    await expect(apiGet('/api/x')).resolves.toEqual({ hello: 'world' })
  })

  it('非 2xx：抛后端 detail 文案（readError）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 500, json: async () => ({ detail: '设备连接失败：超时' }),
    })))
    await expect(apiGet('/api/x')).rejects.toThrow('设备连接失败：超时')
  })

  it('非 2xx 且无 detail：回退 HTTP 状态码', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 404, json: async () => ({}),
    })))
    await expect(apiGet('/api/x')).rejects.toThrow('HTTP 404')
  })

  it('apiPatch：PATCH 方法 + JSON 序列化 body', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }))
    vi.stubGlobal('fetch', fetchMock)
    await apiPatch('/api/devices/dev_1', { name: '新名字' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/devices/dev_1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ name: '新名字' })
  })

  it('超时：到达时限后中止请求并抛中文超时错误', async () => {
    vi.useFakeTimers()
    // 模拟一个尊重 abort 信号的挂起请求（真实 fetch 行为）
    vi.stubGlobal('fetch', vi.fn((url, init) => new Promise((_, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason || new Error('Aborted')))
    })))
    const pending = apiGet('/api/x', 50)
    const assertion = expect(pending).rejects.toThrow(/超时/)
    await vi.advanceTimersByTimeAsync(60)
    await assertion
    vi.useRealTimers()
  })
})
