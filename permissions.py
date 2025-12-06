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
            group_id = event.get_group_id()
            if not group_id or not str(group_id).strip():
                logger.debug("群组ID为空，返回未知权限")
                return PermLevel.UNKNOWN
                
            if not user_id or not str(user_id).strip():
                logger.debug("用户ID为空，返回未知权限")
                return PermLevel.UNKNOWN

            try:
                group_id = int(str(group_id).strip())
                user_id = int(str(user_id).strip())
            except ValueError as e:
                logger.error(f"ID转换失败: group_id={group_id}, user_id={user_id}, error={str(e)}")
                return PermLevel.UNKNOWN

            if group_id == 0 or user_id == 0:
                return PermLevel.UNKNOWN

            if str(user_id) in self.superusers:
                return PermLevel.SUPERUSER

            try:
                info = await event.bot.get_group_member_info(
                    group_id=group_id, 
                    user_id=user_id, 
                    no_cache=True
                )
            except Exception as e:
                logger.error(f"获取群成员信息失败: {str(e)}\n{traceback.format_exc()}")
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
            logger.error(f"权限检查过程中发生错误: {str(e)}\n{traceback.format_exc()}")
            return PermLevel.UNKNOWN