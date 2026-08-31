# 深信服售后技术支持 Agent

面向深信服售后技术支持场景的 Agent：**设备配置自动化备份与智能管理** + **软件更新信息获取与升级建议**。基于深信服官方 REST API 文档与技术支持平台公开资料构建，内置 AF 设备模拟器，可离线完整演示。

## 核心功能

**功能一：设备配置自动化备份与智能管理**
- 🔄 自动备份：结构化配置快照（**网络对象/自定义服务/接口/静态路由/NAT/ACL/用户绑定**全量，可解析/可视化/可恢复，支持导出可读 JSON 供第三方设备迁移）+ 配置文件归档（SHA256 校验）；手动/定时/变更前自动触发
- 📊 配置可视化：设备状态、网络接口与实时流量、NAT/访问控制策略、IP-MAC 绑定；任意两份快照字段级 diff
- 💬 自然语言管理：对话查询/修改接口、NAT、访问控制、用户绑定等——**所有变更经确认卡片人工确认后下发**，执行前自动安全备份，全程审计
- 🩺 配置体检：确定性规则引擎识别规则冲突（遮蔽/矛盾）、空策略、过宽权限、高危端口暴露、资源异常（含 mbuf，关联 AF 8.0.85 已知问题），支持一键修复

**功能二：软件更新信息与升级建议**
- 📥 多源抓取：深信服技术支持平台版本发布说明**免认证按版本精确抓取**（AF/AC 两条产品线，真实条目优先、内置知识库兜底，来源标注透明）；软件下载列表支持界面『平台设置』配置登录 Cookie 抓取；官网 PSIRT 安全公告（公开）
- 🧭 升级建议：升级路线图（含 AF 新旧架构分界）、PSIRT 命中检测、EOL 识别、关键变更点四类归类（新增功能/安全修复/已知问题修复/优化）、升级时机与行动清单

## 快速开始

### 环境要求
- Python 3.10+（开发用 3.12）、Node.js 18+

### 一键启动
```bat
:: Windows
cd sangfor-agent
scripts\start_all.bat
```
```bash
# Linux / macOS
cd sangfor-agent
bash scripts/start_all.sh
```

手动启动：
```bash
# 后端（首次先安装依赖并配置 .env）
cd backend
python -m venv ../.venv && ../.venv/Scripts/python -m pip install -r requirements.txt   # Linux: ../.venv/bin/pip
copy .env.example .env   # 填入 LLM_API_KEY（智谱/DeepSeek 等 OpenAI 兼容接口均可）
../.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8600

# 前端
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173 ，默认已连接「演示-AF模拟器」设备。

### 真实设备兼容（实测 AF 8.0.45.380）
适配层内置针对真实设备的兼容处理：旧密码套件 TLS（SECLEVEL=0）、`/api/v1` 路径、`items` 响应与 `uuid` 字段映射、action 枚举实测校准、API 并发会话复用（按设备缓存登录客户端）、可选端点优雅降级。接入后即可完成状态/配置可视化/体检/备份/升级建议等只读功能。

### LLM 配置（可选）
未配置 Key 时自动进入**离线兜底模式**（支持状态/接口/NAT/ACL/绑定/体检/备份/升级建议等固定意图查询），核心功能可完整演示。配置后获得完整自然语言对话与变更能力：

除 `.env` 外，也可直接在界面左下角「**平台设置 → 大模型（LLM）接入**」填写 API 地址 / Key / 模型（任意 OpenAI 兼容接口），**保存即生效无需重启**：

```ini
# backend/.env 方式（智谱 GLM 示例）
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_API_KEY=your-key
LLM_MODEL=glm-4-flash
# DeepSeek 示例：LLM_BASE_URL=https://api.deepseek.com/v1  LLM_MODEL=deepseek-chat
```

### 运行测试
```bash
cd backend
../.venv/Scripts/python -m pytest -q     # 38 个单元/集成测试
```

### 接入真实设备
前端「+ 添加设备」→ 真实设备 → 填 AF 设备地址（`https://设备IP`）与 API 账号密码。适配层与模拟器同构，同一套代码无缝切换。建议先用只读模式评估。

## 文档
- [方案设计说明书](docs/design.md) —— 架构/选型/流程/模块/测试验证（比赛提交材料）
- [演示脚本](docs/demo-script.md) —— 12 个递进式演示用例

## 项目结构
```
backend/app/
├── adapters/        # DeviceClient 抽象 + AF REST 适配器 + AF 模拟器（同构端点）
├── services/        # 备份恢复 / 配置体检引擎 / 更新抓取 / 升级建议 / 版本知识库
├── agent/           # 编排器（工具循环+确认流）/ 22 个工具 / 安全护栏 / 提示词
└── api/             # REST + SSE 路由
frontend/src/        # Vue3：对话 / 配置可视化 / 备份恢复 / 配置体检 / 更新建议
```

## 安全设计
- 写操作强制**两阶段确认**（变更计划卡片 → 人工确认 → 执行），执行前自动生成回滚备份
- 敏感操作黑名单（恢复出厂/删管理员/关 HA/清空配置）先于 LLM 拦截
- 全局/设备级只读模式；全部工具调用与确认落审计日志
- 升级固件只建议不代执行；LLM 只基于工具返回数据作答，防幻觉
