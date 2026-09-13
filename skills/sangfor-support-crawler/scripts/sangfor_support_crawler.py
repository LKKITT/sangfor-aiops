#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深信服Support社区案例爬虫
通过BBS社区SSO登录，爬取support.sangfor.com.cn的案例信息

功能：
- 社区SSO自动登录
- 案例列表爬取（按产品/分类/关键词/版本筛选）
- 案例详情爬取（完整HTML内容）
- 内容清洗（HTML转纯文本、结构化字段提取）
- 多格式导出（JSON/CSV/Markdown）
"""

import re
import json
import time
import hashlib
import html
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict, field
from datetime import datetime

import requests


@dataclass
class CaseItem:
    """案例数据结构"""
    id: str = ""
    source_id: str = ""
    title: str = ""
    product: str = ""
    product_name: str = ""
    product_version: str = ""
    suite_version: str = ""
    main_module: str = ""
    child_module: str = ""
    category: str = ""
    keywords: List[str] = field(default_factory=list)
    summary: str = ""
    content_html: str = ""
    content_text: str = ""
    content_web: str = ""
    create_time: str = ""
    update_time: str = ""
    type: str = ""
    permission: int = 0
    url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_dict_compact(self) -> dict:
        """精简版字典，去掉大字段（HTML内容等），减少token消耗"""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "title": self.title,
            "product": self.product,
            "product_name": self.product_name,
            "product_version": self.product_version,
            "suite_version": self.suite_version,
            "main_module": self.main_module,
            "child_module": self.child_module,
            "category": self.category,
            "keywords": self.keywords,
            "summary": self.summary,
            "content_text": self.content_text,
            "create_time": self.create_time,
            "update_time": self.update_time,
            "type": self.type,
            "url": self.url,
        }

    def get_brief(self) -> str:
        """生成案例简要信息，用于快速反馈"""
        parts = [f"[{self.product_name}] {self.title}"]
        if self.suite_version:
            parts.append(f"适用版本: {self.suite_version}")
        if self.main_module:
            parts.append(f"模块: {self.main_module}")
        if self.update_time:
            parts.append(f"更新: {self.update_time}")
        parts.append(f"内容: {len(self.content_text)}字")
        return " | ".join(parts)


class SangforSupportCrawler:
    """深信服Support社区案例爬虫"""

    # API端点
    API_BASE = "https://support.sangfor.com.cn/spt/openapi/case/es"
    SUPPORT_URL = "https://support.sangfor.com.cn"
    BBS_URL = "https://bbs.sangfor.com.cn"

    # 请求头
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "X-Requested-With": "xmlhttprequest",
        "Accept": "application/vnd.edusoho.v2+json",
        "Origin": "https://support.sangfor.com.cn",
        "Referer": "https://support.sangfor.com.cn/",
    }

    def __init__(self, bbs_username: str, bbs_password: str,
                 delay: float = 1.0, verbose: bool = False,
                 cache_enabled: bool = True, cache_ttl: int = 3600):
        """
        初始化爬虫

        Args:
            bbs_username: 社区账号（手机号）
            bbs_password: 社区密码
            delay: 请求间隔（秒），默认1.0
            verbose: 是否打印详细日志，默认False（减少token消耗）
            cache_enabled: 是否启用内存缓存，默认True
            cache_ttl: 缓存有效期（秒），默认3600秒（1小时）
        """
        self.bbs_username = bbs_username
        self.bbs_password = bbs_password
        self.delay = delay
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        self.logged_in = False
        self.uid = None
        # 缓存机制
        self.cache_enabled = cache_enabled
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, tuple] = {}  # key -> (timestamp, data)

    def _log(self, msg: str):
        if self.verbose:
            print(f"[爬虫] {msg}")

    def _info(self, msg: str):
        """简洁的信息输出，始终显示"""
        print(msg)

    def _cache_get(self, key: str):
        """从缓存获取数据"""
        if not self.cache_enabled:
            return None
        if key in self._cache:
            timestamp, data = self._cache[key]
            if time.time() - timestamp < self.cache_ttl:
                return data
            else:
                del self._cache[key]
        return None

    def _cache_set(self, key: str, data):
        """写入缓存"""
        if self.cache_enabled:
            self._cache[key] = (time.time(), data)

    def _cache_clear(self):
        """清空缓存"""
        self._cache.clear()

    def _md5(self, text: str) -> str:
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    # ==================== 登录相关 ====================

    def login(self) -> bool:
        """
        社区SSO登录 → 跳转support网站

        Returns:
            bool: 登录是否成功
        """
        try:
            # 第一步：访问社区首页获取formhash
            self._log("访问社区首页获取formhash...")
            resp = self.session.get(self.BBS_URL + "/", timeout=15)
            formhash_match = re.search(r'formhash["\s:=]+([a-f0-9]{8})', resp.text)
            if not formhash_match:
                self._log("获取formhash失败")
                return False
            formhash = formhash_match.group(1)

            # 第二步：社区登录
            self._log(f"社区登录: {self.bbs_username}")
            login_url = f"{self.BBS_URL}/member.php?mod=logging&action=login&loginsubmit=yes&infloat=yes&lssubmit=yes"
            login_data = {
                "formhash": formhash,
                "username": self.bbs_username,
                "password": self._md5(self.bbs_password),
                "quickforward": "yes",
                "handlekey": "ls",
            }
            resp = self.session.post(login_url, data=login_data, timeout=15)

            # 验证登录成功
            resp = self.session.get(self.BBS_URL + "/", timeout=15)
            uid_match = re.search(r'uid=(\d+)', resp.text)
            if uid_match:
                self.uid = uid_match.group(1)
                self._log(f"社区登录成功, uid={self.uid}")
            else:
                self._log("社区登录失败")
                return False

            # 第三步：访问support网站（自动SSO）
            self._log("访问support网站完成SSO...")
            resp = self.session.get(self.SUPPORT_URL + "/", timeout=15)
            if "深信服技术支持" in resp.text:
                self.logged_in = True
                self._log("Support网站SSO登录成功")
                return True
            else:
                self._log("Support网站访问失败")
                return False

        except Exception as e:
            self._log(f"登录异常: {e}")
            return False

    # ==================== 案例列表 ====================

    def get_case_list(self, product_id: str = "13",
                      keyword: str = "",
                      main_module_ids: List[str] = None,
                      child_module_ids: List[str] = None,
                      version_id: str = "",
                      page_num: int = 0,
                      page_size: int = 20) -> Dict[str, Any]:
        """
        获取案例列表

        Args:
            product_id: 产品ID（13=AF, 其他产品需查询）
            keyword: 搜索关键词
            main_module_ids: 主模块ID列表
            child_module_ids: 子模块ID列表
            version_id: 版本ID
            page_num: 页码（从0开始）
            page_size: 每页数量

        Returns:
            dict: {total, total_pages, cases: [CaseItem,...]}
        """
        if not self.logged_in:
            raise RuntimeError("请先调用login()登录")

        url = f"{self.API_BASE}/search"
        data = {
            "productLineId": product_id,
            "keyword": keyword,
            "mainModuleIds": main_module_ids or [],
            "childModuleIds": child_module_ids or [],
            "versionId": version_id,
            "pageNum": page_num,
            "pageSize": page_size,
        }

        try:
            resp = self.session.post(url, json=data, timeout=30)
            result = resp.json()

            if result.get("code") not in (0, 200):
                self._log(f"获取列表失败: {result.get('msg')}")
                return {"total": 0, "total_pages": 0, "cases": []}

            rows = result.get("rows", {})
            total = rows.get("totalElements", 0)
            total_pages = rows.get("totalPages", 0)
            content = rows.get("content", [])

            cases = []
            for item in content:
                case = self._parse_list_item(item)
                cases.append(case)

            self._log(f"获取列表成功: 第{page_num+1}页, 本页{len(cases)}条, 共{total}条")
            return {
                "total": total,
                "total_pages": total_pages,
                "cases": cases,
            }

        except Exception as e:
            self._log(f"获取列表异常: {e}")
            return {"total": 0, "total_pages": 0, "cases": []}

    def _parse_list_item(self, item: dict) -> CaseItem:
        """解析列表项"""
        case = CaseItem()
        case.id = str(item.get("id", ""))
        case.source_id = str(item.get("source_id", ""))
        case.title = item.get("title", "").strip()
        case.product = str(item.get("product", ""))
        case.product_name = item.get("product_name", "") or item.get("productName", "")
        case.product_version = item.get("product_version", "")
        case.main_module = item.get("main_module_names") or ""
        case.child_module = item.get("child_module_names") or ""
        case.category = item.get("cate_name") or ""
        case.keywords = [k.strip() for k in (item.get("keyword") or "").split(",") if k.strip()]
        case.summary = self._clean_html(item.get("content") or "")[:500]
        case.create_time = item.get("create_time") or ""
        case.update_time = item.get("update_time") or ""
        case.type = str(item.get("type", ""))
        case.permission = item.get("permission", 0)
        case.url = f"{self.SUPPORT_URL}/cases/list?product_id={case.product}&category_id={case.source_id}&isOpen=true"
        return case

    # ==================== 案例详情 ====================

    def get_case_detail(self, case_id: str, use_cache: bool = True) -> Optional[CaseItem]:
        """
        获取案例详情

        Args:
            case_id: 案例ID（source_id）
            use_cache: 是否使用缓存，默认True

        Returns:
            CaseItem: 案例详情，失败返回None
        """
        if not self.logged_in:
            raise RuntimeError("请先调用login()登录")

        # 检查缓存
        cache_key = f"detail:{case_id}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached:
                self._log(f"缓存命中: {case_id}")
                return cached

        url = f"{self.API_BASE}/getDetailById/{case_id}"

        try:
            time.sleep(self.delay)
            resp = self.session.get(url, timeout=30)
            result = resp.json()

            if result.get("code") not in (0, 200):
                self._log(f"获取详情失败[{case_id}]: {result.get('msg')}")
                return None

            rows = result.get("rows", {})
            if not rows:
                return None

            case = CaseItem()
            case.id = str(rows.get("id", ""))
            case.source_id = str(rows.get("id", ""))
            case.title = rows.get("name", "").strip()
            case.product = str(rows.get("productId", ""))
            case.product_name = rows.get("productName", "")
            case.suite_version = rows.get("suiteVersion", "")
            case.main_module = rows.get("mainModuleNames", "")
            case.child_module = rows.get("childModuleNames", "")
            case.content_html = rows.get("content", "")
            case.content_web = rows.get("contentWeb", "")
            case.content_text = self._clean_html(rows.get("content", ""))
            case.update_time = rows.get("updateTime", "")
            case.create_time = rows.get("createTime", "")
            case.permission = rows.get("readaccess", 0)
            case.url = f"{self.SUPPORT_URL}/cases/list?product_id={case.product}&category_id={case.source_id}&isOpen=true"

            self._log(f"获取详情成功: {case.title[:50]}")
            # 写入缓存
            if use_cache:
                self._cache_set(cache_key, case)
            return case

        except Exception as e:
            self._log(f"获取详情异常[{case_id}]: {e}")
            return None

    # ==================== 批量爬取 ====================

    def crawl_cases(self, product_id: str = "13",
                    keyword: str = "",
                    max_count: int = 100,
                    fetch_detail: bool = True,
                    page_size: int = 20) -> List[CaseItem]:
        """
        批量爬取案例

        Args:
            product_id: 产品ID
            keyword: 搜索关键词
            max_count: 最大爬取数量
            fetch_detail: 是否爬取详情
            page_size: 每页数量

        Returns:
            List[CaseItem]: 案例列表
        """
        all_cases = []
        page_num = 0
        total = None

        while len(all_cases) < max_count:
            result = self.get_case_list(
                product_id=product_id,
                keyword=keyword,
                page_num=page_num,
                page_size=page_size,
            )

            if total is None:
                total = result["total"]
                self._log(f"总计 {total} 条案例，计划爬取 {min(max_count, total)} 条")

            cases = result["cases"]
            if not cases:
                break

            for case in cases:
                if len(all_cases) >= max_count:
                    break

                if fetch_detail:
                    detail = self.get_case_detail(case.source_id)
                    if detail:
                        # 合并列表和详情数据
                        detail.summary = case.summary
                        detail.keywords = case.keywords
                        all_cases.append(detail)
                    else:
                        all_cases.append(case)
                else:
                    all_cases.append(case)

                self._log(f"进度: {len(all_cases)}/{min(max_count, total)}")

            page_num += 1
            if page_num >= result["total_pages"]:
                break

            time.sleep(self.delay)

        self._log(f"爬取完成，共获取 {len(all_cases)} 条案例")
        return all_cases

    # ==================== 按URL精准爬取 ====================

    @staticmethod
    def parse_support_url(url: str) -> Dict[str, str]:
        """
        解析support案例链接，提取参数

        支持的链接格式：
        - https://support.sangfor.com.cn/cases/list?product_id=13&type=1&category_id=51089&isOpen=true
        - https://support.sangfor.com.cn/cases/list?product_id=13&type=2&category_id=xxx

        Args:
            url: support案例链接

        Returns:
            dict: {product_id, type, category_id, is_open, source_id}
        """
        result = {
            "product_id": "",
            "type": "",
            "category_id": "",
            "is_open": "",
            "source_id": "",
        }

        if "?" not in url:
            return result

        query_string = url.split("?", 1)[1]
        params = {}
        for pair in query_string.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                params[key.strip()] = value.strip()

        result["product_id"] = params.get("product_id", "")
        result["type"] = params.get("type", "")
        result["category_id"] = params.get("category_id", "")
        result["is_open"] = params.get("isOpen", "")
        # category_id就是案例详情的source_id
        result["source_id"] = result["category_id"]

        return result

    def crawl_by_url(self, url: str) -> Optional[CaseItem]:
        """
        按单个support链接精准爬取案例详情

        只爬取该链接对应的案例，不会爬取其他链接，避免影响服务器。

        Args:
            url: support案例链接，如
                 https://support.sangfor.com.cn/cases/list?product_id=13&type=1&category_id=51089&isOpen=true

        Returns:
            CaseItem: 案例详情，失败返回None
        """
        if not self.logged_in:
            raise RuntimeError("请先调用login()登录")

        params = self.parse_support_url(url)
        source_id = params.get("source_id", "")

        if not source_id:
            self._log(f"链接中未找到category_id: {url}")
            return None

        self._log(f"按链接爬取案例: source_id={source_id}")
        case = self.get_case_detail(source_id)

        if case:
            case.url = url
            self._log(f"爬取成功: {case.title[:50]}")

        return case

    def crawl_by_urls(self, urls: List[str], show_progress: bool = True) -> List[CaseItem]:
        """
        按多个support链接批量精准爬取

        只爬取用户指定的链接，不会爬取其他内容。

        Args:
            urls: support案例链接列表
            show_progress: 是否显示进度，默认True

        Returns:
            List[CaseItem]: 案例详情列表
        """
        if not self.logged_in:
            raise RuntimeError("请先调用login()登录")

        all_cases = []
        failed = []
        total = len(urls)
        cache_hits = 0

        for i, url in enumerate(urls, 1):
            # 检查缓存
            params = self.parse_support_url(url)
            source_id = params.get("source_id", "")
            cache_key = f"detail:{source_id}"
            cached = self._cache_get(cache_key) if source_id else None

            if cached:
                cache_hits += 1
                case = cached
                case.url = url
            else:
                case = self.crawl_by_url(url)

            if case:
                all_cases.append(case)
                if show_progress:
                    self._info(f"  [{i}/{total}] ✓ {case.title[:50]}")
            else:
                failed.append(url)
                if show_progress:
                    self._info(f"  [{i}/{total}] ✗ 爬取失败: {url[:60]}")

            # 最后一个不需要延迟（且缓存命中也不需要延迟）
            if i < total and not cached:
                time.sleep(self.delay)

        if show_progress:
            self._info(f"\n完成: 成功{len(all_cases)}/{total}，失败{len(failed)}，缓存命中{cache_hits}")

        return all_cases

    # ==================== 产品/模块信息 ====================

    def get_module_list(self, product_id: str = "13") -> List[dict]:
        """获取产品模块列表"""
        url = f"{self.API_BASE}/getCaseModuleList/{product_id}"
        try:
            resp = self.session.get(url, timeout=15)
            result = resp.json()
            if result.get("code") in (0, 200):
                return result.get("rows", [])
        except Exception as e:
            self._log(f"获取模块列表异常: {e}")
        return []

    def get_version_list(self, product_id: str = "13") -> List[dict]:
        """获取产品版本列表"""
        url = f"{self.API_BASE}/getProductVersionList/{product_id}"
        try:
            resp = self.session.get(url, timeout=15)
            result = resp.json()
            if result.get("code") in (0, 200):
                return result.get("rows", [])
        except Exception as e:
            self._log(f"获取版本列表异常: {e}")
        return []

    # ==================== 内容清洗 ====================

    @staticmethod
    def _clean_html(html_content: str) -> str:
        """HTML转纯文本"""
        if not html_content:
            return ""

        text = html_content

        # 移除script和style
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # 替换块级标签为换行
        text = re.sub(r'<(br|p|div|li|tr|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)

        # 移除所有HTML标签
        text = re.sub(r'<[^>]+>', '', text)

        # HTML实体解码
        text = html.unescape(text)

        # 清理多余空白
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text

    # ==================== 导出 ====================

    @staticmethod
    def export_json(cases: List[CaseItem], filepath: str, compact: bool = True):
        """
        导出为JSON

        Args:
            cases: 案例列表
            filepath: 输出文件路径
            compact: 是否使用精简版（去掉HTML等大字段），默认True
        """
        if compact:
            data = [c.to_dict_compact() for c in cases]
        else:
            data = [c.to_dict() for c in cases]
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"已导出JSON: {filepath} ({len(cases)}条, {'精简' if compact else '完整'})")

    @staticmethod
    def export_csv(cases: List[CaseItem], filepath: str):
        """导出为CSV"""
        import csv
        fields = ["id", "title", "product_name", "product_version", "suite_version",
                  "main_module", "child_module", "category", "keywords",
                  "summary", "update_time", "url"]
        with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            for case in cases:
                row = case.to_dict()
                row["keywords"] = ", ".join(case.keywords)
                writer.writerow(row)
        print(f"已导出CSV: {filepath} ({len(cases)}条)")

    @staticmethod
    def export_markdown(cases: List[CaseItem], filepath: str,
                        include_detail: bool = True):
        """导出为Markdown"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# 深信服Support案例库\n\n")
            f.write(f"> 共 {len(cases)} 条案例，导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("---\n\n")

            # 目录
            f.write("## 目录\n\n")
            for i, case in enumerate(cases, 1):
                f.write(f"{i}. [{case.title}](#case-{i})\n")
            f.write("\n---\n\n")

            # 详情
            for i, case in enumerate(cases, 1):
                f.write(f"## <a id=\"case-{i}\"></a>{i}. {case.title}\n\n")
                f.write(f"- **产品**: {case.product_name}\n")
                if case.suite_version:
                    f.write(f"- **适用版本**: {case.suite_version}\n")
                if case.main_module:
                    f.write(f"- **模块**: {case.main_module}\n")
                if case.keywords:
                    f.write(f"- **关键词**: {', '.join(case.keywords)}\n")
                if case.update_time:
                    f.write(f"- **更新时间**: {case.update_time}\n")
                f.write(f"- **链接**: {case.url}\n")
                f.write("\n")

                if include_detail and case.content_text:
                    f.write("### 案例详情\n\n")
                    f.write(case.content_text)
                    f.write("\n\n")
                elif case.summary:
                    f.write("### 摘要\n\n")
                    f.write(case.summary)
                    f.write("\n\n")

                f.write("---\n\n")

        print(f"已导出Markdown: {filepath} ({len(cases)}条)")


# ==================== 命令行入口 ====================

def main():
    """命令行使用示例"""
    import argparse

    parser = argparse.ArgumentParser(
        description="深信服Support社区案例爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例：
  # 按链接精准爬取（推荐，只爬取指定链接，不影响服务器）
  python3 sangfor_support_crawler.py --username 手机号 --password 密码 \\
      --url "https://support.sangfor.com.cn/cases/list?product_id=13&type=1&category_id=51089&isOpen=true"

  # 按多个链接爬取
  python3 sangfor_support_crawler.py --username 手机号 --password 密码 \\
      --url "链接1" --url "链接2" --url "链接3"

  # 按产品批量爬取（会爬取列表，对服务器压力较大）
  python3 sangfor_support_crawler.py --username 手机号 --password 密码 \\
      --product-id 13 --keyword "API" --max-count 50
        """
    )
    parser.add_argument("--username", required=True, help="社区账号（手机号）")
    parser.add_argument("--password", required=True, help="社区密码")
    parser.add_argument("--url", action="append", default=[],
                        help="support案例链接，可指定多个（推荐方式，只爬取指定链接）")
    parser.add_argument("--url-file", help="包含链接的文本文件，每行一个链接")
    parser.add_argument("--product-id", default="13", help="产品ID（默认13=AF，批量爬取时使用）")
    parser.add_argument("--keyword", default="", help="搜索关键词（批量爬取时使用）")
    parser.add_argument("--max-count", type=int, default=10, help="最大爬取数量（批量爬取时使用）")
    parser.add_argument("--no-detail", action="store_true", help="不爬取详情（批量爬取时使用）")
    parser.add_argument("--output", default="cases", help="输出文件名（不含扩展名）")
    parser.add_argument("--format", choices=["json", "csv", "md", "all"], default="all",
                        help="输出格式")
    parser.add_argument("--delay", type=float, default=1.0, help="请求间隔（秒），建议≥1")
    parser.add_argument("--verbose", action="store_true", help="显示详细日志（默认静默，减少输出）")
    parser.add_argument("--no-cache", action="store_true", help="禁用内存缓存")
    parser.add_argument("--brief", action="store_true", help="只输出简要信息，不导出文件")

    args = parser.parse_args()

    crawler = SangforSupportCrawler(
        bbs_username=args.username,
        bbs_password=args.password,
        delay=args.delay,
        verbose=args.verbose,
        cache_enabled=not args.no_cache,
    )

    print("登录中...", end=" ", flush=True)
    if not crawler.login():
        print("失败")
        return
    print(f"成功 (uid={crawler.uid})")

    # 收集所有URL
    urls = list(args.url) if args.url else []

    # 从文件读取URL
    if args.url_file:
        try:
            with open(args.url_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        urls.append(line)
            print(f"从文件读取 {len(urls)} 个链接")
        except Exception as e:
            print(f"读取URL文件失败: {e}")

    # 按URL爬取（优先，精准爬取）
    if urls:
        print(f"\n=== 按链接精准爬取 {len(urls)} 个案例 ===")
        cases = crawler.crawl_by_urls(urls, show_progress=True)
    else:
        # 按产品批量爬取
        print(f"\n=== 按产品批量爬取（product_id={args.product_id}）===")
        cases = crawler.crawl_cases(
            product_id=args.product_id,
            keyword=args.keyword,
            max_count=args.max_count,
            fetch_detail=not args.no_detail,
        )

    if not cases:
        print("未爬取到案例")
        return

    # 简要模式：只输出摘要，不导出文件
    if args.brief:
        print(f"\n=== 爬取结果摘要（共{len(cases)}条）===")
        for i, case in enumerate(cases, 1):
            print(f"{i}. {case.get_brief()}")
        return

    # 导出
    print(f"\n=== 导出结果 ===")
    if args.format in ("json", "all"):
        SangforSupportCrawler.export_json(cases, f"{args.output}.json", compact=True)
    if args.format in ("csv", "all"):
        SangforSupportCrawler.export_csv(cases, f"{args.output}.csv")
    if args.format in ("md", "all"):
        SangforSupportCrawler.export_markdown(cases, f"{args.output}.md")


if __name__ == "__main__":
    main()
