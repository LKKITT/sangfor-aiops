#!/usr/bin/env python3
"""最终验证：亮/暗双主题 + 逐边悬停截图，确认接口简写标签为**水平胶囊**。

实现已改为自绘 zrender 图层（见 NetDevTopology.vue#drawHoverEdgeLabel），
因此不能再从 echarts option 的 label.show 读状态，改为直接检查
zrender displayList 中是否存在带我们标记的胶囊 Group。
"""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8610"
OUT = "/workspace/sfa-screenshots-nodes"

# 期望的端口简写（与后端拓扑数据一致）
EXPECT = {0: "g0/0/11 ↔ g0/0/24", 1: "g0/0/12 ↔ g0/0/24",
          2: "XG0/0/1 ↔ g0/0/48", 3: "g0/0/1 ↔ g0/0/2"}

errs = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for dark in (False, True):
        tag = "dark" if dark else "light"
        ctx = b.new_context(viewport={"width": 1560, "height": 1020}, device_scale_factor=2)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errs.append(f"[{tag}] {e}"))
        pg.on("console", lambda m: errs.append(f"[{tag}] {m.text}") if m.type == "error" else None)
        pg.goto(f"{BASE}/#/netdev", wait_until="networkidle")
        pg.wait_for_timeout(1400)
        pg.evaluate("""(d)=>{localStorage.setItem('sfa-theme',d?'dark':'light');
            document.documentElement.dataset.theme=d?'dark':'light';}""", dark)
        tab = pg.locator(".el-tabs__item:has-text('网络拓扑')").first
        if tab.count():
            tab.click()
        pg.wait_for_timeout(4500)

        # 扫描出所有边图元的可命中点
        pts = pg.evaluate("""()=>{const zr=window.__topoChart.getZr();const out=[];
            for(let y=5;y<zr.getHeight();y+=5)for(let x=5;x<zr.getWidth();x+=5){
              const el=zr.findHover(x,y).target;
              if(el&&el.type==='ec-line')out.push([x,y]);}return out;}""")
        geo = pg.evaluate("""()=>{const r=document.querySelector('.nt-canvas').getBoundingClientRect();
            return {x:r.x,y:r.y};}""")

        def overlay_text():
            """读取我们自绘的胶囊文本。

            注意：zrender 6 的文字图元 type 是 'tspan'（不是 'text'），
            旧版过滤条件会取不到，这里两者都收。
            """
            return pg.evaluate("""()=>{
                const zr=window.__topoChart.getZr();
                const list=zr.storage.getDisplayList(true);
                const out=[];
                for(const el of list){
                  const t=el.style&&el.style.text;
                  if(typeof t==='string'&&t.includes('↔')){out.push(t);}
                }
                return out;}""")

        got = {}
        for (x, y) in pts:
            pg.mouse.move(geo['x'] + x, geo['y'] + y)
            pg.wait_for_timeout(180)
            txt = overlay_text()
            if txt:
                t = txt[0]
                idx = next((k for k, v in EXPECT.items() if v == t), None)
                if idx is not None and idx not in got:
                    got[idx] = t
                    # 截图前把 tooltip 关掉，否则浮层会压住胶囊。
                    # 直接隐藏 echarts tooltip 的 DOM，胶囊由 zrender 画在 canvas 上不受影响。
                    pg.evaluate("""()=>{
                        document.querySelectorAll('div[style*="position: absolute"]').forEach(d=>{
                          if(d.style && d.style.zIndex && +d.style.zIndex > 1000) d.style.display='none';
                        });}""")
                    pg.wait_for_timeout(350)
                    pg.locator(".nt-canvas").screenshot(path=f"{OUT}/final-{tag}-edge{idx}.png")
                    print(f"[{tag}] 边{idx} -> {t}")
            if len(got) >= len(EXPECT):
                break
        miss = [k for k in EXPECT if k not in got]
        print(f"[{tag}] 命中 {len(got)}/{len(EXPECT)} 条边" + (f"，缺失: {miss}" if miss else "，全部通过"))
        # 离开画布后胶囊应被清理
        pg.mouse.move(10, 990)
        pg.wait_for_timeout(400)
        left = overlay_text()
        print(f"[{tag}] 离开画布后残留胶囊: {left if left else '无（正确清理）'}")
        ctx.close()
    b.close()

real = [e for e in errs if 'favicon' not in e.lower()]
print("errors:", json.dumps(real[:6], ensure_ascii=False) if real else "[]")
