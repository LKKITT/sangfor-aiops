# 更新记录（Update Log）

本项目所有代码提交的更新记录，按时间倒序排列。

## 2026-10-08

### feat: 控制台 AI 辅助（命令速查/回显分析）+ 多租户客户切换

**方向2：网络设备控制台 AI 辅助**
- 命令速查（L1，本地零延迟）：12 厂家命令字典 + 关键字过滤，点击仅插入终端、回车执行由操作者确认
- AI 分析（L2）：最近 150 行回显（口令/密钥行自动打码 + 截断）→ LLM 输出状态研判/可能原因/建议命令（实测真实 H3C 出 4 条建议）；解析三层兜底（JSON strict=False + 围栏剥离 + 伪 JSON 宽松字段提取）
- 安全边界：建议命令只插入终端不自动执行；分析调用落审计（netdev.console.ai）
- 新增 6 个单测（脱敏/参数校验/JSON 解析/审计）

**方向3：多租户客户切换（一期+二期+三期）**
- 数据层：10 张隔离表补 tenant_id（存量归 default，init_db 平滑迁移）；知识库/审计/设置全局共享
- 管道：X-Tenant-Id 请求头 → contextvar → db 层统一注入；设备/网络设备按 id 跨租户访问视为不存在（隔离边界）；企微渠道后台按绑定记录恢复租户上下文；定时备份跨租户枚举
- 前端：左上角客户切换器（可新建客户，切换整页刷新）；目标设备面板保留；配置/体检/备份/更新四页内置设备选择器；对话日志共享视图（跨客户）+ 按客户筛选 + 客户列
- 新增 4 个租户隔离测试 + 6 个控制台 AI 测试；全量 297/297 通过；端到端实测：customer-hz 租户设备隔离、控制台 AI 真实 H3C 分析成功

## 2026-10-08

### fix: 确认执行后的收尾容错——登记类错误不得把成功执行误报为"服务异常"
- 场景：绑定确认卡片执行成功后，收尾步骤（审计等）再出任何意外都会以原始 Python 错误抛出，用户看到"服务异常：cannot access local variable…"，误以为变更失败（实际已下发成功）
- 修复：`resume_confirm` 执行成功后的收尾（缓存失效/状态落库/审计/沉淀/结果回填）整体容错——收尾失败时在结果中附「变更已执行，但结果登记出现内部错误」备注并照常返回确认成功，不再中断或误报
- 新增收尾异常回归测试（审计抛错仍返回确认成功且状态为已执行），287/287 通过

## 2026-10-08

### fix: 回答文本整段重复 + 批量写确认后 write_duration_ms 未定义崩溃
- 文本重复根因：`_run_llm_loop` 中增量 token 已流式下发，有工具调用的轮次结束后又把整段 final_text 作为一条 token 事件重发——前端与企微渠道均按事件累加文本，每轮回答整体出现两遍。移除该重发（Web 与企微同时修复）
- 确认后崩溃根因：`resume_confirm` 执行路径中 `write_duration_ms` 仅在单台分支赋值，批量分支（本次绑定确认带 devices 参数走批量）未赋值，执行成功后的审计步骤触发 UnboundLocalError——执行本身已成功（绑定已下发并实查确认存在），报错属误报
- 修复：批量分支计时并初始化默认值 0.0；286/286 测试通过

## 2026-10-08

### fix: AC 快照容错（昨日 search 必填修复引入的回归）+ 「绑定到」写意图
- 根因：昨日将 AC 绑定查询改为"空关键词报错"后，`snapshot_config` 内部无参调用 `get_user_bindings()` 随之抛错——AC 的备份、快照、变更计划生成（prepare 依赖快照）全部失败，用户创建绑定在 prepare 阶段被"AC 绑定查询需要提供关键词"报错阻断
- 修复：AC 快照对绑定枚举失败容错留空（官方接口 search 必选、无"查询全部"，快照场景无法给关键词，如实留空而非整体失败）；离线兜底的绑定查询同样捕获并返回引导语
- 路由补漏：「绑定到/绑定至/绑个」补入写意图词表（此前"绑定到"未被识别为写意图，仍落入只读技能）
- 新增快照容错与路由回归测试，实测 AC 备份恢复 200（完整快照），286/286 通过

## 2026-10-08

### fix: 写意图技能路由纠偏（绑定修改误入只读技能致"只读模式"误报）+ 绑定变更流程收敛
- 根因：配置查询（只读）技能含「绑定」关键词且排在策略变更之前，`select_skill` 首个命中即返回——「修改绑定信息」「绑定为××」等写操作消息被路由进只读技能，写工具未注入，模型误称"当前会话为只读模式"并引导手动操作（设备实际并未开只读）
- 修复：`select_skill` 写意图消息跳过只读技能（继续匹配能解锁写工具的技能）；写意图仅只读技能命中时回退全量工具模式保证写工具可用；写意图词表补充「做个/绑定为/绑定成/绑上/绑到」口语；策略变更技能补「绑定」关键词
- 提示词新增【绑定变更流程】：查现有绑定确认无冲突后立即调用绑定工具生成可编辑确认卡片，禁止重复查询/长篇分析/建议手动操作；仅缺必要信息（如 MAC）时先询问
- 新增 5 个路由回归测试（写意图→策略变更、纯查询→配置查询、仅只读命中→全量模式），284/284 通过

## 2026-10-08

### feat: 绑定确认卡片可编辑（用户名/描述/绑定目的/IP/MAC），修改也出卡片
- BindingForm 新增「绑定目的」字段；确认卡片的绑定表单从仅 create 扩展到 create+update（修改绑定同样出现可编辑卡片，删除不适用）
- 描述与绑定目的组合落库到厂商接口唯一的 desc 字段（如「办公区打印机；目的：固定 IP 准入」）
- 编排器 edited 覆盖链路原有支持（卡片编辑值安全合并进下发 data），本次打通 purpose 全链路
- 新增 purpose 组合落库测试，279/279 通过；前端 BindingForm/ConfirmCard 编译验证通过

---

## 2026-10-07

### feat: AC 绑定修改能力补齐（纯 IP/MAC 绑定增删 + update 删除+重建）
- 现象：用户要求修改 AC 绑定（10.68.5.16），模型答复"仅支持查询、不支持修改"并引导手动操作
- 根因：官方 AC 开放接口本无「修改」语义（文档 4.1~4.6 仅查/增/删），且适配器只实现了用户绑定（user-bindinfo）的增删——纯 IP/MAC 绑定（ipmac-bindinfo，本例正是此类）的增删未实现
- 修复：_apply_binding_change 识别纯 IP/MAC 绑定走 bindinfo/ipmac-bindinfo（新增 POST {ip,mac,desc} / 删除 POST _method=DELETE {ip}，按文档 4.4/4.6）；update 按官方口径以「删除+重建」实现，创建失败自动回滚旧绑定并如实报告（回滚也失败则明确提示到设备界面核实）
- 工具描述与系统提示词同步：注明修改=删除+重建、确认卡片如实标注；纠正模型"不支持修改"的错误话术
- 新增 6 个单测（mock _post 校验接口路径/body/回滚序列，不触碰设备），全量 278/278 通过；真实绑定变更由用户经确认卡片人工执行

## 2026-10-07

### fix: AC 绑定查询空结果死循环修复（search 必填口径对齐官方接口）
- 根因：AC 开放接口 bindinfo/user-bindinfo 与 ipmac-bindinfo 的 `search` 参数**必选**（官方文档 4.1/4.2），不支持查询全部；适配器按"空关键词=查全部"实现，设备返回「校验参数失败」后被含"校验"的兜底静默吞掉、ipmac 空关键词连请求都不发（审计 0.0ms）——模型连续 7 轮收到原样 `[]` 空转到轮次上限，用户得不到任何答复
- 适配器：绑定查询空关键词时抛出明确引导错误（请提供用户名/IP/MAC）；删除"校验"错误的静默吞掉（无匹配"不存在"仍视为空结果）
- 编排器：重复调用合并时向模型消息注入防循环提示（原提示只发前端预览，模型每轮看到原样结果导致空转）——通用加固
- 工具描述纠偏：注明 AC 绑定查询必须提供关键词（ipmac 需接近完整格式），替换错误的"为空则返回全部"
- 实测：空关键词返回明确引导语；search=10.68.5.2 返回真实绑定（50-e5-49-34-0e-ed）；272/272 测试通过

## 2026-10-07

### fix: v1 四项体验缺陷修复（确认 500 / 头像溢出 / 图谱筛选 / 拓扑双气泡与浮层泄漏）
- 修复配置修改确认提交 HTTP 500：`api/chat.py` 确认接口传参 `skills=` 与编排器 `resume_confirm(skill_folders=)` 命名不一致（TypeError），更正为 `skill_folders=`
- 修复 AI 对话头像文字溢出不可视：流式头像内 `sr-only` 类从未定义，"SFA Agent 正在回复"以可见文本挤进 30px 图标——style.css 补标准 `.sr-only` 定义并给头像加 overflow:hidden 兜底
- 修复知识图谱分类筛选失效与选项乱码：GraphView 下拉把 stats.categories 的对象项当字符串绑定（选中值恒不匹配节点分类）——改为绑定 c.name 并展示「分类（数量）」
- 网络拓扑：① 组件卸载无清理导致 el-select popper 逐次泄漏（页签反复切换后出现重复下拉框，实测堆积 6 份）——补 onBeforeUnmount（dispose 图表/移除悬浮层/清调试钩子）+ 分组下拉 teleported=false；② 链路悬停出现一大一小两个气泡——关闭边级 ECharts tooltip，自绘胶囊改为逐对渲染完整接口名（后端聚合边新增 links 明细数组保留配对），去除「链路聚合 ×N」摘要
- 全量 272/272 测试通过；确认接口实测 200，拓扑页签反复切换 body 级 popper 0 泄漏


## 2026-10-06

