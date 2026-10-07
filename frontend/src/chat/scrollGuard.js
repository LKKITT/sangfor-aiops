/**
 * 聊天区滚动保护（从 ChatView.vue 抽出，便于单测覆盖）
 *
 * 背景：原实现 scrollBottom() 无条件把视口拽到底部，
 * 用户回看历史时会被流式输出反复打断。此处提供纯函数判定，
 * 只有「原本贴底」时才允许自动跟随。
 */

/** 距底多少像素内视为「贴底」 */
export const BOTTOM_THRESHOLD = 80

/**
 * 判断滚动容器是否处于底部附近
 * @param {{scrollHeight:number, scrollTop:number, clientHeight:number}} el
 * @param {number} threshold
 * @returns {boolean}
 */
export function isNearBottom(el, threshold = BOTTOM_THRESHOLD) {
  if (!el) return true   // 容器尚未挂载时按贴底处理，保证首次渲染能滚到底
  const { scrollHeight = 0, scrollTop = 0, clientHeight = 0 } = el
  return scrollHeight - scrollTop - clientHeight < threshold
}

/**
 * 计算滚动容器的「距底距离」
 * @param {{scrollHeight:number, scrollTop:number, clientHeight:number}} el
 * @returns {number}
 */
export function distanceToBottom(el) {
  if (!el) return 0
  const { scrollHeight = 0, scrollTop = 0, clientHeight = 0 } = el
  return Math.max(0, scrollHeight - scrollTop - clientHeight)
}
