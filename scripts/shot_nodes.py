#!/usr/bin/env python3
"""节点造型专项截图：拓扑设备节点 + 知识图谱球体节点，亮/暗双主题。

前置：
  1. 后端 127.0.0.1:8600（SF_SKIP_DEMO_CLEANUP=1）
  2. 演示静态服务 127.0.0.1:8610（scripts/serve_demo.py）

产出：/workspace/sfa-screenshots-nodes/*.png
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8610"
OUT = Path("/workspace/sfa-screenshots-nodes")
W, H = 1560, 1020

errs: list[str] = []


def switch_theme(pg, dark: bool):
    pg.evaluate("""(d) => {
        localStorage.setItem('sfa-theme', d ? 'dark' : 'light');
        document.documentElement.dataset.theme = d ? 'dark' : 'light';
        document.documentElement.classList.toggle('dark', d);
    }""", dark)
    pg.wait_for_timeout(500)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for dark in (False, True):
            tag = "dark" if dark else "light"
            ctx = b.new_context(viewport={"width": W, "height": H},
                                device_scale_factor=2)
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errs.append(f"[{tag}] pageerror: {e}"))
            pg.on("console", lambda m: errs.append(f"[{tag}] console.error: {m.text}")
                  if m.type == "error" else None)

            # ============ 知识图谱（独立页 /graph） ============
            pg.goto(f"{BASE}/#/graph", wait_until="networkidle")
            switch_theme(pg, dark)
            pg.reload(wait_until="networkidle")
            pg.wait_for_timeout(3200)
            pg.screenshot(path=str(OUT / f"graph-{tag}.png"))
            # 图谱主体特写
            wrap = pg.locator(".gv-chart-wrap").first
            if wrap.count():
                wrap.screenshot(path=str(OUT / f"graph-zoom-{tag}.png"))
                box = wrap.bounding_box()
                # 悬停中心附近节点 → tooltip
                pg.mouse.move(box["x"] + box["width"] * 0.5,
                              box["y"] + box["height"] * 0.5)
                pg.wait_for_timeout(1100)
                pg.screenshot(path=str(OUT / f"graph-hover-{tag}.png"))
            # 详情抽屉
            try:
                hub = pg.locator(".gv-hub-card .gv-hub-item").first
                if hub.count():
                    hub.click()
                    pg.wait_for_timeout(1200)
                    pg.screenshot(path=str(OUT / f"graph-drawer-{tag}.png"))
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(600)
            except Exception as e:  # noqa: BLE001
                errs.append(f"[{tag}] graph drawer: {type(e).__name__}: {str(e)[:100]}")

            # ============ 网络拓扑（/netdev → 网络拓扑 tab） ============
            pg.goto(f"{BASE}/#/netdev", wait_until="networkidle")
            pg.wait_for_timeout(1000)
            switch_theme(pg, dark)
            try:
                tab = pg.locator(".el-tabs__item:has-text('网络拓扑')").first
                if tab.count():
                    tab.click()
                    pg.wait_for_timeout(3400)
            except Exception as e:  # noqa: BLE001
                errs.append(f"[{tag}] topo tab: {type(e).__name__}: {str(e)[:100]}")
            pg.screenshot(path=str(OUT / f"topo-{tag}.png"))
            card = pg.locator(".nt-canvas-card").first
            if card.count():
                card.screenshot(path=str(OUT / f"topo-zoom-{tag}.png"))
                box = pg.evaluate("""() => {
                    const el = document.querySelector('.nt-canvas');
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {x: r.x, y: r.y, w: r.width, h: r.height};
                }""")
                if box:
                    # 扫若干位置，命中设备节点后截图（悬停 → 边标签/接口名浮出）
                    cands = [(0.5, 0.45), (0.5, 0.5), (0.4, 0.4), (0.6, 0.55),
                             (0.35, 0.6), (0.65, 0.4)]
                    for i, (rx, ry) in enumerate(cands):
                        pg.mouse.move(box["x"] + box["w"] * rx,
                                      box["y"] + box["h"] * ry)
                        pg.wait_for_timeout(850)
                        pg.screenshot(path=str(OUT / f"topo-hover-{tag}-{i}.png"))
            ctx.close()
        b.close()

    print(json.dumps({"errors": errs[:15], "n": len(errs)},
                     ensure_ascii=False, indent=2))
    print(sorted(x.name for x in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
