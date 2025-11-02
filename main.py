import json
import re
import traceback
import string
from pathlib import Path
import asyncio
from aiofiles import open as aio_open
from aiofiles.os import path as aio_path
from datetime import datetime

from astrbot.api import logger
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api.star import Star, register, Context
from astrbot.api import AstrBotConfig
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.event import filter


# ==================== 工具函数 ====================
def is_valid_userid(userid):
    """验证用户ID格式是否有效"""
    if not userid or len(userid.strip()) == 0:
        return False
    userid = userid.strip()
    if len(userid) > 64:
        return False
    allowed_chars = string.ascii_letters + string.digits + "_-:@."
    return all(c in allowed_chars for c in userid)


# ==================== 通用文件管理基类 ====================
class AsyncJsonFileManager:
    """异步JSON文件管理基类"""
    def __init__(self, data_dir, filename):
        self.data_path = data_dir / filename
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()

    async def _read_json(self):
        """读取JSON文件"""
        try:
            if not await aio_path.exists(self.data_path):
                logger.debug(f"{self.data_path.name}不存在，返回默认值")
                return self._get_default_value()
            
            async with aio_open(self.data_path, "r", encoding="utf-8") as f:
                return json.loads(await f.read())
        except Exception as e:
            logger.error(f"读取{self.data_path.name}失败: {str(e)}")
            return self._get_default_value()

    async def _write_json(self, data):
        """写入JSON文件"""
        try:
            async with aio_open(self.data_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception as e:
            logger.error(f"写入{self.data_path.name}失败: {str(e)}")
            return False

    def _get_default_value(self):
        """获取默认值，子类需要重写"""
        raise NotImplementedError


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
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        superusers=None,
        level_threshold=50,
    ):
        if self._initialized:
            return
        self.superusers = superusers or []
        self.level_threshold = level_threshold
        self._initialized = True

    @classmethod
    def get_instance(
        cls,
        superusers=None,
        level_threshold=50,
    ):
        """获取权限管理器的单例"""
        if cls._instance is None:
            cls._instance = cls(
                superusers=superusers,
                level_threshold=level_threshold,
            )
        return cls._instance

    async def get_perm_level(
        self, event, user_id
    ):
        """获取用户在群内的权限级别"""
        try:
            group_id = event.get_group_id()
            # 添加空值检查
            if not group_id or not str(group_id).strip():
                logger.debug("群组ID为空，返回未知权限")
                return PermLevel.UNKNOWN
                
            if not user_id or not str(user_id).strip():
                logger.debug("用户ID为空，返回未知权限")
                return PermLevel.UNKNOWN

            # 转换为字符串后再转换为整数，避免类型错误
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


# ==================== 全局好感度文件管理 ====================
class GlobalFavourFileManager(AsyncJsonFileManager):
    """管理全局好感度数据文件 (global_favour.json)"""
    def __init__(self, data_dir):
        super().__init__(data_dir, "global_favour.json")

    def _get_default_value(self):
        """默认返回一个空字典"""
        return {}

    async def read_global_favour(self):
        """读取全局好感度数据"""
        raw_data = await self._read_json()
        if not isinstance(raw_data, dict):
            logger.error(f"global_favour.json格式无效，需为dict类型")
            return {}
        
        valid_data = {}
        for userid, favour in raw_data.items():
            try:
                valid_data[str(userid)] = int(favour)
            except (ValueError, TypeError):
                logger.warning(f"global_favour.json无效数据：用户ID[{userid}]，值[{favour}]（跳过）")
        
        return valid_data

    async def write_global_favour(self, data):
        """写入全局好感度数据"""
        if not isinstance(data, dict):
            logger.error("写入数据格式无效，需为dict类型")
            return False
        
        success = await self._write_json(data)
        if success:
            logger.info(f"写入global_favour.json成功，包含{len(data)}个用户数据")
        return success

    async def get_user_global_favour(self, userid):
        """获取用户全局好感度"""
        global_data = await self.read_global_favour()
        return global_data.get(str(userid))

    async def update_global_favour(self, userid, favour):
        """更新用户全局好感度"""
        if not is_valid_userid(userid):
            logger.error(f"更新全局好感度失败：用户ID[{userid}]格式无效")
            return False
        
        async with self.lock:
            data = await self.read_global_favour()
            userid_str = str(userid)
            data[userid_str] = max(-100, min(100, favour))
            return await self.write_global_favour(data)


