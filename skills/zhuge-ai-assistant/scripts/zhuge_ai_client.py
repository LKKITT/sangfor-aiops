#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深信服诸葛小T（深小服）API 客户端
用于将诸葛小T作为子Agent/外挂知识库接入你的Agent系统

技术架构（已完整逆向）：
- 前端：Vue3 SPA + Element Plus（PC端）/ Vant UI（移动端）
- 认证：社区SSO（推荐，已完整实现自动登录）
- 通信：HTTP REST + WebSocket
- API域名：zhugeai.sangfor.com.cn
- WebSocket：wss://zhugeai.sangfor.com.cn/api/transferSocket/chat

SSO自动登录流程（已完整实现）：
1. 登录深信服社区（Discuz标准登录，密码MD5）
2. 调用 /sf.php?mod=api&action=xiaot_ticket 获取outCode
3. 用outCode调用 /api/getTokenBySource?code=xxx&source=bbs 换取JWT token
4. 用JWT token调用所有API和WebSocket
"""

import json
import time
import base64
import hashlib
import threading
import re
import uuid
import os
from typing import Optional, Callable
from urllib.parse import urljoin

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import websocket


# ============================================================
# 配置
# ============================================================
CONFIG = {
    # 诸葛小T配置
    "base_url": "https://zhugeai.sangfor.com.cn",
    "api_base": "/api",
    "ws_url": "wss://zhugeai.sangfor.com.cn/api/socket/chat",
    # 从前端JS逆向得到的固定参数
    "tenant_id": "7810c5606006454ea264a97bbbdc88e1",
    "robot_id": "9c90835650404361bd298832a4849896",
    "app_code": "3888901134",
    "scene_id": "kefu02",  # 客服场景ID（从抓包得到）
    "product": "AF",  # 默认产品（从抓包得到，可根据需要切换）
    # AES加密参数（从前端JS逆向得到）
    "aes_key": b"eaA8eBa7EfeMfcfZ",
    "aes_iv": b"TRYTOCN394402133",
    # 社区登录凭证：仅从环境变量读取，禁止在代码中硬编码
    "bbs_username": os.environ.get("ZHUGE_BBS_USERNAME", ""),
    "bbs_password": os.environ.get("ZHUGE_BBS_PASSWORD", ""),
    # 请求超时
    "timeout": 30,
}


# ============================================================
# 工具函数
# ============================================================
def aes_encrypt(plaintext: str) -> str:
    """AES-CBC-Pkcs7加密，与前端CryptoJS一致"""
    cipher = AES.new(CONFIG["aes_key"], AES.MODE_CBC, CONFIG["aes_iv"])
    encrypted = cipher.encrypt(pad(plaintext.encode(), AES.block_size))
    return base64.b64encode(encrypted).decode()


def md5(text: str) -> str:
    """MD5加密，用于社区登录密码"""
    return hashlib.md5(text.encode()).hexdigest()


# ============================================================
# 社区登录器（Discuz标准登录）
# ============================================================
class BBSLogin:
    """深信服社区登录器"""

    def __init__(self, username: str = None, password: str = None):
        self.username = username or CONFIG["bbs_username"]
        self.password = password or CONFIG["bbs_password"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        self.logged_in = False
        self.uid = None

    def login(self) -> bool:
        """登录深信服社区"""
        # 第一步：访问首页获取formhash
        resp = self.session.get("https://bbs.sangfor.com.cn/")
        formhash_match = re.search(r'formhash["\s:=]+([a-f0-9]{8})', resp.text)
        if not formhash_match:
            formhash_match = re.search(r'name="formhash"\s+value="([^"]+)"', resp.text)
        formhash = formhash_match.group(1) if formhash_match else ""

        # 第二步：提交登录表单（Discuz标准，密码MD5加密）
        login_data = {
            "formhash": formhash,
            "username": self.username,
            "password": md5(self.password),
            "quickforward": "yes",
            "handlekey": "ls",
        }
        self.session.post(
            "https://bbs.sangfor.com.cn/member.php?mod=logging&action=login&loginsubmit=yes&infloat=yes&lssubmit=yes",
            data=login_data,
            headers={"Referer": "https://bbs.sangfor.com.cn/"},
        )

        # 第三步：验证登录状态
        resp = self.session.get("https://bbs.sangfor.com.cn/home.php?mod=space")
        uid_match = re.search(r'uid=(\d+)', resp.text)
        if uid_match:
            self.uid = uid_match.group(1)
            self.logged_in = True
            print(f"[社区登录成功] uid={self.uid}")
            return True
        else:
            print("[社区登录失败] 请检查账号密码")
            return False

    def get_xiaot_ticket(self) -> Optional[str]:
        """
        获取诸葛小T的outCode（ticket）
        流程：调用xiaot_ticket获取consultUrl → 访问页面提取token（即outCode）
        """
        if not self.logged_in:
            print("[获取ticket失败] 未登录社区")
            return None

        # 第一步：调用xiaot_ticket获取consultUrl
        resp = self.session.get(
            "https://bbs.sangfor.com.cn/sf.php?mod=api&action=xiaot_ticket",
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://bbs.sangfor.com.cn/plugin.php?id=info:index",
            },
        )
        data = resp.json()
        if data.get("code") != 0 or not data.get("data", {}).get("consultUrl"):
            print(f"[获取ticket失败] {data}")
            return None

        consult_url = data["data"]["consultUrl"]
        full_url = "https://bbs.sangfor.com.cn" + consult_url

        # 第二步：访问consultUrl页面，从JS中提取token（即outCode）
        resp2 = self.session.get(full_url, allow_redirects=True)
        # token格式: 'LR3IWG7R7e5Q7t67:1788773340:2f4b33ee9f30fc55e68b3e8c4f227cdfa4511783'
        token_match = re.search(r"token:\s*'([^']+)'", resp2.text)
        if token_match:
            out_code = token_match.group(1)
            print(f"[获取ticket成功] outCode: {out_code[:30]}...")
            return out_code

        # 备选：从outCode字段提取
        outcode_match = re.search(r'outCode[=:\"\s]+([^\"&\s\']+)', resp2.text)
        if outcode_match:
            out_code = outcode_match.group(1)
            print(f"[获取ticket成功] outCode(备选): {out_code[:30]}...")
            return out_code

        print("[获取ticket失败] 页面中未找到token/outCode")
        return None


# ============================================================
# 诸葛小T客户端
# ============================================================
class ZhugeAIClient:
    """诸葛小T API客户端"""

    def __init__(self, mobile: str = None, password: str = None,
                 bbs_username: str = None, bbs_password: str = None,
                 username: str = None):
        # 支持多种参数名：mobile/bbs_username/username 都表示社区账号
        self.mobile = mobile or bbs_username or username
        self.password = password or bbs_password
        self.base_url = CONFIG["base_url"]
        self.api_base = CONFIG["api_base"]
        self.tenant_id = CONFIG["tenant_id"]
        self.robot_id = CONFIG["robot_id"]
        self.scene_id = CONFIG.get("scene_id", "kefu02")
        self.product = CONFIG.get("product", "AF")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "Origin": self.base_url,
        })
        self.token: Optional[str] = None
        self.session_id: Optional[str] = None
        self.faq_session_id: Optional[str] = None
        self.link_session_id: Optional[str] = None
        self.scene_id: Optional[str] = None
        self.product: Optional[str] = None
        self.user_info: Optional[dict] = None
        self.robot_config: Optional[dict] = None
        self.bbs_login: Optional[BBSLogin] = None
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._response_callback: Optional[Callable] = None
        self._response_buffer: list = []

    def _api_url(self, path: str) -> str:
        """拼接API URL"""
        return urljoin(f"{self.base_url}{self.api_base}/", path.lstrip("/"))

    def _auth_headers(self) -> dict:
        """获取带认证的header"""
        headers = {}
        if self.token:
            headers["token"] = self.token
        if self.tenant_id:
            headers["tenantId"] = self.tenant_id
        return headers

    # --------------------------------------------------------
    # 认证相关
    # --------------------------------------------------------
    def login_by_sso(self, bbs_username: str = None, bbs_password: str = None) -> bool:
        """
        社区SSO自动登录（推荐，完整实现）
        流程：社区登录 → 获取ticket → 换取JWT token → 初始化
        """
        # 使用实例变量或传入参数
        username = bbs_username or self.mobile
        pwd = bbs_password or self.password
        if not username or not pwd:
            print("[错误] 请提供社区账号和密码")
            return False

        # 第一步：登录社区
        self.bbs_login = BBSLogin(username, pwd)
        if not self.bbs_login.login():
            return False

        # 第二步：获取outCode
        out_code = self.bbs_login.get_xiaot_ticket()
        if not out_code:
            return False

        # 第三步：用outCode换取JWT token
        try:
            resp = self.session.get(
                f"{self.base_url}/api/getTokenBySource?code={out_code}&source=bbs",
                timeout=CONFIG["timeout"],
            )
            data = resp.json()
            if data.get("code") == 0 and data.get("data"):
                self.token = data["data"]
                self.session.headers["token"] = self.token
                print(f"[SSO登录成功] token: {self.token[:30]}...")
                self._init_after_login()
                return True
            else:
                print(f"[换取token失败] {data}")
                return False
        except Exception as e:
            print(f"[换取token异常] {e}")
            return False

    def login_by_password(self) -> bool:
        """
        手机号密码登录（部分账号不可用，需已设置独立密码）
        密码自动AES加密
        """
        if not self.mobile or not self.password:
            print("[密码登录] 未配置手机号和密码")
            return False

        encrypted_pwd = aes_encrypt(self.password)
        payload = {"mobile": self.mobile, "password": encrypted_pwd}
        try:
            resp = self.session.post(
                self._api_url("/loginByUser"),
                json=payload,
                headers={"tenantId": "", "Token": ""},
                timeout=CONFIG["timeout"],
            )
            data = resp.json()
            if data.get("code") == 0 and data.get("data", {}).get("token"):
                self.token = data["data"]["token"]
                self.session.headers["token"] = self.token
                print(f"[密码登录成功] token: {self.token[:20]}...")
                self._init_after_login()
                return True
            else:
                print(f"[密码登录失败] {data.get('msg')}: {data.get('data')}")
                return False
        except Exception as e:
            print(f"[密码登录异常] {e}")
            return False

    def login_with_token(self, token: str, tenant_id: str = None):
        """使用已有token登录（从浏览器提取）"""
        self.token = token
        if tenant_id:
            self.tenant_id = tenant_id
        self.session.headers["token"] = token
        if self.tenant_id:
            self.session.headers["tenantId"] = self.tenant_id
        print(f"[Token登录] token: {token[:20]}...")
        self._init_after_login()

    def _init_after_login(self):
        """登录后初始化：获取用户信息、机器人配置、会话ID"""
        # 获取用户信息
        try:
            resp = self.session.get(
                self._api_url("/ai/sys-user/getLoginUserInfo"),
                headers=self._auth_headers(),
                timeout=CONFIG["timeout"],
            )
            data = resp.json()
            if data.get("code") == 0:
                self.user_info = data.get("data")
                print(f"[用户信息] name={self.user_info.get('name')}, phone={self.user_info.get('phone')}")
        except Exception as e:
            print(f"[获取用户信息失败] {e}")

        # 获取全局会话ID（POST方法）
        try:
            resp = self.session.post(
                self._api_url("/ai/sys-user/getGlobalSessionId"),
                json={},
                headers=self._auth_headers(),
                timeout=CONFIG["timeout"],
            )
            data = resp.json()
            if data.get("code") == 0:
                self.session_id = data.get("data")
                print(f"[会话ID] {self.session_id}")
        except Exception as e:
            print(f"[获取会话ID失败] {e}")

        # 获取机器人配置
        self._get_robot_config()

    def _get_robot_config(self):
        """获取机器人配置"""
        try:
            resp = self.session.post(
                self._api_url("/ai/robot/getRobotByChat"),
                json={"accessType": 1},
                headers=self._auth_headers(),
                timeout=CONFIG["timeout"],
            )
            data = resp.json()
            if data.get("code") == 0 and data.get("data"):
                robots = data["data"]
                if isinstance(robots, list) and len(robots) > 0:
                    self.robot_config = robots[0]
                elif isinstance(robots, dict):
                    self.robot_config = robots
                if self.robot_config:
                    self.robot_id = self.robot_config.get("robotId") or self.robot_id
                    self.tenant_id = self.robot_config.get("tenantId") or self.tenant_id
                    self.faq_session_id = self.robot_config.get("faqSessionId")
                    self.scene_id = self.robot_config.get("currentSceneId")
                    print(f"[机器人配置] robotId={self.robot_id}, faqSessionId={self.faq_session_id}")
        except Exception as e:
            print(f"[获取机器人配置失败] {e}")

    # --------------------------------------------------------
    # 业务API
    # --------------------------------------------------------
    def get_associate(self, keyword: str) -> list:
        """获取联想词/相关问题"""
        try:
            resp = self.session.get(
                self._api_url("/ai/chat/getZhuGeAssociate"),
                params={"content": keyword},
                headers=self._auth_headers(),
                timeout=CONFIG["timeout"],
            )
            data = resp.json()
            return data.get("data", []) if data.get("code") == 0 else []
        except Exception as e:
            print(f"[获取联想词失败] {e}")
            return []

    def recognize_intent(self, message: str) -> dict:
        """意图识别"""
        try:
            resp = self.session.post(
                self._api_url("/ai/intent/recgn"),
                json={"message": message},
                headers=self._auth_headers(),
                timeout=CONFIG["timeout"],
            )
            return resp.json()
        except Exception as e:
            print(f"[意图识别失败] {e}")
            return {}

    def get_history(self, page: int = 1, size: int = 20) -> list:
        """获取历史对话记录"""
        try:
            resp = self.session.post(
                self._api_url("/ai/faq-record/getHistory"),
                json={"page": page, "size": size},
                headers=self._auth_headers(),
                timeout=CONFIG["timeout"],
            )
            data = resp.json()
            return data.get("data", []) if data.get("code") == 0 else []
        except Exception as e:
            print(f"[获取历史记录失败] {e}")
            return []

    # --------------------------------------------------------
    # WebSocket 实时问答
    # --------------------------------------------------------
    # 注意：WebSocket连接已验证可建立，但发送消息后服务器暂未回复。
    # 接收消息格式已完全逆向（a字段+eventType），发送格式需浏览器抓包确认。
    # 抓包方法：F12 → Network → WS → chat → Messages → 查看↑方向的消息
    # 当前已验证：SSO登录✅ REST API✅ WebSocket连接✅ 接收格式✅
    # 待确认：发送消息的完整字段（可能缺少product/特定header等）
    def connect_ws(self, on_message: Callable = None, wait_ready: bool = True):
        """建立WebSocket连接，等待初始化完成"""
        self._response_callback = on_message
        self._response_buffer = []
        self._ws_ready = threading.Event()

        def on_open(ws):
            print("[WebSocket] 连接已建立")
            # 注意：不需要发送初始化消息，直接发送问题即可
            self._ws_ready.set()

        def on_message(ws, message):
            try:
                data = json.loads(message)
                self._response_buffer.append(data)
                if self._response_callback:
                    self._response_callback(data)
                # 处理初始化消息：提取faqSessionId和linkSessionId
                faq_sid = data.get("faqSessionId")
                if faq_sid and not self.faq_session_id:
                    self.faq_session_id = faq_sid
                    print(f"[WS初始化] 获取faqSessionId: {faq_sid}")
                    self._ws_ready.set()
                # 提取linkSessionId（转人工会话ID）
                link_session = data.get("linkSession") or {}
                link_sid = link_session.get("linkSessionId") or data.get("linkSessionId")
                if link_sid and not self.link_session_id:
                    self.link_session_id = link_sid
                # 打印关键信息
                event_type = data.get("eventType", "")
                content = data.get("a", "")
                if event_type == "message" and content:
                    print(f"[WS回答] {content[:100]}", end="", flush=True)
                elif event_type == "reasoning" and content:
                    pass  # 思考过程不打印
                elif event_type == "finalMessage":
                    if not self._ws_ready.is_set():
                        self._ws_ready.set()
                    print("\n[WS] finalMessage")
                else:
                    print(f"\n[WS收到] eventType={event_type}, a={content[:50] if content else '(空)'}")
            except json.JSONDecodeError:
                print(f"\n[WS收到非JSON] {message[:200]}")

        def on_error(ws, error):
            print(f"[WebSocket错误] {error}")

        def on_close(ws, close_status_code, close_msg):
            print(f"[WebSocket关闭] code={close_status_code}, msg={close_msg}")
            self._ws_ready.set()  # 防止阻塞

        # 从session中获取cookies
        cookie_str = "; ".join([f"{c.name}={c.value}" for c in self.session.cookies])

        self._ws = websocket.WebSocketApp(
            CONFIG["ws_url"],
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            header={
                "token": self.token or "",
                "tenantId": self.tenant_id or "",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/",
                "User-Agent": self.session.headers["User-Agent"],
                "Cookie": cookie_str,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )

        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={"ping_interval": 30, "ping_timeout": 10},
            daemon=True,
        )
        self._ws_thread.start()
        print("[WebSocket] 正在连接...")

        if wait_ready:
            # 等待初始化完成（服务器返回faqSessionId）
            ready = self._ws_ready.wait(timeout=15)
            if ready:
                print("[WebSocket] 初始化完成")
            else:
                print("[WebSocket] 初始化超时，继续尝试")

    def _send_init_message(self):
        """发送WebSocket初始化消息（严格按照前端JS格式）"""
        if not self._ws or not self._ws.sock or not self._ws.sock.connected:
            return

        # 严格按照前端JS逆向的格式，不多加字段
        init_msg = {
            "linkSessionDTO": {
                "linkSessionStatus": 1,
                "linkSessionId": "",
                "msgType": "TEXT",
                "qiYuApplyStaffDto": {"staffType": "1", "groupId": "", "staffId": ""},
                "productName": ""
            },
            "token": self.token,
            "robotId": self.robot_id,
            "index": self.tenant_id,
            "sendType": 5,
            "message": "",
            "newLinkSession": 1,
            "newApplyStaff": ""
        }
        try:
            self._ws.send(json.dumps(init_msg))
            print("[WS] 发送初始化消息")
        except Exception as e:
            print(f"[WS初始化失败] {e}")

    def send_message(self, message: str, product: str = None) -> bool:
        """
        发送消息到诸葛小T
        消息格式已从前端JS完整逆向
        关键：linkSessionStatus=0时，linkCloseReason必须为"TEXT"
        """
        if not self._ws or not self._ws.sock or not self._ws.sock.connected:
            print("[WebSocket] 未连接，请先调用 connect_ws()")
            return False

        product_name = product or self.product or ""
        payload = {
            "linkSessionDTO": {
                "linkSessionStatus": 0,
                "linkSessionId": self.link_session_id or "",
                "linkCloseReason": "TEXT",  # 关键：发送问题时必须为"TEXT"
                "msgType": "TEXT",
                "qiYuApplyStaffDto": {"staffType": "1", "groupId": "", "staffId": ""},
                "productName": product_name
            },
            "token": self.token,
            "robotId": self.robot_id,
            "index": self.tenant_id,
            "faqSessionId": self.faq_session_id or "",
            "sceneId": self.scene_id or "kefu02",
            "sendType": 5,
            "message": message,
            "messageId": str(uuid.uuid4()),
            "newLinkSession": 0,
            "newApplyStaff": ""
        }
        try:
            self._ws.send(json.dumps(payload))
            print(f"[WS发送] {message}")
            return True
        except Exception as e:
            print(f"[WS发送失败] {e}")
            return False

    def ask(self, question: str, timeout: int = 60) -> str:
        """
        同步问答：发送问题并等待回答（返回纯文本回答）
        根据实际抓包：回答内容在 a 字段，eventType区分类型
        - reasoning: 思考过程
        - message: 正式回答（流式增量）
        - finalMessage: 回答结束（含引用来源）
        """
        result = self.ask_full(question, timeout)
        return result["answer"]

    def ask_full(self, question: str, timeout: int = 60) -> dict:
        """
        同步问答：返回完整结果（回答+思考过程+引用来源）
        eventType说明：
        - reasoning: 思考过程（流式增量）
        - message: 正式回答（流式增量，含HTML标签）
        - dict: 引用来源（JSON数组）
        - finalMessage: 回答结束
        """
        if not self._ws or not self._ws.sock or not self._ws.sock.connected:
            self.connect_ws()

        # 等待连接就绪
        if hasattr(self, '_ws_ready') and not self._ws_ready.is_set():
            self._ws_ready.wait(timeout=10)

        # 清空缓冲区
        self._response_buffer = []

        self.send_message(question)

        start_time = time.time()
        full_answer = ""
        reasoning = ""
        references = []
        faq_session_id = None

        while time.time() - start_time < timeout:
            if self._response_buffer:
                for msg in self._response_buffer:
                    event_type = msg.get("eventType", "")
                    content = msg.get("a", "")
                    faq_session_id = msg.get("faqSessionId") or faq_session_id

                    if event_type == "reasoning" and content:
                        # 思考过程（流式增量），过滤<tag>标签
                        if not content.startswith("<tag>"):
                            reasoning += content
                    elif event_type == "message" and content:
                        # 正式回答（流式增量）
                        full_answer += content
                        print(f"[回答增量] {content}", end="", flush=True)
                    elif event_type == "dict" and content:
                        # 引用来源（JSON数组字符串）
                        try:
                            refs = json.loads(content)
                            if isinstance(refs, list):
                                references = refs
                        except json.JSONDecodeError:
                            pass
                    elif event_type == "finalMessage":
                        # 回答结束
                        print()  # 换行
                        if faq_session_id:
                            self.faq_session_id = faq_session_id
                        # 清理HTML标签，返回纯文本
                        clean_answer = self._clean_html(full_answer)
                        return {
                            "answer": clean_answer.strip(),
                            "answer_raw": full_answer.strip(),
                            "reasoning": reasoning.strip(),
                            "references": references,
                            "faqSessionId": faq_session_id,
                            "complete": True,
                        }
                self._response_buffer = []
            time.sleep(0.3)

        print()  # 换行
        return {
            "answer": self._clean_html(full_answer).strip(),
            "answer_raw": full_answer.strip(),
            "reasoning": reasoning.strip(),
            "references": references,
            "faqSessionId": faq_session_id,
            "complete": False,
        }

    @staticmethod
    def _clean_html(text: str) -> str:
        """清理HTML标签，返回纯文本"""
        if not text:
            return ""
        # 移除sftooltip标签及其内容
        text = re.sub(r'<span class="sftooltip"[^>]*>.*?</span>', '', text, flags=re.DOTALL)
        # 移除其他HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        # 清理多余空白
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def close_ws(self):
        """关闭WebSocket连接"""
        if self._ws:
            self._ws.close()
            self._ws = None
        if self._ws_thread:
            self._ws_thread.join(timeout=5)
            self._ws_thread = None

    def reset_session(self):
        """重置会话"""
        self.faq_session_id = None
        self.scene_id = None
        self._get_robot_config()
        print("[会话已重置]")


# ============================================================
# 作为子Agent使用的封装
# ============================================================
class ZhugeAISubAgent:
    """
    将诸葛小T封装为子Agent，供你的Agent系统调用

    使用方式：
        # 方式1：SSO自动登录（推荐）
        sub_agent = ZhugeAISubAgent()
        sub_agent.login_by_sso()
        answer = sub_agent.query("深信服防火墙怎么配置？")

        # 方式2：从浏览器提取token
        sub_agent = ZhugeAISubAgent()
        sub_agent.login_with_token("你的token")
    """

    def __init__(self, mobile: str = None, password: str = None):
        self.client = ZhugeAIClient(mobile, password)
        self._connected = False

    def login_by_sso(self, bbs_username: str = None, bbs_password: str = None) -> bool:
        """社区SSO自动登录（推荐）"""
        success = self.client.login_by_sso(bbs_username, bbs_password)
        if success:
            self.client.connect_ws()
            self._connected = True
        return success

    def login_by_password(self) -> bool:
        """手机号密码登录（部分账号不可用）"""
        success = self.client.login_by_password()
        if success:
            self.client.connect_ws()
            self._connected = True
        return success

    def login_with_token(self, token: str, tenant_id: str = None):
        """使用已有token登录"""
        self.client.login_with_token(token, tenant_id)
        self.client.connect_ws()
        self._connected = True

    def query(self, question: str, context: list = None) -> dict:
        """
        向诸葛小T提问
        返回：{
            "answer": "回答内容",
            "reasoning": "思考过程",
            "references": [{"id":"1","title":"...","content":"..."}],
            "faqSessionId": "会话ID",
            "complete": True/False,
            "intent": "意图分类",
        }
        """
        if not self._connected:
            return {"answer": "未登录，请先调用login_by_sso()或login_with_token()", "references": [], "complete": False}

        result = self.client.ask_full(question)
        return result

    def close(self):
        """关闭连接"""
        self.client.close_ws()


# ============================================================
# 使用示例
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("诸葛小T API客户端测试")
    print("=" * 60)

    # 方式1：SSO自动登录（推荐）
    print("\n[方式1] 社区SSO自动登录")
    client = ZhugeAIClient()
    if client.login_by_sso():
        client.connect_ws()
        time.sleep(2)
        client.send_message("深信服防火墙怎么配置端口映射？")
        time.sleep(15)
        client.close_ws()

    # 方式2：从浏览器提取token
    # print("\n[方式2] Token登录")
    # client = ZhugeAIClient()
    # client.login_with_token("你的token")
    # client.connect_ws()
    # client.send_message("测试")
    # time.sleep(10)
    # client.close_ws()

    # 方式3：作为子Agent使用
    # print("\n[方式3] 子Agent模式")
    # sub_agent = ZhugeAISubAgent()
    # if sub_agent.login_by_sso():
    #     result = sub_agent.query("深信服AC怎么配置？")
    #     print(f"回答：{result['answer']}")
    #     sub_agent.close()

    print("\n" + "=" * 60)
    print("提示：如果WebSocket没有收到回答，可能需要根据实际抓包")
    print("调整 ask() 方法中的回答解析逻辑。")
    print("抓包方法：F12 → Network → WS → transferSocket/chat → Messages")
    print("=" * 60)
