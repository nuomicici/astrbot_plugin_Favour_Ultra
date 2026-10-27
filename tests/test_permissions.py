"""权限回归测试；使用最小事件替身，无需启动 AstrBot 或 NapCat。"""
import ast
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]


class FakeEvent:
    def __init__(self, admin=False, group_id="684601136", info=None):
        self.admin = admin
        self.group_id = group_id
        self.bot = types.SimpleNamespace(
            get_group_member_info=AsyncMock(return_value=info)
        )

    def is_admin(self):
        return self.admin

    def get_sender_id(self):
        return "1668223794"

    def get_group_id(self):
        return self.group_id


class FakeOneBotEvent(FakeEvent):
    pass


class PermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        api = types.ModuleType("astrbot.api")
        api.logger = logging.getLogger("permissions-test")
        adapter_path = "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"
        adapter = types.ModuleType(adapter_path)
        adapter.AiocqhttpMessageEvent = FakeOneBotEvent
        modules = {"astrbot.api": api, adapter_path: adapter}
        for name in tuple(modules):
            parts = name.split(".")
            for end in range(1, len(parts)):
                parent = ".".join(parts[:end])
                modules.setdefault(parent, types.ModuleType(parent))
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

        namespace = {}
        source = (ROOT / "permissions.py").read_text(encoding="utf-8")
        exec(compile(source, str(ROOT / "permissions.py"), "exec"), namespace)
        self.level = namespace["PermLevel"]
        self.manager = namespace["PermissionManager"].get_instance()

        # 加载真实权限入口，避免导入 main.py 的数据库、渲染和 LLM 依赖。
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        method = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_check_permission"
        )
        namespace["AstrMessageEvent"] = FakeEvent
        exec(compile(ast.Module(body=[method], type_ignores=[]), "main.py", "exec"), namespace)
        self.check = types.MethodType(namespace["_check_permission"], types.SimpleNamespace())

    async def test_framework_admin_passes_without_default_admin_list(self):
        for event_type in (FakeEvent, FakeOneBotEvent):
            for group_id in ("", "684601136"):
                event = event_type(admin=True, group_id=group_id)
                for required in (self.level.ADMIN, self.level.OWNER, self.level.SUPERUSER):
                    self.assertTrue(await self.check(event, required))
                event.bot.get_group_member_info.assert_not_awaited()

    async def test_owner_cannot_run_bot_admin_commands(self):
        event = FakeOneBotEvent(info={"role": "owner", "level": ""})
        self.assertFalse(await self.check(event, self.level.SUPERUSER))
        event.bot.get_group_member_info.assert_not_awaited()

    async def test_admin_revocation_is_not_cached(self):
        event = FakeOneBotEvent(admin=True)
        self.assertTrue(await self.check(event, self.level.SUPERUSER))
        event.admin = False
        self.assertFalse(await self.check(event, self.level.SUPERUSER))

    async def test_group_roles_ignore_invalid_level(self):
        for role, required in (("owner", self.level.OWNER), ("admin", self.level.ADMIN)):
            for value in ("", None, "not-a-number"):
                with self.subTest(role=role, level=value):
                    event = FakeOneBotEvent(info={"role": role, "level": value})
                    self.assertTrue(await self.check(event, required))
                    if role == "admin":
                        self.assertFalse(await self.check(event, self.level.OWNER))

    async def test_members_do_not_gain_admin_permissions(self):
        for value in ("", None, "not-a-number", "60"):
            event = FakeOneBotEvent(info={"role": "member", "level": value})
            self.assertFalse(await self.check(event, self.level.ADMIN))
            expected = self.level.HIGH if value == "60" else self.level.MEMBER
            self.assertEqual(await self.manager.get_perm_level(event, event.get_sender_id()), expected)

    async def test_api_failure_denies_and_logs(self):
        event = FakeOneBotEvent()
        event.bot.get_group_member_info.side_effect = RuntimeError("NapCat unavailable")
        with self.assertLogs("permissions-test", level="WARNING") as logs:
            self.assertFalse(await self.check(event, self.level.OWNER))
        self.assertIn("NapCat unavailable", logs.output[0])

    async def test_non_admin_private_and_other_platform_are_denied(self):
        for event in (FakeOneBotEvent(group_id=""), FakeEvent()):
            self.assertFalse(await self.check(event, self.level.ADMIN))
            event.bot.get_group_member_info.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