# ==================== 会话级好感度文件管理 ====================
class FavourFileManager(AsyncJsonFileManager):
    """管理会话级好感度数据文件 (haogan.json)"""
    def __init__(self, data_dir, enable_clear_backup):
        super().__init__(data_dir, "haogan.json")
        self.enable_clear_backup = enable_clear_backup

    def _get_default_value(self):
        """默认返回一个空列表"""
        return []

    async def read_favour(self):
        """读取会话级好感度数据"""
        raw_data = await self._read_json()
        if not isinstance(raw_data, list):
            logger.error(f"haogan.json格式无效，需为list类型")
            return []
        
        valid_data = []
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
        
        logger.info(f"读取haogan.json成功，一共{len(valid_data)}条记录")
        return valid_data

    async def write_favour(self, data):
        """写入会话级好感度数据"""
        if not isinstance(data, list):
            logger.error("写入数据格式无效，需为list类型")
            return False
        
        success = await self._write_json(data)
        if success:
            logger.info(f"修改haogan.json成功，写入{len(data)}条记录")
        return success

    async def clear_all_favour(self):
        """清空所有好感度数据"""
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

    async def get_user_favour(self, userid, session_id=None):
        """获取用户好感度记录"""
        userid_str = str(userid)
        data = await self.read_favour()
        for item in data:
            if item["userid"] == userid_str and item["session_id"] == session_id:
                logger.debug(f"查询到用户[{userid_str}]（会话[{session_id}]）的好感度记录")
                return item.copy()
        
        logger.debug(f"未查询到用户[{userid_str}]（会话[{session_id}]）的好感度记录")
        return None

    async def update_user_favour(self, userid, session_id, favour=None, relationship=None):
        """更新用户好感度"""
        userid_str = userid.strip()
        if not is_valid_userid(userid_str):
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

    async def delete_user_favour(self, userid, session_id=None):
        """删除用户好感度数据"""
        userid_str = userid.strip()
        if not is_valid_userid(userid_str):
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
    "好感度管理插件",
    "v2.2"
)
class FavourManagerTool(Star):
    """好感度管理插件主类，负责处理所有逻辑和命令"""
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

    def __init__(self, context, config):
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
        
        # 初始化权限管理器
        self.admins_id = context.get_config().get("admins_id", [])
        self.perm_level_threshold = self.config.get("level_threshold", self.DEFAULT_CONFIG["level_threshold"])
        
        PermissionManager.get_instance(
            superusers=self.admins_id,
            level_threshold=self.perm_level_threshold
        )
        
        # 初始化文件管理器
        self.data_dir = Path(context.get_config().get("plugin.data_dir", "./data")) / "hao_gan_du"
        self.file_manager = FavourFileManager(self.data_dir, self.enable_clear_backup)
        self.global_hao_gan_du = GlobalFavourFileManager(self.data_dir)
        
        # 修改正则表达式，扩大好感度标签的匹配范围
        self.favour_pattern = re.compile(r'[\[［]\s*好感度.*?[\]］]', re.DOTALL | re.IGNORECASE)
        self.relationship_pattern = re.compile(r'[\[［]\s*用户申请确认关系\s*(.*?)\s*[:：]\s*(true|false)\s*[\]］]', re.IGNORECASE)
        
        mode_text = "全局模式（所有对话共享好感度）" if self.is_global_favour else "对话隔离模式"
        logger.info(f"好感度插件(权限分级版)已初始化 - {mode_text}")
        self.pending_updates = {}

    def _validate_config(self):
        """验证配置项的有效性"""
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

    def _is_admin(self, event):
        """判断用户是否为Bot管理员"""
        return str(event.get_sender_id()) in self.admins_id

    async def _get_user_perm_level(self, event):
        """获取用户权限等级"""
        if self._is_admin(event):
            return PermLevel.SUPERUSER
        if not isinstance(event, AiocqhttpMessageEvent):
            return PermLevel.UNKNOWN
        perm_mgr = PermissionManager.get_instance()
        return await perm_mgr.get_perm_level(event, event.get_sender_id())

    async def _check_permission(self, event, required_level):
        """检查用户权限是否满足要求"""
        user_level = await self._get_user_perm_level(event)
        return user_level >= required_level

    def _get_session_id(self, event):
        """获取会话ID：全局模式返回None，非全局模式返回对话标识"""
        if self.is_global_favour:
            logger.debug("全局模式，会话ID为None")
            return None
        else:
            session_id = event.unified_msg_origin
            logger.debug(f"非全局模式，获取会话ID：{session_id}")
            return session_id

    async def _is_envoy(self, userid):
        """判断是否为特使"""
        userid_str = str(userid)
        envoys = [str(envoy) for envoy in self.config.get("favour_envoys", [])]
        return userid_str in envoys

    async def _get_initial_favour(self, event):
        """根据用户权限和配置获取初始好感度"""
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
        
        if user_level >= PermLevel.OWNER or is_envoy:
            base_favour = self.admin_default_favour
            logger.debug(f"用户[{user_id}]为管理员/特使，初始好感度：{base_favour}")
        else:
            base_favour = self.default_favour
            logger.debug(f"用户[{user_id}]为普通用户，初始好感度：{base_favour}")
        
        return max(-100, min(100, base_favour))
        
    @filter.on_llm_request()
    async def inject_favour_prompt(self, event, req):
        """在LLM请求前注入好感度相关的系统提示"""
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
- 请注意，上升要求必须提高，如果不是明显让你（所扮演的角色）感到高兴，则不要上升！
- 如果用户让你感到不高兴，则可以立即降低好感度。
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
    async def handle_llm_response(self, event, resp):
        """
        解析LLM响应，将解析结果通过更新好感度数据。
        """
        # 检查 message_obj 和 message_id 是否存在
        if not hasattr(event, 'message_obj') or not hasattr(event.message_obj, 'message_id'):
            logger.warning("事件对象缺少 message_obj 或 message_id，无法处理好感度。")
            return
            
        message_id = str(event.message_obj.message_id)
        original_text = resp.completion_text

        try:
            update_data = {'favour_change': 0, 'relationship_update': None}

            # 1. 解析好感度变化
            favour_matches = self.favour_pattern.findall(original_text)
            if favour_matches:
                valid_changes = []
                for match in favour_matches:
                    match_str = match.lower().strip()
                    temp_change = None
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
                    
                    if temp_change is not None:
                        valid_changes.append(temp_change)

                if valid_changes:
                    update_data['favour_change'] = valid_changes[-1]
            
            # 2. 解析关系变化
            rel_matches = self.relationship_pattern.findall(original_text)
            if rel_matches:
                rel_name, rel_bool = rel_matches[-1]
                if rel_bool.lower() == "true" and rel_name.strip():
                    update_data['relationship_update'] = rel_name.strip()

            # 只有在检测到有效变化时才存入待办字典
            if update_data['favour_change'] != 0 or update_data['relationship_update'] is not None:
                self.pending_updates[message_id] = update_data
                logger.debug(f"好感度解析完成 (Message ID: {message_id}): {update_data}")

        except Exception as e:
            logger.error(f"解析LLM响应时发生异常: {str(e)}\n{traceback.format_exc()}")
        finally:
            if event.is_stopped():
                event.continue_event()
                
    @filter.on_decorating_result()
    async def cleanup_and_update_favour(self, event):
        """
        在消息发送前，写入对话记录并构建新的消息链以清理标签。
        """
        result = event.get_result()
        if not result or not result.chain:
            return
            
        if not hasattr(event, 'message_obj') or not hasattr(event.message_obj, 'message_id'):
            return
            
        message_id = str(event.message_obj.message_id)
        update_data = self.pending_updates.pop(message_id, None)

        if not update_data:
            return

        change_n = update_data.get('favour_change', 0)
        relationship_update = update_data.get('relationship_update')
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)

        try:
            # 1. 执行数据库更新
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
            else: # 如果是新用户
                initial_favour = await self._get_initial_favour(event)
                new_favour = max(-100, min(100, initial_favour + change_n))
                final_relationship = relationship_update or ""
                
                if new_favour < 0 and final_relationship:
                    final_relationship = ""

                logger.info(f"新用户[{user_id}]注册 (会话: {session_id}), 好感度: {new_favour}, 关系: '{final_relationship}'")
                await self.file_manager.update_user_favour(
                    userid=user_id,
                    session_id=session_id,
                    favour=new_favour,
                    relationship=final_relationship
                )

            # 2. 构建新的消息链来清理标签
            new_chain = []
            cleaned = False
            for comp in result.chain:
                if isinstance(comp, Plain):
                    original_text = comp.text
                    cleaned_text = self.favour_pattern.sub("", original_text)
                    cleaned_text = self.relationship_pattern.sub("", cleaned_text).strip()
                    
                    if original_text != cleaned_text:
                        cleaned = True
                    
                    if cleaned_text:
                        new_chain.append(Plain(text=cleaned_text))
                else:
                    new_chain.append(comp)
            
            if cleaned:
                logger.info(f"消息发送前清理标签完成。")
                result.chain = new_chain

        except Exception as e:
            logger.error(f"更新好感度或清理标签时发生异常: {str(e)}\n{traceback.format_exc()}")
            
    # ==================== 命令系统 ====================
    @filter.command("查看我的好感度", alias={'我的好感度', '好感度查询', '查看好感度', '查询好感度'})
    async def query_my_favour(self, event):
        """命令：查询用户自己的好感度"""
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
    async def modify_favour(self, event, target_uid, value):
        """命令：修改指定用户好感度（管理员及以上）"""
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
    async def delete_user_favour(self, event, userid):
        """命令：删除指定用户好感度数据（管理员及以上）"""
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

    @filter.command("查询好感度数据", alias={'查看好感度数据', '本群好感度查询', '查看本群好感度', '本群好感度'})
    async def query_favour_data(self, event):
        """命令：查看当前会话所有好感度（管理员及以上）"""
        if not await self._check_permission(event, PermLevel.ADMIN):
            yield event.plain_result("❌ 权限不足！需要管理员及以上权限")
            return
        
        session_id = self._get_session_id(event)
        data = await self.file_manager.read_favour()
        
        session_data = [item for item in data if item["session_id"] == session_id]
        
        if not session_data:
            yield event.plain_result(f"📊 当前会话暂无好感度数据")
            return
        
        output_lines = [f"# 当前会话好感度数据 (会话: {session_id or '全局'})\n\n| 用户 | 好感度 | 关系 |\n|----|----|----|"]
        for item in session_data:
            line = (f"| {item['userid']} | "
                    f"{item['favour']} | "
                    f"{item['relationship'] or '无'} |")
            output_lines.append(line)
        
        output_lines.append(f"\n总计：{len(session_data)}条记录")
        text = "\n".join(output_lines)
        try:
            url = await self.text_to_image(text)
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"生成图片失败: {str(e)}")
            yield event.plain_result(text)

    @filter.command("查询全部好感度",alias={'查看全部好感度', '查询全局好感度', '查看全局好感度', '查询好感度全局'})
    async def query_all_favour(self, event):
        """命令：查看所有会话的好感度数据（超级管理员）"""
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("❌ 权限不足！需要超级管理员权限")
            return
        
        data = await self.file_manager.read_favour()
        
        if not data:
            yield event.plain_result("📊 全局好感度数据为空")
            return
        
        session_groups = {}
        for item in data:
            sid = item["session_id"] or "全局"
            if sid not in session_groups:
                session_groups[sid] = []
            session_groups[sid].append(item)
        
        output_lines = ["📊 全部好感度数据："]
        for sid, items in session_groups.items():
            output_lines.append(f"\n# 会话：{sid}\n\n| 用户 | 好感度 | 关系 |\n----|----|----|")
            for item in items:
                line = (f"| {item['userid']} | "
                        f"{item['favour']} | "
                        f"{item['relationship'] or '无'} |")
                output_lines.append(line)
        
        output_lines.append(f"\n总计：{len(data)}条记录")
        text = "\n".join(output_lines)
        try:
            url = await self.text_to_image(text)
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"生成图片失败: {str(e)}")
            yield event.plain_result(text)
            
    @filter.command("清空当前好感度")
    async def clear_conversation_favour_prompt(self, event):
        """命令：清空当前会话好感度（群主，需二次确认）"""
        if not await self._check_permission(event, PermLevel.OWNER):
            yield event.plain_result("❌ 权限不足！需要群主权限")
            return
        
        backup_hint = "（已开启自动备份）" if self.enable_clear_backup else "（⚠️已关闭自动备份，数据将无法恢复！）"
        yield event.plain_result(f"❌ 请确认是否清空当前会话的好感度数据？{backup_hint}\n如果确认，请输入【清空当前好感度 确认】")

    @filter.command("清空当前好感度 确认")
    async def clear_conversation_favour(self, event):
        """命令：确认清空当前会话好感度（群主）"""
        if not await self._check_permission(event, PermLevel.OWNER):
            yield event.plain_result("❌ 权限不足！需要群主权限")
            return
        
        session_id = self._get_session_id(event)
        
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
    async def clear_global_favour_prompt(self, event):
        """命令：清空所有好感度数据（超级管理员，需二次确认）"""
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("❌ 权限不足！需要超级管理员权限")
            return
        
        backup_hint = "（已开启自动备份）" if self.enable_clear_backup else "（⚠️已关闭自动备份，数据将无法恢复！）"
        yield event.plain_result(f"❌ 请确认是否清空所有好感度数据？{backup_hint}\n如果确认，请输入【清空全局好感度数据 确认】")

    @filter.command("清空全局好感度数据 确认")
    async def clear_global_favour(self, event):
        """命令：确认清空所有好感度数据（超级管理员）"""
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("❌ 权限不足！需要超级管理员权限")
            return
        
        success = await self.file_manager.clear_all_favour()
        
        if success:
            yield event.plain_result("✅ 已清空全局好感度数据")
            logger.info(f"超级管理员[{event.get_sender_id()}]清空全局好感度数据")
        else:
            yield event.plain_result("❌ 清空失败")

    @filter.command("查看好感度帮助",alias={'好感度帮助', '好感度插件帮助'})
    async def help_text(self, event):
        """命令：显示帮助文档"""
        current_mode = "全局模式（所有对话共享好感度）" if self.is_global_favour else "对话隔离模式（每个对话独立计算）"
        
        is_admin = await self._check_permission(event, PermLevel.OWNER)

        if is_admin:
            help_text = f"""
======⭐ 好感度插件 - 管理员帮助 ⭐======

📌 当前模式：{current_mode}

⚙️ 权限等级说明
- Bot管理员：配置中的admins_id，拥有最高权限。
- 群主：QQ群的创建者。
- 群管理员：QQ群的管理员角色。
- 高等级成员：群等级达到阈值 {self.perm_level_threshold} 的成员。
- 普通用户：普通群成员。
▶ 权限继承关系：Bot管理员 ≥ 群主 ≥ 群管理员

📋 普通命令
1. 查看我的好感度 - 查询自己的好感度信息。

🔑 管理员命令
1. 修改好感度 <用户ID> <数值> - (群管理员及以上)
2. 删除好感度数据 <用户ID> - (群管理员及以上)
3. 查询好感度数据 - (群管理员及以上, 查看当前会话)
4. 清空当前好感度 - (群主及以上, 清空当前会话)
5. 查询全部好感度 - (Bot管理员, 查看所有会话)
6. 清空全局好感度数据 - (Bot管理员, 清空所有数据)

⚠️ 注意事项
- 数据文件位于 ./data/hao_gan_du/ 目录。
- 清空操作支持自动备份（可在配置中开关）。

==================================
"""
        else:
            help_text = f"""
====== 好感度帮助 ======

📋 可用命令
1. 查看我的好感度 :查看当前好感度
2. 查看好感度帮助 :显示此帮助信息

请注意~查询到的数值仅供参考哦~

==========================
"""
        yield event.plain_result(help_text)
        
    async def terminate(self):
        """插件卸载时的清理工作"""
        pass