### chore: 开源准备（MIT LICENSE、免责声明、CI、贡献指南）
- 新增 MIT LICENSE、CONTRIBUTING.md、GitHub Actions（后端 pytest + 前端构建）
- README 增加开源与贡献、免责声明章节（商标归属/逆向参数仅供学习/设备操作风险自担）
- design.md 与模拟器 state.py 去除"比赛"等内部措辞，中性化表述

## 2026-10-05
### fix: MCP 会话建立挂起——venv 解析到 mcp 2.x 与 uvicorn 后端不兼容，锁定 1.x

复盘：用户后端（launcher 启动的 .venv 环境）MCP 测试稳定 30s 超时，而系统 Python
 环境 2-4s 成功。对比发现 .venv 将 requirements 的 mcp>=1.2 解析为 **mcp 2.3.0**
（大版本升级，anyio 4.14），其在 uvicorn 后端上下文中 stdio initialize 挂起
（独立 asyncio.run 可复现成功，差异锁定为后端事件循环 + mcp 2.x 组合）。

**修复**：requirements 锁定 mcp>=1.2,<2（附原因注释），.venv 安装 mcp 1.30.0，
 requirements.lock 用 venv 实际版本重新生成（64 包）

**验证**：重启 .venv 后端后 mcp/test 3.8s 返回 35 工具；后端 272/272（venv 运行时）、
 前端 35/35、构建通过

---
### fix: MCP 失败占位被无限缓存（服务恢复后仍不可用）

复盘真实对话「通过MCP调取最近一次巡检报告」：模型调用 mcp_acheck-readonly_unavailable
 占位工具——根因是 _tools_cache 把连接失败时的占位工具**无限缓存**（仅配置变更才失效），
 一次瞬时失败（如后端启动早于 MCP 服务就绪）后，即使服务恢复也永远不再重试

**修复**：agent_tools 改为「全部成功才缓存」——任一服务连接失败时本次以占位返回但
 不写缓存（下次调用自动重试），并记 warning 日志（含服务名与失败原因）

**验证**：失败场景两次调用触发两次连接（不再缓存）；真实 acheck-readonly 服务
 35 工具桥接 + platform_version 真实调用通过；后端 272/272

---
### fix: MCP 调用失效修复——会话管理两处根因（AsyncExitStack 误导入 + 跨任务取消作用域）

复盘：配置的 acheck-readonly MCP 服务在对话中完全不可用。两个叠加根因：
① AsyncExitStack 误从 asyncio 导入（该类在 contextlib）→ 会话建立必抛 AttributeError
 → _tool_scope 捕获后仅缺失注入，MCP 工具全程未出现；② 会话关闭（配置变更/停机）
 从其它任务退出 anyio 取消作用域 → cancel scope in a different task 报错

**修复**：① import 改 contextlib；② 会话管理重写为驻留后台任务模式——每服务一个
 专属 asyncio 任务内建立上下文并挂起（enter/exit 同任务，满足 anyio 约束），
 对话任务只借用 session；重置=取消驻留任务（在自身任务内干净退出）；
 建立 30s 超时 + 失败自清理；③ 应用停机时关闭全部 MCP 会话

**验证（真实 acheck-readonly 服务）**：连接成功并列出 35 个工具，桥接为 Agent 工具；
 真实调用 platform_version 返回平台构建信息；跨任务复用会话正常；重置无跨任务报错、
 重连正常。后端 272/272、ruff 全过

---
### fix: `/` 命令菜单位置改为浮层显示在输入框上方

此前菜单在输入台内联渲染（挤在左下、随输入框换行），现改为绝对定位浮层——
锚定输入框上缘居上展开、与输入台同宽（z-index 高于对话内容），选择后浮层自动关闭。
浏览器几何校验：菜单底边紧贴输入框上缘（above=true）、宽度同输入台

---
### feat: 对话输入台 `/` 命令菜单（快速选用 MCP/技能）+ 扩展能力选择器精简

**`/` 命令菜单**：输入以 / 开头即弹出可用能力列表（技能 + MCP 服务，名称即令牌）；
↑↓ 选择、Enter/Tab 确认、Esc 关闭；确认后输入框补全为 /令牌（如 /find-skills ），
继续输入问题一起发送；send() 解析消息前缀中的 /令牌——匹配到的技能/MCP 作为本次
对话的能力注入（与选择器勾选取并集），令牌从消息文本剥离；非能力令牌按普通文本发送
（仅全名匹配，不误吞）

**选择器精简（按要求只显示名字）**：去掉技能描述与说明信息，仅保留名称

**验证**：浏览器实测——输入 / 弹出菜单、Enter 补全 /find-skills、发送后模型调用
 load_skill 并基于技能内容回答（第二轮追问直接引用已加载说明）；后端 272/272、
 前端 35/35、构建通过

---
### fix: 侧栏菜单点击右侧无反应修复（路由视图渲染崩溃）

**根因链**：扩展能力选择器的技能项渲染调用 s.description.slice(...)，但 extSkills
 映射未携带 description 字段 → undefined.slice 抛错 → ChatView 渲染中断 →
 路由过渡的 vnode 树损坏（vnode null / nextSibling / parentNode 连环错误）→
 此后所有侧栏导航只改 URL 与高亮、右侧内容不再切换

**修复**：① extSkills 映射携带 description（后端缺省为空串）；② 模板对 description
 加 || '' 兜底；③ 路由视图过渡加 mode="out-in" 并移除冗余 :key（消除异步路由组件
 与过渡并发导致的 vnode 竞态，杜绝同类导航损坏）

**验证**：浏览器逐菜单连续切换两轮（9 个视图，含来回），零错误、全部正常渲染；
 后端 272/272、前端 35/35、构建通过

---
### fix: 勾选的技能在对话中未生效修复（load_skill 从未注入的根因）

**根因**：A-2 重构把 tool_scope 移入 routing.py 后，外部工具注入被 `except: pass`
 静默吞掉一个 TypeError——ext_tools.get_external_tools 的参数名仍是 skills，
 而 routing 以 skill_folders 关键字调用 → TypeError → load_skill 与 MCP 工具从未注入，
 用户勾选技能后模型因无此工具而不使用（回答中甚至如实说“没有 load_skill 工具”）。
 此前冒烟“已加载”为模型凭系统提示清单臆述，未真调用——测试盲区。

**修复**：① ext_tools 参数改名 skill_folders 并对齐 routing 调用；② 注入失败由静默
 改为 warning 日志（防再次无声失败）；③ 技能清单系统提示从软引导升级为强规则——
 描述相关必须先 load_skill、询问有哪些技能时逐个加载介绍、脚本类技能如实告知执行环境

**前端**：扩展能力选择器中技能/MCP 增加描述展示（此前只有名称，用户无法判断匹配）

**验证**：以用户原问题实测——模型先调用 load_skill 加载 find-skills 说明再回答
 （修复前同问题 0 次工具调用）；后端 272/272、前端 35/35、构建通过

---
### fix: 扩展能力选择器无法勾选修复

根因：el-checkbox-group 的 v-model 初始化为 null（非数组）——Element Plus 的 group
要求 model 为数组，null 时点击复选框不生效。修复：selMcps/selSkills 初始化并加载后
默认全选（数组），「恢复全部」= 重新全选；语义约定 [] = 本次对话明确不使用任何
 MCP 服务/技能（后端 None=全部、[]=无 的区分保持不变）。浏览器实测勾选/取消/复勾正常

---
### feat: 对话级 MCP/Skills 选用——输入台扩展能力选择器

AI 对话输入台新增「扩展能力」选择器（popover）：分 MCP 服务 / Agent Skills 两组勾选，
默认全部已启用项；取消勾选后本次对话仅注入所选服务/技能，带「恢复全部」一键复位。
选择随对话请求（chat 与 confirm 续答）透传后端，未传（None）保持兼容=全部已启用。

**后端链路贯通**：ChatIn/ConfirmIn 增加 mcps/skills 字段 → stream_chat/resume_confirm
 → _run_llm_loop → routing.tool_scope（agent_tools 按 server_id 过滤，桥接工具记录
 _mcp_server_id；enabled_catalog 按 folder 过滤，技能清单系统提示同步收窄）

**端到端验证**：真实对话勾选 find-skills 后提问，模型经系统提示清单调用 load_skill
 加载技能说明并基于内容作答（回答中正确引用技能用途）；无已启用项时选择器隐藏

**修复**：选用参数 skills 曾遮蔽编排器/routing 的 skills 模块导入（F821 13 处失败），
 统一改名 skill_folders

---
### feat: 平台设置扩展 MCP/Skills 管理 + AI 对话接入外部能力（MCP/Agent Skills）

**平台设置重构为三标签**：基本配置（原 BBS/LLM/企微原样迁入）｜MCP 配置｜Skills 配置

**MCP 配置**：服务列表（启停开关/编辑/删除/连接测试并列出工具清单）、自定义新增
（stdio 本机子进程：command+args+env；http 远端：url+headers）、从 MCP 官方注册表
（registry.modelcontextprotocol.io）搜索并一键安装（http 远端直装；npm/pypi 包自动映射
 npx/uvx 启动命令）、粘贴 Claude Desktop/Cursor 格式 mcpServers JSON 批量导入（默认停用）

**Skills 配置**：自动扫描本机已安装（~/.agents/skills、~/.claude/skills、应用导入目录，
真机发现 20 个技能）、GitHub 仓库文件夹 URL 导入（校验 SKILL.md 规范）、开关/删除
（删除仅限导入目录，外部目录只开关）

**AI 对话接入**：① 已启用 MCP 服务的原生工具自动注入编排器（名称前缀 mcp_<服务>_，
schema 透传，调用转发会话；会话 AsyncExitStack 常驻 + 懒连接 + 配置变更热重建，单服务
失联不拖垮其它）；② 新增 load_skill 工具 + 已启用技能清单注入系统提示——模型按需加载
 SKILL.md 说明并遵循执行（Agent Skills 运行时入口）

