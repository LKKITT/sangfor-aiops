# 深信服售后技术支持 Agent —— 方案设计说明书

> 面向售后技术支持场景的设备配置自动化管理与软件更新建议 Agent
> 版本 3.0 ｜ 2026-09（依据当前代码全量核对重写，修正 v2 版本的编号错乱、过时参数与缺失描述）

***

## 1. 项目背景与目标

深信服售后技术支持场景中，工程师与网络管理员面临三类高频痛点：

1. **配置管理依赖手工操作**：AF/AC 设备配置的备份、检查、变更都要逐台登录控制台手工完成，缺乏自动化与集中视图；配置改错后难以快速回退。
2. **配置隐患难发现**：规则冲突（遮蔽/矛盾）、空策略、过宽权限、高危端口暴露等问题散落在大量策略中，人工巡检成本高、易遗漏。
3. **版本升级决策缺依据**：深信服技术支持平台上的版本发布说明、安全公告（PSIRT）信息分散且部分需认证，工程师难以快速回答"当前版本是否需要升级、升到哪个版本、什么时候升"。

本项目构建一个 **Web 化的售后技术支持 Agent**，以自然语言对话为主要交互方式，提供两大核心能力：

- **功能一：设备配置的自动化备份与智能管理**（备份/可视化/恢复 + 自然语言配置查询与修改 + 配置合理性分析）
- **功能二：深信服技术支持平台软件更新信息获取与升级建议**

并叠加两项增值能力：**官方知识库问答**（诸葛小T）与**个人知识库沉淀**（LLM WIKI），覆盖 AF / AC / SCP 三类设备。

## 2. 关键调研结论（方案设计依据）

方案基于对深信服官方文档与公开资料的调研，以下事实直接决定了技术路线：

| # | 调研结论 | 对方案的影响 |
| - | --- | --- |
| 1 | AF 8.0.x 官方 REST API 覆盖 1235 个操作：登录（`POST /api/v1/namespaces/{ns}/login` 换取 token 放 Cookie）、接口/路由/NAT/应用控制策略/黑白名单/HA/管理员 CRUD、状态中心（cpuusage/memoryusage/diskusage/systemversion 等）、引用关系与完整性校验 | 配置查询/变更/状态监控全部走官方 REST API |
| 2 | **官方 API 不提供配置文件备份/恢复端点**；AF 配置备份走 Web 控制台下载 `.conf`（私有格式），AC 为 `.bcf`（官方明确不可查看） | 备份分两层设计：**结构化配置快照**（API 拉取，可解析/可视化/可恢复）+ **配置文件归档**（私有格式，只存储与 SHA256 校验，不解析；端点不可用自动降级为仅快照备份并记录） |
| 3 | AC 开放接口使用 md5 共享密钥签名 + IP 白名单 + HTTP 9999 端口，API 文档仅随设备发布 | 实现 AcApiClient 适配器（md5 签名认证，覆盖在线用户/网络策略/流控策略/绑定/吞吐量等数据采集，实测 AC 12.0.40） |
| 4 | **版本发布说明页面免认证可抓取**（AF: productDocument category_id=360973；AC: category_id=324129），内容为页面内嵌 JSON，含各版本【新增】/【优化】条目；软件下载列表与部分正文需登录 Cookie；**官网安全中心 PSIRT 公告详情页公开可抓取** | 更新信息采用**多源适配器**：发布说明免认证按版本精确解析（真实条目优先，缺失版本回退内置知识库）+ 软件列表（.env 配置 Cookie 抓取）+ PSIRT（公开）+ 内置知识库（兜底），全部标注来源 |
| 5 | AF 8.0.48 为旧架构末端（旧架构内可直升），新架构自 8.0.69 起；跨架构不可直接升级覆盖：需联系客服评估并按官方方案重装系统盘；AC 12.x/早期 13.x 可直接升级至稳定版 13.0.121（需前置检测）；升级需重启、双机按双机方案；8.0.85 存在 mbuf 占满已知问题（8.0.107 修复） | 升级建议引擎内置版本知识库与升级路线图，输出"是否升级/路径/时机/行动清单"，跨架构行动清单自动分段 |

## 3. 总体架构

