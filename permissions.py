import traceback
from typing import Optional, List
from astrbot.api import logger
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

class PermLevel:
    """权限级别枚举类"""
    UNKNOWN = -1
    MEMBER = 0
    HIGH = 1
    ADMIN = 2
    OWNER = 3
    SUPERUSER = 4

class PermissionManager:
    """权限管理器单例类"""
    _instance: Optional["PermissionManager"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        superusers: Optional[List[str]] = None,
        level_threshold: int = 50,
    ):
        if self._initialized:
            return
        self.superusers = superusers or []
        self.level_threshold = level_threshold
        self._initialized = True

    @classmethod
    def get_instance(
        cls,
        superusers: Optional[List[str]] = None,
        level_threshold: int = 50,
    ) -> "PermissionManager":
        if cls._instance is None:
            cls._instance = cls(
                superusers=superusers,
                level_threshold=level_threshold,
            )
        return cls._instance

    async def get_perm_level(
        self, event: AiocqhttpMessageEvent, user_id: str | int
    ) -> int:
        """获取用户在群内的权限级别"""
        try:
            # 检查超级用户
            if str(user_id) in self.superusers:
                return PermLevel.SUPERUSER

            group_id = event.get_group_id()
            if not group_id:
                # 私聊或其他情况，默认为 MEMBER，除非是超级用户
                return PermLevel.MEMBER
                
            if not user_id:
                return PermLevel.UNKNOWN

            # 尝试转换为 int，因为 OneBot 协议通常使用 int
            # 但为了兼容性，如果转换失败则保持原样或返回 UNKNOWN
            try:
                group_id_int = int(str(group_id).strip())
                user_id_int = int(str(user_id).strip())
            except ValueError:
                # 非数字ID，无法通过 get_group_member_info 获取信息
                # 这里可以扩展其他平台的获取方式，目前暂返回 MEMBER
                return PermLevel.MEMBER

            try:
                info = await event.bot.get_group_member_info(
                    group_id=group_id_int, 
                    user_id=user_id_int, 
                    no_cache=True
                )
            except Exception as e:
                # 获取失败（可能不在群里），返回 UNKNOWN
                return PermLevel.UNKNOWN

            role = info.get("role", "unknown")
            level = int(info.get("level", 0))

            if role == "owner":
                return PermLevel.OWNER
            elif role == "admin":
                return PermLevel.ADMIN
            elif role == "member":
                return PermLevel.HIGH if level >= self.level_threshold else PermLevel.MEMBER
            else:
                return PermLevel.UNKNOWN

        except Exception as e:
            logger.error(f"权限检查过程中发生错误: {str(e)}")
            return PermLevel.UNKNOWN