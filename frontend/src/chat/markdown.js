/**
 * Markdown 渲染增强（从 ChatView 内联实现抽出为独立模块）
 *
 * 改造点（对应 UI 评审 P1）：
 * 1. 代码高亮：原来 new MarkdownIt({breaks:true}) 裸渲染，代码块是无高亮的灰底纯文本
 * 2. 代码块外壳：加语言标签 + 复制按钮（运维场景大量 CLI 命令，复制是高频操作）
 * 3. 表格样式：原 table 无样式；这里补 .cb-table 类，配合 style.css 做圆角/斑马纹/悬浮
 * 4. 表格横向滚动：窄屏不撑破气泡
 * 5. 外链 target=_blank：知识库引用链接原会当前页跳转，丢失对话状态
 * 6. DOMPurify 净化：v-html 的安全底线（LLM 输出可能被设备返回内容污染）
 *
 * 依赖：markdown-it / highlight.js / dompurify
 */
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({ breaks: true, linkify: true })

/** 代码块：产出带语言标签栏与复制按钮的深色外壳 */
md.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const lang = (token.info || '').trim().split(/\s+/)[0] || 'text'
  let inner
  // 语言未注册时 hljs 会抛错 —— 先查注册表再高亮
  if (hljs.getLanguage(lang)) {
    try {
      inner = hljs.highlight(token.content, { language: lang, ignoreIllegals: true }).value
    } catch {
      inner = md.utils.escapeHtml(token.content)
    }
  } else {
    inner = md.utils.escapeHtml(token.content)
  }
  return `<div class="cb">`
    + `<div class="cb-bar"><span class="cb-lang">${md.utils.escapeHtml(lang)}</span>`
    + `<button class="cb-copy" type="button" data-copy>复制</button></div>`
    + `<pre><code class="hljs">${inner}</code></pre></div>`
}

/** 表格：包一层横向滚动容器并标记样式类 */
const defaultTableOpen = md.renderer.rules.table_open
  || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.table_open = (tokens, idx, options, env, self) =>
  `<div class="table-wrap">${defaultTableOpen(tokens, idx, options, env, self)}`

const defaultTableClose = md.renderer.rules.table_close
  || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.table_close = (tokens, idx, options, env, self) =>
  `${defaultTableClose(tokens, idx, options, env, self)}</div>`

/** 渲染 + 净化（外链加 target/rel，避免把用户带离应用） */
export function renderMarkdown(text) {
  if (!text) return ''
  const raw = md.render(text)
  const withLinks = raw.replace(
    /<a href="(https?:\/\/[^"]+)"/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer"'
  )
  return DOMPurify.sanitize(withLinks, { ADD_ATTR: ['target', 'rel'] })
}

/** 代码块复制（事件委托调用；失败时回退 execCommand） */
export async function copyCodeBlock(btn) {
  const code = btn.closest('.cb')?.querySelector('code')
  if (!code) return
  const text = code.innerText
  const old = btn.textContent
  try {
    await navigator.clipboard.writeText(text)
    btn.textContent = '已复制'
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy'); btn.textContent = '已复制' }
    catch { btn.textContent = '复制失败' }
    document.body.removeChild(ta)
  }
  setTimeout(() => { btn.textContent = old }, 1400)
}
