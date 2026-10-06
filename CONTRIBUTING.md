# 贡献指南

欢迎参与贡献！这是一个个人开源项目，Issue 与 Pull Request 均欢迎。

## 开发环境

- Windows / Linux / macOS 均可；Python 3.10+、Node.js 18+
- 全新环境推荐先跑 `skills/windows-quickstart/scripts/setup_windows.ps1`（见 [SKILL.md](skills/windows-quickstart/SKILL.md)）
- 手动方式：`pip install -r backend/requirements.txt`、`cd frontend && npm install`
- 复制 `backend/.env.example` 为 `backend/.env`（不配 LLM Key 也可跑通，走离线兜底模式）

## 运行测试

```bash
cd backend
python -m pytest -q
```

提交前请确保全量测试通过；修复缺陷时建议附带回归测试用例。

## 提交规范

- 提交信息使用约定式前缀 + 中文描述：`feat:` / `fix:` / `docs:` / `chore:` / `refactor:` / `perf:`
- 每次功能性提交请同步更新根目录 `update.md`（按时间倒序，注明日期与要点）

## 代码约定

- 后端 FastAPI + 自研编排器，前端 Vue3 + Element Plus；与现有代码风格保持一致
- 涉及设备交互的代码注意：凭据不落日志、API 出参不回传密码、写操作必须走确认流
- 不提交凭据、密钥、厂商文档到仓库（.gitignore 已配置，请注意新文件类型）

## 开源许可

提交即表示你同意以 [MIT](LICENSE) 许可证授权你的贡献。
