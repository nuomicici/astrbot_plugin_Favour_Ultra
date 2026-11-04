"""针对 Issue #32、#33、#34 及旧版 global/适配器别名兼容性的回归单元测试。"""
import ast
import importlib
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]

try:
    import aiofiles  # noqa: F401
    import aiosqlite  # noqa: F401
    import greenlet  # noqa: F401
    import sqlmodel  # noqa: F401
    HAS_DB_DEPS = True
except ImportError:
    HAS_DB_DEPS = False


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
        stub._get_known_platform_ids = lambda: {"aiocqhttp"}
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

        # 兼容旧版纯平台名记录（如 aiocqhttp）及已改名孤儿别名（单平台下自动映射）
        rec_legacy = types.SimpleNamespace(session_id="aiocqhttp", user_id="123456")
        self.assertEqual(stub._resolve_proactive_target_umo(rec_legacy), "aiocqhttp:FriendMessage:123456")
        rec_alias = types.SimpleNamespace(session_id="涩涩号", user_id="123456")
        self.assertEqual(stub._resolve_proactive_target_umo(rec_alias), "aiocqhttp:FriendMessage:123456")
        rec_global = types.SimpleNamespace(session_id="global", user_id="123456")
        self.assertEqual(stub._resolve_proactive_target_umo(rec_global), "aiocqhttp:FriendMessage:123456")

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
        method.decorator_list = []
        from typing import Optional
        ns = {"Optional": Optional}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "storage.py", "exec"), ns)
        is_shared = ns["is_shared_session_id"]

        self.assertTrue(is_shared("global"))
        self.assertTrue(is_shared("aiocqhttp"))
        self.assertTrue(is_shared("涩涩号"))
        self.assertTrue(is_shared("aiocqhttp:123456"))
        self.assertFalse(is_shared("aiocqhttp:FriendMessage:123456"))
        self.assertFalse(is_shared("aiocqhttp:GroupMessage:99999"))

    @unittest.skipUnless(HAS_DB_DEPS, "需要 sqlmodel/aiosqlite/aiofiles 依赖")
    async def test_legacy_global_and_alias_claim_and_migration_zero_loss(self):
        pkg = types.ModuleType("astrbot_plugin_Favour_Ultra")
        pkg.__path__ = [str(ROOT)]
        with patch.dict(sys.modules, {"astrbot_plugin_Favour_Ultra": pkg}):
            storage_mod = importlib.import_module("astrbot_plugin_Favour_Ultra.storage")
            FavourDBManager = storage_mod.FavourDBManager

            with tempfile.TemporaryDirectory() as tmpdir:
                db = FavourDBManager(Path(tmpdir), min_val=-200, max_val=1000)
                db.platform_resolver = lambda: {"aiocqhttp", "telegram"}
                await db.init_db()

                # 1. 旧版 global 记录：首次通过 aiocqhttp:10001 查询时应完整认领（好感+关系+唯一性）
                await db.update_favour("10001", "global", favour=320, relationship="恋人", is_unique=True)
                claimed_g = await db.get_favour("10001", "aiocqhttp:10001")
                self.assertIsNotNone(claimed_g)
                self.assertEqual(claimed_g.session_id, "aiocqhttp:10001")
                self.assertEqual(claimed_g.favour, 320)
                self.assertEqual(claimed_g.relationship, "恋人")
                self.assertTrue(claimed_g.is_unique)

                # 2. 旧版适配器别名记录（如 '涩涩号'，不再属于存活平台列表）：
                #    通过当前平台 'aiocqhttp:20002' 查询或更新时应自动认领，不归零
                await db.update_favour("20002", "涩涩号", favour=180, relationship="挚友", is_unique=False)
                claimed_alias = await db.get_favour("20002", "aiocqhttp:20002")
                self.assertIsNotNone(claimed_alias)
                self.assertEqual(claimed_alias.session_id, "aiocqhttp:20002")
                self.assertEqual(claimed_alias.favour, 180)
                self.assertEqual(claimed_alias.relationship, "挚友")

                # 3. 另一存活平台（如 'telegram:30003'）的数据绝不能被 'aiocqhttp' 误认领
                await db.update_favour("30003", "telegram:30003", favour=95, relationship="搭档")
                cross_rec = await db.get_favour("30003", "aiocqhttp:30003")
                self.assertIsNone(cross_rec)

                # 4. /查询当前好感度 使用的 get_all_in_session("aiocqhttp") 应包含未认领的 global 与孤儿别名用户
                await db.update_favour("40004", "global", favour=260, relationship="青梅竹马")
                await db.update_favour("50005", "default", favour=150, relationship="损友")
                plat_view = await db.get_all_in_session("aiocqhttp")
                by_uid = {r.user_id: r for r in plat_view}
                self.assertIn("10001", by_uid)
                self.assertIn("20002", by_uid)
                self.assertIn("40004", by_uid)
                self.assertEqual(by_uid["40004"].favour, 260)
                self.assertIn("50005", by_uid)
                self.assertEqual(by_uid["50005"].favour, 150)
                self.assertNotIn("30003", by_uid)  # telegram 存活平台用户不混入 aiocqhttp 视图

                # 5. 修复已在 v4.4.12 被重置为初值的用户：
                #    假设用户 60006 在 global 有 500 好感/老婆，但升级后发消息新建了 aiocqhttp:60006 (好感 2, 关系空)
                await db.update_favour("60006", "global", favour=500, relationship="老婆", is_unique=True)
                await db.update_favour("60006", "aiocqhttp:60006", favour=2, relationship="", is_unique=False)
                # 执行单平台或双平台自动迁移修复
                migrated, _ = await db.auto_migrate_global_to_adapters(known_platforms={"aiocqhttp"})
                self.assertGreater(migrated, 0)
                repaired = await db.get_favour("60006", "aiocqhttp:60006")
                self.assertIsNotNone(repaired)
                self.assertEqual(repaired.favour, 500)
                self.assertEqual(repaired.relationship, "老婆")
                self.assertTrue(repaired.is_unique)

                # 验证迁移前自动生成了整库备份快照
                backups = await db.list_backups()
                self.assertTrue(any(b["filename"].startswith("pre_migrate_") for b in backups))

                await db.engine.dispose()

    def test_issue33_webui_switch_active_class_and_css(self):
        app_js = (ROOT / "pages" / "settings" / "app.js").read_text(encoding="utf-8")
        style_css = (ROOT / "pages" / "settings" / "style.css").read_text(encoding="utf-8")

        self.assertIn("card.classList.toggle('active', el.checked)", app_js)
        self.assertNotIn("\n.switch-card.active .switch-ctrl {", style_css)
        self.assertIn('input[type="checkbox"]:checked + .switch-card .switch-ctrl', style_css)

        # 验证数据中心黄色提醒框与快速迁移指引、未保存改动回退检测与版本号 4.4.13
        self.assertIn('id="legacy-global-alert"', app_js)
        self.assertIn('jumpToLegacyMigrate', app_js)
        self.assertIn('.legacy-global-alert', style_css)
        self.assertIn('function isConfigEqual(a, b)', app_js)
        self.assertIn('function syncDirtyState()', app_js)
        meta_text = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        self.assertIn("version: v4.4.13", meta_text)

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
