"""单台 SSH 分阶段耗时剖析：connect / 登录横幅 / 关分页 / 命令。

用法：cd backend && python ../scripts/netdev_probe_one.py [设备名关键词]
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import db  # noqa: E402
from app.services import netdev_service as ns  # noqa: E402
import asyncssh  # noqa: E402


async def probe(device: dict) -> None:
    cmd = "display version"
    t0 = time.monotonic()
    mark = lambda: round(time.monotonic() - t0, 2)  # noqa: E731
    conn = await asyncio.wait_for(asyncssh.connect(**ns.ssh_connect_kwargs(device)), timeout=15)
    print(f"  connect      : {mark():6.2f}s")
    async with conn.create_process(term_type="vt100", term_size=(220, 60)) as proc:
        t = mark()
        banner = await ns.read_until_idle(proc.stdout, ns.LOGIN_IDLE_WINDOW,
                                          time.monotonic() + 25, ns.PROMPT_FALLBACK)
        print(f"  login banner : {mark():6.2f}s  (started at {t}) tail={banner.rstrip()[-50:]!r}")
        t = mark()
        proc.stdin.write("screen-length disable\n")
        page = await ns.read_until_idle(proc.stdout, 1.2, time.monotonic() + 10, ns.PROMPT_FALLBACK, 1)
        print(f"  paging off   : {mark():6.2f}s  (started at {t}) tail={page.rstrip()[-50:]!r}")
        t = mark()
        proc.stdin.write(cmd + "\n")
        out = await ns.read_until_idle(proc.stdout, 1.5, time.monotonic() + 25,
                                       ns.PROMPT_FALLBACK, min_len=1)
        print(f"  {cmd:<12} : {mark():6.2f}s  (started at {t}) out_len={len(out)}")
        tail = out.rstrip().splitlines()[-3:] if out.strip() else []
        for l in tail:
            print(f"    | {l[:90]}")
    conn.close()
    print(f"  TOTAL        : {mark():6.2f}s")


async def main() -> None:
    kw = sys.argv[1] if len(sys.argv) > 1 else "WW-14F"
    devices = [d for d in db.list_netdev_devices() if kw.lower() in d["name"].lower()]
    for d in devices[:3]:
        print(f"== {d['name']} ({d['vendor']}) {d['host']}:{d['port']}")
        try:
            await probe(d)
        except Exception as e:   # noqa: BLE001
            print(f"  FAILED: {e}")


asyncio.run(main())
