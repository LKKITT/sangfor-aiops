"""深信服 AF/AC 版本知识库（内置快照）。

数据来源：深信服技术支持平台（support.sangfor.com.cn）新版本发布信息/产品升级文档、
官网安全中心 PSIRT 公告、深信服社区（bbs.sangfor.com.cn）公开资料，经调研整理固化。
用于离线演示与官方平台需认证时的降级数据源；条目均标注来源说明。
"""
import re
from dataclasses import dataclass, field

AF = "af"
AC = "ac"
SCP = "scp"
HCI = "hci"

PRODUCT_NAMES = {AF: "下一代防火墙 AF", AC: "上网行为管理 AC",
                 SCP: "SCP 云计算平台", HCI: "HCI 超融合"}

# SCP/HCI 无内置升级链：升级价值以官方『新版本发布信息』页抓取版本为骨架（update_service 锚定）
PRODUCTS_WITH_CHAIN = (AF, AC)

# 升级路线（官方功能页核实结论）：
# AF 8.0.48 为旧架构末端；新架构自 8.0.69 起（与官方『新版本发布信息』页覆盖版本一致）。
# 跨架构（8.0.48 → 8.0.69+）不是常规升级：需联系深信服客服评估，并按官方方案重装系统盘。
AF_ARCH_SPLIT = "8.0.69"
AF_CHAIN_OLD = ["8.0.23", "8.0.26", "8.0.32", "8.0.35", "8.0.45", "8.0.48"]
AF_CHAIN_NEW = ["8.0.69", "8.0.75", "8.0.85", "8.0.106", "8.0.107"]
AF_LATEST = "8.0.107"
AC_CHAIN = ["12.0.5", "12.0.25", "12.0.45", "12.0.80", "13.0.47", "13.0.102",
            "13.0.121", "13.0.140"]
AC_LATEST = "13.0.140"
AC_LATEST_STABLE = "13.0.121"

CHAIN = {AF: AF_CHAIN_NEW, AC: AC_CHAIN}
LATEST = {AF: AF_LATEST, AC: AC_LATEST}


@dataclass
class Release:
    version: str
    product: str
    arch: str = "new"                      # new / old
    release_date: str = ""
    is_latest: bool = False
    eol: bool = False
    notes: list = field(default_factory=list)      # [{type,title,detail}]
    known_issues: list = field(default_factory=list)
    upgrade_notes: list = field(default_factory=list)


RELEASES: dict[str, Release] = {}


def _reg(r: Release) -> None:
    RELEASES[f"{r.product}:{r.version}"] = r


_reg(Release("8.0.85", AF, "new", "2023-12(约)", notes=[
    {"type": "新增", "title": "新架构标准版本", "detail": "新架构截止标准版本（社区口径），作为向 8.0.95+ 演进的基线"},
], known_issues=[
    {"type": "已知问题", "title": "邮件安全功能占满 mbuf", "detail": "Mbuf pool 占用 ~100%，导致新会话建立失败；8.0.107 已彻底修复"},
    {"type": "已知问题", "title": "特定网卡组合接口概率性无法 up", "detail": "创实 C3000 KUKA/NIVA + X553 网卡组合下概率性出现"},
]))

_reg(Release("8.0.106", AF, "new", "2025(约)", notes=[
    {"type": "新增", "title": "DDNS 策略", "detail": "支持动态域名解析配置"},
    {"type": "新增", "title": "VPN 隧道带宽管理", "detail": "支持对 VPN 隧道进行带宽管控"},
    {"type": "新增", "title": "未授权外联检测与防伪造", "detail": "增强内网外联行为检测能力"},
    {"type": "修复", "title": "修复 8.0.95 系列已知问题", "detail": "修复端口池延迟回收、弱密码拦截等历史问题"},
], upgrade_notes=[
    "官方提供《AF8.0.106版本升级方案》专项文档",
]))

