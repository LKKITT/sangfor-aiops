<template>
  <div class="app-shell" :class="{ 'side-open': mobileOpen }">
    <div class="side-scrim" @click="mobileOpen = false" aria-hidden="true"></div>

    <aside class="side">
      <header class="brand">
        <svg class="brand-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true">
          <defs>
            <linearGradient id="sfaBrandGrad" x1="6" y1="4" x2="34" y2="36" gradientUnits="userSpaceOnUse">
              <stop offset="0" stop-color="#4A70FF" />
              <stop offset="1" stop-color="#0FB9A4" />
            </linearGradient>
          </defs>
          <path d="M20 3.5 34.3 11.8v16.4L20 36.5 5.7 28.2V11.8L20 3.5Z"
                stroke="url(#sfaBrandGrad)" stroke-width="2.5" stroke-linejoin="round" />
          <path d="M20 13.2v3.4M23 21.5l3.4 2M17 21.5l-3.4 2" stroke="#4E5B7E" stroke-width="1.4" stroke-linecap="round" />
          <circle cx="20" cy="20" r="3.4" fill="url(#sfaBrandGrad)" />
          <circle cx="20" cy="10.6" r="2" fill="#4A70FF" />
          <circle cx="28.6" cy="25" r="2" fill="#0FB9A4" />
          <circle cx="11.4" cy="25" r="2" fill="#4A70FF" />
        </svg>
        <div class="brand-text">
          <div class="brand-name">SFA&nbsp;Agent</div>
          <div class="brand-tag">深信服售后智能体</div>
        </div>
      </header>

      <div class="device-panel">
        <div class="device-panel-head"><span class="device-label">客户</span></div>
        <el-select v-model="tenantSel" filterable allow-create default-first-option
                   placeholder="选择客户" class="device-select" aria-label="切换客户" @change="setTenant"
                   style="width: 100%">
          <el-option v-for="t in store.tenants" :key="t" :value="t"
                     :label="tenantLabel(t)" />
        </el-select>
        <div class="device-tags">
          <span class="mini-chip" :class="llmChip.cls"><i class="mc-dot"></i>{{ llmChip.text }}</span>
          <span v-if="store.health.readonly_mode" class="mini-chip danger"><i class="mc-dot"></i>全局只读</span>
        </div>
      </div>

      <nav class="nav" aria-label="主导航">
        <template v-for="group in navGroups" :key="group.label">
          <div class="nav-group-label">{{ group.label }}</div>
          <button v-for="item in group.items" :key="item.key" class="nav-item"
                  :class="{ active: route.path === item.to }" :title="item.label"
                  :aria-current="route.path === item.to ? 'page' : undefined"
                  @click="router.push(item.to); mobileOpen = false">
            <span class="nav-bar" aria-hidden="true"></span>
            <el-icon class="nav-ico" aria-hidden="true"><component :is="item.icon" /></el-icon>
            <span class="nav-label">{{ item.label }}</span>
          </button>
        </template>
      </nav>

      <footer class="side-foot">
        <button class="theme-toggle" @click="toggleTheme" :title="theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'">
          <el-icon><component :is="theme === 'dark' ? 'Sunny' : 'Moon'" /></el-icon>
          <span>{{ theme === 'dark' ? '亮色模式' : '暗色模式' }}</span>
        </button>
        <div class="health-line" :title="store.health.llm_configured ? '大模型已接入' : '未配置 LLM，使用离线兜底模式'">
          <span class="pulse-dot" :class="store.health.llm_configured ? 'ok' : 'warn'"></span>
          <span class="health-text">{{ store.health.llm_configured ? '智能体在线' : '离线兜底模式' }}</span>
        </div>
      </footer>
    </aside>

    <div class="workspace">
      <div class="mobile-topbar">
        <button class="hamburger" @click="mobileOpen = true" aria-label="打开菜单">
          <span></span><span></span><span></span>
        </button>
        <span class="mt-title">深信服售后 Agent</span>
        <span class="mt-dot"></span>
      </div>

      <main class="main">
        <router-view v-slot="{ Component }">
          <transition name="view" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
