"""客户管理 API：客户（工作区/租户）档案的增删改查。

客户 = 租户的业务档案：code 为隔离键（与 X-Tenant-Id 同一口径），name/contact 等
为展示信息。客户档案全局共享；删除前校验业务表无引用，避免产生悬空租户数据。
"""
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/api/customers", tags=["customers"])

# 与请求中间件 X-Tenant-Id 的合法性口径一致（非法编码会被中间件回落 default）
_CODE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


class CustomerIn(BaseModel):
    code: str
    name: str
    contact: str = ""
    phone: str = ""
    email: str = ""
    note: str = ""


class CustomerPatch(BaseModel):
    name: str | None = None
    contact: str | None = None
    phone: str | None = None
    email: str | None = None
    note: str | None = None


def _with_usage(c: dict) -> dict:
    return {**c, "usage": db.count_tenant_usage(c["code"])}


@router.get("")
def list_customers() -> dict:
    return {"customers": [_with_usage(c) for c in db.list_customers()]}


@router.post("")
def create_customer(payload: CustomerIn) -> dict:
    code = payload.code.strip()
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "客户名称不能为空")
    if code == "default":
        raise HTTPException(400, "default 为内置默认客户，不可占用")
    if not _CODE_RE.fullmatch(code):
        raise HTTPException(400, "客户编码仅限字母/数字/下划线/中划线，1-32 位（切换客户时使用）")
    if db.get_customer_by_code(code):
        raise HTTPException(409, f"客户编码「{code}」已存在")
    customer = db.insert_customer({
        "id": db.new_id("cus_"), "code": code, "name": name,
        "contact": payload.contact.strip(), "phone": payload.phone.strip(),
        "email": payload.email.strip(), "note": payload.note.strip(),
        "created_at": db.now(), "updated_at": db.now(),
    })
    db.audit("customer.create", {"code": code, "name": name}, actor="user")
    return _with_usage(customer)


@router.put("/{customer_id}")
def update_customer(customer_id: str, payload: CustomerPatch) -> dict:
    if not db.get_customer(customer_id):
        raise HTTPException(404, "客户不存在或已被删除")
    fields = {k: v.strip() for k, v in payload.model_dump().items() if v is not None}
    if "name" in fields and not fields["name"]:
        raise HTTPException(400, "客户名称不能为空")
    customer = db.update_customer(customer_id, fields)
    db.audit("customer.update", {"id": customer_id, "fields": list(fields)}, actor="user")
    return _with_usage(customer)


@router.delete("/{customer_id}")
def delete_customer(customer_id: str) -> dict:
    customer = db.get_customer(customer_id)
    if not customer:
        raise HTTPException(404, "客户不存在或已被删除")
    if customer["code"] == "default":
        raise HTTPException(400, "内置默认客户不可删除（名称等信息可编辑）")
    usage = db.count_tenant_usage(customer["code"])
    total = usage["devices"] + usage["netdev_devices"] + usage["conversations"]
    if total:
        raise HTTPException(
            409,
            f"客户「{customer['name']}」仍有关联数据（设备 {usage['devices']}、网络设备 "
            f"{usage['netdev_devices']}、会话 {usage['conversations']}），请先迁移或删除后再删除客户")
    db.delete_customer(customer_id)
    db.audit("customer.delete", {"code": customer["code"], "name": customer["name"]}, actor="user")
    return {"ok": True}