_reg(Release("8.0.107", AF, "new", "2025(当前最新)", is_latest=True, notes=[
    {"type": "新增", "title": "云威胁串接", "detail": "与云端威胁情报串接联动（升级时设备重启、业务中断，属高危操作）"},
    {"type": "优化", "title": "上网安全管控能力演进", "detail": "组网能力、上网安全管控和功能扩展多项改进"},
    {"type": "修复", "title": "修复 8.0.59 日志记录问题", "detail": "修复历史版本日志记录缺陷"},
    {"type": "修复", "title": "修复 8.0.75 双机规则库回滚", "detail": "修复双机部署手动更新规则库后内容自动回滚（hasyncd 版本信息不匹配）"},
    {"type": "修复", "title": "修复 8.0.85 mbuf 占满", "detail": "彻底解决邮件安全功能导致 mbuf pool 占满问题"},
]))

_reg(Release("8.0.45", AF, "old", "2022(约)", notes=[
    {"type": "优化", "title": "旧架构版本", "detail": "可直升至旧架构末端 8.0.48；跨架构到新架构需客服评估 + 重装系统盘"},
], known_issues=[], upgrade_notes=[
    "旧架构先升级至末端 8.0.48；跨架构到新架构（8.0.69+）需联系深信服客服评估",
    "跨架构迁移涉及重装系统盘，迁移前务必完成完整配置备份",
]))

_reg(Release("8.0.48", AF, "old", "2023(约)", notes=[
    {"type": "优化", "title": "旧架构末端版本", "detail": "旧架构路线最后一个版本，旧架构内可直接升级至此"},
    {"type": "安全", "title": "多项安全修复", "detail": "修复旧架构已知安全漏洞"},
], upgrade_notes=[
    "8.0.48 之后跨架构升级到新架构（8.0.69+）：需联系深信服客服评估，按官方方案重装系统盘，不可直接升级覆盖",
]))

_reg(Release("13.0.102", AC, "new", "2024(约)", notes=[
    {"type": "新增", "title": "文件外发通路管控", "detail": "支持基于文件属性控制文件外发"},
    {"type": "新增", "title": "外发审计增强", "detail": "新增浏览器外发审计、解密行为审计"},
    {"type": "新增", "title": "防泄密外发管控授权", "detail": "防泄密场景管控授权"},
    {"type": "新增", "title": "外联检查与横向隔离", "detail": "外联检查功能、内网横向隔离外联管控"},
]))

_reg(Release("13.0.121", AC, "new", "2025-04-22", notes=[
    {"type": "修复", "title": "修复 13.0.120 已知问题", "detail": "提升系统稳定性与安全性（升级包 1.63G）"},
], upgrade_notes=[
    "官方说明：支持 AC 11.0 正式版以后直升该升级包",
]))

_reg(Release("13.0.140", AC, "new", "2026(较新)", is_latest=True, notes=[
    {"type": "优化", "title": "13.0 线持续演进", "detail": "13.0.140 及 .001 补丁（Build20260613）；已有 iOS WiFi 连接问题案例反馈"},
]))

_reg(Release("12.0.80", AC, "old", "2023(约)", eol=True, notes=[
    {"type": "新增", "title": "打印审计等管控增强", "detail": "新增打印审计、Windows 文件外发截屏、飞书/阿里邮箱文件外发审计、资产识别"},
], known_issues=[
    {"type": "已知问题", "title": "共享接入管理误判", "detail": "误判鸿蒙 PC（Win 虚拟机同 IP）冻结网络"},
    {"type": "EOL", "title": "版本生命周期终止", "detail": "停留在 12.0.80 不再获得新版本功能与安全更新，建议升级 13.0.x"},
]))