const tenantSel = ref(store.tenant)
import { useRoute } from 'vue-router'
import { router } from './router'
import { useTheme } from './composables/useTheme'
import { store, loadDevices, loadHealth, setTenant, refreshTenants } from './store.js'

// 视图路由化：URL 可寻址（刷新保持/可分享），组件按路由级动态 import 分片加载

// 侧栏导航分组（图标为全局注册的 Element 图标组件名；to 为路由路径）
const navGroups = [
  { label: '工作台', items: [
    { key: 'chat', to: '/chat', label: 'AI 对话', icon: 'ChatDotRound' },
    { key: 'config', to: '/config', label: '配置可视化', icon: 'SetUp' },
    { key: 'checkup', to: '/checkup', label: '配置体检', icon: 'Odometer' },
    { key: 'backup', to: '/backup', label: '备份与恢复', icon: 'CopyDocument' },
  ]},
  { label: '资产运营', items: [
    { key: 'security', to: '/security', label: '安全设备管理', icon: 'Lock' },
    { key: 'netdev', to: '/netdev', label: '网络设备管理', icon: 'Cpu' },
    { key: 'updates', to: '/updates', label: '软件更新建议', icon: 'Download' },
  ]},
  { label: '知识沉淀', items: [
    { key: 'knowledge', to: '/knowledge', label: '个人知识库', icon: 'Collection' },
    { key: 'graph', to: '/graph', label: '知识图谱', icon: 'Share' },
    { key: 'chatlog', to: '/chatlog', label: '对话日志', icon: 'Notebook' },
  ]},
  { label: '系统', items: [
    { key: 'settings', to: '/settings', label: '平台设置', icon: 'Setting' },
  ]},
]

const mobileOpen = ref(false)
const route = useRoute()
const { theme, toggle: toggleTheme } = useTheme()

// 客户显示名：客户管理里登记过的显示名称优先（默认客户改名后侧栏同步显示），未登记回退编码
function tenantLabel(t) {
  return store.tenantNames?.[t] || (t === 'default' ? '默认客户' : t)
}

// LLM 徽章：后端返回 false（未配置）/ true（已接入无模型名）/ 模型名字符串
const llmChip = computed(() => {
  const v = store.health.llm_configured
  if (!v) return { text: 'LLM 未配置', cls: '' }
  if (v === true) return { text: 'LLM 已接入', cls: 'ok' }
  return { text: `LLM ${v}`, cls: 'ok' }
})

// 移动端抽屉：Esc 键关闭（当前仅能点遮罩，键盘用户无法退出）
function onGlobalKeydown(e) {
  if (e.key === 'Escape' && mobileOpen.value) mobileOpen.value = false
}

onMounted(async () => {
  refreshTenants()
  window.addEventListener('keydown', onGlobalKeydown)
  await Promise.all([loadDevices(), loadHealth()])
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
})

// 其他视图（如空状态引导）请求维护设备：深信服设备管理已独立成页，直接带跳
watch(() => store.uiAddDeviceTick, v => { if (v > 0) router.push('/security') })
</script>

<style scoped>
.app-shell { display: flex; height: 100vh; overflow: hidden; }

/* ================= 侧栏 ================= */
.side {
  width: 252px; flex-shrink: 0;
  display: flex; flex-direction: column;
  background:
    radial-gradient(420px 300px at -60px -40px, rgba(59, 99, 255, .16), transparent 60%),
    radial-gradient(360px 280px at 110% 108%, rgba(15, 185, 164, .1), transparent 62%),
    var(--side-bg);
  border-right: 1px solid var(--side-line);
  position: relative; z-index: 40;
}

