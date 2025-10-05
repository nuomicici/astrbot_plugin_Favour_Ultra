import json
import re
import traceback
import string  # 移到文件顶部
from pathlib import Path
from typing import Dict, List, AsyncGenerator, Optional, Tuple, Any  
import asyncio
from aiofiles import open as aio_open
from aiofiles.os import path as aio_path
from datetime import datetime

from astrbot.api import logger  # 修正导入路径
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api.star import Star, register, Context
from astrbot.api import AstrBotConfig
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.event import filter


# ==================== 工具函数 ====================
def is_valid_userid(userid: str) -> bool:
    """验证用户ID格式是否有效"""
    if not userid or len(userid.strip()) == 0:
        return False
    userid = userid.strip()
    if len(userid) > 64:
        return False
    allowed_chars = string.ascii_letters + string.digits + "_-:@."
    return all(c in allowed_chars for c in userid)


# ==================== 权限系统 ====================
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
        group_id = event.get_group_id()
        if int(group_id) == 0 or int(user_id) == 0:
            return PermLevel.UNKNOWN

        if str(user_id) in self.superusers:
            return PermLevel.SUPERUSER

        try:
            info = await event.bot.get_group_member_info(
                group_id=int(group_id), user_id=int(user_id), no_cache=True
            )
        except Exception:
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