```
┌────────────────────────── 前端（Vue3 + Element Plus + ECharts）─────────────────────────┐
│  ① AI 对话（SSE 流式 + 变更确认卡片[可编辑参数]） ② 配置可视化（状态/接口/区域/NAT/ACL/绑定+流量图）│
│  ③ 备份与恢复（时间线 + 快照diff + 备份vs设备diff + 两阶段恢复） ④ 配置体检（评分+风险分级）      │
│  ⑤ 软件更新建议（结论卡+关键变更分类+时机与行动清单） ⑥ 个人知识库（LLM WIKI+图谱）             │
│  ⑦ 对话日志（会话记录分页检索） ⑧ 平台设置/添加设备/编辑设备（弹窗）                          │
└───────────────────────────────────┬─────────────────────────────────────────────────┘
                       REST + SSE   │
┌───────────────────────────────────▼────────────────────── FastAPI 后端 ─────────────────┐
│  Agent 编排层（自研轻量状态机，OpenAI function-calling 协议，核心约 300 行）                  │
│    意图推理 ⇄ 工具执行（最多 8 轮）→ 写操作挂起 → 人工确认[可改参] → 执行 → 审计 → 继续推理     │
│  ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────────────────┐│
│  │ 功能1：配置管理服务    │ │ 功能2：更新信息服务    │ │ 配置合理性分析引擎（确定性规则）   ││
│  │ 快照/文件双层备份      │ │ 官方平台适配器(免认证) │ │ 40 个 check_id：规则冲突/空策略/ ││
│  │ 结构化 diff/两阶段恢复 │ │ Cookie列表+PSIRT公开  │ │ 过宽权限/高危端口/资源/绑定路由   ││
│  │ 恢复前自动安全备份     │ │ 内置版本知识库(兜底)  │ │ 评分分级 + 结构化修复计划         ││
│  └──────────┬───────────┘ └──────────┬───────────┘ └──────────────────────────────────┘│
│  ┌──────────▼────────────────────────▼─────────────────────────────────────────────────┐│
│  │ 设备适配层 DeviceClient 抽象（AF/AC/SCP 三类设备 + 同构模拟器）                        ││
│  │   ├─ AfRestClient：AF 官方 REST API（token Cookie / 1003·1012 自动重登 / 8 项真机兼容）││
│  │   ├─ AcApiClient：AC 开放接口（md5 共享密钥签名 / HTTP 9999 / GET+POST body 认证）    ││
│  │   ├─ ScpApiClient：SCP 云平台（EC2 AK/SK 签名 / 只读 / 查询+监控接口）               ││
│  │   └─ AF 模拟器：与官方 API 同构的 FastAPI 应用（进程内 ASGI + 挂载 /simulator 调试）   ││
│  └────────────────────────────────────────────────────────────────────────────────────┘│
│  ┌───────────────────┐ ┌───────────────────┐ ┌────────────────────────────────────────┐│
│  │ 官方知识库服务      │ │ 个人知识库服务      │ │ 记忆管理（memory_items 长期             ││
│  │ 诸葛小T技能封装     │ │ LLM WIKI 词条沉淀  │ │  + conv_summaries 会话摘要）            ││
│  │ (BBS SSO+WS 问答)  │ │ + 反思总结报告      │ │ 按设备隔离，后台提取，不阻塞 SSE        ││
│  └───────────────────┘ └───────────────────┘ └────────────────────────────────────────┘│
│  SQLite（13 张表：设备/备份/待确认动作/审计/会话/消息/设置/更新缓存/记忆/摘要/知识库×3）        │
│  定时任务：每日 02:00 全设备备份 ｜ 03:30 更新缓存刷新 ｜ 每 3 分钟设备会话 keepalive          │
│  LLM：OpenAI 兼容协议可配置（默认智谱 glm-4-flash）                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 技术选型与理由

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 后端框架 | Python 3.12 + FastAPI | 异步原生（设备 IO + SSE 流式），自动 OpenAPI 文档，生态成熟 |
| LLM 接入 | openai SDK（OpenAI 兼容协议） | 一套代码适配智谱 GLM / DeepSeek / 通义等，比赛指定模型可一键切换（界面平台设置 > .env） |
| Agent 编排 | 自研轻量状态机（LangGraph 同构设计） | 核心是"工具循环 + 人工确认挂起/恢复"，自研实现约 300 行，零框架锁定、全链路状态持久化（SQLite），工具协议为标准 function-calling，可平移至 LangGraph |
| 设备接入 | httpx + 官方 REST API + 内置模拟器 | 模拟器与官方 API 同构（同 URL/响应封套/错误码），适配层同一套代码双后端；进程内 ASGI 传输免端口依赖 |
| 数据库 | SQLite（WAL） | 单文件零运维，满足演示与中小规模部署；备份文件落盘 + SHA256 |
| 前端 | Vue3 + Element Plus + ECharts + markdown-it | 组件丰富开发快，ECharts 承载流量图与知识图谱，markdown 渲染对话；轻量 view-switch 切换（非路由库） |
| 测试 | pytest + pytest-asyncio(auto) + httpx ASGI | 全链路不依赖真实设备与网络 |

### 3.2 目录结构

```
├── backend/
│   ├── app/
│   │   ├── adapters/              # 设备适配层（AF/AC/SCP + 模拟器）
│   │   │   ├── base.py            #   数据模型 + DeviceClient 抽象
│   │   │   ├── af_rest.py         #   AF 官方 REST API 适配器（含 8 项真机兼容层）
│   │   │   ├── ac_rest.py         #   AC 开放接口适配器（md5 共享密钥）
│   │   │   ├── scp_rest.py        #   SCP 云平台适配器（EC2 AK/SK 签名，只读）
│   │   │   ├── factory.py         #   客户端工厂（per-device 登录锁/登录超时/失败负缓存）
│   │   │   └── simulator/         #   AF 模拟器（同构端点 + 种子数据，state.py 独立状态）
│   │   ├── agent/
│   │   │   ├── orchestrator.py    #   编排器（工具循环/技能路由/确认流/SSE/记忆/离线兜底）
│   │   │   ├── tools.py           #   49 个工具（schema + 执行器 + 内部参数模板）
│   │   │   ├── skills.py          #   技能层（8 技能/关键词路由/写权限收窄）
│   │   │   ├── guardrails.py      #   护栏（消息黑名单/只读/升级代执行拦截/审计）
│   │   │   └── prompts.py         #   系统提示词（数据为准/变更必确认/设备上下文/记忆注入）
│   │   ├── services/
│   │   │   ├── config_service.py  #   备份/快照 diff/两阶段恢复
│   │   │   ├── report_generator.py#   自包含离线 HTML 报告
│   │   │   ├── analyzer.py        #   配置体检规则引擎（40 个 check_id）
│   │   │   ├── update_service.py  #   多源更新信息抓取与缓存
│   │   │   ├── upgrade_advisor.py #   升级建议引擎
│   │   │   ├── zhuge_kb_service.py#   官方知识库服务（包装 skills/zhuge-ai-assistant）
│   │   │   ├── personal_kb_service.py # 个人知识库（LLM WIKI 词条 + 反思报告）
│   │   │   ├── app_settings.py    #   运行时可变配置（LLM/BBS 账号，界面 > .env）
│   │   │   └── knowledge/versions.py  # AF/AC/SCP/HCI 版本知识库（调研固化）
│   │   ├── api/                   # devices/backups/chat/knowledge/updates/settings + health
│   │   ├── db.py / config.py / main.py
│   └── tests/                     # 12 个测试文件，112 个用例（pytest.ini: asyncio auto）
├── frontend/                      # Vue3 七视图 + 三个全局弹窗（平台设置/添加设备/编辑设备）
├── skills/zhuge-ai-assistant/     # 诸葛官方知识库技能（SKILL.md + 协议文档 + 客户端脚本）
├── scripts/
│   ├── start_all.bat              # 一键启动（venv → check_deps 补装 → .env → 分离进程 → 开浏览器）
│   ├── launcher.py                # 服务管理 GUI（tkinter：启停/监控/监听配置/恢复默认/退出保护）
│   ├── check_deps.py              # 依赖检查与补装（requirements.txt 唯一事实源，--check/--quiet）
│   └── launcher-config.json       # launcher 持久化配置（backend/frontend 的 host+port）
├── api/                           # 深信服官方 API 文档拆分件（AF 879 页 PDF → markdown 索引+分章）
└── docs/                          # 设计文档 / 演示脚本 / 参赛材料
```

> 注：早期版本的 `launch.bat / launch.ps1 / launcher.bat / start_all.sh / run_simulator.py` 已在提交 44c9dc0 中整合移除，统一为 `start_all.bat`（一键）与 `launcher.py`（GUI 控制台）；模拟器改为进程内 ASGI 运行，并挂载在后端 `/simulator` 路径供设备视角调试。

### 3.3 配置项总表（backend/app/config.py）

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| SF_DATA_DIR | `<项目>/data` | 数据目录（数据库/备份/缓存） |
| LLM_BASE_URL | `https://open.bigmodel.cn/api/paas/v4` | OpenAI 兼容接口地址（界面平台设置优先） |
| LLM_API_KEY | 空 | 大模型 Key；为空进入离线兜底模式 |
| LLM_MODEL | `glm-4-flash` | 模型名 |
| LLM_TEMPERATURE | 0.2 | 对话流式调用温度（技能路由固定 0、记忆提取固定 0.3） |
| READONLY_MODE | false | 全局只读模式（拦截一切写操作） |
| AUTO_BACKUP_HOUR | 2 | 每日全设备自动备份时刻（小时） |
| UPDATE_REFRESH_HOUR | 3（实际调度 03:30） | 更新缓存定时刷新时刻 |
| SANGFOR_SUPPORT_COOKIE | 空 | 技术支持平台 Cookie，**仅 .env 配置**（界面只回显是否已配置） |
| ZHUGE_BBS_USERNAME / PASSWORD | 空 | 诸葛知识库社区账号（界面平台设置 > .env） |
| DEVICE_HTTP_TIMEOUT | 30 | 设备 HTTP 请求超时（秒） |
| DEVICE_LOGIN_TIMEOUT | 8 | 设备登录独立超时（秒），不可达设备快速失败 |
| SIMULATOR_USERNAME / PASSWORD | admin / Sangfor@123 | 内置模拟器登录账号 |