**依赖与验证**：requirements 增加 mcp>=1.2（官方 SDK，stdio/streamable-http 双传输）；
新增 7 项测试（配置 CRUD/函数名清洗/Claude JSON 导入/注册表安装映射/技能扫描开关/
删除保护/load_skill），后端 272/272、前端 35/35、构建通过；真机验证 Skills 扫描
（20 个）与 MCP 表单

---
### feat: 网络拓扑四项优化——缓存直读、链路合并、现代图标、手动布局修复

**T1 缓存直读**：topology_payload 默认只读缓存（进页面零 SSH 采集，实测 0.13s 出图，
此前 TTL 15 分钟外每次进入全量重采 18 台）；仅「重新采集」按钮（force）与终端定位
（_ensure_fresh，保证定位数据实时性）触发采集

**T2 链路合并**：同对设备 ≥2 条物理链路（LACP 聚合/主备上联，如 GA-6F 与 GA-14F-7508-1
 的 XGE1/0/51+XGE1/0/52 双口互联）合并为 1 条加粗聚合线（×N 徽标），端口清单进 tooltip
 ——图上不再重复绘制并行线；互联网分组验证 29 条边（原 32，合并 3 组双口链路）

**T3 节点图标现代化**：设备节点从圆形改为机架式圆角矩形（线性渐变 + 光泽描边 + 
投影），核心交换机 accent 描边放大、离线设备灰阶渐变；外部/未知邻居弱化为小圆形
 淡色节点；标签文字色随主题（暗色浅字）

**T4 手动布局修复**：layout:'none' 要求全部节点有有限坐标，缺保存位置（新设备/
历史遗留）的节点 x/y 为 undefined 会被 echarts 整体不渲染——表现为切手动布局空白；
修复：缺失坐标按网格兜底（140+col*170, 110+row*130），保证全部节点可见可拖

**验证**：后端 57 项 netdev 相关测试全绿；真实环境浏览器验证——缓存直读秒开
（采集于时间戳为缓存时间）、聚合线加粗显示、机架式图标、手动布局切换节点全部显示

---
### fix: 暗色对话页表格/代码修复 + AI 对话 UI 层次与科技感增强

**暗色显示修复**：md-body 表格斑马纹硬编码 #FAFBFD（暗色下偶数行整行白底）改
 panel-soft token；行内代码底/文字（#EEF1FA/#3346B8）、pre 边框、引用块底色、滚动条
（全局 #C7CEDC → scrollbar token，暗色深色系）、工具轨迹芯片（#F2F5FC/#3D5BD8/#DFE6F8
 → tint/chip-border token，完成态图标 accent 色）全部 token 化——新增 code-ink/
scrollbar/scrollbar-hover 3 组 token，亮色值不变、暗色自动适配

**对话 UI 层次与科技感**：AI 气泡改纵向渐变（surface→bg-deep）+ 顶部内高光 + 
 暗色专属描边色，气泡间轮廓与分隔感增强；AI 头像加主色光环（3px 柔和 ring）；
 欢迎区标题渐变文字（primary→accent background-clip）；输入台 focus 光环与渐变遮罩维持

**验证**：暗色对话页截图逐项核对（表格无白底行/芯片轮廓清晰/气泡层次分明），亮色回归无差异

---
### feat: 工作台三页支持网络设备（配置可视化/配置体检/备份与恢复）

**后端**：新增 services/netdev_ops_service 与 /api/netdev 运维端点——
① GET snapshot：一次 SSH 会话采集 7 分区只读快照（设备状态/接口/VLAN/路由/ARP/MAC/
运行配置，按厂家命令目录），同分区多命令输出合并，分区独立截断上限，60s 进程内缓存
（force 强制重采）；② POST checkup：运行配置规则分析（明文口令/SNMP 公共团名/Telnet/
FTP-HTTP 服务/vty 无 ACL/SSH 未启用/无日志主机/无 NTP），输出结构同深信服体检
（score/grade/counts/items），结果缓存 5 分钟；undo/no 关闭态不误报、stelnet 词边界
不误命中 telnet；③ 备份：运行配置全文存档（.conf 文本 + SHA256 + 快照 JSON），
列表/下载/删除/unified diff 差异对比——**无任何恢复端点（红线：不做恢复，避免影响生产）**，
服务层含红线守卫测试（禁止 restore/apply/rollback 类函数）

**前端**：三视图网络设备分支替换守卫横幅为工作面板——NetDevConfigPanel（分区 tab + 
运行配置本地行过滤 + 分节复制 + 截断提示）、NetDevCheckupPanel（分数环 + 高中低计数 + 
发现项卡片 + 只读说明）、NetDevBackupPanel（时间线/下载/删除/勾选对比/深链高亮，
顶部醒目“不支持一键恢复”警示）；深信服内容区在 netdev 模式下隐藏

**验证**：新增 7 项测试（体检规则/undo 不误报/vty ACL 判定/备份创建 diff/类型守卫/
无恢复红线守卫），后端 265/265、ruff 全过、前端构建通过；真实设备浏览器冒烟
（WW-14F-B5-H3C：快照 7 分区真实采集、体检 90 分 2 低危、备份创建/下载/删除可用）

---
### fix: 接口配置查询一次性正确化（定位→查配置流程优化）

复盘会话「定位172.20.10.23 → 查看这个接口的配置」：LLM 3 轮 7 次工具调用反复试错
（netdev_get_config 的 include 过滤只返回匹配行拿不到配置块；接口缩写 GE1/0/21 查详情
报参数错误；中途文字解释 + 最终重复结论形成“重复回答”），未能一次性答对。三项根治：

**① 接口名缩写自动展开**：GE→GigabitEthernet、XGE→Ten-GigabitEthernet、BAgg→
Bridge-Aggregation 等 12 组映射（expand_ifname）；接口配置查看优先用展开名（避免注定
失败的设备往返），回显报参数错误时回退原名重试；netdev_get_interfaces 详情查询同样展开

**② 一次性序列修正**：H3C/华为的 interface 命令必须先 system-view（用户视图下不可用——
此前序列直接进视图会失败）；新序列 = display interface（状态/计数）→ system-view → 
interface（接口视图）→ display this（生效配置）→ return，一次调用同时拿配置与状态

**③ 流程衔接引导**：定位结果 _llm_summary 直接指路“下一步查接口配置用
netdev_get_interface_config，不要用 netdev_get_config 的 keyword 过滤”；
netdev_get_config 描述注明 include 过滤的局限

**验证**：更新/新增 2 项测试（一次性序列断言、缩写展开与回退重试），258/258、ruff 全过

---
### feat: 网络设备四项修复——控制台分页/退格、终端定位增强、AI 查询能力扩展、批量截断治理

**控制台（172.16.118.254 反馈）**：① WebSocket 控制台连接后自动下发厂家分页关闭命令
（H3C screen-length disable 等）——dis cu 等长输出不再停在 ---- More ---- 中断；
该命令为会话级显示设置，不写配置、不影响其他会话，失败静默跳过；② 前端 Backspace
由 xterm 默认  映射为 （Comware/VRP 更通用的删除符），修复输入后无法退格删除

**终端定位增强（政务网 10.72.25.16 反馈）**：netdev_locate_terminal 定位前校验缓存
新鲜度——为空或过期（TTL 外）自动重新采集（此前仅缓存全空才采集，陈旧 ARP/MAC 导致
定位失败）；指定分组未命中时降级搜索其余全部分组（终端可能接在别的分组设备上），
降级命中在结果中注明来源分组

**AI 查询能力扩展**：① 新增 netdev_get_interface_config 工具——自动进入接口视图执行
 display this（锐捷 show run interface），看单接口生效配置的正确姿势；② 新增
 netdev_query 只读自由查询工具（免确认卡片）——仅放行 dis/display/show 词根命令，
 覆盖 VLAN/STP/MAC/链路聚合/LLDP/光模块/OSPF-BGP 邻居/在线用户/DHCP 等常用运维查询，
 非只读命令拒绝并指引走确认工具；③ 工具描述内置按厂家整理的常用命令清单供 LLM 选用

**批量查询截断治理**：TOOL_RESULT_LIMIT 8000→24000（18 台批量健康查询此前在 8000 处
腰斩，LLM 漏答设备）；截断从静默改为显式标记（提示 LLM 缩小过滤范围重查）；
 netdev 每设备输出上限自适应（≤3 台 6000/台，大批量按 ~20K 总量均摊、下限 500），
 保证多台批量不因编排器总限丢设备

**红线遵守**：全部改动为只读/会话级/客户端侧，未对现有设备下发任何配置变更

**验证**：新增 5 项测试（接口配置命令序列/只读守卫/工具写语义/过期重采集/分组降级），
后端 257/257、ruff 全过、前端构建通过

---
### fix: 暗色主题全站细修（语义状态 token + echarts 主题化 + 表格/表头清扫）

**语义状态 token**：style.css 新增 16 个 --sfa-tint-*/chip-border/hover-soft/panel-soft/
bar-track/code-bg/warn-*/ok-* 语义 token（亮色值=现行字面量，暗色=深色等价），
37 处视图硬编码亮色字面量（#F3F6FF/#EEF1FF/#FAFBFD/#FFF7E8 等）批量替换——
主色悬浮底/选中底/着色面板在两主题下各自有正确对应

**统计卡片与表格**：.sfa-stat 基类渐变改 token（原白底渐变在暗色下刺眼白块）、
stat-warn 变体改 warn token；el-table 表头/行悬浮、.md-body th、el-dialog 关闭钮
悬浮等 style.css 残留字面量全部 token 化（此前对话日志/网络设备表头为浅色条带）

**echarts 主题化**：知识库 3 图（知识图谱/分类分布/沉淀时间线）与网络拓扑图
按主题注册 dark（文字/轴色随主题），选项加透明背景；主题切换时销毁重建实例
（颜色注册在 init，setOption 改不了）；拓扑 tooltip 暗色深底、节点标签随主题取色

