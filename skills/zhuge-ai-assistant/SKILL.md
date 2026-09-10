---
name: zhuge-ai-assistant
description: 深信服诸葛小T（深小服）智能客服问答接入。通过社区SSO自动登录，使用WebSocket实时问答，查询深信服全产品线（AF/AC/AD/EDR等）技术知识库。当用户询问深信服产品配置、故障排查、版本兼容性、API使用等技术问题，或需要将诸葛小T作为子Agent/外挂知识库接入自有Agent系统时使用。用户只需提供社区账号密码即可自动完成登录和问答。
---

# 深信服诸葛小T（深小服）问答助手

通过社区SSO自动登录诸葛小T，使用WebSocket实时问答，查询深信服技术知识库。

## 快速开始

用户只需提供社区账号（手机号）和密码，即可自动完成登录并问答。

```python
import sys
sys.path.insert(0, '<skill_dir>/scripts')
from zhuge_ai_client import ZhugeAIClient

client = ZhugeAIClient(bbs_username="用户手机号", bbs_password="用户密码")
if client.login_by_sso():
    client.connect_ws()
    answer = client.ask("用户的问题")
    print(answer)
    client.close_ws()
```

## 核心工作流

1. **社区登录**：Discuz标准登录，密码MD5加密，获取formhash
2. **获取ticket**：调用 `sf.php?mod=api&action=xiaot_ticket` 获取consultUrl
3. **提取outCode**：访问consultUrl页面，从JS中提取token字段
4. **换取JWT**：调用 `/api/getTokenBySource?code=outCode&source=bbs` 获取JWT token
5. **WebSocket问答**：连接 `wss://zhugeai.sangfor.com.cn/api/socket/chat`，发送问题，接收流式回答

## 主要方法

| 方法 | 说明 |
|------|------|
| `login_by_sso()` | 社区SSO自动登录，返回bool |
| `connect_ws()` | 建立WebSocket连接 |
| `ask(question, timeout)` | 简单问答，返回纯文本回答 |
| `ask_full(question, timeout)` | 完整问答，返回dict（answer/reasoning/references） |
| `close_ws()` | 关闭WebSocket连接 |

### ask_full返回结构

```python
{
    "answer": "清理HTML后的纯文本回答",
    "answer_raw": "原始HTML回答",
    "reasoning": "AI思考过程",
    "references": [{"id": "1", "title": "引用来源标题", "content": "..."}],
    "faqSessionId": "会话ID",
    "complete": True
}
```

## 重要约束

- **必须清除代理环境变量**：运行前执行 `unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY`
- **WebSocket URL**：`wss://zhugeai.sangfor.com.cn/api/socket/chat`（不是transferSocket）
- **不需要初始化消息**：连接后直接发送问题
- **productName必须填写**：问题消息中 `linkSessionDTO.productName` 需指定产品线（AF/AC/AD等），为空时服务器返回"请携带产品线信息提问"
- **回答是流式增量**：需拼接所有 `eventType=message` 的 `a` 字段
- **JWT token有效期约7天**，过期需重新登录

## 作为子Agent接入

```python
class ZhugeSubAgent:
    def __init__(self, username, password):
        self.client = ZhugeAIClient(bbs_username=username, bbs_password=password)
        self.client.login_by_sso()
        self.client.connect_ws()
    
    def query(self, question):
        return self.client.ask(question)
    
    def close(self):
        self.client.close_ws()
```

## 依赖

```bash
pip install requests pycryptodome websocket-client
```

## 详细协议

完整的API清单、WebSocket消息格式、逆向分析细节见 [references/protocol.md](references/protocol.md)。
