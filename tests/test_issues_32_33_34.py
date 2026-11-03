"""针对 Issue #32、#33、#34 的回归单元测试。"""
import ast
import logging
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]


class FakeAt:
    def __init__(self, qq: str, name: str = ""):
        self.qq = qq
        self.name = name


class FakePlain:
    def __init__(self, text: str = ""):
        self.text = text


class FakeEvent:
    def __init__(self, sender_id="10001", umo="aiocqhttp:GroupMessage:99999", self_id="88888", components=None):
        self._sender_id = sender_id
        self.unified_msg_origin = umo
        self.message_obj = types.SimpleNamespace(
            self_id=self_id,
            message=components or [],
        )

    def get_sender_id(self):
        return self._sender_id

    def get_group_id(self):
        return "99999"


class Issues32To34Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        api = types.ModuleType("astrbot.api")
        api.logger = logging.getLogger("issues-test")
        patcher = patch.dict(sys.modules, {"astrbot.api": api, "astrbot": types.ModuleType("astrbot")})
        patcher.start()
        self.addCleanup(patcher.stop)

        # 提取 main.py 中的目标方法进行隔离测试
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        target_names = {
            "_get_session_id",
            "_is_shared_session",
            "_resolve_proactive_target_umo",
            "_extract_mentioned_users",
            "_get_level_name",
            "_build_mentioned_users_context",
            "_get_initial_favour",
        }
        methods = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in target_names
        ]

        utils_ns = {}
        exec(compile((ROOT / "utils.py").read_text(encoding="utf-8"), str(ROOT / "utils.py"), "exec"), utils_ns)

        from typing import Optional, List, Tuple, Dict, Any
        ns = {
            "Optional": Optional,
            "List": List,
            "Tuple": Tuple,
            "Dict": Dict,
            "Any": Any,
            "AstrMessageEvent": FakeEvent,
            "FavourRecord": types.SimpleNamespace,
            "At": FakeAt,
            "Plain": FakePlain,
            "is_valid_userid": utils_ns["is_valid_userid"],
            "PermLevel": types.SimpleNamespace(OWNER=3, ADMIN=2, SUPERUSER=4),
        }
        mod = ast.Module(body=methods, type_ignores=[])
        exec(compile(mod, "main.py", "exec"), ns)
        self.ns = ns

    def _make_plugin_stub(self, is_global=True):
        stub = types.SimpleNamespace(
            is_global_favour=is_global,
            _platform_cache={"aiocqhttp": {"self_id": "88888"}},
            default_relationship="陌生人",
            max_favour_value=100,
            min_favour_value=-100,
            default_favour=0,
            admin_default_favour=15,
            favour_envoys=[],
            favour_levels=[
                {"min": -100, "max": -50, "name": "厌恶", "desc": ""},
                {"min": -49, "max": 49, "name": "中立", "desc": ""},
                {"min": 50, "max": 100, "name": "喜欢", "desc": ""},
            ],
        )
        stub._get_session_id = types.MethodType(self.ns["_get_session_id"], stub)
        stub._is_shared_session = self.ns["_is_shared_session"]
        stub._resolve_proactive_target_umo = types.MethodType(self.ns["_resolve_proactive_target_umo"], stub)
        stub._extract_mentioned_users = types.MethodType(self.ns["_extract_mentioned_users"], stub)
        stub._get_level_name = types.MethodType(self.ns["_get_level_name"], stub)
        stub._build_mentioned_users_context = types.MethodType(self.ns["_build_mentioned_users_context"], stub)
        stub._get_initial_favour = types.MethodType(self.ns["_get_initial_favour"], stub)
        stub._check_permission = AsyncMock(return_value=False)
        stub._get_user_display_name = AsyncMock(side_effect=lambda evt, uid: f"昵称_{uid}")
        return stub

    def test_issue32_global_session_id_and_proactive_umo_splicing(self):
        stub = self._make_plugin_stub(is_global=True)
        event = FakeEvent(sender_id="123456", umo="aiocqhttp:GroupMessage:99999")

        # 1. 全局模式下记录为 platform:uid（如 aiocqhttp:123456）
        sid = stub._get_session_id(event)
        self.assertEqual(sid, "aiocqhttp:123456")
        self.assertTrue(stub._is_shared_session(sid))

        # 指定 target_uid 时（如管理员操作或 @ 他人）记录为 aiocqhttp:target_uid
        sid_target = stub._get_session_id(event, target_uid="654321")
        self.assertEqual(sid_target, "aiocqhttp:654321")

        # 2. 主动搭话时将 FriendMessage 插入其中进行拼接
        rec_new = types.SimpleNamespace(session_id="aiocqhttp:123456", user_id="123456")
        self.assertEqual(stub._resolve_proactive_target_umo(rec_new), "aiocqhttp:FriendMessage:123456")

        # 兼容旧版纯平台名记录（如 aiocqhttp）
        rec_legacy = types.SimpleNamespace(session_id="aiocqhttp", user_id="123456")
        self.assertEqual(stub._resolve_proactive_target_umo(rec_legacy), "aiocqhttp:FriendMessage:123456")

        # 独立会话（非全局）保持原样
        rec_group = types.SimpleNamespace(session_id="aiocqhttp:GroupMessage:99999", user_id="123456")
        self.assertFalse(stub._is_shared_session(rec_group.session_id))
        self.assertEqual(stub._resolve_proactive_target_umo(rec_group), "aiocqhttp:GroupMessage:99999")

    async def test_issue32_storage_is_shared_session_id(self):
        tree = ast.parse((ROOT / "storage.py").read_text(encoding="utf-8"))
        method = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "is_shared_session_id"
        )
        # 移除 @staticmethod 装饰器以便直接编译调用
        method.decorator_list = []
        from typing import Optional
        ns = {"Optional": Optional}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "storage.py", "exec"), ns)
        is_shared = ns["is_shared_session_id"]

        self.assertTrue(is_shared("global"))
        self.assertTrue(is_shared("aiocqhttp"))
        self.assertTrue(is_shared("aiocqhttp:123456"))
        self.assertFalse(is_shared("aiocqhttp:FriendMessage:123456"))
        self.assertFalse(is_shared("aiocqhttp:GroupMessage:99999"))

    def test_issue33_webui_switch_active_class_and_css(self):
        app_js = (ROOT / "pages" / "settings" / "app.js").read_text(encoding="utf-8")
        style_css = (ROOT / "pages" / "settings" / "style.css").read_text(encoding="utf-8")

        # 确认 app.js 在 checkbox 变化时同步切换 switch-card 的 active 类
        self.assertIn("card.classList.toggle('active', el.checked)", app_js)
        # 确认 style.css 不再让未勾选的 .switch-card.active 强制覆盖关闭状态
        self.assertNotIn("\n.switch-card.active .switch-ctrl {", style_css)
        self.assertIn('input[type="checkbox"]:checked + .switch-card .switch-ctrl', style_css)

    async def test_issue34_mentioned_users_favour_context(self):
        stub = self._make_plugin_stub(is_global=False)
        records_map = {
            ("20002", "aiocqhttp:GroupMessage:99999"): types.SimpleNamespace(
                user_id="20002",
                session_id="aiocqhttp:GroupMessage:99999",
                favour=-80,
                relationship="",
                username="Alice",
            ),
        }
        stub.db_manager = types.SimpleNamespace(
            get_favour=AsyncMock(side_effect=lambda uid, sid: records_map.get((uid, sid)))
        )

        # 构造包含 @Bot、@发送者自己、@全体、重复@Alice、以及无记录用户 @Bob 的消息
        components = [
            FakeAt(qq="88888", name="Bot"),        # Bot 自身 -> 应忽略
            FakePlain(" 你觉得 "),
            FakeAt(qq="20002", name="@Alice"),     # 被提及用户 Alice（好感 -80）
            FakeAt(qq="20002", name="Alice"),      # 重复提及 -> 应去重
            FakeAt(qq="10001", name="Charlie"),    # 发送者自身 -> 应忽略
            FakeAt(qq="all", name="全体成员"),      # @全体 -> 应忽略
            FakeAt(qq="30003", name=""),           # 无数据库记录的新用户 Bob
            FakePlain(" 怎么样"),
        ]
        event = FakeEvent(
            sender_id="10001",
            umo="aiocqhttp:GroupMessage:99999",
            self_id="88888",
            components=components,
        )

        extracted = stub._extract_mentioned_users(event, "10001")
        self.assertEqual(extracted, [("20002", "Alice"), ("30003", "")])

        ctx_text = await stub._build_mentioned_users_context(event, "10001")
        lines = ctx_text.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("用户:Alice | UID:20002 | 好感:-80/100 | 等级:厌恶 | 该用户是你的陌生人", lines[0])
        self.assertIn("用户:昵称_30003 | UID:30003 | 好感:0/100 | 等级:中立 | 该用户是你的陌生人", lines[1])


if __name__ == "__main__":
    unittest.main()