**验证**：逐页截图（对话/知识库/对话日志/网络设备/备份/配置可视化/软件更新）暗色
渲染正常，亮色回归无差异（统计卡片渐变观感保持）；后端 252/252、前端 35/35、构建通过

---
### feat: 第 2.5 批（N-1~N-7）——交互闭环收尾、上下文条、四态组件、暗色主题、响应契约

**N-1 HCI-1 收尾**：ChatView onBeforeUnmount 中止进行中的流（先 POST 取消端点再本地
abort，半截内容留档标记'对话已终止'），切视图不再 token 空耗；确认流挂起态不受影响

**N-2 回退点深链高亮**：确认卡片'查看回退点'带 ?highlight=bk_x 跳转，BackupView
加载后定位对应行（发光高亮 + 滚动居中），跨设备时提示先切换，行按钮变为'从此回退点恢复'

**N-3 CI 矩阵补 3.14**：3.12/3.13/3.14 三版本（3.14 本地已实测），README 版本口径同步

**N-4 ContextBar 设备上下文条**：components/ContextBar.vue（设备名/类型徽章/只读标记/
内联切换），接入配置可视化/配置体检/备份与恢复/软件更新建议四个工作台页头，
与侧栏/输入台三方同步

**N-5 AsyncSection 四态组件**：loading 骨架/error+重试/empty/内容状态机；
KnowledgeView 词条列表完成示范迁移（数据仍由父级持有）；@vue/test-utils 引入，
组件级测试从 0 到 1（状态机 + 重试恢复 5 项）

**N-6 暗色主题**：style.css 增加 [data-theme='dark'] 覆盖块（中性色阶 + EP 亮色
ramp 字面量的暗色重定义），main.js 引入 EP 官方 dark 变量，useTheme composable
（localStorage 持久化 + 启动即应用防闪白），侧栏亮/暗切换按钮，9 处硬编码白底
替换为 --sfa-surface token

**N-7 设备响应契约**：DeviceOut response_model（password 掩码单点化 + extra=ignore
防字段泄漏），list/patch 端点接入，契约测试断言掩码与字段白名单

**验证**：后端 ruff 全过 + pytest 252/252；前端 Vitest 35/35 + 构建通过；浏览器冒烟
（暗色切换与持久化/上下文条三方同步/历史会话续接/回退点高亮）——冒烟中发现并修复
ContextBar 引用了不存在的 --sfa-bg-card token（暗色下白条回退 #fff）

---
### feat: 架构与交互 P2 批次（BE-3/ARC-5/UX-2/ARC-4/FE-4/UX-5）

**BE-3 设备域统一**：services/device_scope.py 为 global/nd_/深信服三态与常量的
单一来源，chat/devices 校验、orchestrator、prompts、offline、netdev_tools、
channel_gateway 共 9 处魔法前缀判断全部收口

**ARC-5 可观测性**：http 中间件 request-id 贯穿（响应头回带 X-Request-ID）+
访问日志（方法/路径/状态/耗时）；audit_tool 增加 duration_ms 并接入全部工具
执行路径；/api/health 扩展运行快照（uptime/资产数/待确认动作/近 1h 工具耗时
p50/p95，取自审计明细聚合）

**UX-2 写前安全备份 + 回退点显性化**：深信服设备写操作执行前自动创建 pre_change
备份（失败即中止执行，保证始终可回退）；confirm_result 事件携带 safety_backup_id，
确认卡片'已执行'状态渲染'查看回退点'直达备份页（评估勘误：规则变更 diff 展示此前已存在，
实际缺口是写路径无自动备份与回退入口，本批次补齐）

**ARC-4 SSE 事件契约**：app/agent/events.py 以 Pydantic 定义 11 类事件模型，schema
导出 docs/sse-events.schema.json（scripts/gen_sse_schema.py），测试守卫漂移；
前端 agentStream.js 增加契约来源注释与 confirm_result 归约

**FE-4 API 内核**：api.js 五个同构封装收敛为 request 内核，导出签名不变，
apiDelete 错误文案升级为 readError detail 优先

**UX-5 输入台升级**：多行 textarea（1-6 行自适应），Enter 发送 / Shift+Enter 换行；
流式期间可继续输入并支持排队（回答结束自动发送，可取消排队）

**新增测试**：test_p2_contracts.py 6 项（设备域解析/存在性、耗时统计、健康快照与
request-id、事件样例契约校验、schema 漂移守卫）+ 前端 confirm_result 归约用例，
后端 251/251、前端 30/30 全绿

---

### feat: 交互与架构 P1 批次落地（路由化/确认卡片组件化/失败重试/历史会话/前端测试 0→1）

**ARC-1 前端视图路由化**：引入 vue-router（hash 模式，前端独立部署无 SPA fallback 依赖），
9 个视图路由级懒加载；侧栏导航与活跃态由路由驱动，移除 store.view；URL 可寻址——
刷新保持当前页、支持分享/收藏指定页面（依赖 vue-router@4）

**HCI-2/FE-2 确认卡片组件化 + 表单校验**：新增 components/chat/ConfirmCard.vue（卡片
整体内聚：头部/高危标识/冲突核实/diff/批量计划/恢复清单/高危二次确认弹窗），5 类交互
表单拆分为 forms/（BindingForm/RuleForm[NAT+ACL]/ObjectForm/ServiceForm）并接入 el-form
校验：IP/网段/范围/MAC/端口严格校验（形似即校验、组名放行口径与后端一致），校验失败
阻断提交；ChatView 1070→671 行，确认卡片区约 180 行模板与 120 行脚本外移

**HCI-3 失败重试路径**：error/网络失败事件标记 aiMsg.failed，最后一条助手消息渲染
'重试上一条提问'（保留 lastUserText），失败后一键重发不再重新打字

**chat/agentStream.js 事件归约抽离**：SSE 事件归约纯逻辑（token 累加/trace 按名配对/
confirm 挂起/error 标记），视图副作用经 handlers 回调——对应后端事件契约，可单测直测

**UX-1 对话页历史会话**：输入台新增'历史会话'抽屉——按当前设备（全局模式为全部设备）
分页拉取会话（标题/最后消息/设备名/时间），关键词搜索 + 加载更多；点击续接自动加载
该会话最近 200 条并切换到会话原设备上下文（跨设备续接不串上下文）；流式进行中禁止切换

**ARC-2/FE-6 前端测试 0→1**：Vitest（jsdom 环境）4 套件 29 项——agentStream 事件归约
矩阵（含并行轮次乱序配对）、api 统一封装（readError detail 优先/fake timers 超时中止/
PATCH JSON body）、store 设备失效回退全局与网络设备接口降级、表单校验器（999.1.1.1
类非法输入拦截口径锁定）；package.json 增加 npm test，CI 前端 job 增加 Vitest 步骤

**快速项**：X-1 .env.example 超时默认值与 config.py 校对一致（10→30，补 LOGIN_TIMEOUT）；
BE-4 README 声明后端单进程部署约束（取消信号/客户端缓存/保活为进程内单例）

**验证**：后端 ruff 全过 + pytest 245/245；前端 Vitest 29/29 + 构建通过

---

## 2026-10-04

### refactor: dbcore 基础设施剥离、定时备份并发、微项清理（第三批第 4 步：A-1 + N-4 + N-6）

**A-1 第 1 步（dbcore 剥离，为渐进拆分铺路）**
- 新增 `app/dbcore.py`：线程本地连接/SCHEMA/init_db/now/new_id/_row_to_dict/audit/backup_file_path/get_setting(s)/set_setting 等共享基础设施
- `db.py` 顶部显式 re-export（noqa F401），全部调用方（services/api/tests 的 `db.xxx`、`db._connect`）零改动；db.py 1247→约 990 行，后续按域迁移 repo 时只动 db.py
- 约定：dbcore 禁止反向依赖领域函数，repo 之间禁止互相 import

**N-4 定时备份限流并发**：`scheduled_backup_all` 由逐台串行改为 `asyncio.Semaphore(3)` 限流并发（多设备凌晨窗口耗时显著缩短，设备侧管理会话有限不宜全并发）

**N-6 微项**
- `factory.forget_device(device_id)`：设备删除时清理客户端缓存/签名/锁/失败负缓存残留（api/devices.py DELETE 接入），防工厂字典慢性增长
- 记忆节流字典 `_mem_extracted_at` 超 500 条截断最旧一半，防长期运行慢性增长

**验证**：ruff 全过，pytest 245/245，前端构建通过

### refactor: af_rest 读写翻译分离、orchestrator 分层提取（第三批第 3 步：A-3 + A-2）

**A-3 af_rest.py 读写分离（1426 → 1132 行）**
- 新增 `adapters/af_write.py`（332 行）：15 个写格式翻译纯函数外移（ip_ranges/service_payload/ip_like/parse_port_spec/acl_action_int/acl_native_payload/acl_create_payload/transfer_addr_str/transfer_port_str/split_refs/dst_ipobj/parse_ports/apply_transfer/nat_native_payload/nat_create_payload），可绕开 mock 设备直测
- 新增 `adapters/af_mapping.py`（66 行）：读侧扁平化映射（acl_flat/nat_flat_of_raw + first/join 同口径辅助）
- `AfRestClient` 保留 HTTP/认证/分页/端点编排与需要设备交互的引用自动创建（`_ensure_acl_*`），翻译函数改为薄委托——类内调用点与测试 API（`AfRestClient._acl_flat` 等）完全兼容；af_write 对 af_mapping 采用函数内惰性导入避免循环依赖
- 现有 `test_af_nat_write` / `test_af_acl_write` 全部直测通过，作为拆分回归基线

