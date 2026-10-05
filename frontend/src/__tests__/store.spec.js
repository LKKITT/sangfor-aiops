import { describe, it, expect, vi, beforeEach } from 'vitest'

// store 回归：设备列表加载、失效回退全局（当前设备被删除后不悬挂）。
// store 是模块级单例：每个用例 resetModules 取全新实例。

const devicesMock = vi.fn()
const netdevMock = vi.fn()

vi.mock('../api.js', () => ({
  Devices: { list: (...a) => devicesMock(...a) },
  NetDev: { devices: (...a) => netdevMock(...a) },
  Health: { get: vi.fn() },
}))

async function freshStore() {
  vi.resetModules()
  return await import('../store.js')
}

const AF = { id: 'dev_af', name: '总部-AF', type: 'af' }
const ND = { id: 'nd_sw1', name: '核心交换机', vendor: 'huawei', host: '10.0.0.2' }

beforeEach(() => {
  devicesMock.mockReset()
  netdevMock.mockReset()
})

describe('loadDevices', () => {
  it('正常加载深信服 + 网络设备列表', async () => {
    devicesMock.mockResolvedValue([AF])
    netdevMock.mockResolvedValue([ND])
    const { store, loadDevices } = await freshStore()
    await loadDevices()
    expect(store.devices).toEqual([AF])
    expect(store.netdevDevices).toEqual([ND])
  })

  it('当前选中设备被删除时回退全局模式', async () => {
    devicesMock.mockResolvedValue([AF])
    netdevMock.mockResolvedValue([ND])
    const { store, loadDevices } = await freshStore()
    store.currentDeviceId = 'dev_gone'
    await loadDevices()
    expect(store.currentDeviceId).toBe('global')
  })

  it('网络设备接口异常不阻塞主列表（降级为空）', async () => {
    devicesMock.mockResolvedValue([AF])
    netdevMock.mockRejectedValue(new Error('boom'))
    const { store, loadDevices } = await freshStore()
    await loadDevices()
    expect(store.devices).toEqual([AF])
    expect(store.netdevDevices).toEqual([])
  })

  it('aiNetdevs 只保留 AI 支持的厂家（华为/H3C/锐捷）', async () => {
    devicesMock.mockResolvedValue([AF])
    netdevMock.mockResolvedValue([ND, { id: 'nd_x', name: '老交换机', vendor: 'cisco' }])
    const { store, loadDevices, aiNetdevs } = await freshStore()
    await loadDevices()
    expect(aiNetdevs().map(d => d.id)).toEqual(['nd_sw1'])
  })
})