## 4. 功能一：设备配置自动化备份与智能管理

### 4.1 设备适配层与模拟器

**DeviceClient 抽象**统一设备能力：`login/keepalive`、`get_status/interfaces/nat_rules/acl_rules/user_bindings/static_routes/network_objects/services/whiteblacklist/security_zones`、`snapshot_config()`（结构化快照）、`apply_change(ChangeOp)`（变更）、`backup_config_file/restore_config_file`（配置文件，尽力而为）。

**AfRestClient** 按官方 API 实现：token 放 Cookie、失败码 1003/1012 自动重登（带并发锁）、`_start/_length` 分页（最大 200）、统一 `{code,message,data}` 封套解析；并针对**真实设备实测做了兼容层**（详见 §4.1.1）。

**AcApiClient** 按 AC 开放接口规范实现：md5 共享密钥签名（params 排序 + md5）、HTTP 9999 端口、GET/POST 双模式、`_method=GET` 参数注入、空响应/非 JSON 响应/单对象返回兼容。数据采集覆盖：在线用户（`/api/onlineuser`）、网络策略（`/api/sysconf`）、流控策略（`/api/flowctrl`）、IP/MAC 绑定（`/api/ipmac-bindinfo`、`/api/user-bindinfo`）、应用流量排行、用户流量排行、吞吐量。

**ScpApiClient**（第三类设备，只读）：EC2 AK/SK 签名（AWS4-HMAC-SHA256），API 版本前缀按资源选择文档推荐版本，统一 `{code,message,data}` 解包 + next_page_num 分页聚合；六个 AF 语义查询方法返回空并以 capability_gaps 声明，apply_change 一律拒绝（详见 §6.4）。

**AF 模拟器**是保证比赛演示可离线完整跑通的关键：

- 与官方 API **同构**（相同 URL 结构、响应封套、错误码 1002/1003/1404/1409），`AfRestClient` 无差别访问；
- 内置贴近真实运维场景的种子数据：4 个接口（trust/untrust/dmz/预留）、2 条路由、6 条 NAT、9 条访问控制策略、5 条 IP-MAC 绑定、版本 8.0.85——**刻意埋入遮蔽、动作矛盾、any/any、445/3389 暴露、僵尸策略、mbuf 偏高等典型隐患**供体检演示（演示实测体检得分 2/100：高危 4、中危 5、低危 4）；
- 状态中心指标随时间小幅波动，界面"活"起来；支持配置文件下载（模拟 `.conf` 私有格式）与上传恢复（格式校验 + 重启模拟）；
- 两种运行形态：进程内 ASGI（后端默认，免端口）或挂载于后端 `/simulator` 路径（设备视角调试），均可被真实适配器直连联调。

**§4.1.1 真实设备兼容层（已在 AF 8.0.45.380 实测验证）**

| 差异点 | 适配方案 |
| --- | --- |
| 设备 HTTPS 使用旧密码套件（TLS1.2 + AES256-SHA，OpenSSL 默认安全等级握手失败） | permissive SSL 上下文：不校验证书 + SECLEVEL=0 |
| 读取端点需要 `/api/v1` 前缀（登录同），无前缀被 302 到登录页 | 统一 `/api/v1/namespaces/{ns}/...`，模拟器镜像注册同构路由 |
| 列表响应为 `{items:[...]}`（新版文档为 `list`），主键为 `uuid` 而非 `id` | `_rows()` 双形态兼容；字段映射层按 `uuid` 探测分支 |
| 接口/策略/NAT/路由字段为嵌套结构（ipv4.staticIp、src/dst 对象、natType/dnat/snat、action 整数枚举） | 逐类型字段映射：策略 action 实测 **0=拒绝、1=允许**（以 Default Policy 兜底拒绝与放行策略命中行为佐证）；`lastHitTime=1970` 映射为命中 0 |
| 无聚合状态端点（status/summary 返回未找到 API） | 降级为逐项查询 cpuusage/memoryusage/diskusage/uptimes 并做字段防御解析 |
| API 并发会话数限制（超限报"当前在线用户已超过最大并发用户限制"） | **按设备缓存已登录客户端**复用 token（每设备仅 1 会话），调用方共享、应用退出统一登出 |
| 绑定/对象/服务等端点随版本而异 | 可选端点失败自动降级为空列表并记录 capability_gaps，不阻塞快照/体检（AF zones 无独立端点时从接口信息提取） |
| 系统预置服务/对象（uuid 大段为 0）未引用属正常 | 分析引擎跳过系统内置条目的未引用检查，避免误报 |

**接入真实设备**：前端"添加设备"选"真实设备"，填设备地址与 API 账号即可（AC 只填 IP 自动构造 `http://{ip}:9999`；SCP 填平台地址 + AccessKey/SecretKey）；同一套适配代码，无需改动。

### 4.2 自动化备份与快速恢复

**双层备份**（对应调研结论 2）：

1. **结构化配置快照**：经 API 拉取网络对象、自定义服务、接口、静态路由、NAT、ACL、用户绑定全量配置为规范化 JSON——可解析、可视化、可 diff、可恢复，并可一键导出为可读 JSON（支持设备故障后向第三方设备迁移参照与审计存档）；
2. **配置文件归档**：调用设备配置文件下载端点获取 `.conf` 原件归档，SHA256 完整性校验；真实设备若该端点不可用则自动降级为"仅快照备份"并在审计中记录（诚实标注，不假装成功）。