**A-2 orchestrator.py 分层提取（998 → 785 行，工具循环保持不拆）**
- 新增 `agent/offline.py`：离线兜底（`offline_reply`/`offline_answer`，含全部固定意图分支）纯函数化；GLOBAL_DEVICE_ID 延迟导入避免循环
- 新增 `agent/memory.py`：记忆管理（`parse_memory_extract`/`build_memory_context`/`extract_memory`/`schedule_memory_extraction` + MEMORY_EXTRACT_INTERVAL 常量）
- 新增 `agent/routing.py`：技能路由（`llm_select_skill`/`tool_scope`）
- orchestrator 三区块改为薄委托，保留原方法签名（`_parse_memory_extract` 等测试引用兼容）；工具循环（SSE 生成器 + 并行快路径 + 确认流）按评估结论不拆

**验证**：ruff 全过，pytest 245/245，前端构建通过

### perf: 设备数据 TTL 缓存、数据保留策略、会话消息分页（第三批第 2 步：A-5 + N-2 + N-3）

**A-5 服务端设备缓存（可视化端点读加速）**
- 新增 `services/device_cache.py`：按 (device_id, key) 的进程内 TTL 缓存 + single-flight（同设备同 key 并发加载合并为一次实拉，防缓存击穿）
- 边界红线（变更安全）：只缓存在 API/服务层——Agent 变更计划（tool.prepare）与工具 handler 直调适配器读实时数据，永不经过缓存，脏缓存不会污染 before/after 比对
- 端点接入：`api/devices.py` `_with_client` 增加 cache_key/ttl 参数；状态/接口/AC 在线用户/吞吐 TTL 4s，zones/nat/acl/bindings/ipmac/objects/services/routes/snapshot/AC 策略 TTL 30s；bindings/ipmac 缓存键含 keyword
- 写后失效（三处）：确认流批准执行（单台直接失效 + 批量在 `_execute_write_batch` 内逐台失效）、备份恢复 apply 后、设备 PATCH/DELETE 后

**N-2 数据保留策略（此前全库零清理，四类数据无限增长）**
- `db.cleanup_expired(retention_days, audit_days)`：过期会话级联清理（消息/摘要/沉淀状态/已处理的待确认动作）；pending 状态动作永不自动删；指向过期会话的渠道绑定仅解绑不删；审计日志按独立保留期清理
- `config_service.cleanup_scheduled_backups(keep)`：每设备 scheduled 备份仅保留最近 N 份（记录 + .conf 配置文件同步删除）；manual/pre_change 备份永不自动清理
- `main.py` 新增每日定时清理任务（CLEANUP_HOUR，默认 4:45）；新增设置 RETENTION_DAYS=180 / RETENTION_AUDIT_DAYS=365 / BACKUP_KEEP_SCHEDULED=30（均为 0 关闭）

**N-3 会话消息分页（此前唯一无界接口）**
- `GET /api/chat/conversations/{id}` 增加 limit（默认 200，上限 1000）与 before_id 游标参数；`db.get_messages` 支持 before_id 向上翻页
- ChatLogView 详情弹窗默认加载最近 200 条，"加载更早消息"按钮按需向上翻页

**新增测试**：`tests/test_retention_cache.py` 6 项（缓存命中/设备隔离/失效/single-flight 合并/端点级缓存与写后失效/保留清理含 pending 与绑定保护/备份保留策略），全量 245/245 通过

### build: 工程基建与前端首屏优化（第三批第 1 步：A-7 基建 + N-1 代码分割）

**A-7 工程基建**
- 新增 `backend/pyproject.toml`：ruff lint 配置（E/F/W/B 规则集，行宽 120；测试目录放宽 E402/F841/B904 等），存量 58 处问题全部清零——自动修复 26 处（未用导入/f-string/文件尾换行），人工修复 32 处：死赋值删除（transfer_src/dev_by_id/unit 等 8 处）、异常链补全 `from e`（B904 ×5：factory 登录超时/ac 302 引导/devices 网关超时）、zip 显式 `strict=True`（B905 ×4，均为长度恒等场景）、循环变量改名（B007 ×2）、闭包捕获循环变量改为参数显式传入（B023，`_exec_read` 增加-sem 形参）、测试数据重复键修正（F601）、中文标点字符集 lstrip 标注 noqa（B005 有意行为）
- 新增 `backend/requirements.lock`：由当前已测环境（pytest 239 全绿）按依赖闭包导出的 54 个精确版本，CI 复现测试环境；requirements.txt 保持宽松区间供本地开发
- 新增 `.github/workflows/ci.yml`：后端（锁版本安装 → ruff check → pytest）+ 前端（npm ci → build）双 job，push/PR 触发

**N-1 前端代码分割**
- App.vue 全部 9 个视图改 `defineAsyncComponent` 按需加载（`<component :is>` 切换机制原生兼容），首次进入仅加载当前视图，其余视图首次切换时拉取
- vite.config.js 增加 `manualChunks`：element-plus / element-icons / vue vendor 独立分片，业务代码改动不再使框架缓存失效
- 构建产物对比：首屏 JS 由单文件 2482KB（gzip 823KB）降至约 460KB gzip（entry 28KB + vendor 84KB + element-plus 918KB + 图标 171KB + 公共块 93KB，**降约 44%**）；echarts（1MB/gzip 343KB）随知识库/拓扑视图按需加载，xterm 维持既有动态加载，各视图代码 5-35KB 独立分片

**验证**：ruff check 全过；pytest 239/239；npm run build 通过且分片符合预期

### fix: 代码审查修复（第一批 10 项 + 第二批 4 项，基于第三方审查建议的复核修正版实施）

**缺陷修复**
- 保活失败释放连接（B-2）：`factory._keepalive_loop` 丢弃缓存客户端时补 `await _close_quietly(client)`，堵住设备反复掉线时 httpx 连接池泄漏
- 配置文件读取关句柄（B-3）：`config_service.restore_config_file` 改 `with open`，异常路径不再泄漏文件句柄
- 前端待沉淀轮询定时器卸载清理（B-4）：KnowledgeView `onBeforeUnmount` 补 `clearTimeout(pendingTimer)`
- `Devices.patch` 接入统一请求封装（B-5）：api.js 新增 `apiPatch`（超时 + `!resp.ok` 抛错），替换裸 fetch
- 死代码清理（B-6）：`_llm_select_skill` 冗余异常元组改 `except Exception`；`_offline_answer` 的 `if True:` 调试残留删除并整体反缩进（新增意图匹配测试锁定行为）
- 装饰器清理（B-1）：`af_rest._transfer_addr_str` 删除叠加的 `@classmethod`（复核确认 Python 3.9+ 描述符链式绑定下非 bug，属误导性写法清理）
- 移除空转 PRAGMA（B-7）：`db._connect` 删除 `foreign_keys=ON`（SCHEMA 无外键声明，级联由业务层手工完成）

**效率优化**
- 只读工具整轮并行化（E-1）：`_run_llm_loop` 新增快路径——一轮内全部为可执行只读调用时经信号量（READ_CONCURRENCY=3）并发执行，延迟由"各工具之和"降为"最慢者"；写操作/未知工具/全局缺 devices 轮次回退串行路径（确认流零改动）；同轮/跨轮同参数调用去重（失败结果不缓存，重复调用复用失败原因不重试）；结果按原调用顺序落库保证 tool_call_id 一一对应。配套 ChatView trace 改按工具名配对收尾（兼容"先全部 tool_call 后全部 tool_result"事件序）
- internal 模板提升（E-2）：原本每个工具调用重复构建的工具表过滤 + dict 推导提升到整轮一次
- `kb_stats` 反思计数改 `SELECT COUNT(*)`（E-4a），不再加载 1000 条正文只为 count
- 数据库补 10 个索引（E-5）：messages/audit/pending/memory/kb/netdev 等高频过滤列，`EXPLAIN QUERY PLAN` 验证 SCAN→SEARCH
- LLM 配置读取合并（E-3）：db 新增 `get_settings` 单条 IN 查询，`get_llm_config` 由 4 次 SQL 降为 1 次
- `Tool.schema()` 惰性缓存（A-4）：schema 静态，构建一次复用（4 个调用点均为只读已核实）
- 前端轮询退避（E-6）：NetDevView 1.2s→2.4s→4s 退避、KnowledgeView 3s→6s→9s 退避，页面隐藏时低频待机

**新增测试**：`tests/test_read_parallel.py`（并发重叠/同轮去重/写操作回退挂起/失败复用 4 项）+ 保活释放连接 + 离线意图匹配，全量 239/239 通过；前端构建通过

---

## 2026-10-03

### feat: 「平台设置」升级为左侧主菜单页面
- 新增 SettingsView 独立视图（系统分组，侧栏底部）：BBS 社区账号 / LLM 接入 / 企微机器人三组配置由弹窗改为整页折叠面板呈现，保存按钮置于页头，加载与保存逻辑原样迁移（状态徽章、敏感信息不回显口径不变）
- App.vue 移除原「平台设置」弹窗与底部按钮，侧栏底栏仅保留「添加设备」；新增「系统」导航分组
- 浏览器实测：菜单高亮、页面渲染、配置读取（社区账号/LLM/企微状态）均正常

### fix: 官方知识库引用链接丢失修复（词条「官方引用」不可点击）
- 根因（两个叠加缺陷）：① 诸葛官方 dict 事件的引用条目自带 `linkUrl` 完整链接，但 `URL_KEYS` 白名单漏收该字段，URL 提取时被丢弃；② `_ask_via_buffer` 从未透传 `answer_raw`，`ask_official_kb` 里"从回答原文找回被 `_clean_html` 剥掉的链接"的设计从未生效（回答正文里的参考来源 markdown 链接也随之丢失）
- 修复：`URL_KEYS` 补收 `linkUrl/link_url/jumpUrl`；`answer_raw` 透传并新增 markdown 链接提取（`[标题](url)` 与 `<a href>` 双通道）；无 url 引用按标题回填链接——包含关系 + 字符二元组 Jaccard 相似度（阈值 0.45，实测同文档不同措辞 ≥0.47、不同文档 ≤0.42 分界），同一文档"客服知识库内容-SCP导入云图授权…"vs"深信服云管平台导入云图授权…"这类前缀差异可正确对齐；回填后的 url 进入去重集合避免重复追加
- 存量修复：最近一次对话生成的词条 `SCP aPOS授权介绍与适用版本` 的 3 条官方引用已逐条重问官方知识库回填可点击链接（全库排查近 20 条词条中 15 条存在同类缺链，新沉淀的词条从此自动带链接；存量旧词条如需修复可对相应主题重新沉淀）
- 新增 6 个回归测试（linkUrl 提取/markdown 链接找回/相似度阈值/回填/端到端 URL 保留），全量 233/233 通过