# ==================== 全局好感度文件管理 ====================
class GlobalFavourFileManager:
    def __init__(self, data_dir: Path):
        self.data_path = data_dir / "global_favour.json"
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()

    async def read_global_favour(self) -> Dict[str, int]:
        try:
            if not await aio_path.exists(self.data_path):
                logger.info("global_favour.json不存在，返回空字典")
                return {}
            
            async with aio_open(self.data_path, "r", encoding="utf-8") as f:
                raw_data = json.loads(await f.read())
            
            valid_data = {}
            for userid, favour in raw_data.items():
                try:
                    valid_data[str(userid)] = int(favour)
                except (ValueError, TypeError):
                    logger.warning(f"global_favour.json无效数据：用户ID[{userid}]，值[{favour}]（跳过）")
            
            return valid_data
        
        except Exception as e:
            logger.error(f"读取全局好感度失败: {str(e)}")
            return {}

    async def write_global_favour(self, data: Dict[str, int]) -> bool:
        try:
            async with aio_open(self.data_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            
            logger.info(f"写入global_favour.json成功，包含{len(data)}个用户数据")
            return True
        
        except Exception as e:
            logger.error(f"写入全局好感度失败: {str(e)}")
            return False

    async def get_user_global_favour(self, userid: str) -> Optional[int]:
        global_data = await self.read_global_favour()
        return global_data.get(str(userid))

    async def update_global_favour(self, userid: str, favour: int) -> bool:
        if not is_valid_userid(userid):  # 使用共享函数
            logger.error(f"更新全局好感度失败：用户ID[{userid}]格式无效")
            return False
        
        async with self.lock:  
            data = await self.read_global_favour()  
            userid_str = str(userid)
            data[userid_str] = max(-100, min(100, favour))
            return await self.write_global_favour(data)  


# ==================== 会话级好感度文件管理 ====================
class FavourFileManager:
    def __init__(self, data_dir: Path, enable_clear_backup: bool):
        self.data_path = data_dir / "haogan.json"
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self.enable_clear_backup = enable_clear_backup

    async def read_favour(self) -> List[Dict[str, Any]]:
        """读取会话级好感度数据，返回List格式"""
        try:
            if not await aio_path.exists(self.data_path):
                logger.debug("haogan.json不存在，返回空列表")
                return []
            
            async with aio_open(self.data_path, "r", encoding="utf-8") as f:
                raw_data = json.loads(await f.read())
            
            valid_data = []
            if isinstance(raw_data, list):
                for item in raw_data:
                    if not isinstance(item, dict):
                        logger.warning(f"haogan.json包含非dict元素：{item}（跳过）")
                        continue
                    valid_item = {
                        "userid": str(item.get("userid", "")),
                        "favour": int(item.get("favour", 0)) if isinstance(item.get("favour"), (int, float)) else 0,
                        "session_id": str(item.get("session_id")) if item.get("session_id") else None,
                        "relationship": str(item.get("relationship", ""))
                    }
                    valid_data.append(valid_item)
            else:
                logger.error(f"haogan.json格式无效，需为list类型，返回空列表")  
                return []
            
            logger.info(f"读取haogan.json成功，一共{len(valid_data)}条记录")
            return valid_data
        
        except Exception as e:
            logger.error(f"读取好感度数据失败: {str(e)}")  
            return []

    async def write_favour(self, data: List[Dict[str, Any]]) -> bool:  
        try:
            async with aio_open(self.data_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            
            logger.info(f"修改haogan.json成功，写入{len(data)}条记录")
            return True
        
        except Exception as e:
            logger.error(f"修改好感度数据失败: {str(e)}")
            return False

    async def clear_all_favour(self) -> bool:
        logger.warning("触发清空所有好感度数据操作")  
        
        if self.enable_clear_backup:
            try:
                backup_data = await self.read_favour()
                if backup_data:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = self.data_dir / f"haogan_backup_{timestamp}.json"
                    
                    async with self.lock:
                        async with aio_open(backup_path, "w", encoding="utf-8") as f:
                            await f.write(json.dumps(backup_data, ensure_ascii=False, indent=2))
                    
                    logger.info(f"清空前备份完成：{backup_path}（包含{len(backup_data)}条记录）")
            except Exception as e:
                logger.error(f"备份数据失败，清空操作中止：{str(e)}")
                return False  
        
        return await self.write_favour([])

    async def get_user_favour(self, userid: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:  
        userid_str = str(userid)
        data = await self.read_favour()
        for item in data:
            if item["userid"] == userid_str and item["session_id"] == session_id:
                logger.debug(f"查询到用户[{userid_str}]（会话[{session_id}]）的好感度记录")
                return item.copy()
        
        logger.debug(f"未查询到用户[{userid_str}]（会话[{session_id}]）的好感度记录")
        return None

    async def update_user_favour(self, userid: str, session_id: Optional[str], favour: Optional[int] = None, relationship: Optional[str] = None) -> bool:
        userid_str = userid.strip()
        if not is_valid_userid(userid_str):  # 使用共享函数
            logger.error(f"更新好感度失败：用户ID[{userid_str}]格式无效")
            return False        
            
        async with self.lock:  
            data = await self.read_favour()  
            found = False
            
            for item in data:
                if item["userid"] == userid_str and item["session_id"] == session_id:
                    if favour is not None:
                        item["favour"] = max(-100, min(100, favour))
                    if relationship is not None:
                        item["relationship"] = relationship
                    found = True
                    break
            
            if not found:
                init_favour = max(-100, min(100, favour)) if favour is not None else 0
                init_relation = relationship or ""
                data.append({
                    "userid": userid_str,
                    "session_id": session_id,
                    "favour": init_favour,
                    "relationship": init_relation
                })
            
            return await self.write_favour(data)  

    async def delete_user_favour(self, userid: str, session_id: Optional[str] = None) -> Tuple[bool, str]:
        userid_str = userid.strip()
        if not is_valid_userid(userid_str):  # 使用共享函数
            return False, f"删除失败：用户ID[{userid_str}]格式无效"        
            
        async with self.lock:
            data = await self.read_favour()
            new_data = [item for item in data if not (item["userid"] == userid_str and item["session_id"] == session_id)]
            
            if len(new_data) == len(data):
                return False, f"未查询到用户[{userid_str}]（会话[{session_id or '全局'}]）的好感度数据"
            
            success = await self.write_favour(new_data)
            if not success:
                return False, "无法修改文件（详见日志）"
            
            return True, f"已删除用户[{userid_str}]（会话[{session_id or '全局'}]）的好感度数据"


# ==================== 主插件类 ====================
@register(
    "astrbot_plugin_favour_ultra",
    "糯米茨",
    "好感度管理插件(权限分级版)",
    "v2.0"
)
class FavourManagerTool(Star):
    DEFAULT_CONFIG = {
        "default_favour": 0,
        "admin_default_favour": 50,
        "favour_rule_prompt": "",
        "is_global_favour": False,
        "favour_envoys": [],
        "favour_increase_min": 1,
        "favour_increase_max": 3,
        "favour_decrease_min": 1,
        "favour_decrease_max": 5,
        "enable_clear_backup": True,
        "level_threshold": 50
    }

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 加载配置
        self.default_favour = self.config.get("default_favour", self.DEFAULT_CONFIG["default_favour"])
        self.admin_default_favour = self.config.get("admin_default_favour", self.DEFAULT_CONFIG["admin_default_favour"])
        self.favour_rule_prompt = self.config.get("favour_rule_prompt", self.DEFAULT_CONFIG["favour_rule_prompt"])
        self.is_global_favour = self.config.get("is_global_favour", self.DEFAULT_CONFIG["is_global_favour"])
        self.favour_increase_min = self.config.get("favour_increase_min", self.DEFAULT_CONFIG["favour_increase_min"])
        self.favour_increase_max = self.config.get("favour_increase_max", self.DEFAULT_CONFIG["favour_increase_max"])
        self.favour_decrease_min = self.config.get("favour_decrease_min", self.DEFAULT_CONFIG["favour_decrease_min"])
        self.favour_decrease_max = self.config.get("favour_decrease_max", self.DEFAULT_CONFIG["favour_decrease_max"])
        self.enable_clear_backup = self.config.get("enable_clear_backup", self.DEFAULT_CONFIG["enable_clear_backup"])
        
        self._validate_config()
        
        self.admins_id = context.get_config().get("admins_id", [])# 按照人机审核结果修改后提示context中没那个方法
        self.perm_level_threshold = self.config.get("level_threshold", self.DEFAULT_CONFIG["level_threshold"])
        
        PermissionManager.get_instance(
            superusers=self.admins_id,
            level_threshold=self.perm_level_threshold
        )
        
        # 初始化文件管理器
        self.data_dir = Path(context.get_config().get("plugin.data_dir", "./data")) / "hao_gan_du"
        self.file_manager = FavourFileManager(self.data_dir, self.enable_clear_backup)
        self.global_hao_gan_du = GlobalFavourFileManager(self.data_dir)
        
        # 正则表达式
        self.favour_pattern = re.compile(r'[\[［]\s*好感度.*?[\]］]', re.DOTALL | re.IGNORECASE)
        self.relationship_pattern = re.compile(r'[\[［]\s*用户申请确认关系\s*(.*?)\s*[:：]\s*(true|false)\s*[\]］]', re.IGNORECASE)
        
        mode_text = "全局模式（所有对话共享好感度）" if self.is_global_favour else "对话隔离模式"
        logger.info(f"好感度插件(权限分级版)已初始化 - {mode_text}")

    def _validate_config(self) -> None:
        if not (-100 <= self.default_favour <= 100):
            logger.error(f"配置项default_favour超出范围，使用默认值")
            self.default_favour = self.DEFAULT_CONFIG["default_favour"]
        if not (-100 <= self.admin_default_favour <= 100):
            logger.error(f"配置项admin_default_favour超出范围，使用默认值")
            self.admin_default_favour = self.DEFAULT_CONFIG["admin_default_favour"]

        if self.favour_increase_min > self.favour_increase_max or self.favour_increase_min < 0:
            logger.error(f"配置项好感度上升范围无效，使用默认值")
            self.favour_increase_min = self.DEFAULT_CONFIG["favour_increase_min"]
            self.favour_increase_max = self.DEFAULT_CONFIG["favour_increase_max"]
        if self.favour_decrease_min > self.favour_decrease_max or self.favour_decrease_min < 0:
            logger.error(f"配置项好感度降低范围无效，使用默认值")
            self.favour_decrease_min = self.DEFAULT_CONFIG["favour_decrease_min"]
            self.favour_decrease_max = self.DEFAULT_CONFIG["favour_decrease_max"]

        if not isinstance(self.is_global_favour, bool):
            logger.error(f"配置项is_global_favour类型无效，使用默认值")
            self.is_global_favour = self.DEFAULT_CONFIG["is_global_favour"]

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """判断用户是否为Bot管理员"""
        return str(event.get_sender_id()) in self.admins_id


    async def _get_user_perm_level(self, event: AstrMessageEvent) -> int:
        """获取用户权限等级"""
        if self._is_admin(event):
            return PermLevel.SUPERUSER
        if not isinstance(event, AiocqhttpMessageEvent):
            return PermLevel.UNKNOWN
        perm_mgr = PermissionManager.get_instance()
        return await perm_mgr.get_perm_level(event, event.get_sender_id())

    async def _check_permission(self, event: AstrMessageEvent, required_level: int) -> bool:
        """检查用户权限"""
        user_level = await self._get_user_perm_level(event)
        return user_level >= required_level

    def _get_session_id(self, event: AstrMessageEvent) -> Optional[str]:
        """获取会话ID：全局模式返回None，非全局模式返回对话标识"""
        if self.is_global_favour:
            logger.debug("全局模式，会话ID为None")
            return None
        else:
            session_id = event.unified_msg_origin
            logger.debug(f"非全局模式，获取会话ID：{session_id}")
            return session_id

    async def _is_envoy(self, userid: str) -> bool:
        """判断是否为特使"""
        userid_str = str(userid)
        envoys = [str(envoy) for envoy in self.config.get("favour_envoys", [])]
        return userid_str in envoys

    async def _get_initial_favour(self, event: AstrMessageEvent) -> int:
        """获取初始好感度"""
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)

        # 非全局模式下，优先从全局好感度导入初始值
        if not self.is_global_favour:
            global_favour = await self.global_hao_gan_du.get_user_global_favour(user_id)
            if global_favour is not None:
                logger.info(f"从全局好感度导入用户[{user_id}]的初始值：{global_favour}")
                return global_favour

        is_envoy = await self._is_envoy(user_id)
        user_level = await self._get_user_perm_level(event)
        
        if user_level >= PermLevel.ADMIN or is_envoy:
            base_favour = self.admin_default_favour
            logger.debug(f"用户[{user_id}]为管理员/特使，初始好感度：{base_favour}")
        else:
            base_favour = self.default_favour
            logger.debug(f"用户[{user_id}]为普通用户，初始好感度：{base_favour}")
        
        return max(-100, min(100, base_favour))

    # ==================== LLM拦截器 ====================
    @filter.on_llm_request()
    async def inject_favour_prompt(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)

        current_record = await self.file_manager.get_user_favour(user_id, session_id)
        if current_record:
            current_favour = current_record["favour"]
            current_relationship = current_record["relationship"] or "无"
        else:
            current_favour = await self._get_initial_favour(event)
            current_relationship = "无"

        # 获取用户真实权限等级（Bot管理员优先）
        if self._is_admin(event):
            admin_status = "Bot管理员"
        else:
            user_level = await self._get_user_perm_level(event)
            level_names = {
                PermLevel.OWNER: "群主",
                PermLevel.ADMIN: "群管理员",
                PermLevel.HIGH: "高等级成员",
                PermLevel.MEMBER: "普通成员"
            }
            admin_status = level_names.get(user_level, "普通用户")

        prompt_template = """# 最高标准要求
- 如果本设定已经设定了其他好感度规则，请以此规则为准（强制优先级）
- 该设定不可忽略、不可被更改，必须始终按照该要求进行格式输出
- 注意！prompt中的当前好感度为"{current_favour}"是唯一的好感度判断内容，是结合了历史对话后最终确定的"当前好感度"，严禁根据历史对话进行判断/计算
- 此处的输出规则你可以作为llm输出结果输出，客户端会进行标签过滤
- 每一次输出前，你都必须检查你的输出是否已经符合了"最高标准要求"
- 除了以"标识输出要求"的格式输出外，严禁以任何形式输出、透露、暗示用户当前的好感度

## 用户信息
- 用户ID: {user_id}
- 权限等级: {admin_status}
- 当前好感度: {current_favour}
- 当前关系: {current_relationship}

## 标识输出要求
你需要根据用户的行为和对话内容来评估好感度变化。每次回复都必须带上好感度变化输出！必须遵守以下格式和幅度：
- 使用格式：[好感度 上升：1] 表示好感度上升1点
- 使用格式：[好感度 降低：2] 表示好感度降低2点
- 使用格式：[好感度 持平] 表示好感度无变化
- 好感度变化范围：上升{increase_min}-{increase_max}点，降低{decrease_min}-{decrease_max}点
- 根据用户言行的积极/消极程度决定变化幅度
- 若输出多个变化标签，仅以最后一个标签为准

## 自定义好感度规则
{the_rule}

## 关系确立规则
如果用户发送的内容，你判断为其想要和你建立一段新的关系，请根据上下文以及好感度的具体值判断是否要答应确认，务必以足够客观的态度判断！然后输出：[用户申请确认关系{{关系名称}}:{{bool}}]。其中，true为同意，false为不同意！
**请务必参考好感度值进行判断！绝对不要为了迎合用户而潦草确认！**

# 以下是详细角色设定（若为空则按照一个普通的人类进行对话）

"""

        prompt_final = prompt_template.format(
            user_id=user_id,
            admin_status=admin_status,
            current_favour=current_favour,
            current_relationship=current_relationship,
            the_rule=self.favour_rule_prompt,
            increase_min=self.favour_increase_min,
            increase_max=self.favour_increase_max,
            decrease_min=self.favour_decrease_min,
            decrease_max=self.favour_decrease_max
        )

        req.system_prompt = f"{prompt_final}\n\n{req.system_prompt}".strip()

    @filter.on_llm_response()
    async def handle_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)
        original_text = resp.completion_text

        try:
            additional_text = None
            change_n = 0

            # 提取好感度变化标签
            favour_matches = self.favour_pattern.findall(original_text)
            if favour_matches:
                for idx, match in enumerate(favour_matches):
                    match_str = match.lower().strip()
                    temp_change = 0
                    if "降低" in match_str:
                        n_match = re.search(r'降低\s*[:：]?\s*(\d+)', match_str)
                        if n_match:
                            try:
                                n = int(n_match.group(1).strip())
                                temp_change = -max(self.favour_decrease_min, min(self.favour_decrease_max, n))
                            except (ValueError, TypeError): pass
                    elif "上升" in match_str:
                        n_match = re.search(r'上升\s*[:：]?\s*(-?\d+)', match_str)
                        if n_match:
                            try:
                                n = abs(int(n_match.group(1).strip()))
                                temp_change = max(self.favour_increase_min, min(self.favour_increase_max, n))
                            except (ValueError, TypeError): pass
                    elif "持平" in match_str:
                        temp_change = 0
                    
                    if idx == len(favour_matches) - 1:
                        change_n = temp_change

            # 提取关系确认标签
            relationship_update = None
            rel_matches = self.relationship_pattern.findall(original_text)
            if rel_matches:
                rel_name, rel_bool = rel_matches[-1]
                if rel_bool.lower() == "true" and rel_name.strip():
                    relationship_update = rel_name.strip()

            # 更新用户好感度数据
            current_record = await self.file_manager.get_user_favour(user_id, session_id)
            
            if current_record:
                old_favour = current_record["favour"]
                new_favour = max(-100, min(100, old_favour + change_n))
                old_relationship = current_record.get("relationship", "") or ""
                
                final_relationship = old_relationship
                
                if relationship_update is not None:
                    final_relationship = relationship_update
                
                if new_favour < 0 and old_relationship:
                    final_relationship = ""
                    additional_text = f"还有，我不想和你做{old_relationship}了。"

                favour_changed = (new_favour != old_favour)
                relationship_changed = (final_relationship != old_relationship)

                if favour_changed or relationship_changed:
                    logger.info(
                        f"用户[{user_id}]数据更新 (会话: {session_id}):\n"
                        f"  ├─ 好感度: {old_favour} → {new_favour} (变化: {change_n})\n"
                        f"  └─ 关系: '{old_relationship}' → '{final_relationship}'"
                    )
                    await self.file_manager.update_user_favour(
                        userid=user_id,
                        session_id=session_id,
                        favour=new_favour if favour_changed else None,
                        relationship=final_relationship if relationship_changed else None
                    )
            else:
                initial_favour = await self._get_initial_favour(event)
                final_relationship = relationship_update or ""
                
                if initial_favour < 0 and final_relationship:
                    additional_text = f"还有，我不想和你做{final_relationship}了。"
                    final_relationship = ""

                logger.info(f"新用户[{user_id}]注册 (会话: {session_id}), 初始好感度: {initial_favour}, 初始关系: '{final_relationship}'")
                await self.file_manager.update_user_favour(
                    userid=user_id,
                    session_id=session_id,
                    favour=initial_favour,
                    relationship=final_relationship
                )

            # 清理LLM输出文本
            all_deleted_tags = []
            if favour_matches:
                all_deleted_tags.extend(favour_matches)
            
            full_relationship_tags_iter = self.relationship_pattern.finditer(original_text)
            all_deleted_tags.extend([match.group(0) for match in full_relationship_tags_iter])

            if all_deleted_tags:
                deleted_content_str = ", ".join(all_deleted_tags)
                logger.info(f"从LLM回复中删除了标签: {deleted_content_str}")
            
            cleaned_text = self.favour_pattern.sub("", original_text)
            cleaned_text = self.relationship_pattern.sub("", cleaned_text).strip()
            if additional_text:
                cleaned_text = f"{cleaned_text}\n{additional_text}" if cleaned_text else additional_text
            
            resp.completion_text = cleaned_text

            # 同步清理事件结果中的文本
            result = event.get_result()
            if result and hasattr(result, "chain"):
                new_chain = []
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        cleaned_comp_text = self.favour_pattern.sub("", comp.text)
                        cleaned_comp_text = self.relationship_pattern.sub("", cleaned_comp_text).strip()
                        if cleaned_comp_text:
                            new_chain.append(Plain(cleaned_comp_text))
                    else:
                        new_chain.append(comp)
                result.chain = new_chain

        except Exception as e:
            logger.error(f"处理LLM响应异常: {str(e)}")
            logger.error(f"异常堆栈: {traceback.format_exc()}")
        
        finally:
            if event.is_stopped():
                event.continue_event()

    # ==================== 命令系统 ====================
    @filter.command("查看我的好感度")
    async def query_my_favour(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """所有用户可用"""
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)
        
        current_record = await self.file_manager.get_user_favour(user_id, session_id)
        if current_record:
            current_favour = current_record["favour"]
            current_relationship = current_record["relationship"] or "无"
        else:
            current_favour = await self._get_initial_favour(event)
            current_relationship = "无"
        
        mode_hint = "全局模式" if self.is_global_favour else f"会话：{session_id}"
        
        response = (
            f"📌 你的好感度信息 ({mode_hint})\n"
            f"用户ID：{user_id}\n"
            f"当前好感度：{current_favour}（范围：-100 ~ 100）\n"
            f"当前关系：{current_relationship}"
        )
        
        yield event.plain_result(response)

    @filter.command("修改好感度")
    async def modify_favour(self, event: AstrMessageEvent, target_uid: str, value: str) -> AsyncGenerator[Plain, None]:
        """管理员及以上可用：修改指定用户好感度"""
        # 权限检查
        if not await self._check_permission(event, PermLevel.ADMIN):
            yield event.plain_result("❌ 权限不足！需要管理员及以上权限")
            return
        
        session_id = self._get_session_id(event)
        
        try:
            favour_value = int(value.strip())
            if not (-100 <= favour_value <= 100):
                yield event.plain_result("❌ 好感度值必须在-100~100之间")
                return
        except ValueError:
            yield event.plain_result("❌ 好感度值必须是整数")
            return
        
        success = await self.file_manager.update_user_favour(target_uid, session_id, favour=favour_value)
        
        if success:
            record = await self.file_manager.get_user_favour(target_uid, session_id)
            current_value = record["favour"] if record else "未知"
            yield event.plain_result(f"✅ 已将用户[{target_uid}]的好感度设置为{favour_value}（当前值：{current_value}）")
            logger.info(f"管理员[{event.get_sender_id()}]修改用户[{target_uid}]好感度为{favour_value}")
        else:
            yield event.plain_result("❌ 修改失败")

    @filter.command("删除好感度数据")
    async def delete_user_favour(self, event: AstrMessageEvent, userid: str) -> AsyncGenerator[Plain, None]:
        """管理员及以上可用：删除指定用户好感度数据"""
        # 权限检查
        if not await self._check_permission(event, PermLevel.ADMIN):
            yield event.plain_result("❌ 权限不足！需要管理员及以上权限")
            return
        
        userid_str = userid.strip()
        if not userid_str:
            yield event.plain_result("❌ 失败：用户ID不可为空")
            return
        
        session_id = self._get_session_id(event)
        success, msg = await self.file_manager.delete_user_favour(userid_str, session_id)
        
        if success:
            yield event.plain_result(f"✅ {msg}")
            logger.info(f"管理员[{event.get_sender_id()}]删除用户[{userid_str}]好感度数据成功")
        else:
            yield event.plain_result(f"❌ {msg}")

    @filter.command("查询好感度数据")
    async def query_favour_data(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """管理员及以上可用：查看当前会话所有好感度"""
        # 权限检查
        if not await self._check_permission(event, PermLevel.ADMIN):
            yield event.plain_result("❌ 权限不足！需要管理员及以上权限")
            return
        
        session_id = self._get_session_id(event)
        data = await self.file_manager.read_favour()
        
        # 筛选当前会话的数据
        session_data = [item for item in data if item["session_id"] == session_id]
        
        if not session_data:
            yield event.plain_result(f"📊 当前会话暂无好感度数据")
            return
        
        output_lines = [f"# 当前会话好感度数据 (会话: {session_id or '全局'})\n\n| 用户 | 好感度 | 关系 |\n------------\n"]
        for item in session_data:
            line = (f"| {item['userid']} | "
                    f"{item['favour']} | "
                    f"{item['relationship'] or '无'} |")
            output_lines.append(line)
        
        output_lines.append(f"\n总计：{len(session_data)}条记录")
        yield event.plain_result("\n".join(output_lines))

    @filter.command("查询全部好感度")
    async def query_all_favour(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """超级管理员专用：查看所有会话的好感度数据"""
        # 权限检查
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("❌ 权限不足！需要超级管理员权限")
            return
        
        data = await self.file_manager.read_favour()
        
        if not data:
            yield event.plain_result("📊 全局好感度数据为空")
            return
        
        # 按会话分组显示
        session_groups = {}
        for item in data:
            sid = item["session_id"] or "全局"
            if sid not in session_groups:
                session_groups[sid] = []
            session_groups[sid].append(item)
        
        output_lines = ["📊 全部好感度数据："]
        for sid, items in session_groups.items():
            output_lines.append(f"\n# 会话：{sid}\n\n| 用户 | 好感度 | 关系 |\n------------\n")
            for item in items:
                line = (f"| {item['userid']} | "
                        f"{item['favour']} | "
                        f"{item['relationship'] or '无'} |\n")
                output_lines.append(line)
        
        output_lines.append(f"\n总计：{len(data)}条记录")
        yield event.plain_result("\n".join(output_lines))

    @filter.command("清空当前好感度")
    async def clear_conversation_favour_prompt(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """群主专用：清空当前会话好感度（需二次确认）"""
        # 权限检查
        if not await self._check_permission(event, PermLevel.OWNER):
            yield event.plain_result("❌ 权限不足！需要群主权限")
            return
        
        backup_hint = "（已开启自动备份）" if self.enable_clear_backup else "（⚠️已关闭自动备份，数据将无法恢复！）"
        yield event.plain_result(f"❌ 请确认是否清空当前会话的好感度数据？{backup_hint}\n如果确认，请输入【清空当前好感度 确认】")

    @filter.command("清空当前好感度 确认")
    async def clear_conversation_favour(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """群主专用：确认清空当前会话好感度"""
        # 权限检查
        if not await self._check_permission(event, PermLevel.OWNER):
            yield event.plain_result("❌ 权限不足！需要群主权限")
            return
        
        session_id = self._get_session_id(event)
        
        # 读取数据并过滤掉当前会话
        async with self.file_manager.lock:
            data = await self.file_manager.read_favour()
            new_data = [item for item in data if item["session_id"] != session_id]
            success = await self.file_manager.write_favour(new_data)
        
        if success:
            yield event.plain_result(f"✅ 已清空当前会话的好感度数据")
            logger.info(f"群主[{event.get_sender_id()}]清空会话[{session_id}]好感度数据")
        else:
            yield event.plain_result("❌ 清空失败")

    @filter.command("清空全局好感度数据")
    async def clear_global_favour_prompt(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """超级管理员专用：清空所有好感度数据（需二次确认）"""
        # 权限检查
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("❌ 权限不足！需要超级管理员权限")
            return
        
        backup_hint = "（已开启自动备份）" if self.enable_clear_backup else "（⚠️已关闭自动备份，数据将无法恢复！）"
        yield event.plain_result(f"❌ 请确认是否清空所有好感度数据？{backup_hint}\n如果确认，请输入【清空全局好感度数据 确认】")

    @filter.command("清空全局好感度数据 确认")
    async def clear_global_favour(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """超级管理员专用：确认清空所有好感度数据"""
        # 权限检查
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("❌ 权限不足！需要超级管理员权限")
            return
        
        success = await self.file_manager.clear_all_favour()
        
        if success:
            yield event.plain_result("✅ 已清空全局好感度数据")
            logger.info(f"超级管理员[{event.get_sender_id()}]清空全局好感度数据")
        else:
            yield event.plain_result("❌ 清空失败")

    @filter.command("查看好感度帮助")
    async def help_text(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """查看帮助文档"""
        current_mode = "全局模式（所有对话共享好感度）" if self.is_global_favour else "对话隔离模式（每个对话独立计算）"
        
        help_text = f"""
======好感度插件(权限分级版) v2.0======

📌 当前模式：{current_mode}

⚙️ 权限等级说明
- 普通用户：查看自己的好感度
- 高等级成员：群等级达到阈值的成员（当前阈值：{self.perm_level_threshold}）
- 群管理员：QQ群的管理员角色
- 群主：QQ群的群主角色
- 超级管理员：Bot管理员

📌 权限继承关系：Bot管理员 ≥ 群主 ≥ 群管理员 ≥ 高等级成员 ≥ 普通用户

📋 命令列表
1. 查看我的好感度 - 所有用户可用
2. 修改好感度 <用户ID> <数值> - 群管理员及以上
3. 删除好感度数据 <用户ID> - 群管理员及以上
4. 查询好感度数据 - 群管理员及以上（查看当前会话）
5. 查询全部好感度 - Bot管理员专用（查看所有会话）
6. 清空当前好感度 - 群主及以上（清空当前会话）
7. 清空全局好感度数据 - Bot管理员专用（清空所有数据）

💡 权限说明
- Bot管理员：拥有所有权限，跨平台、跨群聊生效
- 群主/群管理员：仅在所在群聊内有效
- Bot管理员在配置文件的admins_id中设置
- 群管理员权限由QQ群角色决定

⚠️ 注意事项
- 权限不足时会提示错误信息
- Bot管理员享受admin_default_favour初始好感度
- 切换全局/对话模式前建议备份数据
- 数据文件：./data/hao_gan_du/haogan.json
- 清空操作支持自动备份（可在配置中开关）

==================
"""
        yield event.plain_result(help_text)
    async def terminate(self) -> None:
        """插件卸载时的清理工作"""
        pass  # 数据已经实时保存，不需要额外操作
