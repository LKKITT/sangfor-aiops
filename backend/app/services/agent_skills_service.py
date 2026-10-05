"""Agent Skills（SKILL.md 规范）管理：扫描本机已安装、开关状态、GitHub 导入与删除。

- 扫描目录：~/.agents/skills、~/.claude/skills、<data_dir>/skills（本应用导入目录）；
- 技能 = 含 SKILL.md 的文件夹（YAML frontmatter：name/description）；
- 开关状态存 DB（app_settings key=agent_skills_state），不影响原文件夹；
- 删除仅限本应用导入目录（<data_dir>/skills）内的技能，外部目录只允许开关。
"""
import json
import re
import shutil
from pathlib import Path

from app import db
from app.config import settings

STATE_KEY = "agent_skills_state"
SCAN_DIRS = [
    ("user-agents", Path.home() / ".agents" / "skills"),
    ("user-claude", Path.home() / ".claude" / "skills"),
    ("imported", Path(settings.data_dir) / "skills"),
]


def _state() -> dict:
    try:
        st = json.loads(db.get_setting(STATE_KEY, "{}"))
        return st if isinstance(st, dict) else {}
    except ValueError:
        return {}


def _set_state(state: dict) -> None:
    db.set_setting(STATE_KEY, json.dumps(state, ensure_ascii=False))


def _parse_frontmatter(md_text: str) -> dict:
    """SKILL.md 头部 YAML（name/description 等）宽松解析，失败返回空。"""
    m = re.match(r"^---\s*\n(.*?)\n---", md_text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip().lower(), v.strip().strip("'\"")
        if v:
            meta[k] = v
    return meta


def _scan_dir(dir_path: Path, source: str) -> list[dict]:
    out = []
    if not dir_path.is_dir():
        return out
    for skill_md in sorted(dir_path.glob("*/SKILL.md")):
        folder = skill_md.parent
        try:
            meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            meta = {}
        out.append({
            "folder": folder.name,
            "name": meta.get("name") or folder.name,
            "description": meta.get("description") or "",
            "source": source,
            "path": str(folder),
            "deletable": source == "imported",
        })
    return out


def list_skills() -> list[dict]:
    """全部已安装技能（去重：同名 folder 优先 imported 目录），附开关状态。"""
    state = _state()
    seen, out = set(), []
    for source, base in SCAN_DIRS:
        for s in _scan_dir(base, source):
            if s["folder"] in seen:
                continue
            seen.add(s["folder"])
            s["enabled"] = bool(state.get(s["folder"], {}).get("enabled", False))
            out.append(s)
    return out


def toggle_skill(folder: str, enabled: bool) -> None:
    state = _state()
    state.setdefault(folder, {})["enabled"] = enabled
    _set_state(state)


def delete_skill(folder: str) -> None:
    """删除技能：仅允许删本应用导入目录内的文件夹；状态一并清除。"""
    imported_base = dict(SCAN_DIRS).get("imported")
    imported_dir = Path(imported_base).resolve()
    target = imported_dir / folder
    if folder not in {s["folder"] for s in _scan_dir(imported_dir, "imported")}:
        raise ValueError("仅可删除通过本应用导入的技能（外部目录技能只能关闭）")
    if target.resolve().parent != imported_dir or not target.is_dir():
        raise ValueError("非法的技能目录")
    shutil.rmtree(target, ignore_errors=True)
    state = _state()
    state.pop(folder, None)
    _set_state(state)


def skill_body(folder: str, cap: int = 12000) -> str:
    """读取技能说明（SKILL.md 正文，供对话中 load_skill 工具回传给 LLM）。"""
    for _source, base in SCAN_DIRS:
        f = base / folder / "SKILL.md"
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            return text[:cap] + ("\n…（超长截断）" if len(text) > cap else "")
    raise ValueError(f"技能「{folder}」不存在或缺少 SKILL.md")


def enabled_catalog() -> list[dict]:
    """已启用技能清单（注入系统提示：名称 + 描述，引导 LLM 按需 load_skill）。"""
    return [{"folder": s["folder"], "name": s["name"], "description": s["description"]}
            for s in list_skills() if s["enabled"]]


# ---------------- GitHub 文件夹导入 ----------------

_GH_TREE_RE = re.compile(r"^https://github\.com/([\w.\-]+)/([\w.\-]+)/tree/([^/]+)/(.+?)/?$")


async def import_from_github(url: str, force: bool = False) -> dict:
    """从 GitHub 仓库文件夹 URL 导入技能（下载该文件夹全部文件到 <data_dir>/skills/<名>）。"""
    import httpx

    m = _GH_TREE_RE.match((url or "").strip())
    if not m:
        raise ValueError("请粘贴 GitHub 文件夹链接（形如 https://github.com/owner/repo/tree/main/skills/xxx）")
    owner, repo, branch, subdir = m.groups()
    api_base = f"https://api.github.com/repos/{owner}/{repo}/contents/{subdir}?ref={branch}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(api_base, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 404:
            raise ValueError("GitHub 目录不存在（检查链接/分支/目录名）")
        if r.status_code == 403:
            raise ValueError("GitHub API 限流（未认证每小时 60 次），稍后再试或手动下载放入 skills 目录")
        r.raise_for_status()
        files = [f for f in r.json() if f.get("type") == "file"]

    folder_name = subdir.split("/")[-1]
    if not folder_name or not re.fullmatch(r"[\w.\-]+", folder_name):
        raise ValueError("目录名不合法")
    dest = Path(settings.data_dir) / "skills" / folder_name
    if dest.exists() and not force:
        raise ValueError(f"技能「{folder_name}」已存在（导入请勾选覆盖）")

    imported_files = []
    dest.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for f in files[:40]:   # 单技能文件数保护
            fr = await client.get(f["download_url"])
            fr.raise_for_status()
            (dest / f["name"]).write_bytes(fr.content)
            imported_files.append(f["name"])
    if not (dest / "SKILL.md").is_file():
        raise ValueError("该文件夹不含 SKILL.md，不符合 Agent Skills 规范")
    return {"folder": folder_name, "files": imported_files,
            "path": str(dest), "meta": _parse_frontmatter(
                (dest / "SKILL.md").read_text(encoding="utf-8", errors="replace"))}
