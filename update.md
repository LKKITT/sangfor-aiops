# 更新记录（Update Log）

本项目所有代码提交的更新记录，按时间倒序排列。

---

## 2026-09-13

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
