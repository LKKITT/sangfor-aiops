---
name: sangfor-support-crawler
description: 深信服Support社区案例爬虫。通过BBS社区SSO自动登录，爬取support.sangfor.com.cn的技术案例库，支持按产品/分类/关键词/版本筛选，自动获取案例详情并清洗内容，导出为JSON/CSV/Markdown。当用户需要爬取深信服官方案例、收集故障排查案例、批量下载技术文档、建立本地案例知识库时使用。用户只需提供社区账号密码即可自动完成登录和爬取。
---

# 深信服Support社区案例爬虫

通过BBS社区SSO自动登录，爬取深信服技术支持社区的案例库，支持多格式导出。

## 优化特性

- **默认静默模式**：减少日志输出，降低token消耗
- **内存缓存**：同一会话内重复爬取自动命中缓存，避免重复请求
- **精简导出**：JSON默认导出精简版（去掉HTML等大字段），节省87%空间
- **简要模式**：`--brief`只输出摘要，不导出文件，快速查看结果
- **精准爬取**：按URL爬取只请求指定案例，不影响服务器

## 快速开始

### 方式一：按链接精准爬取（推荐，只爬取指定链接）

用户只需提供support案例链接，爬虫只会爬取该链接对应的案例，不会爬取其他内容，避免影响服务器。

**命令行：**
```bash
python3 scripts/sangfor_support_crawler.py \
  --username "社区手机号" \
  --password "社区密码" \
  --url "https://support.sangfor.com.cn/cases/list?product_id=13&type=1&category_id=51089&isOpen=true" \
  --output case_51089 \
  --format all
```

**多个链接：**
```bash
python3 scripts/sangfor_support_crawler.py \
  --username "社区手机号" \
  --password "社区密码" \
  --url "链接1" \
  --url "链接2" \
  --url "链接3" \
  --output my_cases
```

**从文件读取链接（每行一个）：**
```bash
python3 scripts/sangfor_support_crawler.py \
  --username "社区手机号" \
  --password "社区密码" \
  --url-file urls.txt \
  --output my_cases
```

**Python代码：**
```python
from sangfor_support_crawler import SangforSupportCrawler

crawler = SangforSupportCrawler("手机号", "密码")
crawler.login()

# 单个链接
case = crawler.crawl_by_url("https://support.sangfor.com.cn/cases/list?product_id=13&type=1&category_id=51089&isOpen=true")

# 多个链接
cases = crawler.crawl_by_urls(["链接1", "链接2", "链接3"])

# 查看简要信息
print(case.get_brief())
```

**简要模式（只输出摘要，不导出文件）：**
```bash
python3 scripts/sangfor_support_crawler.py \
  --username "社区手机号" \
  --password "社区密码" \
  --url "案例链接" \
  --brief
```

### 方式二：按产品批量爬取（会爬取列表，对服务器压力较大）

```bash
python3 scripts/sangfor_support_crawler.py \
  --username "社区手机号" \
  --password "社区密码" \
  --product-id 13 \
  --keyword "API" \
  --max-count 50 \
  --output af_api_cases \
  --format all
```

## 核心功能

### 1. 社区SSO自动登录

- 自动访问社区首页获取formhash
- Discuz标准登录（密码MD5加密）
- 自动跳转support网站完成SSO
- 无需手动处理验证码

### 2. 按链接精准爬取（推荐）

- 支持直接输入support案例链接
- 自动解析链接中的product_id、category_id等参数
- 只爬取指定链接对应的案例，不爬取其他内容
- 支持单个链接、多个链接、从文件读取链接
- 对服务器影响最小

### 3. 案例列表批量爬取

API: `POST /spt/openapi/case/es/search`

支持筛选参数：
- `product_id`: 产品ID（13=AF, 其他产品需查询）
- `keyword`: 搜索关键词
- `main_module_ids`: 主模块ID列表
- `child_module_ids`: 子模块ID列表
- `version_id`: 版本ID
- `page_num`: 页码（从0开始）
- `page_size`: 每页数量（最大20）