**触发方式**：手动（界面/对话"创建备份"）、定时（APScheduler 每日 `AUTO_BACKUP_HOUR`（默认 02:00）全设备自动备份）、**联动式**（恢复执行前、Agent 变更执行前自动生成 `pre_change` 安全备份）。

**可视化与对比**：备份时间线（类型/版本/是否含文件标签）；任意两份快照的结构化 diff——按配置节分组，新增/删除/修改三色标注到字段级（如 `action: allow → deny`）；打开备份管理页时自动对比最近两份；另支持**"备份 vs 设备当前配置"diff**（`POST .../{bid}/diff-device`），用于核查漂移。

**两阶段恢复**：

```
选择备份 → 生成恢复计划（删除 N 项 / 修改 N 项 / 重建 N 项，字段级明细）
        → 用户确认 → 自动生成安全备份 → 按序回放（先删→再改→后建）
        → 任一步失败立即停止并报告（现场保留，可用安全备份回退）
```

恢复以备份为目标态反推变更计划，只下发必要的最小变更集，避免全量覆盖风险；**回放顺序按依赖安全编排**：先创建/更新网络对象与自定义服务（被引用方），再处理策略/路由，最后删策略、删对象/服务，避免恢复过程中引用悬空。

### 4.3 自然语言配置管理（Agent 工具集与编排）

**49 个工具**覆盖查询（状态/接口/NAT/ACL/绑定/路由/对象/服务/黑白名单/备份/更新/审计/设备列表/官方知识库/SCP 集群·物理机·虚拟机·存储）、分析（体检）、变更（NAT/ACL/绑定/对象/服务/黑白名单的增删改共 18 个规则类写工具、恢复执行、添加设备、知识库沉淀 record_to_kb / ingest_url_to_kb）。工具按设备类型过滤（`get_tools(device_type)`）：通用 24 个 + AF 专属 18 个（AF 可见 42）+ AC 专属 1 个（AC 可见 25）+ SCP 专属 6 个（SCP 可见 30）。规则类写工具通过 `tool.internal` 注入 `_resource/_op` 内部参数模板，编排器在调用时合并（LLM 只需给业务字段）。

**SSE 事件流协议**：`meta → skill_selected*(技能命中提示) → token*(流式文本) → tool_call/tool_result*(工具进度芯片) → confirm_required(确认卡片) → confirm_result → done`（另有 `offline_notice`/`error`），前端实时渲染推理过程与工具调用轨迹。

**对话可靠性设计**：

- **会话按设备归属**：`conversations.device_id` 记录会话设备，`GET /api/chat/last-conversation/{device_id}` 返回该设备最近会话的消息与待确认动作——切换主菜单/设备后对话历史完整恢复（文本+工具轨迹芯片+pending 卡片），并可续接对话；
- **新会话**：一键开启新会话，避免上下文污染与 token 浪费；设备上下文与长期记忆每轮自动注入；欢迎语与快捷提问按 AF/AC/SCP 三套差异化；
- **重复调用合并**：同一提问内相同工具+相同参数的再次调用直接复用已执行结果（读/写两条路径均有，orchestrator.py:437-446、486-495），写工具同参数只生成一次变更计划；**写工具失败后终止同工具重试**（failed_write_tools），防止模型变参无限重试；
- **工具结果瘦身**：查询类工具在返回 JSON 中内嵌 `_llm_summary` 作答指令（Top N 摘要、"不要再次调用本工具"等），配合 TOOL_RESULT_LIMIT=8000 字符截断，控制上下文规模并提升一次答对率；
- **对话终止**：前端「终止」按钮经 AbortController 中断 SSE，后端 `/api/chat/{conv_id}/cancel` 停止 Agent 执行；
- **首答提速**：技能路由兜底 LLM 调用 temperature=0、max_tokens=16、2.5s 独立超时；记忆提取/知识库沉淀后台化；历史消息 SQL LIMIT（24 条）。

**变更确认流（两阶段 + 人工在环）**：

1. LLM 发起写工具调用 → 护栏检查 → `prepare()` 拉取设备当前状态生成**人类可读变更计划**（变更前后对照、风险提示、定向核实结果）；
2. 计划落库为 `pending_action`（状态机：pending → approved/rejected/blocked/failed/executed），对话暂停，前端弹出**确认卡片**；
3. 用户可**在卡片上直接编辑业务参数**后确认——`resume_confirm(edited=...)` 白名单式合并（仅允许覆盖 data 内字段，绝不改资源与操作类型，orchestrator.py:100-108）；挂起期间用户继续对话时，卡片内容作为 system 消息注入（`_get_pending_actions_context`），模型能理解"把卡片里的端口改成 8443"这类修改意图并生成新计划；
4. "确认执行" → 复核护栏 → 下发设备 → 执行结果回填对话 → LLM 继续推理给出总结；"拒绝" → 记录拒绝审计；护栏拦截记 blocked、设备失败记 failed；
5. 全程状态持久化 SQLite，页面刷新/服务重启后确认流不丢失。

**安全护栏（分层防御）**：

- **能力层**：恢复出厂、管理员账号、HA、固件等敏感资源未开放任何 Agent 工具（不存在对应工具，模型无法发起）；
- **用户消息黑名单（先于 LLM 拦截）**：恢复出厂、删除/重置管理员、关闭 HA、批量清空配置四类正则直接拒绝并说明原因；
- **工具级检查**：资源黑名单（admin/account/factory/ha/firmware）、只读模式（全局 `READONLY_MODE` / 设备级 readonly）拦截一切写操作、未知工具直接拒、升级代执行正则拦截（`UPGRADE_EXEC_PATTERN`，升级只建议不代执行）；
- **变更必确认**：所有写操作强制人工在环；
- **高危二次确认**：删除操作、涉及 445/3389/22 等端口的操作、任意地址到 untrust 区域，在确认卡片上以风险提示强化；
- **全程审计**：每个工具调用、每次确认、每次执行均落审计日志（含参数与结果）。

> **已知改进项（诚实声明）**：工具级资源黑名单当前读取 `args["resource"]`，而规则类写工具的内部参数键为 `_resource`（由 internal 模板注入），该层对 18 个规则类工具暂未实际命中；安全兜底依赖前述能力层（敏感资源无工具）与消息黑名单、确认流。修复方向：`check_tool_call` 同时检查 `args.get("_resource")`（一行改动），已列入改进清单。

**离线兜底**：未配置 LLM Key 时启用规则意图匹配的兜底助手，支持 **10 类固定意图**（状态/接口/网络对象/自定义服务/NAT/ACL/绑定/体检/备份列表/升级建议），直接调工具并格式化 Markdown 输出，并明确提示当前处于离线兜底模式——保证评审环境零 Key 也能演示核心闭环。

### 4.4 AC 设备管理

