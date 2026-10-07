from playwright.sync_api import sync_playwright
from pathlib import Path
import json

BASE = "http://127.0.0.1:8610"
OUT = Path("/workspace/sfa-screenshots-v2")
W, H = 1560, 1000
errs = []

def switch_theme(pg, dark):
    pg.evaluate("""(d) => {
        localStorage.setItem('sfa-theme', d ? 'dark' : 'light');
        document.documentElement.dataset.theme = d ? 'dark' : 'light';
        document.documentElement.classList.toggle('dark', d);
    }""", dark)
    pg.wait_for_timeout(400)

with sync_playwright() as p:
    b = p.chromium.launch()
    for dark in (False, True):
        tag = "dark" if dark else "light"
        ctx = b.new_context(viewport={"width": W, "height": H}, device_scale_factor=1.5)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        pg.on("console", lambda m: errs.append(f"console.error: {m.text}") if m.type == "error" else None)

        # === 知识图谱 ===
        pg.goto(f"{BASE}/#/knowledge", wait_until="networkidle")
        switch_theme(pg, dark); pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(2800)
        pg.screenshot(path=str(OUT / f"kb-graph-{tag}.png"))
        pg.locator(".chart-card-main").screenshot(path=str(OUT / f"kb-graph-zoom-{tag}.png"))
        # 悬停一个节点，验证 tooltip
        try:
            box = pg.locator(".chart-graph").bounding_box()
            pg.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
            pg.wait_for_timeout(900)
            pg.screenshot(path=str(OUT / f"kb-graph-tooltip-{tag}.png"))
        except Exception as e:
            errs.append(f"kb tooltip: {e}")

        # === 网络拓扑 ===
        pg.goto(f"{BASE}/#/netdev", wait_until="networkidle")
        pg.wait_for_timeout(700)
        pg.get_by_text("网络拓扑", exact=True).first.click(timeout=8000)
        switch_theme(pg, dark); pg.wait_for_timeout(2600)
        pg.screenshot(path=str(OUT / f"topology-{tag}.png"))
        pg.locator(".nt-canvas-card").screenshot(path=str(OUT / f"topology-zoom-{tag}.png"))
        # 悬停核心节点，验证 tooltip
        try:
            box = pg.locator(".nt-canvas").bounding_box()
            pg.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
            pg.wait_for_timeout(900)
            pg.screenshot(path=str(OUT / f"topology-tooltip-{tag}.png"))
        except Exception as e:
            errs.append(f"topo tooltip: {e}")
        # 点击核心节点，验证弹出面板
        try:
            pg.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(OUT / f"topology-panel-{tag}.png"))
        except Exception as e:
            errs.append(f"topo panel: {e}")
        ctx.close()
    b.close()

print(json.dumps({"errors": errs[:10], "n": len(errs)}, ensure_ascii=False, indent=2))
print(sorted(x.name for x in OUT.glob("*.png")))