### feat: 企微渠道默认全局对话，超时新会话回到全局
- 企微渠道与 Web 端对齐：新发送方初始绑定为**全局模式**（不指向第一台设备），绑定失效/设备被删除时同样回退全局；「当前设备」指令展示全局模式与设备数量
- 会话超时（CHANNEL_SESSION_TIMEOUT_MINUTES，默认 30 分钟）自动开启新会话时**回到全局模式**：绑定设备复位，提示语注明"已回到全局模式"
- 「切换设备 全局」（或 所有/all）可随时回到全局模式；「新会话」指令保持只重置会话不改变设备绑定；帮助文案同步
- 设备列表指令新增全局行（▶ 标记当前视角），格式统一
- 渠道测试更新 3 个（默认全局绑定/失效回退/超时重置全局），全量 228/228 通过

### feat: AI 对话默认全局模式（设备选择与对话日志全局视角）
- 设备选择器（侧栏 + 对话输入台）新增「全局（所有设备）」选项并作为**默认值**：不绑定单一设备，会话 device_id 记为 `global`，设备选择失效时自动回退全局
- 全局模式编排器：`global` 合成设备上下文（含深信服/网络设备数量）；工具全量注入、全部技能可见；深信服设备类工具未指定 `devices` 时——仅一台设备自动定向，多台回填设备清单引导 LLM 询问用户（不触发写工具重试禁令）；`devices` 参数支持 `["all"]`/`["全部"]` 表示全部设备（深信服与网络设备工具同支持）
- 确认流：全局模式确认变更时回退到动作所属会话绑定的设备执行（避免对 global 建连）；对话日志设备名对全局会话显示「全局」
- 前端：全局模式专属欢迎语/快捷提问/输入提示；对话日志筛选支持「全局会话」与网络设备分组，默认展示全部设备（含全局会话）；配置可视化/备份/体检/升级建议在全局模式下显示引导横幅并跳过加载
- 新增 7 个全局模式测试（上下文解析/工具与技能范围/all 匹配/引导文案/确认回退/全局对话 API），全量 228/228 通过

### feat: 下线演示设备，AI 对话升级为全局助手（深信服 + 网络设备统一对话管理）

**演示设备下线**
- 移除启动时自动种入的「演示-AF模拟器」：不再默认创建设备、移除 `/simulator` 挂载；启动时自动清理存量模拟器设备记录（`SF_SKIP_DEMO_CLEANUP=1` 供测试环境跳过）；设备 API 拒绝 simulator 模式（默认 real、base_url 必填），前端添加/编辑弹窗移除「内置模拟器」选项；企微渠道默认设备改为第一台设备（帮助文案同步）
- 内置 AF 模拟器包保留为测试夹具（测试仍经其验证 AF 适配器全链路），生产入口全部关闭

**全局 AI 对话助手**
- 会话设备上下文支持两类设备：深信服（`dev_`）+ 网络设备（`nd_`，取自 netdev_devices 表）；编排器按前缀分流加载，chat API 校验、记忆/摘要/待确认卡片按设备维度自然兼容
- 工具上下文过滤：`get_tools("netdev")` 只注入网络设备工具与免连接通用工具（知识库/设备列表）；深信服上下文在原有工具外注入网络设备工具（跨域查询）；设备类工具 schema 自动注入可选 `devices` 批量参数
- **跨设备/批量 fan-out**：深信服工具传 devices 列表（名称/IP）→ 只读逐台执行聚合结果（单台失败不阻塞）；写操作逐台生成变更计划合并为一张确认卡片（含逐台明细 `batch_devices`），确认后逐台下发（护栏/连接/执行相互独立），全失败标记 failed；网络设备工具自持 devices 解析（唯一子串匹配 + 多台歧义提示）
- 系统提示词重写为「全局运维 AI 助手」定位，新增跨设备批量与网络设备规则章节；`device_context_message` 支持网络设备（厂家/管理地址/分组）

**网络设备 AI 工具集（`agent/netdev_tools.py`，限华为/H3C/锐捷）**
- 查询：健康（版本/CPU/内存/环境）、配置（display current-configuration | include）、路由表（可按协议）、接口（brief 概览/单接口详情）、ARP 表项、故障日志（display logbuffer/show logging，附分析定位指引）；终端定位复用拓扑服务 search_asset（缓存为空自动触发分组采集）
- 写操作（确认卡片）：`netdev_apply_config`（自动 system-view/configure terminal + 可选接口视图，默认不保存、save=true 持久化——华为 save+y / H3C save force / 锐捷 write）、`netdev_run_commands`（任意命令原样下发兜底）
- 厂家白名单校验：非华为/H3C/锐捷返回友好指引到「网络设备管理」页；复用 netdev_service 连接池/分页关闭/提权/脱敏；写操作入审计
- 新增「网络设备运维」技能（关键词路由 + 写工具解锁，置于技能目录首位）；深信服系技能限定 af/ac/scp 上下文，网络设备上下文不再误注入

**前端**
- 设备选择器（侧栏 + 对话输入台）分组列出深信服设备与网络设备（华为/H3C/锐捷）；侧栏对网络设备隐藏编辑/删除（引导到网络设备管理页）
- ChatView 网络设备欢迎语/快捷提问/输入提示、工具轨迹中文名、批量确认卡片逐台明细渲染；欢迎语改为全局助手口径
- 配置可视化/备份/体检/升级建议四个视图对网络设备显示引导横幅并跳过数据加载
- 企微渠道设备列表/切换指令支持网络设备（标注厂家与 AI 支持范围），切换后走同一编排器
- 对话日志设备名联查兼容 nd_ 会话（devices 与 netdev_devices COALESCE）

**测试**：新增 test_netdev_agent.py 22 个用例（工具注册/上下文过滤/schema 注入/设备解析/厂家白名单/命令目录/配置包装与保存/确认计划/终端定位/技能路由/批量 fan-out 读写与护栏/演示设备下线 API 行为）；渠道默认设备用例随演示下线更新；全量 221/221 通过

---

### `3e1396a` feat: 网络设备拓扑发现与前端全局视觉升级
- 新增网络拓扑服务：按分组经 SSH 采集并解析 LLDP 邻居 / ARP 表（复用 netdev 通道与连接池，每设备一条会话，快照缓存 TTL 15 分钟），按厂家宽容正则解析（Comware/VRP 为主）；邻居名归一匹配构建组内拓扑，未命中的作为外部节点虚挂展示；ARP 用于资产（IP/MAC）→ 设备+端口定位；凭据不落日志、出参不含密码
- 前端新增 NetDevTopology 拓扑视图（采集加载/空态提示）；netdev API 与服务扩展拓扑端点
- 前端全局视觉升级：App.vue 导航与布局、ChatView 大幅重构、体检/知识库/备份/更新等各视图样式统一，style.css 设计变量扩充
- 新增性能剖析与基线脚本（netdev_probe_one 单台分阶段耗时 / netdev_perf_test 批量耗时分布）
- 全量 199/199 测试通过

## 2026-09-19

### feat: 网络设备管理扩充厂家至 12 个 + 设备筛选
- 新增 6 个厂家档案：Juniper JunOS（set cli screen-length 0）、HPE Aruba（no paging）、Dell Networking、TP-Link（terminal length 0）、MikroTik RouterOS（无分页，/system resource print）、Nokia SR OS（environment no more）——连同既有华为/H3C/思科/锐捷/中兴/通用共 12 种，批量执行与 WS 控制台自动生效
- 分页命令为空时跳过发送（MikroTik 无分页概念）；API 厂家白名单、前端配色标签、批量导入校验（动态取厂家接口+静态兜底）、CSV 模板示例同步扩展
- 设备列表两个 Tab（设备管理/批量执行选择器）新增**筛选框**：单关键字模糊匹配名称/IP/分组/型号，前端即时过滤并显示命中数，两 Tab 共用筛选状态（勾选不受影响）
- 新增 3 个测试（新厂家档案/接口白名单收录/下拉与保存联动），全量 195/195 通过

### fix: 老设备 SSH 传统算法兼容 + 设备导出
- 修复 H3C 老固件 SSH 连接失败（No matching encryption algorithm…aes128-cbc,3des-cbc）：asyncssh 默认提案不含 CBC/3DES 传统加密与 SHA1 DH 交换——新增共享连接参数 `ssh_connect_kwargs()`，以 "+" 前缀在默认算法基础上追加传统算法（encryption/kex/host-key 三类，与 AF 适配器放宽 SSL SECLEVEL 同理的存量设备兼容取舍），批量执行与 WS 控制台共用
- 头部去掉「CSV 模板」按钮（保留在批量导入弹窗内），新增**设备导出**：`GET /api/netdev/devices/export` 输出与导入模板同格式的 CSV（含口令列，导出→导入可往返迁移），前端一键下载带 BOM
- 新增 3 个测试：连接参数经 SSHClientConnectionOptions 真实构造校验（防再犯 encrypt_algs 类错误）、算法 "+" 追加格式断言、导出 CSV 往返；全量 196/196 通过（venv）