本版本已完整实现 AC（上网行为管理）设备支持，与 AF 防火墙并行管理。AC 适配器要点：md5 共享密钥签名（params 按 key 排序串联 + md5 生成 sign，无账号密码登录）、HTTP 9999 端口（非 HTTPS）、GET/POST 双模式。**AC 兼容层（基于 AC 12.0.40 实测）**：

| 差异点 | 适配方案 |
| --- | --- |
| 响应缺少 `code` 字段（部分 POST 接口） | `_parse()` 兼容 `code` 缺失，`code is not None and code != 0` 再报错 |
| 参数 `noauth`/`limitlogon` 为布尔值，但工具 schema 定义为字符串 | `_to_bool()` 兼容 `"true"`/`"false"` 字符串与布尔值 |
| 空响应 HTTP 200 但 body 为空 | `_parse()` 检查空 body 返回 `{}` 不报错 |
| 非 JSON 响应（部分接口返回纯文本） | `_parse()` 捕获 `ValueError` 返回 `{}` |
| 单对象返回非数组 | `_rows()` 检查 `isinstance(data, dict)` 转为 `[data]` |

前端为 AC 提供专属 Tab（设备状态、上网策略、流控策略、在线用户、应用/用户流量排行、吞吐量、用户绑定）；系统提示词动态注入 AC 适配规则（绑定查询必须分步调用 `get_user_bindings` + `get_ipmac_bindings` 并分开展示；不对 AC 询问 NAT 策略）。

### 4.5 设备记忆管理（跨会话持久化）

双层记忆，均按设备隔离（SQLite `memory_items` + `conv_summaries` 两张表）：

| 层 | 存储 | 注入 | 提取 |
| --- | --- | --- | --- |
| 长期记忆 | `memory_items`（device_id + category 四类：fact 事实 / preference 用户偏好 / action_history 操作记录 / device_context 设备上下文） | 每轮进入 LLM 循环时注入**最近 15 条**（memory_injection_message） | 对话以纯文本回答结束后**后台任务**提取（asyncio.create_task，不阻塞 SSE、失败仅记日志）：触发条件为全会话消息数 **≥6**（MEMORY_EXTRACT_INTERVAL）；取最近 10 条用户 + 10 条助手文本（截断 3000 字符）；**两次 LLM 调用**（会话摘要 max_tokens=300、事实提取 max_tokens=500，temperature=0.3）；事实按行解析分类，与最近 30 条记忆做前 30 字符去重，单条截断 300 字符，设备记忆**保留上限 50 条**（超出删旧） |
| 短期记忆 | `conv_summaries`（conv_id 唯一，滚动更新） | 每轮注入"当前会话摘要" | 同上第一次调用生成 |

另有两类上下文每轮注入：**设备上下文**（名称/类型/模式/运行概况/只读提示/设备类型能力提示）与**待确认卡片上下文**（挂起的变更计划内容，供模型理解用户的改参指令）。历史消息拉取上限 24 条（HISTORY_LIMIT）。

设计权衡：提取复用主模型（glm-4-flash 成本极低）而非独立小模型；≥6 条消息门槛过滤寒暄；前缀去重简单有效，语义级去重（embedding）列入改进方向。

### 4.6 平台设置（运行时可视化配置）

| 配置项 | 功能 | 优先级 |
| --- | --- | --- |
| LLM API 地址 / Key / 模型 | 任意 OpenAI 兼容接口（智谱/DeepSeek/通义） | 界面配置 > .env |
| 知识库社区账号（BBS） | 诸葛知识库 ZHUGE_BBS_USERNAME/PASSWORD | 界面配置 > .env |
| 深信服平台 Cookie | 技术支持平台认证（软件下载列表抓取） | **仅 .env 配置**（界面只回显是否已配置） |

实现位于 `services/app_settings.py`（运行时配置读取）+ `api/settings.py`（REST 端点）+ `App.vue`（设置弹窗），保存即生效，LLM 配置变更自动重建客户端（orchestrator.py:52-61）。

### 4.7 对话记录与审计

- **会话记录**：全部消息（user/assistant/tool/system）落 `conversations`/`messages` 表；前端「对话日志」页支持按关键词/设备/时间分页检索（`/api/chat/conversations/detail`，单 SQL 聚合）与会话详情回看；
- **审计日志**：所有工具调用、变更确认（含拒绝）、执行结果、离线兜底调用均记录到 `audit_logs`（含参数与结果）；后端另提供 `/api/chat/audit` 查询端点。

### 4.8 备份报告生成

一键生成自包含 HTML 报告（内嵌 CSS，离线可看），内容三板块：配置可视化（快照 + 实时状态）、配置体检（评分与分级）、软件更新建议（路线图/变更分类/时机/行动清单）；SCP 设备输出专属章节（版本/集群资源/物理机/虚拟机/网口功能 IP/桥接端口组/存储 + 体检，数据标注备份时点）。AF 旧架构设备在报告中醒目显示**跨架构迁移警告**（先升至 8.0.48 → 客服评估 + 重装系统盘 → 新架构逐级），行动清单同步三段拆分。

### 4.9 版本信息获取（多级降级方案）

AF 设备版本信息获取按以下优先级降级（af_rest.py:158-289）：

```
优先级 1: systemversion 端点
优先级 2: systemversion?filter=ALL 重试
优先级 3: softwareversion 端点
优先级 4: 登录响应中提取（login 时缓存 _login_data）
优先级 5: Web 页面提取（login.php 优先于 /）：
          9 种正则模式（data-version / 元素文本 / afVersion / js 变量 /
          appVersion / SVG 路径 / "AF X.X.X" 文本 / 关键词后纯数字 / 通用 X.X.X）
          + XOR 解码（支持无 var 关键字声明的解码函数）
```

### 4.10 接口状态检测

AF 接口状态 = **配置层 + 实时层合并**（配置端点无运行链路字段，实测结论）：`GET /interfacestatus/{接口名}` 返回 `connectStatus` 与 `speed.recv/send`（kbps），按网口名逐个查询（并发限 5、单口失败静默），合并进 `/interfaces` 响应；实时查询不可用时配置层兜底（`shutdown=false` 显示"已启用"，不再误判为 down），前端徽标三态（运行中/已启用/未运行）；多 IP 配置解析 `staticIp` 数组。AC 资源指标修正：吞吐量统一 ×8 换算 bps 并规范化 up/down_throughput；带宽使用率为百分比直出。

### 4.11 配置可视化缓存机制

可视化数据在页面挂载时加载一次，设备切换时重新加载（无定时自动刷新），顶部工具栏提示"配置数据缓存于页面，点击刷新重新获取"，手动刷新按钮触发重新获取——避免高频轮询设备。

