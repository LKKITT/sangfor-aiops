"""Agent 技能路由：LLM 兜底技能选择与工具注入范围（从 orchestrator 拆出的独立模块）。"""
import asyncio

from app.agent import skills
from app.agent.tools import TOOLS_BY_NAME
from app.config import settings
from app.services.app_settings import get_llm_config


async def llm_select_skill(orch, user_message: str, dtype: str):
    """LLM 兜底技能选择：一次非流式小调用；带独立超时（不拖慢首 token），失败回退全量模式。

    仅在关键词路由未命中且消息疑似写操作时触发（见 _run_llm_loop）：
    技能的价值在收窄写工具集与注入流程指引，纯查询直接走全量模式即可。
    """
    try:
        resp = await asyncio.wait_for(orch._llm().chat.completions.create(
            model=get_llm_config()["model"],
            messages=[
                {"role": "system", "content": skills.skill_catalog_message(dtype)},
                {"role": "user", "content": user_message[:500]},
            ],
            temperature=settings.llm_temperature, top_p=settings.llm_top_p,
            max_tokens=1024, extra_body=settings.llm_extra_body(),
        ), timeout=4.0)
        return skills.parse_skill_choice(resp.choices[0].message.content or "", dtype)
    except Exception:   # noqa: BLE001 —— 选择失败/超时（TimeoutError 亦为 Exception 子类）不影响主流程
        return None


async def tool_scope(skill, dtype: str, use_knowledge: bool,
                     mcps=None, skill_folders=None) -> tuple[list[dict], dict]:
    """工具注入范围：只读全量 + 技能解锁的写工具；勾选知识库时附加官方知识库工具；
    末尾追加外部能力工具（对话级选用的 MCP 服务工具 + Agent Skills 加载器，
    mcps/skills 为 None 时=全部已启用）。"""
    scope = skills.resolve_skill_tools(skill, dtype)
    tools_by_name = {t.name: t for t in scope}
    tool_schemas = [t.schema() for t in scope]
    if use_knowledge and skills.KB_TOOL_NAME not in tools_by_name:
        kb_tool = TOOLS_BY_NAME.get(skills.KB_TOOL_NAME)
        if kb_tool is not None:
            tool_schemas.append(kb_tool.schema())
            tools_by_name[kb_tool.name] = kb_tool
    try:
        from app.agent.ext_tools import get_external_tools
        for t in await get_external_tools(mcps=mcps, skill_folders=skill_folders):
            if t.name not in tools_by_name:
                tool_schemas.append(t.schema())
                tools_by_name[t.name] = t
    except Exception:   # noqa: BLE001 —— MCP SDK 未安装/服务全失联时仅缺失这部分工具
        pass
    return tool_schemas, tools_by_name
