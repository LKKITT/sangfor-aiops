import { createRouter, createWebHashHistory } from 'vue-router'

// 视图路由化：URL 可寻址（刷新保持、可分享/收藏），视图级代码分割由动态 import 承担。
// 采用 hash 模式：前端独立部署（无 SPA fallback 服务端配置依赖），部署形态零约束。
const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', name: 'chat', component: () => import('../views/ChatView.vue') },
  { path: '/config', name: 'config', component: () => import('../views/ConfigView.vue') },
  { path: '/checkup', name: 'checkup', component: () => import('../views/CheckupView.vue') },
  { path: '/backup', name: 'backup', component: () => import('../views/BackupView.vue') },
  { path: '/updates', name: 'updates', component: () => import('../views/UpdatesView.vue') },
  { path: '/netdev', name: 'netdev', component: () => import('../views/NetDevView.vue') },
  { path: '/knowledge', name: 'knowledge', component: () => import('../views/KnowledgeView.vue') },
  { path: '/graph', name: 'graph', component: () => import('../views/GraphView.vue') },
  { path: '/chatlog', name: 'chatlog', component: () => import('../views/ChatLogView.vue') },
  { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export const NAV_KEYS = {
  chat: '/chat', config: '/config', checkup: '/checkup', backup: '/backup',
  updates: '/updates', netdev: '/netdev', knowledge: '/knowledge',
  chatlog: '/chatlog', settings: '/settings',
}