# ---------------- PSIRT 安全公告（真实公开通告） ----------------
PSIRT_ADVISORIES = [
    {"id": "SF-PSIRT-20220472", "product": AC,
     "title": "深信服全网行为管理存在远程代码执行漏洞",
     "cvss": 9.8, "severity": "严重",
     "affected_versions": ["13.0.47", "13.0.48", "13.0.49", "13.0.52", "13.0.53", "13.0.70", "13.0.71"],
     "fixed_in": "SP_AC_JG_29 及之后安全补丁包（纪元平台）",
     "published": "2023-01-09",
     "mitigation": "收缩设备暴露端口；漏洞详情见官网安全中心公告详情页",
     "source": "https://www.sangfor.com.cn/sec_center"},
    {"id": "AF-AUTH-BYPASS-2023", "product": AF,
     "title": "深信服下一代防火墙身份验证绕过漏洞通告",
     "cvss": 8.6, "severity": "高危",
     "affected_versions": ["8.0.17"],
     "fixed_in": "升级至 8.0.6x 及以上新架构版本",
     "published": "2023(约)",
     "mitigation": "限制管理口来源地址",
     "source": "https://www.sangfor.com.cn/sec_center"},
]

# ---------------- 版本解析与升级路径 ----------------

def normalize_version(sw_version: str) -> str:
    """'AF 8.0.85' / 'AC&SG 13.0.121' / 'SCP6.7.33_B-...' / '8.0.85' → '8.0.85'"""
    v = (sw_version or "").upper().replace("AF", " ").replace("AC", " ").replace("&SG", " ")         .replace("SCP", " ").replace("HCI", " ")
    digits = [tok for tok in v.replace("-", ".").replace("_", ".").split() if tok[:1].isdigit()]
    for tok in digits:
        parts = tok.split(".")
        if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
            return ".".join(parts[:3])
    return (sw_version or "").strip()


def version_key(v: str) -> tuple[int, ...]:
    m = re.search(r"\d+(?:\.\d+)*", str(v))
    v = m.group(0) if m else "0"
    return tuple(int(x) for x in v.split("."))


def product_of(sw_version: str) -> str:
    v = (sw_version or "").upper()
    if "SCP" in v:
        return SCP
    if "AC" in v:
        return AC
    if "HCI" in v:
        return HCI
    if "AF" in v:
        return AF
    return ""   # 未知产品线（不再兜底 AF，交由调用方走 fallback）


def _chain_for(product: str, version: str) -> list[str]:
    if product not in PRODUCTS_WITH_CHAIN:
        return []   # SCP/HCI 无内置升级链
    if product != AF:
        return CHAIN[AC]
    return AF_CHAIN_OLD if version_key(version) < version_key(AF_ARCH_SPLIT) else AF_CHAIN_NEW