### fix: 网络设备管理——控制台无反应修复 + 布局美化
- 修复点击「控制台」无反应：① 组件内 `console` ref 遮蔽浏览器全局 console 且模板 v-model 对嵌套 ref 属性写入不解包——改名为独立布尔/对象状态；② xterm 的 `css/xterm.css` 此前未导入，终端渲染不可见——静态导入；③ 终端初始化套 try/catch + 消息提示不再静默；连接状态指示灯（连接中/已连接）
- 修复 asyncssh 终端参数误用（第二次同类问题）：`create_process` 无 `term_width/term_height`，正确为 `term_size=(宽,高)`（批量执行与 WS 控制台两处）；新增签名回归测试——用真实 asyncssh 的 create_session 签名校验源码中所有 create_process 关键字参数，此类拼错今后在测试阶段即被拦截
- 布局美化：顶部渐变标题条 + 统计卡片（设备总数/分组/连通正常/待测）；厂家彩色标签（华为红/H3C 橙/思科蓝等）+ 等宽字体地址列 + 连通性圆点徽标；批量执行改左右栅格（设备选择 1/3 + 命令与结果 2/3），结果改折叠面板（状态 tag/耗时/错误行内展示，终端风输出区）；控制台连接状态点、统一间距字号

### fix: 网络设备管理四项修复（测试 500 / CSV 模板 / 控制台）
- 修复连接测试 HTTP 500 与批量执行报错：run_commands 误传 asyncssh 不存在的 `encrypt_algs` 参数抛 TypeError 未捕获——移除该参数、补全异常兜底（未预期异常转友好失败不逃逸）；WebSocket 控制台端点与批量执行共用参数口径
- 批量导入升级：支持**上传 CSV 文件**导入（带双引号字段解析，口令含逗号可用引号包裹，自动跳过表头行），并提供 **CSV 模板下载**（含表头与 3 行示例，带 BOM 防 Excel 乱码）
- 新增**设备控制台**：每台设备一键打开浏览器内交互式 SSH 终端（xterm.js + 后端 WebSocket 双向透传，支持终端尺寸自适应与提权），关闭弹窗即断开会话
- vite 代理开启 ws 转发；新增 3 个回归测试（异常兜底/连接参数白名单/真实 asyncssh 不可达降级，无 asyncssh 的解释器自动跳过），全量 192/192 通过

### feat: 新增「网络设备管理」主菜单（SSH 交换机/路由器批量管理）
- 新增主菜单「网络设备管理」：管理 AI 对话设备（AF/AC/SCP）之外的常见厂家交换机/路由器，SSH 远程管理
- 设备管理：单台添加 / 批量添加（每行一台 CSV 格式，同 host:port 幂等更新）；厂家档案覆盖华为 VRP / H3C Comware / 思科 IOS / 锐捷 RGSOS / 中兴 ZXR10 / 通用（各自分页关闭命令、版本命令与提示符特征）；连接测试（SSH 建连 + 版本命令，回显关键信息）；可选 enable/super 提权口令
- 批量执行子页：勾选任意多台设备（跨厂家混合），粘贴每行一条的命令清单，**并行执行**（asyncio.gather + 信号量限流，同批最多 10 台同时在线 SSH 会话），进度实时落库并 1.2s 轮询刷新（等待/执行中/成功/失败 + 耗时），结果终端风着色展示、支持导出 txt；单台失败不阻塞同批其他设备，全失败任务标 failed
- 后端：`netdev_devices/netdev_tasks/netdev_task_items` 三表 + CRUD；`netdev_service`（asyncssh 交互式会话、提示符/空闲窗口双判定输出结束、输出口令脱敏）；API `/api/netdev/*`（设备 CRUD/批量导入/连接测试/执行/任务进度），出参不回传密码
- 依赖新增 asyncssh（requirements.txt）；前端新增 NetDevView（设备管理/批量执行两 Tab）；新增 7 个后端测试（CRUD 幂等/厂商档案/假 SSH 批量编排/进度落库/API 冒烟），全量 189/189 通过

### fix: AF NAT 策略真实设备写格式翻译（静默失败修复）
- 修复真实 AF 设备上修改 NAT（如 DNAT 目的地址）返回"成功"但配置不变：PATCH /nats 要求原生嵌套结构（DNAT 目的地址在 dnat.dstIpobj.specifyIp），扁平字段被设备静默忽略。新增 NAT 写翻译层——update 以设备当前原生规则为基底仅覆盖变更字段（dst_addr→dstIpobj、translated_addr→transfer（IP/IP_RANGE/IPGROUP）、translated_port→transferPort、SNAT dst_zone→dstNetobj.zone），create 按文档构造完整载荷；BNAT 仅翻译通用字段
- 顺带修复 NAT 读取缺口：转换地址 ipRange（地址池范围）与 transferPort 数组形态此前回读为空
- 新增 9 个 NAT 翻译测试（复刻故障场景/引用形态/范围池往返/创建载荷），全量 184/184 通过

## 2026-09-18

### feat: 接入推理型模型官方要求参数（temperature/top_p/thinking/tool_stream）
- 按模型官方要求全量调整 LLM 调用参数：`temperature=1`、`top_p=0.95`、`reasoning_effort=max`、`thinking.type=enabled`（仅支持 enabled）+ `clear_thinking=false`；流式工具调用开启 `tool_stream`（经 openai SDK `extra_body` 透传，标准参数直接传递）
- 新增配置项（`LLM_TOP_P`/`LLM_REASONING_EFFORT`/`LLM_TOOL_STREAM`，`LLM_TEMPERATURE` 默认改 1），`.env`/`.env.example` 同步；`Settings.llm_extra_body(stream)` 统一生成附加参数
- 适配思考模型的输出预算：主对话不设上限不变；技能路由 16→1024（超时 1.5s→4s，超时仍回退全量模式）、记忆提取 800→2048、知识沉淀提炼 3500→8000（流式调用不受长生成超时影响）、反思报告 2500→4096
- 思考过程的 reasoning_content 增量默认不进入对话正文（编排器仅聚合 content），前端观感不变
- 更新流式提炼测试断言（stream/max_tokens/top_p/thinking 参数透传），全量 175/175 通过

## 2026-09-17

### fix: AF 应用控制策略（ACL）真实设备写格式翻译与引用对象自动创建
- 修复真实 AF 设备上 ACL 修改报「应用控制策略[策略动作]：参数类型不匹配」：新增 ACL 写格式翻译层——update 以设备当前原生规则为基底，仅把变更字段翻译覆盖（action→0/1 整数、log→advanceOption.logEnable、地址/服务→原生名称数组），未改字段保持设备原样；create 按 API 文档样例构造完整原生载荷（schedule/group/dstAddrType 等）；`_acl_flat` 统一 ACL 读写字段口径
- 引用 IP组 不存在时自动创建：裸 IP/网段直接下发自动建同名网络对象（CIDR 的"/"替换为"_"并同步改写引用），内置「全部」等不代建
- 服务引用三级解析：自定义服务（USRDEF_SERV）→ 预定义服务（新增 `get_predefined_services`，servType=PREDEF_SERV，覆盖 ftp/any 等内置服务）→ 端口形态（TCP5211/tcp/5211/5211-5220/UDP 53 等）自动创建同名自定义服务（同协议同端口已存在则复用不重建）；纯名称均不存在时明确报错并指引指定端口
- 地址字段 any/任意/所有 规范化为内置网络对象「全部」（变更计划展示与下发数据双层），修复「网络对象[any] 不存在」——区域字段的 any 惯例与地址字段的对象名约定冲突导致
- 确认卡片预告引用缺失行为：地址组/端口服务将自动创建、纯名称服务需改用已有服务名或指定端口
- 系统提示词新增【ACL地址引用】与【变更留痕】规则（任意地址写「全部」、一次变更一份变更前备份、被拒后修正参数一次性重发）；record_to_kb/ingest_url_to_kb/add_device 标记 `needs_device=False`，知识库沉淀与添加设备不再依赖绑定设备在线（修复设备登录超时连累链接录入失败）
- 新增 17 个 ACL 写格式/自动建组建服务/卡片预告测试

### feat: 知识沉淀口径收紧与进度可视化
- 待沉淀入队口径收紧：仅「官方知识库实际命中」（新审计 `agent.kb.hit`，澄清反问亦算命中）或「用户明确要求沉淀」（record_to_kb）的对话入队；仅调用过知识库但未命中的会话不再堆积在待沉淀列表
- 自动沉淀只取本轮：命中官方知识库才触发沉淀（勾选与 kb_auto 自动路由口径一致），材料仅含本轮知识库相关轮次（提问 + 知识库返回 + 本轮回答），不带入之前的会话；手动批量沉淀同样按知识库轮次过滤，record_to_kb 显式沉淀保留完整会话材料
- 新增沉淀进度表 `kb_sediment_status`：排队/提炼中/完成条数/跳过原因/失败全程可视，待沉淀列表新增「沉淀进度」列并自动轮询刷新
- 链接录入结果明确化：内容全部被合并进已有词条时，明确列出被更新的词条主题（原口径"新增 0 条/更新 N 条"易被误解为界面应有新词条）；个人知识库词条列表新增「最近更新」排序（`order=updated`）
- 修复提炼材料未包含知识库返回原文的存量缺陷（工具结果分支被文本空判提前跳过）

### fix: 企微渠道写操作放开、跨渠道确认与裸确认
- `CHANNEL_READONLY_MODE` 默认改为 false：企微与 Web 同能力（写操作确认卡片、确认/取消指令均可用），置 true 可整体关闭渠道写入口
- 裸「确认/取消/确定/同意/拒绝/放弃」自动定位当前会话最近待确认动作——原必须带单号，裸确认落 LLM 后会重复生成变更卡片
- 跨渠道确认兼容：Web 端生成的确认卡片可在企微回复「确认 <ID>」执行（反之亦然），执行设备以动作所属会话绑定的设备为准
- 切换设备支持只给 IP（匹配管理地址 base_url，带端口亦可）；设备列表与切换回复标注只读设备
- 新增 6 个渠道网关测试（裸确认定位/无动作不误触/跨渠道确认/按 IP 切换/只读标注）

## 2026-09-16