### 4.12 设备连接可用性保护

- **per-device 锁**：登录锁按 device_id 随离，坏设备只阻塞自身；
- **登录独立超时**：`DEVICE_LOGIN_TIMEOUT`（默认 8s），不可达设备快速失败；
- **失败负缓存**：登录失败后 10s 冷却期内直接返回友好错误，不反复冲击设备；
- **端点兜底**：设备类 API 统一 `asyncio.wait_for` 超时（HTTP 504 中文提示）；前端请求 45s 超时（LLM 长调用 300s）；登录失败的新建 client 及时 `aclose()` 防连接泄漏；
- **会话保活**：每 3 分钟对在线设备 keepalive。

### 4.13 AC 设备 IP 输入优化

AC 添加设备只需输入纯 IP，提交时自动构造 `http://{ip}:9999`；编辑设备时从 base_url 反解 IP 回显；测试连接与添加均使用构造后的完整 URL。

### 4.14 配置合理性分析引擎

确定性规则引擎（不依赖 LLM，结论稳定可解释），从快照 + 实时状态计算风险。**40 个 check_id** 分七类：

| 类别 | 检查项（check_id 节选） | 级别 |
| --- | --- | --- |
| 规则冲突 | ACL_CONFLICT（deny 被 allow 覆盖）/ ACL_SHADOW / ACL_DUP / NAT_SHADOW / NAT_BNAT | high/medium/low |
| 空策略 | ACL_NO_LOG / ACL_DISABLED / ACL_ZOMBIE / NAT_ZOMBIE | medium/low |
| 过宽权限 | ACL_ANY_ANY / ACL_DANGEROUS_PORT（22/23/3389/445/1433/3306/21）/ ACL_ALL_ZONE_ALLOW / NAT_BROAD / NAT_MGMT_EXPOSE | high/medium |
| 资源异常 | RES_CPU/RES_MEM/RES_DISK/RES_MBUF 双阈值（mbuf 显式关联 AF 8.0.85 已知问题）/ RES_SESSION | high/medium |
| 绑定与路由 | BIND_DUP_IP / BIND_DUP_MAC / BIND_NO_MAC / ROUTE_NO_DEFAULT | high/low |
| 对象与服务 | OBJ_UNREFERENCED / OBJ_BROAD / SVC_UNREFERENCED / SVC_DANGEROUS_PORT | medium/low |
| SCP 专属 | SCP_CLUSTER_CPU/MEM/STORAGE/STATUS、SCP_HOST_CPU/MEM/STATUS/ALARM、SCP_HOST_CPU/MEM_OVERALLOC（超分 >5:1 / >1.5:1）、SCP_VM_STATUS/ALARM/CPU_HOT/MEM_HOT/ZOMBIE、SCP_STORAGE(_STATUS) | high/medium/low |

匹配域判定支持 CIDR 包含关系（ipaddress 模块）、zone/服务 token 集合包含、`IP:PORT` 端口提取。输出：体检得分（100 − 高危15/中危6/低危2 加权扣分）+ 分级报告（说明/建议/涉及规则）；可修复项携带**结构化 fix plan**，经"让 AI 一键修复"进入对话确认流执行（执行前自动备份）。SCP 体检建议动态携带实际值与剩余余量，引用业界容量标准（持续 ≤80%、预留 20%~30%、关键业务 N+1）。

## 5. 功能二：软件更新信息获取与升级建议

### 5.1 多源更新信息抓取（带缓存与来源标注）

| 来源 | 可达性 | 实现 |
| --- | --- | --- |
| 官方平台 support.sangfor.com.cn | 发布说明免认证可抓（页面内嵌 JSON，按版本段落解析【新增】/【优化】条目）；软件下载列表需登录 Cookie | 解析器三层适配：内嵌 JSON 提取 → 版本段落切分 → 【标记】条目 + 散文段特性短句；Cookie 经 .env 配置；未认证时明确降级并说明原因 |
| 官网安全中心 PSIRT 公告 | 详情页公开 | 抓取公告详情解析（标题/CVSS/影响版本/修复方案），失败降级内置快照 |
| 内置版本知识库 | 离线可用 | 调研固化的 AF/AC/SCP/HCI 版本发布说明、已知问题、EOL 状态、PSIRT 通告（真实编号如 SF-PSIRT-20220472），标注 `builtin_snapshot` |

抓取结果统一落 SQLite 缓存（`update_cache`：source + fetched_at），发布说明按关键词规则**确定性归类**为：新增功能 / 安全修复 / 已知问题修复 / 优化。LLM 只做基于结构化数据的解说，不做事实生成（**防幻觉**）。定时任务每日 03:30 刷新四条产品线缓存（`UPDATE_REFRESH_HOUR`），并提供手动强刷端点（`POST /api/updates/refresh`，绕过缓存新鲜期）。

### 5.2 升级建议引擎（确定性核心 + LLM 解说）

```
读设备当前版本 → 版本归一化 → 匹配升级路线图
   ├─ AF 旧架构（<8.0.69）：旧链内逐级升级至末端 8.0.48；跨架构到新架构需客服评估 + 重装系统盘
   ├─ AF 新架构：链内相邻版本逐级升级（如 8.0.85→8.0.106→8.0.107）
   └─ AC：11.0+ 直升稳定版 13.0.121 线（需前置检测）；SCP/HCI：直达 + 前置检测提示
风险判定：
   ├─ 命中 PSIRT 受影响版本 → high（附公告编号/CVSS/修复方案）
   ├─ EOL 版本 → high
   ├─ 已知问题触发（如 8.0.85 + mbuf 占用>60%）→ medium
   └─ 仅有新版本 → low / 已是最新 → info
输出：结论标签（强烈建议/建议窗口升级/常规迭代/维持现状）
     + 升级路径 + 关键变更点四类清单 + 升级时机（业务中断/低峰窗口/双机方案/前置条件）
     + 行动清单（可由 Agent 执行的步骤打标：一键备份等；跨架构自动三段拆分）
```

"关键变更点"以官方『新版本发布信息』页抓取到的真实版本集合为骨架（低于关注下限 AF 8.0.7 / AC 13.0.62 不展示），官方抓取缺失时回退内置版本知识库。

## 6. 技能化（Skill）与知识库

### 6.1 工具技能化

49 个工具按运维场景组织为 **8 个技能**，编排器按"用户消息 → 技能"路由后按需注入：

