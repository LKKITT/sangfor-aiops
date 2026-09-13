# 深信服Support社区案例API参考

## 基础信息

- **Base URL**: `https://support.sangfor.com.cn/spt/openapi/case/es`
- **认证方式**: BBS社区SSO登录后的Cookie会话
- **请求头**:
  - `Content-Type: application/json`
  - `X-Requested-With: xmlhttprequest`
  - `Accept: application/vnd.edusoho.v2+json`

## 登录流程

### 1. 社区登录

```
POST https://bbs.sangfor.com.cn/member.php?mod=logging&action=login&loginsubmit=yes&infloat=yes&lssubmit=yes
Content-Type: application/x-www-form-urlencoded

formhash=<从首页获取>&username=<手机号>&password=<MD5加密密码>&quickforward=yes&handlekey=ls
```

### 2. 跳转Support（自动SSO）

```
GET https://support.sangfor.com.cn/
```

访问后自动完成SSO，获取PHPSESSID等cookie。

---

## API端点

### 1. 案例列表搜索

**请求**:
```
POST /spt/openapi/case/es/search
```

**请求体 (JSON)**:
```json
{
  "productLineId": "13",
  "keyword": "",
  "mainModuleIds": [],
  "childModuleIds": [],
  "versionId": "",
  "pageNum": 0,
  "pageSize": 20
}
```

**参数说明**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| productLineId | string | 是 | 产品线ID（13=AF） |
| keyword | string | 否 | 搜索关键词 |
| mainModuleIds | array | 否 | 主模块ID列表 |
| childModuleIds | array | 否 | 子模块ID列表 |
| versionId | string | 否 | 版本ID |
| pageNum | int | 是 | 页码（从0开始） |
| pageSize | int | 是 | 每页数量（最大20） |

**响应**:
```json
{
  "code": 0,
  "msg": "操作成功",
  "rows": {
    "content": [
      {
        "id": "2:51089",
        "source_id": "51089",
        "title": "案例标题",
        "product": "13",
        "product_name": "下一代防火墙AF",
        "product_version": "8.0.95",
        "main_module_names": "监控",
        "child_module_names": "",
        "cate_name": "故障案例",
        "keyword": "流量统计,日志",
        "content": "案例摘要（HTML）",
        "create_time": "2024-01-01 00:00:00",
        "update_time": "2026-09-12 19:45:28",
        "type": "1",
        "permission": 0
      }
    ],
    "totalElements": 3059,
    "totalPages": 612,
    "size": 20,
    "number": 0
  }
}
```

---

### 2. 案例详情

**请求**:
```
GET /spt/openapi/case/es/getDetailById/{id}
```

**路径参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| id | string | 案例ID（source_id） |

**响应**:
```json
{
  "code": 0,
  "msg": "操作成功",
  "rows": {
    "id": 51089,
    "productId": 13,
    "productName": "下一代防火墙AF",
    "contentId": 51154,
    "name": "案例标题",
    "content": "<div>完整HTML内容</div>",
    "contentWeb": "<div>Web格式内容</div>",
    "suiteVersion": "AF8.0.45以上",
    "mainModuleNames": "监控",
    "childModuleNames": "",
    "updateTime": "2026-09-12 19:45:28",
    "createTime": null,
    "readaccess": 0,
    "collected": false,
    "isCategory": 0,
    "versionType": 3
  }
}
```

---

### 3. 产品模块列表

**请求**:
```
GET /spt/openapi/case/es/getCaseModuleList/{productLineId}
```

**路径参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| productLineId | string | 产品线ID |

**响应**:
```json
{
  "code": 0,
  "msg": "操作成功",
  "rows": [
    {
      "id": "1",
      "name": "监控",
      "children": [
        {"id": "1-1", "name": "流量统计"}
      ]
    }
  ]
}
```

---

### 4. 产品版本列表

**请求**:
```
GET /spt/openapi/case/es/getProductVersionList/{productLineId}
```

**路径参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| productLineId | string | 产品线ID |

**响应**:
```json
{
  "code": 0,
  "msg": "操作成功",
  "rows": [
    {
      "id": "100",
      "code": "AF8.0.107",
      "productId": "13"
    }
  ]
}
```

---

### 5. 场景案例模块列表

**请求**:
```
GET /spt/openapi/case/es/getSceneCaseModuleList/{productLineId}
```

**路径参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| productLineId | string | 产品线ID |

**响应**: 树形结构，包含典型场景排障思路分类

---

## 错误码

| code | 说明 |
|------|------|
| 0 | 成功 |
| 200 | 成功 |
| 500 | 服务器异常 |
| 666 | 无权限访问 |

---

## 注意事项

1. **POST请求参数必须放在JSON body中**，不能放在query params中
2. **必须携带正确的请求头**，特别是 `X-Requested-With: xmlhttprequest`
3. **pageNum从0开始**，不是从1开始
4. **pageSize最大20**，超过会被截断
5. **登录态有效期**：建议每次运行前重新登录
6. **请求频率**：建议间隔≥1秒，避免被限流