def upgrade_path(product: str, current: str, target: str | None = None) -> dict:
    """计算升级路径。

    - AF 旧架构（≤8.0.45）：旧链走完后接新架构链（8.0.48 为跨架构节点，标注需按官方
      迁移指引执行）；
    - AC 12.x / 早期 13.x：可直接升级至稳定版 13.0.121（官方支持直升，需前置检测）。
    """
    target = target or LATEST.get(product, "")
    if not current or not target:
        return {"product": product, "current": current, "target": target, "hops": [],
                "cross_arch_migration": False, "direct_upgrade": False,
                "notes": ["暂无官方版本数据或无法解析当前版本，升级建议不可用；"
                          "请先确认设备版本并刷新官方更新信息"]}
    cur_k, tgt_k = version_key(current), version_key(target)
    result = {"product": product, "current": current, "target": target, "hops": [],
              "cross_arch_migration": False, "direct_upgrade": False, "notes": []}

    # SCP/HCI：无内置升级链，升级价值以官方功能页版本为准（update_service 锚定后 hops 覆盖）
    if product in (SCP, HCI):
        if cur_k < tgt_k:
            result["hops"] = [target]
            result["direct_upgrade"] = True
            result["notes"].append(
                f"{current} 可直接升级至 {target}；升级前需完成前置检测"
                "（配置/虚拟机备份、集群健康状态、磁盘空间与授权核对）")
        else:
            result["notes"].append("当前已是最新版本，无需升级")
        return result

    # AC：12.x 及早于稳定版的 13.x 可直接升级至稳定版 13.0.121（无需逐级过渡）
    if product == AC and cur_k < version_key(AC_LATEST_STABLE):
        result["hops"] = [AC_LATEST_STABLE]
        result["direct_upgrade"] = True
        result["notes"].append(
            f"{current} 可直接升级至稳定版 {AC_LATEST_STABLE}（无需逐级过渡）；"
            "升级前需完成前置检测：配置备份、磁盘空间、接口与 License 状态核对")
        if tgt_k > version_key(AC_LATEST_STABLE):
            result["notes"].append(f"{LATEST[AC]} 为最新版本，稳健场景推荐 {AC_LATEST_STABLE}")
        return result

    chain = _chain_for(product, current)
    path = [v for v in chain if cur_k < version_key(v) <= tgt_k]
    result["hops"] = path

    # AF 跨架构：旧架构（<8.0.69）先走旧链到末端 8.0.48，跨架构需客服评估 + 重装系统盘
    if product == AF and cur_k < version_key(AF_ARCH_SPLIT):
        old_tail = [v for v in AF_CHAIN_OLD if cur_k < version_key(v) <= tgt_k]
        new_part = [v for v in AF_CHAIN_NEW if cur_k < version_key(v) <= tgt_k]
        result["hops"] = old_tail + new_part
        result["cross_arch_migration"] = True
        if old_tail:
            result["notes"].append(
                f"旧架构内先逐级升级至末端 {old_tail[-1]}；此后跨架构升级到新架构（{new_part[0] if new_part else target} 起）"
                "不可直接升级覆盖：需联系深信服客服评估，并按官方方案重装系统盘")
        elif new_part:
            result["notes"].append(
                f"当前已处于旧架构末端 {current}；跨架构升级到新架构（{new_part[0]} 起）"
                "不可直接升级覆盖：需联系深信服客服评估，并按官方方案重装系统盘")
    elif product == AF and cur_k >= version_key(AF_ARCH_SPLIT) and not path:
        result["notes"].append("当前已在新架构链上，按链内相邻版本逐级升级")

    if not result["hops"] and cur_k >= tgt_k:
        result["notes"].append("当前已是最新版本，无需升级")
    return result


def advisories_for(product: str, version: str) -> list[dict]:
    return [a for a in PSIRT_ADVISORIES
            if a["product"] == product and version in a["affected_versions"]]


def releases_between(product: str, current: str, target: str | None = None) -> list[Release]:
    """返回 current 之后（含 target）的已知版本发布说明，按版本升序。

    AF 旧架构版本：跨架构后的新架构版本一并纳入，完整呈现升级价值。
    """
    target = target or LATEST.get(product, "")
    cur_k, tgt_k = version_key(current), version_key(target)
    chain = _chain_for(product, current)
    vers = [v for v in chain if cur_k < version_key(v) <= tgt_k]
    if product == AF and cur_k < version_key(AF_ARCH_SPLIT):
        vers += [v for v in AF_CHAIN_NEW if cur_k < version_key(v) <= tgt_k]
        vers = sorted(set(vers), key=version_key)
    out = []
    for v in vers:
        rel = RELEASES.get(f"{product}:{v}")
        if rel:
            out.append(rel)
        else:
            out.append(Release(v, product, release_date="", notes=[
                {"type": "说明", "title": f"{v} 版本详情未在内置知识库中收录",
                 "detail": "可在技术支持平台『新版本发布信息』页查看（需认证账号）"}]))
    return out


def is_eol(product: str, version: str) -> tuple[bool, str]:
    rel = RELEASES.get(f"{product}:{version}")
    if rel and rel.eol:
        detail = next((n["detail"] for n in rel.known_issues if n["type"] == "EOL"), "")
        return True, detail
    return False, ""