| 技能 | 场景 | 解锁的写工具 |
| --- | --- | --- |
| 状态巡检 | 设备状态/健康/接口流量/区域 | 无（只读） |
| 配置查询 | NAT/ACL/对象/服务/路由/绑定等明细 | 无（只读） |
| 配置体检与修复 | 体检报告与风险修复 | 12 个 update/delete 类修复工具 |
| 备份与恢复 | 备份/diff/恢复 | restore_backup、execute_restore |
| 策略变更 | 各类策略增删改 | 全部 18 个 create/update/delete 工具 |
| 设备接入 | 设备列表/对话添加设备 | add_device |
| 软件升级建议 | 更新信息/升级路径 | 无（只读，只建议不代执行） |
| 知识库检索 | 官方知识库问答（**条件技能**，勾选时注入，不参与关键词路由） | search_official_knowledge（只读） |

设计原则：

- **只读工具永远全量可用**，技能只收窄写工具作用域——跨技能查询不被卡死，写权限只收不扩；
- **知识沉淀常开**：record_to_kb 与 ingest_url_to_kb 在任意技能下保持解锁（skills.py:154）——沉淀是用户明确动作，不应被技能收窄卡住；
- **关键词路由零延迟、离线可用**（`skills.select_skill` 子串匹配），未命中时 LLM 按技能目录兜底选择（temperature=0、max_tokens=16、2.5s 超时的一次非流式小调用，不占 8 轮上限），仍无命中回退全量模式；
- 技能通过工具名反向引用，`tools.py` 不感知技能存在；技能模式下未加载的工具被调用时如实回填错误提示，保证收窄生效。

### 6.2 官方知识库对话（查询知识库）

```
用户勾选「查询知识库」 → /api/chat(use_knowledge=true)
  → 编排器注入 kb-search 技能指引 + search_official_knowledge 工具 schema
  → LLM 调用 search_official_knowledge(question)
  → zhuge_kb_service：BBS SSO 登录 → WebSocket 问答（按设备产品线 AF/AC 定向）
  → 回答 + 官方引用来源 → LLM 归纳作答（末尾「官方参考」以 markdown 链接原样呈现）
```

- 技能脚本复用：`skills/zhuge-ai-assistant/scripts/zhuge_ai_client.py`（806 行协议实现）按路径 importlib 加载，不复制代码；
- 凭据链：『平台设置』界面（DB）→ `.env`（ZHUGE_BBS_USERNAME/PASSWORD）→ 技能内置账号兜底；
- 每条产品线一个登录会话并缓存复用（JWT 约 7 天有效），同步客户端经线程池调用；
- 统一降级：服务不可用/超时/未命中时返回明确原因，LLM 如实告知用户、不编造；设备实时数据必须走设备工具，不允许用知识库内容替代。

### 6.3 个人知识库（LLM WIKI）

三条沉淀路径（均走确认卡片）：

1. **对话沉淀**：勾选过知识库的对话，结束后自动沉淀——增量提取仅取本轮勾选后的消息（`since_id` 水位），不引入之前的会话内容；
2. **对话要求沉淀**：用户说「把这个问题记录/沉淀到知识库」→ `record_to_kb` 工具登记（不依赖知识库勾选）；
3. **链接沉淀**：用户给出 URL 并要求录入 → `ingest_url_to_kb` 抓取页面内容（内嵌数据优先、全文剥标签兜底），LLM 提炼为词条，引用附带来源链接。

```
沉淀触发 → LLM WIKI 提示词提炼结构化词条 JSON（主题/分类/摘要/要点/步骤/官方引用/标签）
  → 按主题去重：已有同主题词条刷新内容（保持知识最新），否则新增 → 落库 kb_entries
可视化（KnowledgeView + ECharts）
  → 知识图谱（词条节点按引用数定大小、归一化标签连边）+ 分类分布 + 沉淀时间线
反思与总结 → 自定义日期范围生成「阶段总结/知识盲区/待验证结论/学习建议」报告（kb_reflections）
```

待沉淀对话支持勾选沉淀/忽略（忽略项落 `kb_dismissed` 移出队列）。

### 6.4 SCP 云计算平台接入（只读）

第三种设备类型 `scp`（对照 vCenter 定位，纳管 HCI 节点≈ESXi）：

- **认证**：EC2 AK/SK 签名（AWS4-HMAC-SHA256，region=cn-south-1、service=open-api），每请求实时签名；签名规范化按官方 JS 示例复刻（SignedHeaders=path;x-amz-date——非标准 AWS 写法，联调 401 时优先核对）。凭据映射：devices 表 username=AccessKey、password=SecretKey、base_url=https://{平台地址}。
- **适配器**（`adapters/scp_rest.py`）：API 版本前缀按资源选择文档推荐版本（clusters=20210725 / servers=20220725 / hosts=20190725 / azs·storages=20200725 / platform·overview·host-interfaces=20180725 / classic-bvswitches=20190725；集群/物理机另有 20180725 双版本降级）；统一 {code,message,data} 解包 + next_page_num 分页聚合；六个 AF 语义查询方法返回空（capability_gaps 声明），apply_change 一律拒绝（只读边界）。
- **工具**（device_type='scp'，6 个只读）：get_scp_clusters / get_scp_hosts / get_scp_host_interfaces / get_scp_vms / get_scp_vm_detail / get_scp_storages；get_status 由 /platform + /overview 折算；get_interfaces 聚合全部物理机网口（zone=功能口类型）。
- **配置备份**（结构化快照）：scp_platform / scp_clusters / scp_hosts / scp_host_interfaces / scp_vms / scp_storages / scp_bvswitches；保留 AF 语义空节，diff/预览机制通用。
- **数据真实性**（实测校准）：`ratio` 字段部分版本返回 0-1 比例，归一化层统一修正为百分比并写回原字段；**虚拟机使用率真源 = 监控接口**（servers 列表的 cpu_status/memory_status 恒 0，`GET /metrics/{id}?object_type=server&metric_names=cpu.util,memory.util` 返回真实百分比；VM 列表批量补取上限 30 台、并发 5、单台失败静默；未部署采集 agent 的 VM 如实显示空）；OS 型号码解码（`l2664` = Linux kernel 2.6.64，os_name 优先）；物理机存储使用率开放接口不提供（仅 total_mb），如实显示总量。
- **边界**：第一阶段不支持 SCP 模拟器；全部增删改/生命周期操作（开机/迁移/快照回滚等 POST 类）排除在外。

## 7. 典型交互流程

**变更场景**（自然语言修改配置）：

```
用户："把 445 端口对公网暴露的策略停用"
→ 技能路由命中「策略变更」（关键词）→ LLM 调用 get_acl_rules 定位 acl-004
→ 发起 update_acl_rule(write) → 护栏检查 → prepare 生成变更计划卡片
   [停用访问控制策略「放行外部访问服务器445」 acl-004: enabled=true→false
    ⚠ 删除/停用操作影响该策略覆盖的全部流量]
→ 用户可直接改卡片参数或确认/拒绝 → 复核护栏 → 自动 pre_change 备份 → 下发设备
→ 回执回填对话 → LLM 总结并建议体检复核（全程工具芯片展示轨迹，审计日志记录每一步）
```

