"""批量执行性能基线/对比脚本：对全部可达设备跑 dis version，统计耗时分布。

用法：cd backend && python ../scripts/netdev_perf_test.py [并发数]
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services import netdev_service as ns  # noqa: E402
from app import db  # noqa: E402


async def main() -> None:
    concurrency = int(sys.argv[1]) if len(sys.argv) > 1 else ns.BATCH_CONCURRENCY
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    devices = [d for d in db.list_netdev_devices() if d.get("host")]
    print(f"devices={len(devices)} concurrency={concurrency} rounds={rounds}")

    async def run_once(tag: str) -> None:
        sem = asyncio.Semaphore(concurrency)

        async def one(d):
            async with sem:
                t0 = time.monotonic()
                r = await ns.run_commands(d, ["display version"], timeout=25)
                return d["name"], r["ok"], round(time.monotonic() - t0, 2)

        wall0 = time.monotonic()
        results = await asyncio.gather(*[one(d) for d in devices], return_exceptions=True)
        wall = time.monotonic() - wall0
        rows = [r for r in results if not isinstance(r, BaseException)]
        ok_dt = sorted(r[2] for r in rows if r[1])
        n = len(ok_dt)
        print(f"[{tag}] ok={n}/{len(devices)} fail={len(rows)-n} wall={wall:.2f}s "
              + (f"p50={ok_dt[n//2]:.2f}s p90={ok_dt[int(n*0.9)]:.2f}s max={ok_dt[-1]:.2f}s" if ok_dt else ""))
        if wall0 and tag == "round1":
            pass
    for i in range(rounds):
        await run_once(f"round{i+1}")
        if i < rounds - 1:
            await asyncio.sleep(3)   # 模拟运维间隔（池 TTL 内）

asyncio.run(main())
