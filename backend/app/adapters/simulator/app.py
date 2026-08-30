"""深信服 AF REST API 模拟器（FastAPI 应用工厂）。

端点结构与响应封套 {code, message, data} 与 AF 8.0.x 官方 REST API 同构：
- POST /api/v1/namespaces/public/login → data.loginResult.token（Cookie: token=xxx）
- GET  /namespaces/public/{systemversion|cpuusage|memoryusage|diskusage|uptimes|interfaces|interfacetraffics}
- CRUD /namespaces/public/{nats|appcontrols/policys|userbindings|staticroutes/ipv4}
- GET  /namespaces/public/configfile/download（模拟 .conf 私有格式文件下载）

AfRestClient 可用同一套代码指向本模拟器或真实设备。
"""
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException

from app.adapters.simulator.state import DEVICE_ACCOUNTS, STATE

API = "/namespaces/public"


def _ok(data=None, message: str = "ok"):
    return {"code": 0, "message": message, "data": data}


def _err(code: int, message: str, status_code: int = 200):
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message, "data": None})


def _check_token(request: Request) -> str:
    token = request.cookies.get("token", "")
    if not token or token not in STATE.tokens:
        _err(1003, "会话无效或已过期，请重新登录")
    return STATE.tokens[token]


def _body_dict(request_data: dict) -> dict:
    return request_data or {}


