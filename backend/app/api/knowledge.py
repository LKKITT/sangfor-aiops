"""个人知识库 API：词条管理、沉淀触发、反思报告、统计与知识图谱数据。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app.services import personal_kb_service

router = APIRouter(prefix="/api/kb", tags=["knowledge"])


class ProcessIn(BaseModel):
    limit: int = 5
    convs: list[str] = []   # 指定要沉淀的对话（为空则按队列顺序处理）


class ReflectionIn(BaseModel):
    start: str = ""   # 沉淀日期起（YYYY-MM-DD），空=最早
    end: str = ""     # 沉淀日期止（YYYY-MM-DD），空=今天


def _build_graph(entries: list[dict]) -> dict:
    """知识图谱数据：词条为节点（大小按引用数），共享标签连边。

    连边使用归一化后的标签（db.tag_display_map）——「深信服AF」「AF防火墙」「AF」
    等同义标签归到同一展示名，避免同概念词条之间连不上线。
    """
    nodes = [{
        "id": e["id"], "name": e["topic"], "category": e["category"],
        "symbolSize": min(18 + 5 * len(e.get("references") or []), 55),
    } for e in entries]
    dmap = db.tag_display_map(entries)
    tag_sets = [{dmap.get(t, t) for t in (e.get("tags") or [])} for e in entries]
    edges = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if tag_sets[i] & tag_sets[j]:
                edges.append({"source": entries[i]["id"], "target": entries[j]["id"]})
    return {"nodes": nodes, "edges": edges, "categories":
            sorted({e["category"] for e in entries})}


@router.get("/entries")
def list_entries(category: str = "", keyword: str = "", limit: int = 200) -> list[dict]:
    return db.list_kb_entries(category=category, keyword=keyword, limit=min(limit, 500))


@router.get("/entries/{entry_id}")
def get_entry(entry_id: str) -> dict:
    entry = db.get_kb_entry(entry_id)
    if not entry:
        raise HTTPException(404, "词条不存在")
    return entry


@router.delete("/entries/{entry_id}")
def remove_entry(entry_id: str) -> dict:
    if not db.get_kb_entry(entry_id):
        raise HTTPException(404, "词条不存在")
    db.delete_kb_entry(entry_id)
    db.audit("kb.entry.delete", {"entry_id": entry_id}, actor="user")
    return {"ok": True}


@router.get("/pending")
def pending() -> dict:
    """待沉淀对话列表（含标题/消息数/知识库提问明细，供勾选沉淀）。"""
    items = personal_kb_service.pending_items()
    return {"count": len(items), "convs": [i["conv_id"] for i in items], "items": items}


@router.delete("/pending/{conv_id}")
def dismiss_pending(conv_id: str) -> dict:
    """把对话移出待沉淀队列（自动/手动沉淀均不再处理）。"""
    db.dismiss_kb_conv(conv_id)
    db.audit("kb.pending.dismiss", {"conv_id": conv_id}, actor="user")
    return {"ok": True}


@router.post("/process")
async def process(payload: ProcessIn) -> dict:
    """沉淀对话为知识词条：指定 convs 时只处理选中的，否则按队列顺序。"""
    return await personal_kb_service.process_pending(limit=payload.limit,
                                                     convs=payload.convs or None)


@router.post("/reflection")
async def create_reflection(payload: ReflectionIn | None = None) -> dict:
    """基于词条生成反思与总结报告；支持自定义时间节点（按沉淀日期过滤）。"""
    p = payload or ReflectionIn()
    return await personal_kb_service.generate_reflection(start=p.start, end=p.end)


@router.get("/reflections")
def reflections(limit: int = 20) -> list[dict]:
    return db.list_kb_reflections(limit=min(limit, 100))


@router.get("/stats")
def stats() -> dict:
    """统计 + 知识图谱（可视化数据源）。"""
    entries = db.list_kb_entries(limit=500)
    return {**db.kb_stats(), "graph": _build_graph(entries)}