**升级咨询场景**：

```
用户："有新版本可以升级吗？"
→ get_upgrade_advice → 引擎判定（8.0.85 + mbuf 76% → 已知问题触发，medium）
→ 回答：建议近期维护窗口升级；路径 8.0.106→8.0.107；关键变更（新增4/已知问题修复4/优化2）；
       升级会重启中断业务→建议低峰窗口；双机按双机方案；行动清单第 1 步"立即备份"可由 Agent 代办
```

**知识沉淀场景**：

```
用户勾选「查询知识库」后提问 → search_official_knowledge（SSO+WS）→ 回答带官方引用
→ 对话结束后台自动提炼词条入库（仅取本轮勾选后的消息）→ ECharts 知识图谱可查
```

## 8. 附录：API 端点清单（共 69 个）

| 分组 | 端点 |
| --- | --- |
| 健康（1） | GET /api/health |
| 设备（33） | GET/POST /api/devices；PATCH/DELETE /api/devices/{id}；POST /api/devices/test-connection、POST /{id}/test；GET /{id}/status、/interfaces、/zones、/nat、/acl、/bindings、/ipmac_bindings、/objects、/services、/routes、/snapshot；POST /{id}/checkup、GET /{id}/checkup/last；AC 采集 6 个（/ac/online-users、/net-policies、/flux-policies、/throughput、/app-rank、/user-rank）；SCP 采集 8 个（/scp/platform、/clusters、/hosts、/hosts/{id}/interfaces、/vms、/vms/{id}、/storages、/bvswitches） |
| 备份（11） | POST/GET /api/devices/{id}/backups；GET .../{bid}/snapshot、/snapshot/export、/file、/report；DELETE .../{bid}；GET /api/backups/diff；POST .../{bid}/diff-device、/restore/preview、/restore/apply |
| 对话（8） | POST /api/chat；POST /{conv_id}/cancel；POST /confirm（支持 edited 参数）；GET /conversations、/conversations/detail、/conversations/{conv_id}、/last-conversation/{device_id}、/audit |
| 更新（4） | GET /api/devices/{id}/updates、GET /api/software-list、GET /api/devices/{id}/upgrade-advice、POST /api/updates/refresh |
| 知识库（10） | GET /api/kb/entries、/entries/{id}、DELETE /entries/{id}；GET /pending、DELETE /pending/{conv_id}；POST /process、/reflection；GET /reflections、DELETE /reflections/{id}；GET /stats |
| 设置（2） | GET/POST /api/settings |

## 9. 测试验证方式

**112 个用例 / 12 个测试文件全部通过**（pytest-asyncio auto 模式，全链路不依赖真实设备与网络）：

| 测试文件 | 用例数 | 覆盖内容 |
| --- | --- | --- |
| test_analyzer.py | 11 | 体检规则引擎（冲突/遮蔽/重复/any-any/高危端口/僵尸/NAT/资源阈值/绑定/路由/评分，含误报反例） |
| test_upgrade_advisor.py | 15 | 升级建议 + 版本知识库（版本归一化/排序/路径/跨架构/PSIRT/EOL/变更区间） |
| test_personal_kb.py | 18 | 个人知识库（CRUD/去重/待沉淀/统计/WIKI 解析/降级） |
| test_skills.py | 13 | 技能层（路由/设备类型过滤/写权限收窄/LLM 选择解析） |
| test_zhuge_kb.py | 10 | 诸葛知识库服务（WS 协议 mock、降级） |
| test_scp_adapter.py | 11 | SCP 适配器（MockTransport 全端点） |
| test_simulator_adapter.py | 8 | 模拟器 + AF REST 适配器（登录/错误码/快照/NAT 回环/配置文件） |
| test_agent_guardrails.py | 7 | 护栏黑名单/只读/审计 + Agent 离线兜底 |
| test_experience.py | 7 | 工厂可用性保护（锁/超时/负缓存）、会话按设备恢复、消息 LIMIT |
| test_objects_settings.py | 7 | 对象/服务分析、设置存储、快照导出迁移语义 |
| test_confirm_flow.py | 3 | 确认流端到端（含拒绝路径） |
| test_config_service.py | 2 | 备份/diff/恢复闭环回归（终态与基线零差异）、双层备份与 SHA256 |

另有端到端视觉验收：浏览器实测前端七页（对话流式/配置可视化/备份对比/体检报告/更新建议/个人知识库/对话日志），确认卡片与数据渲染正确。

运行方式：`cd backend && ../.venv/Scripts/python -m pytest -q`。

## 10. 边界与合规说明（诚实声明）

1. 配置文件 `.conf/.bcf` 为深信服私有格式，本方案**只做归档与完整性校验，不解析内容**；结构化管理基于官方 REST API。
2. 官方 API 未开放配置文件备份端点，真实设备的文件级备份依赖 Web 控制台私有端点，不可用时自动降级为快照备份并明确告知。
3. support.sangfor.com.cn 认证内容不破解、不绕过；无 Cookie 时使用公开 PSIRT 与内置知识库（标注 `builtin_snapshot`，数据为公开资料调研整理）。
4. 演示数据运行在本地模拟器，接入生产设备前应使用只读模式评估，并遵循深信服官方升级/变更流程。
5. 升级固件等高危操作 Agent 只建议、不代执行；SCP 全程只读（生命周期操作排除）；AC 暂不支持写操作。
6. **已知改进项**（如实公开）：①工具级资源黑名单的参数键与规则类工具内部键不一致（`resource` vs `_resource`），该层对规则类写工具暂未命中，依赖能力层与消息黑名单、确认流兜底（见 §4.3）；②记忆去重为前缀匹配，语义级去重待引入 embedding；③记忆/摘要提取复用主模型，未拆分小模型；④工具注册模块内有一处被遮蔽的 `rule_tool` 死代码（tools.py 模块级定义被 `_register_write_tools` 内层同名函数覆盖），可在下次清理。

## 11. 后续规划

- 适配零信任设备、超融合 CLI 运维管理
- 多设备批量体检与巡检报告导出（PDF/邮件）、定时巡检与异常告警推送
- 基于 RAG 的深信服知识库问答（案例库/配置指南），与现有双知识库形成混合检索；记忆语义去重
- 对话式工单生成：体检报告一键转化为售后工单
- AC 配置变更支持：扩展 AC 写操作（策略/流控/绑定修改）
- 护栏资源键位统一修复（§4.3 已知改进项）、工具注册死代码清理