### 4. 案例详情爬取

API: `GET /spt/openapi/case/es/getDetailById/{id}`

获取完整案例内容，包括：
- 案例标题、产品、适用版本
- 完整HTML内容
- 自动清洗为纯文本
- 更新时间、权限信息

### 4. 内容清洗优化

- HTML标签自动清理
- 脚本/样式移除
- HTML实体解码
- 多余空白清理
- 结构化字段提取

### 5. 多格式导出

| 格式 | 说明 | 适用场景 |
|------|------|---------|
| JSON | 完整结构化数据 | 程序处理、数据库导入 |
| CSV | 表格格式 | Excel分析、数据筛选 |
| Markdown | 可读文档 | 知识库、文档分享 |

## 案例数据结构

```python
@dataclass
class CaseItem:
    id: str              # 案例ID
    source_id: str       # 源ID（用于详情查询）
    title: str           # 标题
    product: str         # 产品ID
    product_name: str    # 产品名称
    product_version: str # 产品版本
    suite_version: str   # 适用版本范围
    main_module: str     # 主模块
    child_module: str    # 子模块
    category: str        # 分类
    keywords: List[str]  # 关键词列表
    summary: str         # 摘要（500字）
    content_html: str    # 完整HTML内容
    content_text: str    # 清洗后纯文本
    create_time: str     # 创建时间
    update_time: str     # 更新时间
    url: str             # 案例链接
```

## 常用产品ID

| 产品ID | 产品名称 |
|--------|---------|
| 13 | 下一代防火墙AF |
| 其他 | 可通过get_module_list()查询 |

## 辅助方法

```python
# 解析support链接参数
params = SangforSupportCrawler.parse_support_url(
    "https://support.sangfor.com.cn/cases/list?product_id=13&type=1&category_id=51089&isOpen=true"
)
# 返回: {product_id, type, category_id, is_open, source_id}

# 按单个链接爬取
case = crawler.crawl_by_url("support案例链接")

# 按多个链接爬取
cases = crawler.crawl_by_urls(["链接1", "链接2", "链接3"])

# 获取产品模块列表
modules = crawler.get_module_list(product_id="13")

# 获取产品版本列表
versions = crawler.get_version_list(product_id="13")

# 单页列表（不批量）
result = crawler.get_case_list(product_id="13", page_num=0, page_size=20)
print(f"总计: {result['total']} 条")
print(f"本页: {len(result['cases'])} 条")

# 单条详情
detail = crawler.get_case_detail(case_id="51089")
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--username` | 社区账号（手机号），必填 | - |
| `--password` | 社区密码，必填 | - |
| `--url` | support案例链接，可多次指定 | - |
| `--url-file` | 包含链接的文本文件，每行一个 | - |
| `--product-id` | 产品ID（批量爬取时使用） | 13 |
| `--keyword` | 搜索关键词（批量爬取时使用） | - |
| `--max-count` | 最大爬取数量（批量爬取时使用） | 10 |
| `--no-detail` | 不爬取详情（批量爬取时使用） | False |
| `--output` | 输出文件名（不含扩展名） | cases |
| `--format` | 输出格式：json/csv/md/all | all |
| `--delay` | 请求间隔（秒），建议≥1 | 1.0 |
| `--verbose` | 显示详细日志 | False |
| `--no-cache` | 禁用内存缓存 | False |
| `--brief` | 只输出简要信息，不导出文件 | False |

## 注意事项

1. **请求间隔**：建议delay≥1秒，避免频繁请求被限制
2. **登录态有效期**：会话cookie有有效期，长时间运行需重新登录
3. **权限控制**：部分案例有权限限制，无权限时返回空内容
4. **产品ID**：不同产品ID不同，13为AF，其他产品需查询
5. **代理环境**：运行前需清除代理环境变量（http_proxy等）

## 依赖

```bash
pip install requests
```

## 详细API文档

完整的API端点、参数说明、返回格式见 [references/api_reference.md](references/api_reference.md)。