.brand { display: flex; align-items: center; gap: 11px; padding: 20px 18px 16px; }
.brand-mark { width: 36px; height: 36px; flex-shrink: 0; filter: drop-shadow(0 2px 8px rgba(59, 99, 255, .35)); }
.brand-name {
  font-size: 15.5px; font-weight: 750; letter-spacing: .01em;
  color: #F2F5FF; line-height: 1.2;
  font-family: var(--sfa-font);
}
.brand-tag { font-size: 11px; color: #99A5C4; margin-top: 3px; letter-spacing: .07em; }

/* 设备面板 */
.device-panel { margin: 2px 14px 12px; padding: 12px; border-radius: 12px; background: rgba(148, 163, 199, .07); border: 1px solid var(--side-line); }
.device-panel-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.device-label { font-size: 10.5px; letter-spacing: .14em; color: #8A96B8; font-weight: 650; }

.device-select :deep(.el-select__wrapper) {
  background: rgba(10, 15, 30, .6);
  box-shadow: 0 0 0 1px rgba(148, 163, 199, .2) inset;
  border-radius: 8px; min-height: 30px;
}
.device-select :deep(.el-select__wrapper.is-hovering) { box-shadow: 0 0 0 1px rgba(120, 145, 255, .55) inset; }
.device-select :deep(.el-select__wrapper.is-focused) { box-shadow: 0 0 0 1px var(--sfa-primary) inset, 0 0 0 3px rgba(59, 99, 255, .2) !important; }
.device-select :deep(.el-select__placeholder), .device-select :deep(.el-select__selected-item) { color: #D6DDF0; font-size: 12.5px; }
.device-select :deep(.el-select__caret) { color: var(--side-text-dim); }

.device-tags { margin-top: 9px; display: flex; gap: 5px; flex-wrap: wrap; }
.mini-chip {
  font-size: 11px; padding: 2px 8px; border-radius: 999px; letter-spacing: .02em;
  background: rgba(148, 163, 199, .12); color: #C9D1E6;
  border: 1px solid rgba(148, 163, 199, .14);
  display: inline-flex; align-items: center; gap: 5px;
}
.mc-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.mini-chip.ok { background: rgba(15, 185, 164, .13); color: #52D6C4; border-color: rgba(15, 185, 164, .25); }
.mini-chip.danger { background: rgba(229, 72, 77, .14); color: #FF8085; border-color: rgba(229, 72, 77, .28); }

/* 导航 */
.nav { flex: 1; overflow-y: auto; padding: 2px 12px 12px; display: flex; flex-direction: column; gap: 2px; }
.nav::-webkit-scrollbar { width: 4px; }
.nav-group-label {
  font-size: 10.5px; letter-spacing: .18em; color: #A6B1CE;
  padding: 16px 10px 7px; font-weight: 700;
  border-top: 1px solid rgba(148, 163, 199, .1);
  margin-top: 10px;
}
.nav > :first-child.nav-group-label { border-top: none; margin-top: 0; padding-top: 6px; }
.nav-item {
  position: relative; display: flex; align-items: center; gap: 10px;
  width: 100%; padding: 8.5px 10px; border: none; cursor: pointer;
  background: transparent; border-radius: 9px;
  color: var(--side-text); font-size: 13px; font-family: var(--sfa-font);
  transition: background-color var(--dur-1) var(--ease-out), color var(--dur-1), transform var(--dur-1) var(--ease-out);
}
.nav-item:hover { background: rgba(148, 163, 199, .09); color: #E6EBF8; }
.nav-item:hover .nav-ico { transform: translateX(1px); }
.nav-item:active { transform: scale(.98); }
.nav-item .nav-ico { font-size: 15.5px; transition: transform var(--dur-1) var(--ease-out); }
.nav-item .nav-bar {
  position: absolute; left: -12px; top: 50%; transform: translateY(-50%);
  width: 3px; height: 0; border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, #6A87FF, #0FB9A4);
  transition: height var(--dur-2) var(--ease-out), box-shadow var(--dur-2);
}
.nav-item.active { background: linear-gradient(90deg, rgba(59, 99, 255, .22), rgba(59, 99, 255, .07) 75%, transparent); color: #fff; font-weight: 620; }
.nav-item.active .nav-ico { color: #7D96FF; }
.nav-item.active .nav-bar { height: 18px; box-shadow: 0 0 12px rgba(93, 126, 255, .8); }

/* 侧栏底栏 */
.side-foot { padding: 14px 14px 14px; border-top: 1px solid var(--side-line); }
.health-line { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; padding: 0 2px; }
.health-text { font-size: 11px; color: #8A96B8; letter-spacing: .04em; }
/* ================= 工作区 ================= */
.workspace { flex: 1; min-width: 0; display: flex; flex-direction: column; position: relative; }
.main { flex: 1; overflow: auto; padding: 22px 26px; position: relative; }

/* 顶部微点阵：工作区的精密质感 */
.workspace::before {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background-image: radial-gradient(rgba(16, 24, 40, .05) 1px, transparent 1px);
  background-size: 22px 22px;
  -webkit-mask-image: linear-gradient(180deg, rgba(0, 0, 0, .8), transparent 220px);
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, .8), transparent 220px);
}

/* 视图转场：旧视图立即卸载（防快速切换卡死），新视图淡入上浮 */
.view-enter-active { transition: opacity .24s var(--ease-out), transform .3s var(--ease-out); }
.view-leave-active { display: none; }
.view-enter-from { opacity: 0; transform: translateY(10px); }

/* ================= 移动端顶栏（默认隐藏） ================= */
.side-scrim { display: none; }
.mobile-topbar { display: none; }

@media (max-width: 1240px) {
  .side { width: 224px; }
  .main { padding: 18px 20px; }
}

/* 中屏：图标轨道 */
@media (max-width: 1060px) {
  .side { width: 68px; }
  .brand { justify-content: center; padding: 18px 0 12px; }
  .brand-text, .device-panel, .nav-group-label, .nav-label, .health-text, .side-foot { display: none; }
  .nav { padding: 4px 10px; align-items: center; }
  .nav-item { justify-content: center; padding: 11px 0; width: 46px; }
  .nav-item .nav-bar { left: -10px; }
  .side-foot { border-top: 1px solid var(--side-line); padding: 10px; }
}

/* 小屏：抽屉式侧栏 + 顶栏 */
@media (max-width: 820px) {
  .side {
    position: fixed; inset: 0 auto 0 0; width: 252px;
    transform: translateX(-102%);
    transition: transform var(--dur-2) var(--ease-out);
    box-shadow: none;
  }
  .app-shell.side-open .side { transform: none; box-shadow: var(--sfa-shadow-3); }
  .brand-text, .device-panel, .nav-group-label, .nav-label, .health-text { display: initial; }
  .nav { align-items: stretch; }
  .nav-item { justify-content: flex-start; width: 100%; padding: 8.5px 10px; }
  .side-foot { display: block; }
  .side-scrim {
    display: block; position: fixed; inset: 0; z-index: 35;
    background: rgba(12, 18, 34, .5); backdrop-filter: blur(3px);
    opacity: 0; pointer-events: none; transition: opacity var(--dur-2);
  }
  .app-shell.side-open .side-scrim { opacity: 1; pointer-events: auto; }
  .mobile-topbar {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 16px; z-index: 30;
    background: rgba(244, 246, 251, .82); backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--sfa-border);
  }
  .mt-title { font-weight: 700; font-size: 14.5px; letter-spacing: -.01em; }
  .mt-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--sfa-accent); margin-left: auto; }
  .hamburger {
    width: 34px; height: 34px; border-radius: 9px; border: 1px solid var(--sfa-border);
    background: var(--sfa-surface); cursor: pointer; padding: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
  }
  .hamburger span { width: 15px; height: 1.8px; border-radius: 2px; background: var(--sfa-text-2); }
  .main { padding: 14px; }
}
</style>

<style>
.theme-toggle {
  display: flex; align-items: center; gap: 7px; width: 100%;
  padding: 7px 10px; margin-bottom: 8px; border-radius: 9px; cursor: pointer;
  background: transparent; border: 1px solid var(--side-line);
  color: var(--side-text); font-size: 12.5px;
  transition: color var(--dur-1) var(--ease-out), border-color var(--dur-1) var(--ease-out);
}
.theme-toggle:hover { color: #fff; border-color: var(--side-text-dim); }
</style>
