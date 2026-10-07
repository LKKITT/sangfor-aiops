import { describe, it, expect } from 'vitest'
import { renderMarkdown } from '../markdown'

/**
 * 回归测试：统一 Markdown 渲染
 *
 * 对应缺陷：KnowledgeView / ChatLogView 原用裸 markdown-it 直出 v-html，
 * 既无 DOMPurify 净化（XSS 面），也无代码高亮，与 ChatView 渲染不一致。
 */
describe('renderMarkdown 净化', () => {
  it('剥离 script 标签（XSS 防护）', () => {
    const html = renderMarkdown('正常文本<script>alert(1)</script>')
    expect(html).not.toContain('<script')
    expect(html).toContain('正常文本')
  })

  it('裸 HTML 标签被转义，不会构造出真实 img 元素', () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)">')
    // markdown-it 将裸 HTML 视为文本转义输出，标签被中和；不会出现可执行的 <img 标签
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
  })

  it('javascript: 伪协议链接不会被渲染成可点击 href', () => {
    const html = renderMarkdown('[点我](javascript:alert(1))')
    // markdown-it 默认不把 javascript: 识别为合法链接，故不产出 href 属性
    expect(html).not.toContain('href="javascript:')
    expect(html).not.toContain('<a ')
  })

  it('空输入返回空串', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(null)).toBe('')
    expect(renderMarkdown(undefined)).toBe('')
  })
})

describe('renderMarkdown 外链处理', () => {
  it('外链补 target=_blank 与 rel=noopener', () => {
    const html = renderMarkdown('[官网](https://example.com)')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })
})

describe('renderMarkdown 代码块增强', () => {
  it('已注册语言产出高亮 class 与外壳', () => {
    const html = renderMarkdown('```bash\nls -la\n```')
    expect(html).toContain('class="cb"')
    expect(html).toContain('cb-lang')
    expect(html).toContain('data-copy')      // 复制按钮
    expect(html).toContain('hljs')           // 高亮容器
  })

  it('未注册语言降级为转义纯文本而非抛错', () => {
    const html = renderMarkdown('```notalang\n<x>&y\n```')
    expect(html).toContain('cb')
    expect(html).toContain('&lt;x&gt;')      // 已转义
  })

  it('代码块内容被转义，不会逃逸执行', () => {
    const html = renderMarkdown('```\n<script>alert(1)</script>\n```')
    expect(html).not.toContain('<script>alert(1)')
  })
})

describe('renderMarkdown 表格增强', () => {
  it('表格被包裹进横向滚动容器', () => {
    const html = renderMarkdown('| A | B |\n| - | - |\n| 1 | 2 |')
    expect(html).toContain('table-wrap')
    expect(html).toContain('<table')
  })
})
