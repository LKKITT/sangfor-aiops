import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { WarningFilled } from '@element-plus/icons-vue'
import AsyncSection from '../AsyncSection.vue'

// 测试环境与 App 一致地注册 Element Plus 与图标（全局组件在单测中不自动可用）
const mountSection = (options) => mount(AsyncSection, {
  ...options,
  global: { plugins: [ElementPlus], components: { WarningFilled } },
})

// 请求四态状态机回归：loading → ready / empty / error(+重试恢复)。

const flush = () => flushPromises()

describe('AsyncSection 四态', () => {
  it('loading → ready：渲染 slot 内容并回传数据', async () => {
    const wrapper = mountSection({
      props: { load: vi.fn(async () => [{ id: 1 }]) },
      slots: { default: `<div class="content">列表内容</div>` },
    })
    expect(wrapper.text()).not.toContain('列表内容')   // 初始 loading（骨架）
    await flush()
    expect(wrapper.find('.content').exists()).toBe(true)
    expect(wrapper.emitted('loaded')?.[0]?.[0]).toEqual([{ id: 1 }])
  })

  it('空数组 → empty 态（自定义文案）', async () => {
    const wrapper = mountSection({
      props: { load: vi.fn(async () => []), emptyText: '没有匹配的词条' },
    })
    await flush()
    expect(wrapper.text()).toContain('没有匹配的词条')
  })

  it('emptyWhen 自定义空态判定', async () => {
    const wrapper = mountSection({
      props: { load: vi.fn(async () => ({ total: 0 })), emptyWhen: d => d.total === 0 },
    })
    await flush()
    expect(wrapper.text()).toContain('暂无数据')
  })

  it('load 拒绝 → error 态展示原因；重试成功恢复 ready', async () => {
    const load = vi.fn()
      .mockRejectedValueOnce(new Error('设备连接失败：超时'))
      .mockResolvedValueOnce([{ id: 2 }])
    const wrapper = mountSection({ props: { load }, slots: { default: `<div class="ok">ok</div>` } })
    await flush()
    expect(wrapper.text()).toContain('设备连接失败：超时')
    expect(wrapper.text()).toContain('重试')
    await wrapper.find('button').trigger('click')
    await flush()
    expect(wrapper.find('.ok').exists()).toBe(true)
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('immediate=false 时不自动加载', async () => {
    const load = vi.fn(async () => [])
    mountSection({ props: { load, immediate: false } })
    await flush()
    expect(load).not.toHaveBeenCalled()
  })
})