### perf: 对话响应链路优化（企微与 Web 共用编排器）
- 技能路由 LLM 兜底仅对疑似写操作消息触发（新增写意图词表），纯查询/问答直接全量工具模式，每问最多省 2.5s 首字等待；保留场景超时 2.5s→1.5s
- 历史消息工具结果截断：仅最近 3 条保留全文（单条上限 8000 字），更早的截为开头摘要——多轮工具对话每轮 prompt 不再膨胀，速度不随轮次衰减
- 设备版本探测缓存（AF/AC，成功 10 分钟/失败 1 分钟 TTL）：状态/体检/知识库/升级建议等高频调用不再重复串行试探多个版本端点或抓取设备 Web 页
- 后台记忆提取：摘要与事实提取两次 LLM 调用合并为一次，并按新增消息数节流（满 `MEMORY_EXTRACT_INTERVAL`=6 条才提取）；personal_kb_service LLM 客户端模块级复用
- 诸葛官方知识库链路收敛：总超时 75s→45s、反问澄清宽限 8s→3s、缓冲轮询 0.5s→0.2s（`KB_ASK_TIMEOUT`/`KB_CLARIFY_GRACE`/`KB_POLL_INTERVAL` 环境变量可调）；后端启动时后台预热 SSO 会话，首次知识库提问免登录等待
- 升级信息缓存过期改为 stale-while-revalidate：先返回旧缓存立即响应、官网抓取转后台静默刷新（按产品线去重），对话路径不再同步等官网 12~15s
- 新增响应优化回归测试

## 2026-09-15

### fix: 企微渠道自然语言指令识别（开新会话/切换设备）
- 修复自然语言表达（如「开启一个新会话」「把设备切换到XX」）未被指令匹配命中、落入 LLM 后误报「工具不存在或无权限」的问题：网关意图识别由精确匹配升级为自然语言短文本识别（疑问句「如何/怎么/？」不拦截，普通提问含「切换」字样不误判）
- 切换设备为纯绑定变更，全程不检测设备连通性（原路径会因在当前不可达设备上调用查询工具而报连接错误）；目标缺省时返回设备列表与用法提示
- 新增 2 个测试（切换意图解析变体 9 例 / 自然语言会话与切换端到端），全量 128/128 通过

## 2026-09-15

### feat: 企微渠道对话体验优化（会话自动管理）
- 渠道初始绑定默认指向演示设备（模拟器优先，无模拟器回退第一台）
- 会话超时自动分段：`channel_bindings` 新增 `last_active_at`（init_db 平滑迁移），超过 `CHANNEL_SESSION_TIMEOUT_MINUTES`（默认 30 分钟，0=关闭）未对话，下次消息自动开启新会话并提示，对话日志按时间段独立成条
- 「切换设备」指令同时重置会话：修复旧会话日志被改归属到新设备的问题，新对话在新设备下开启；「新对话」指令增加「新会话」别名
- 新增 4 个网关测试（默认设备模拟器优先/新用户绑定演示设备/超时新会话/超时关闭），全量 126/126 通过

## 2026-09-15

### feat: 企业微信智能机器人渠道接入（WebSocket 长连接）
- 新增渠道无关网关层 `channel_gateway`：发送方绑定（新表 `channel_bindings`）、白名单、管理指令（设备列表/切换设备/新对话/帮助）、编排器事件流聚合为分段文本回复，Web SSE 渠道零改动复用编排器
- 新增企微长连接服务 `wecom_bot_service`（官方 wecom-aibot SDK）：30s 心跳、指数退避自动重连、msgid 去重、流式占位+聚合回复、待确认变更按钮卡片，默认关闭（WECOM_AIBOT_ENABLED）不影响既有部署
- 渠道默认只读（CHANNEL_READONLY_MODE=true）：写操作生成变更计划后引导回 Web 界面确认；设为 false 支持企微内「确认/取消 <ID>」文本指令与卡片按钮确认执行，黑名单护栏与审计照常生效
- 新增 `/api/channel/wecom/status` 连接状态接口，`/api/channel/message`、`/api/channel/confirm` 渠道联调入口
- 新增 9 个渠道网关测试（token 聚合/绑定复用/确认执行/越权拒绝/只读拦截/白名单/管理指令/长文分段/指令解析），全量 121/121 通过
- README 增加功能六与企微渠道配置说明；design.md 后续规划标注企微已上线

## 2026-09-14

### `ab2d1c5` docs: README 定位补充知识库能力，更新赛事交付物
- README 开头定位补充『知识库分层检索与沉淀』
- design 后续规划：告警推送标注渠道（webhook/邮件），新增微信/飞书/QQ 机器人对接
- contest：产品文档更新，新增 HTML 版与新命名版 PPT

### `ed3e0c3` docs: README 多设备支持概要，design 痛点强调配置文件不可读难迁移
- README 功能一：『AC 设备管理』条目改为 AF/AC/SCP 三类设备支持情况概要
- design 痛点一重写：设备自带配置文件为私有格式（AF .conf / AC .bcf），人不可读且无法迁移（AF 版本跨度大无法导入、第三方设备无对应配置），恢复时间过长

## 2026-09-13

### `7415f30` docs: design 核心功能补充知识库能力（功能三）
- 文档定位与背景痛点扩展（新增"知识经验难沉淀复用"痛点）
- 核心能力由两大扩为三大：新增"运维知识库（分层检索 + 沉淀复用）"

### `d37cd15` docs: README 推荐 Agent 技能启动方式，平台设置描述与界面对齐
- README 启动方式按推荐顺序重排：Agent 技能启动（windows-quickstart）> 一键脚本 > GUI 控制台 > 手动
- design.md 平台设置表：Cookie 行改为 BBS 社区账号/密码，Cookie 标注仅 .env 可配
- demo-script.md 修正两处过时的 Cookie 界面配置描述；删除 docs/new/ 草稿文档

### `710f3a5` docs: 对照代码审查并修订 README 与 design
- 工具数量口径统一为 50 个，意图模式修正为 70+，测试结果更新为 112/112
- 移除已删除文件的失效引用（launch.bat / start_all.sh / run_simulator.py）
- 凭据链描述更新（代码不再内置任何账号）
- 修正 design 小节编号错乱与记忆表名；项目结构补齐 SCP/quickstart/contest

### `0f0a1e2` feat: 知识库与编排器增强，赛事文档更新
- 个人知识库服务与测试改进，编排器/技能/工具链增强
- db 初始化调整，ChatView 小修复
- 赛事文档（产品文档 / PPT / PDF / 渲染页）同步更新
- .gitignore 增加 `_patch_*.py` 与 Office 锁定文件（`~$*`）规则

### `6b201b2` feat: 新增 windows-quickstart 技能，全新 Windows 电脑一键部署
- skills/windows-quickstart：SKILL.md 部署指南 + setup_windows.ps1 引导脚本
- 引导脚本仅依赖系统自带 PowerShell，自动检测/经 winget 安装 Python 3.10+ 与 Node 18+，幂等可重复执行
- 支持 -Mirror（国内镜像）与 -Launch（完成后直接启动）参数
- 收录 contest 问答准备文档与 docs/new 设计文档

### `a790b6e` feat: 新增深信服支持平台爬虫技能，赛事文档迁移至 contest 目录
- 新增 sangfor-support-crawler 技能：社区登录抓取官方支持内容（凭据经参数传入，无硬编码）
- 赛事交付物（产品文档 / 演讲PPT / 图表素材 / 构建脚本）移入 contest/
- 编排器与工具链增强，个人知识库服务改进；更新 README 与 design.md

### `0e9bfdd` fix: launcher 前端服务增加 frontend_host 绑定配置

### `aed5677` security: 移除硬编码的社区登录凭据，改为环境变量读取
- zhuge_ai_client 的 BBS 账号密码改为 `ZHUGE_BBS_USERNAME` / `ZHUGE_BBS_PASSWORD` 环境变量
- 运行时仍可经界面平台设置或 .env 配置（zhuge_kb_service 优先级更高）
- 已用 git-filter-repo 清洗全部历史并强制推送，旧提交中的凭据已抹除

### `44c9dc0` feat: 新增 SCP 设备适配器，整合启动脚本
- 新增 SCP（HCI 节点）适配器 scp_rest 与对应测试
- 适配器工厂、编排器、提示词与工具链同步增强
- 启动脚本整合：统一 launcher，移除冗余 bat/ps1/sh
- 前端对话 / 配置 / 更新页面改进

### `546042d` docs: 新增 update.md 更新记录

## 2026-09-12

### `06a6147` feat: 升级顾问支持跨架构迁移拆分，新增网页录入知识库能力
- 升级顾问：AF 跨架构升级清单拆分为旧架构升级 / 客服迁移 / 新架构升级三段
- 智能体新增 `ingest_url_to_kb` 工具，支持抓取网页沉淀到个人知识库
- 更新系统提示词与技能、知识库版本及个人知识库服务改进
- 同步更新 README、design.md 与相关测试；ChatView 小修复

### `d804ca6` feat: 设备连接稳定性与体验优化，升级顾问增强
- 适配器工厂增加设备级锁、登录超时与负缓存保护
- 会话按设备恢复，聊天消息记录加 LIMIT 限制
- 升级顾问与更新服务增强，知识库与个人知识库改进
- 前端对话 / 聊天记录 / 配置 / 知识库 / 更新页面体验优化
- 新增体验优化测试（test_experience）

## 2026-09-11

### `3732596` feat: 新增技能系统、知识库模块与报告生成，前后端功能升级
- 新增技能系统（skills.py）与诸葛 AI 助手技能包
- 新增个人知识库、诸葛知识库服务与 knowledge API
- 新增报告生成器（report_generator）
- 前端新增聊天记录、知识库视图，升级配置 / 备份 / 对话页面
- 新增启动器脚本与依赖检查工具

## 2026-09-01

### `61f5d50` docs: 更新 README.md 和 design.md 反映最新功能

## 2026-08-31

### `5732a24` v2: 新增 AC 适配器、应用设置模块，前后端功能升级

### `4ff0022` Initial commit: sangfor-agent-v1
