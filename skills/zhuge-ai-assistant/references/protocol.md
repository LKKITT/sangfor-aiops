# 诸葛小T协议详细参考

## 固定参数

| 参数 | 值 |
|------|-----|
| tenantId | `7810c5606006454ea264a97bbbdc88e1` |
| robotId | `9c90835650404361bd298832a4849896` |
| appCode/client_id | `3888901134` |
| sceneId | `kefu02`（知识库搜索问答场景） |

## AES加密参数

- 密钥：`eaA8eBa7EfeMfcfZ`
- IV：`TRYTOCN394402133`
- 模式：CBC
- 填充：Pkcs7

## SSO登录流程

### 1. 社区登录

```
POST https://bbs.sangfor.com.cn/member.php?mod=logging&action=login&loginsubmit=yes&infloat=yes&lssubmit=yes
Content-Type: application/x-www-form-urlencoded

formhash=<从首页获取>&username=<手机号>&password=<MD5加密密码>&quickforward=yes&handlekey=ls
```

### 2. 获取ticket

```
GET https://bbs.sangfor.com.cn/sf.php?mod=api&action=xiaot_ticket&_=<timestamp>
```

返回JSON，包含 `consultUrl` 字段（含hash参数）。

### 3. 提取outCode

访问 `consultUrl` 页面，从页面JS中提取 `token: 'xxx'` 字段。

outCode格式：`随机字符串:时间戳:hash`

### 4. 换取JWT token

```
GET https://zhugeai.sangfor.com.cn/api/getTokenBySource?code=<outCode>&source=bbs
```

返回JWT token，解码payload包含：sub=kitt, csdpUserId=社区uid, login_source=bbs

## REST API清单

### 认证类
- `GET /api/getTokenBySource?code=&source=bbs` - SSO换取token
- `GET /api/getLoginUserInfo` - 获取用户信息
- `POST /api/ai/robotScene/getRobotByChat` - 获取机器人配置

### 业务类
- `GET /api/ai/knowledge/getProductLineList` - 获取产品线列表
- `GET /api/ai/knowledge/getSceneList` - 获取场景列表
- `POST /api/ai/robotScene/forward` - 场景转发（部分场景不可用）

### 知识库类
- `GET /api/ai/knowledge/getZhuGeAssociate` - 联想搜索
- `POST /api/ai/knowledge/search` - 知识库搜索

## WebSocket协议

### 端点

```
wss://zhugeai.sangfor.com.cn/api/socket/chat
```

### 握手Header

- `token`: JWT token
- `tenantId`: 租户ID

### 发送消息格式

```json
{
  "linkSessionDTO": {
    "linkSessionStatus": 0,
    "linkSessionId": "",
    "linkCloseReason": "TEXT",
    "msgType": "TEXT",
    "qiYuApplyStaffDto": {"staffType": "1", "groupId": "", "staffId": ""},
    "productName": "AF"
  },
  "token": "JWT token",
  "robotId": "9c90835650404361bd298832a4849896",
  "index": "7810c5606006454ea264a97bbbdc88e1",
  "faqSessionId": "",
  "sceneId": "kefu02",
  "sendType": 5,
  "message": "用户问题",
  "messageId": "UUID",
  "newLinkSession": 0,
  "newApplyStaff": ""
}
```

### 接收消息类型

| eventType | 说明 | a字段内容 |
|-----------|------|-----------|
| `reasoning` | 思考过程（流式增量） | 思考文本，含`<tag>`标签表示查阅资料进度 |
| `message` | 正式回答（流式增量） | 回答文本，含HTML标签（如`<span class="sftooltip">`引用标记） |
| `dict` | 引用来源 | JSON数组字符串，每条含id/title/content |
| `finalMessage` | 回答结束 | 空 |

### 接收消息示例

```json
{
  "id": "消息ID",
  "a": "回答内容增量",
  "eventType": "message",
  "sceneId": "kefu02",
  "product": "AF",
  "robotId": "...",
  "tenantId": "...",
  "recordAnswerType": "4"
}
```

## 常见问题

### Q: WebSocket连接成功但服务器不响应？
A: 检查URL是否为 `/api/socket/chat`（不是 `/api/transferSocket/chat`），检查 `productName` 是否填写了产品线。

### Q: 服务器返回"请携带产品线信息提问"？
A: `linkSessionDTO.productName` 必须填写产品线名称（AF/AC/AD/EDR等），不能为空。

### Q: 回答中包含HTML标签？
A: 使用 `ask()` 方法会自动清理HTML标签返回纯文本；`ask_full()` 返回 `answer`（纯文本）和 `answer_raw`（原始HTML）。

### Q: token过期？
A: JWT token有效期约7天，过期后重新调用 `login_by_sso()` 即可。

## 技术栈

- 前端：Vue3 + Element Plus + Axios + Pinia（PC端）
- 移动端：Vue3 + Vant UI
- 通信：REST API + WebSocket
- 加密：AES-CBC-Pkcs7
