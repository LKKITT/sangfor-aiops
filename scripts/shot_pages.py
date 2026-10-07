#!/usr/bin/env python3
"""逐页截图：亮色 / 暗色双主题。

前置：
  1. 后端在 127.0.0.1:8600（SF_SKIP_DEMO_CLEANUP=1）
  2. 演示静态服务在 127.0.0.1:8610（scripts/serve_demo.py）
  3. 已执行 scripts/seed_demo_data.py

产出：/workspace/sfa-screenshots/<页面>-<主题>.png
      /workspace/sfa-screenshots/_report.json（页面错误与控制台错误取证）
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8610"
OUT = Path("/workspace/sfa-screenshots")
W, H = 1560, 1000

AF_NAME = "总部边界防火墙 AF-2000"
AC_NAME = "总部无线控制器 AC-1000"
SCP_NAME = "超融合集群 SCP-01"

errors: list[str] = []
shots: list[dict] = []


def collect_errors(pg, tag):
    pg.on("pageerror", lambda e: errors.append(f"[{tag}] pageerror: {e}"))
    pg.on("console", lambda m: errors.append(f"[{tag}] console.error: {m.text}")
          if m.type == "error" else None)


def switch_theme(pg, dark: bool):
    """直接改 localStorage + data-theme，避免依赖按钮位置。"""
    pg.evaluate("""(dark) => {
        localStorage.setItem('sfa-theme', dark ? 'dark' : 'light');
        document.documentElement.dataset.theme = dark ? 'dark' : 'light';
        document.documentElement.classList.toggle('dark', dark);
    }""", dark)
    pg.wait_for_timeout(600)


def pick_device(pg, name: str):
    """通过侧栏 el-select 选择设备。"""
    try:
        sel = pg.locator(".device-select").first
        sel.click()
        pg.wait_for_timeout(400)
        # 下拉项文本匹配
        opt = pg.locator(f".el-select-dropdown__item:has-text('{name}')").first
        if opt.count():
            opt.click()
            pg.wait_for_timeout(1800)
            return True
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
    except Exception as e:   # noqa: BLE001
        errors.append(f"pick_device({name}) failed: {e}")
    return False


def shot(pg, name: str, theme: str):
    f = OUT / f"{name}-{theme}.png"
    pg.screenshot(path=str(f), full_page=False)
    shots.append({"name": name, "theme": theme, "file": str(f)})
    return f


def go(pg, hash_path: str, wait: int = 1800):
    pg.goto(f"{BASE}/#{hash_path}", wait_until="networkidle")
    pg.wait_for_timeout(wait)


def both_themes(pg, name: str, prep=None):
    """先亮色截图，再暗色截图。prep 在每次主题切换后调用（用于重选设备）。"""
    for theme, dark in (("light", False), ("dark", True)):
        switch_theme(pg, dark)
        if prep:
            prep()
        pg.wait_for_timeout(900)
        shot(pg, name, theme)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": W, "height": H}, device_scale_factor=1.5)
        pg = ctx.new_page()
        collect_errors(pg, "global")

        # 预热：加载一次，让 store 拿到设备列表
        go(pg, "/chat", 2200)

        # ---------- 1. 对话（全局） ----------
        print("· 对话（全局）")
        switch_theme(pg, False)
        pg.evaluate("""() => { /* 触发回全局 */ }""")
        both_themes(pg, "01-chat-global")

        # ---------- 2. 对话（AF 设备，含确认卡片/轨迹） ----------
        print("· 对话（AF，确认卡片 + 工具轨迹）")
        def prep_chat():
            go(pg, "/chat", 1200)
            pick_device(pg, AF_NAME)
            pg.wait_for_timeout(2200)
        switch_theme(pg, False)
        prep_chat()
        shot(pg, "02-chat-af", "light")
        switch_theme(pg, True)
        prep_chat()
        shot(pg, "02-chat-af", "dark")

        # ---------- 3. 配置查看（AF） ----------
        print("· 配置查看（AF）")
        def prep_cfg():
            go(pg, "/config", 1200)
            pick_device(pg, AF_NAME)
            pg.wait_for_timeout(2500)
        switch_theme(pg, False)
        prep_cfg()
        shot(pg, "03-config-af", "light")
        switch_theme(pg, True)
        prep_cfg()
        shot(pg, "03-config-af", "dark")

        # ---------- 4. 配置体检（AF） ----------
        print("· 配置体检（AF）")
        def prep_ck():
            go(pg, "/checkup", 1200)
            pick_device(pg, AF_NAME)
            pg.wait_for_timeout(2800)
        switch_theme(pg, False)
        prep_ck()
        shot(pg, "04-checkup-af", "light")
        switch_theme(pg, True)
        prep_ck()
        shot(pg, "04-checkup-af", "dark")

        # ---------- 5. 备份管理（AF） ----------
        print("· 备份管理（AF）")
        def prep_bk():
            go(pg, "/backup", 1200)
            pick_device(pg, AF_NAME)
            pg.wait_for_timeout(2200)
        switch_theme(pg, False)
        prep_bk()
        shot(pg, "05-backup-af", "light")
        switch_theme(pg, True)
        prep_bk()
        shot(pg, "05-backup-af", "dark")

        # ---------- 6. 软件更新（AF） ----------
        print("· 软件更新（AF）")
        def prep_up():
            go(pg, "/updates", 1200)
            pick_device(pg, AF_NAME)
            pg.wait_for_timeout(3000)
        switch_theme(pg, False)
        prep_up()
        shot(pg, "06-updates-af", "light")
        switch_theme(pg, True)
        prep_up()
        shot(pg, "06-updates-af", "dark")

        # ---------- 7. 网络设备管理 ----------
        print("· 网络设备（设备管理）")
        both_themes(pg, "07-netdev-list",
                    prep=lambda: go(pg, "/netdev", 2400))

        # ---------- 8. 网络拓扑 ----------
        print("· 网络拓扑")
        def prep_topo():
            go(pg, "/netdev", 1600)
            try:
                tab = pg.locator(".el-tabs__item:has-text('网络拓扑')").first
                if tab.count():
                    tab.click()
                    pg.wait_for_timeout(3000)
            except Exception as e:   # noqa: BLE001
                errors.append(f"topo tab: {e}")
        switch_theme(pg, False)
        prep_topo()
        shot(pg, "08-netdev-topology", "light")
        switch_theme(pg, True)
        prep_topo()
        shot(pg, "08-netdev-topology", "dark")

        # ---------- 9. 个人知识库 ----------
        print("· 个人知识库")
        both_themes(pg, "09-knowledge",
                    prep=lambda: go(pg, "/knowledge", 4000))

        # ---------- 9b. 知识图谱（独立标签页） ----------
        print("· 知识图谱")
        both_themes(pg, "09b-graph",
                    prep=lambda: go(pg, "/graph", 4200))

        # ---------- 10. 对话日志 ----------
        print("· 对话日志")
        both_themes(pg, "10-chatlog",
                    prep=lambda: go(pg, "/chatlog", 2600))

        # ---------- 11. 对话日志详情 ----------
        print("· 对话日志（详情弹窗）")
        def prep_logdetail():
            go(pg, "/chatlog", 2400)
            # 关掉可能残留的弹窗（上一轮暗色截图的弹窗未关闭会拦截点击）
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(700)
            try:
                link = pg.locator(".conv-link").first
                link.click(timeout=12000)
                pg.wait_for_timeout(2200)
            except Exception as e:   # noqa: BLE001
                errors.append(f"chatlog detail: {type(e).__name__}: {str(e)[:120]}")
        switch_theme(pg, False)
        prep_logdetail()
        shot(pg, "11-chatlog-detail", "light")
        switch_theme(pg, True)
        prep_logdetail()
        shot(pg, "11-chatlog-detail", "dark")

        # ---------- 12. 平台设置 ----------
        print("· 平台设置")
        both_themes(pg, "12-settings",
                    prep=lambda: go(pg, "/settings", 2600))

        # ---------- 13. 配置查看（AC） ----------
        print("· 配置查看（AC）")
        def prep_ac():
            go(pg, "/config", 1200)
            pick_device(pg, AC_NAME)
            pg.wait_for_timeout(2500)
        switch_theme(pg, False)
        prep_ac()
        shot(pg, "13-config-ac", "light")
        switch_theme(pg, True)
        prep_ac()
        shot(pg, "13-config-ac", "dark")

        b.close()

    report = {"shots": shots, "errors": errors}
    (OUT / "_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"\n共 {len(shots)} 张截图 → {OUT}")
    print(f"错误 {len(errors)} 条")
    for e in errors[:20]:
        print("  ", e)


if __name__ == "__main__":
    main()
