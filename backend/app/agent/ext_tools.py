"""外部能力工具：Agent Skills 加载（load_skill）+ 已启用 MCP 服务工具的聚合注入。

编排器 _tool_scope 末尾统一追加本模块产出的工具：
- load_skill：把启用技能的 SKILL.md 说明回传给 LLM（Agent Skills 的运行时入口）；
- mcp_*：各已启用 MCP 服务的原生工具（schema 透传，调用转发对应会话）。
"""
from app.agent.tools import Tool
from app.services import agent_skills_service, mcp_service


async def _h_load_skill(client, args, device):
    folder = str(args.get("skill") or "").strip()
    enabled = {s["folder"]: s for s in agent_skills_service.enabled_catalog()}
    if folder not in enabled:
        avail = "、".join(sorted(enabled)) or "（无）"
        return {"error": f"技能「{folder}」未启用或不存在。已启用技能：{avail}"}
    body = agent_skills_service.skill_body(folder)
    return {"skill": folder, "name": enabled[folder]["name"], "instructions": body,
            "_llm_summary": ("技能说明已加载，请严格按 instructions 的步骤与约束执行；"
                             "说明中引用的脚本/文件路径为相对技能目录的路径。")}


def skills_catalog_message(folders=None) -> str | None:
    """已启用技能的系统提示清单（无启用技能返回 None 不注入）。"""
    catalog = agent_skills_service.enabled_catalog(folders)
    if not catalog:
        return None
    lines = [f"- {s['name']}（调用 load_skill 时 skill 传 \"{s['folder']}\"）：{s['description']}"
             for s in catalog]
    return ("## 已启用 Agent Skills（可扩展能力包）\n"
            "当用户请求与以下技能描述相关时，先调用 load_skill 工具加载该技能的完整说明，"
            "再严格按说明执行（技能可能包含脚本、模板或特定流程约束）：\n" + "\n".join(lines))


async def get_external_tools(mcps=None, skills=None) -> list[Tool]:
    """外部工具全集：load_skill + 指定 MCP 服务的桥接工具（对话级选用）。"""
    tools = [Tool(
        name="load_skill",
        description="加载已启用的 Agent Skill 的完整说明（含操作步骤/脚本用法）。"
                    "当用户请求命中系统提示中列出的技能描述时，先调用本工具再执行。",
        parameters={"type": "object",
                    "properties": {"skill": {"type": "string",
                                             "description": "技能文件夹名（见系统提示的技能清单）"}},
                    "required": ["skill"]},
        handler=_h_load_skill,
        device_type=None, needs_device=False, batch_devices=False)]
    tools += await mcp_service.agent_tools(server_ids=mcps)
    return tools
