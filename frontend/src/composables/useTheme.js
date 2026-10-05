import { ref } from 'vue'

// 主题切换：localStorage 持久化；data-theme 驱动自研 token 覆盖层，
// 同步 html.dark 开启 Element Plus 官方暗色变量。
const theme = ref('light')

export function useTheme() {
  function apply(t = theme.value) {
    theme.value = t
    document.documentElement.dataset.theme = t
    document.documentElement.classList.toggle('dark', t === 'dark')
  }
  function init() {
    const saved = localStorage.getItem('sfa-theme')
    apply(saved === 'dark' ? 'dark' : 'light')
  }
  function toggle() {
    apply(theme.value === 'dark' ? 'light' : 'dark')
    localStorage.setItem('sfa-theme', theme.value)
  }
  return { theme, apply, init, toggle }
}