def create_simulator_app(state=STATE) -> FastAPI:
    app = FastAPI(title="Sangfor AF Simulator", docs_url=None, redoc_url=None)

    # ---------- 认证 ----------
    @app.post("/api/v1" + API + "/login")
    async def login(request_data: dict):
        name = (request_data or {}).get("name", "")
        password = (request_data or {}).get("password", "")
        if DEVICE_ACCOUNTS.get(name) == password and name:
            token = f"simtoken-{datetime.now().timestamp():.0f}-{len(state.tokens) + 1}"
            state.tokens[token] = name
            return _ok({"loginResult": {"token": token}, "authResult": "LOCAL", "role": "admin"})
        _err(1002, "用户名或密码错误")

    @app.post("/api/v1" + API + "/logout")
    async def logout(request: Request):
        state.tokens.pop(request.cookies.get("token", ""), None)
        return _ok()

    @app.get("/api/v1" + API + "/keepalive")
    async def keepalive(request: Request):
        _check_token(request)
        return _ok({"alive": True})

    # ---------- 状态中心 ----------
    @app.get(API + "/systemversion")
    async def systemversion(request: Request):
        _check_token(request)
        return _ok({"version": state.version, "model": state.model, "serial": "AF2000-6688-DEMO"})

    @app.get(API + "/status/summary")
    async def status_summary(request: Request):
        _check_token(request)
        return _ok(state.status_now())

    @app.get(API + "/cpuusage")
    async def cpuusage(request: Request):
        _check_token(request)
        return _ok({"usage": state.status_now()["cpu_usage"]})

    @app.get(API + "/memoryusage")
    async def memoryusage(request: Request):
        _check_token(request)
        return _ok({"usage": state.status_now()["memory_usage"]})

    @app.get(API + "/diskusage")
    async def diskusage(request: Request):
        _check_token(request)
        return _ok({"usage": state.status_now()["disk_usage"]})

    @app.get(API + "/uptimes")
    async def uptimes(request: Request):
        _check_token(request)
        return _ok({"uptime": state.status_now()["uptime"]})

    @app.get(API + "/interfacetraffics")
    async def interfacetraffics(request: Request):
        _check_token(request)
        return _ok(state.traffic_now())

    # ---------- 网络：接口 ----------
    @app.get(API + "/interfaces")
    async def get_interfaces(request: Request, _start: int = 0, _length: int = 10000):
        _check_token(request)
        traffic = state.traffic_now()
        rows = []
        for itf in state.interfaces:
            row = dict(itf)
            row.update(traffic.get(itf["name"], {"rx_kbps": 0, "tx_kbps": 0}))
            rows.append(row)
        return _ok({"list": rows, "total": len(rows)})

    @app.patch(API + "/interfaces/{name}")
    async def patch_interface(name: str, request_data: dict, request: Request):
        _check_token(request)
        body = _body_dict(request_data)
        for itf in state.interfaces:
            if itf["name"] == name:
                itf.update({k: v for k, v in body.items() if k in itf})
                return _ok(itf)
        _err(1404, f"接口 {name} 不存在")

    # ---------- 网络：静态路由 ----------
    @app.get(API + "/staticroutes/ipv4")
    async def get_routes(request: Request):
        _check_token(request)
        return _ok({"list": state.static_routes, "total": len(state.static_routes)})

    @app.post(API + "/staticroutes/ipv4")
    async def create_route(request_data: dict, request: Request):
        _check_token(request)
        row = _body_dict(request_data)
        row["id"] = state.next_id("rt")
        row.setdefault("enabled", True)
        state.static_routes.append(row)
        return _ok(row)

    @app.delete(API + "/staticroutes/ipv4/{rid}")
    async def delete_route(rid: str, request: Request):
        _check_token(request)
        state.static_routes = [r for r in state.static_routes if r["id"] != rid]
        return _ok()

    # ---------- NAT 策略 ----------
    @app.get(API + "/nats")
    async def get_nats(request: Request, _start: int = 0, _length: int = 10000):
        _check_token(request)
        rows = state.nat_rules[_start:_start + _length]
        return _ok({"list": rows, "total": len(state.nat_rules)})

    @app.post(API + "/nats")
    async def create_nat(request_data: dict, request: Request):
        _check_token(request)
        row = _body_dict(request_data)
        if not row.get("name"):
            _err(1400, "策略名称不能为空")
        new_id = row.get("id") or state.next_id("nat")
        if any(r["id"] == new_id for r in state.nat_rules):
            _err(1409, f"NAT 策略 ID {new_id} 已存在")
        row["id"] = new_id
        row.setdefault("enabled", True)
        row.setdefault("hit_count", 0)
        state.nat_rules.insert(0, row)   # 新策略默认置顶（与真实设备一致：匹配从上往下）
        return _ok(row)

    @app.patch(API + "/nats/{rid}")
    async def patch_nat(rid: str, request_data: dict, request: Request):
        _check_token(request)
        body = _body_dict(request_data)
        for i, row in enumerate(state.nat_rules):
            if row["id"] == rid:
                row.update(body)
                state.nat_rules[i] = row
                return _ok(row)
        _err(1404, f"NAT 策略 {rid} 不存在")

    @app.put(API + "/nats/{rid}")
    async def put_nat(rid: str, request_data: dict, request: Request):
        return await patch_nat(rid, request_data, request)

    @app.delete(API + "/nats/{rid}")
    async def delete_nat(rid: str, request: Request):
        _check_token(request)
        before = len(state.nat_rules)
        state.nat_rules = [r for r in state.nat_rules if r["id"] != rid]
        if len(state.nat_rules) == before:
            _err(1404, f"NAT 策略 {rid} 不存在")
        return _ok()

    # ---------- 应用控制（访问控制）策略 ----------
    @app.get(API + "/appcontrols/policys")
    async def get_acls(request: Request, _start: int = 0, _length: int = 10000):
        _check_token(request)
        rows = state.acl_rules[_start:_start + _length]
        return _ok({"list": rows, "total": len(state.acl_rules)})

    @app.post(API + "/appcontrols/policys")
    async def create_acl(request_data: dict, request: Request):
        _check_token(request)
        row = _body_dict(request_data)
        if not row.get("name"):
            _err(1400, "策略名称不能为空")
        new_id = row.get("id") or state.next_id("acl")
        if any(r["id"] == new_id for r in state.acl_rules):
            _err(1409, f"策略 ID {new_id} 已存在")
        row["id"] = new_id
        row.setdefault("enabled", True)
        row.setdefault("hit_count", 0)
        row.setdefault("action", "deny")
        state.acl_rules.insert(0, row)
        return _ok(row)

    @app.patch(API + "/appcontrols/policys/{rid}")
    async def patch_acl(rid: str, request_data: dict, request: Request):
        _check_token(request)
        body = _body_dict(request_data)
        for i, row in enumerate(state.acl_rules):
            if row["id"] == rid:
                row.update(body)
                state.acl_rules[i] = row
                return _ok(row)
        _err(1404, f"策略 {rid} 不存在")

    @app.put(API + "/appcontrols/policys/{rid}")
    async def put_acl(rid: str, request_data: dict, request: Request):
        return await patch_acl(rid, request_data, request)

    @app.delete(API + "/appcontrols/policys/{rid}")
    async def delete_acl(rid: str, request: Request):
        _check_token(request)
        before = len(state.acl_rules)
        state.acl_rules = [r for r in state.acl_rules if r["id"] != rid]
        if len(state.acl_rules) == before:
            _err(1404, f"策略 {rid} 不存在")
        return _ok()

    # ---------- IP-MAC 绑定 ----------
    @app.get(API + "/userbindings")
    async def get_bindings(request: Request):
        _check_token(request)
        return _ok({"list": state.user_bindings, "total": len(state.user_bindings)})

    @app.post(API + "/userbindings")
    async def create_binding(request_data: dict, request: Request):
        _check_token(request)
        row = _body_dict(request_data)
        if not row.get("ip"):
            _err(1400, "绑定 IP 不能为空")
        for b in state.user_bindings:
            if b["ip"] == row.get("ip") and b.get("enabled", True):
                _err(1409, f"IP {row['ip']} 已存在绑定记录 {b['id']}")
        new_id = row.get("id") or state.next_id("ub")
        if any(b["id"] == new_id for b in state.user_bindings):
            _err(1409, f"绑定记录 ID {new_id} 已存在")
        row["id"] = new_id
        row.setdefault("enabled", True)
        row.setdefault("binding_type", "static")
        state.user_bindings.append(row)
        return _ok(row)

    @app.patch(API + "/userbindings/{rid}")
    async def patch_binding(rid: str, request_data: dict, request: Request):
        _check_token(request)
        body = _body_dict(request_data)
        for i, row in enumerate(state.user_bindings):
            if row["id"] == rid:
                row.update(body)
                state.user_bindings[i] = row
                return _ok(row)
        _err(1404, f"绑定记录 {rid} 不存在")

    @app.delete(API + "/userbindings/{rid}")
    async def delete_binding(rid: str, request: Request):
        _check_token(request)
        before = len(state.user_bindings)
        state.user_bindings = [b for b in state.user_bindings if b["id"] != rid]
        if len(state.user_bindings) == before:
            _err(1404, f"绑定记录 {rid} 不存在")
        return _ok()

    # ---------- 配置文件下载/上传（模拟 Web 控制台私有端点） ----------
    @app.get(API + "/configfile/download")
    async def download_conf(request: Request):
        _check_token(request)
        content = state.render_conf_file()
        filename = f"{state.model}_{state.version.replace('AF ', '')}_{datetime.now():%Y%m%d}.conf"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post(API + "/configfile/upload")
    async def upload_conf(request: Request):
        _check_token(request)
        data = await request.body()
        if not data:
            _err(1400, "配置文件内容为空")
        if b"#SANGFOR-AF-CONF" not in data.split(b"\n", 1)[0]:
            _err(1400, "配置文件格式校验失败（非本系列设备配置文件）")
        state.restore_marker = f"restored@{datetime.now().isoformat(timespec='seconds')}"
        return _ok({"accepted": True, "reboot_required": True,
                    "note": "恢复配置后设备将自动重启（模拟）"})

    # ---------- 命中数刷新（模拟策略命中统计） ----------
    @app.post(API + "/devops/tick")
    async def tick(request: Request):
        _check_token(request)
        for row in state.acl_rules + state.nat_rules:
            if row.get("enabled", True) and row.get("hit_count", 0) >= 0:
                row["hit_count"] = row.get("hit_count", 0) + 1
        return _ok()

    return app
