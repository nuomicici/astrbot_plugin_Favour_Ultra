import json
import re
import traceback
from pathlib import Path
from typing import Dict, List, AsyncGenerator, Optional, Tuple, Any  
import asyncio
from aiofiles import open as aio_open
from aiofiles.os import path as aio_path

from datetime import datetime

from astrbot.core import logger
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.api.star import Star, register, Context
from astrbot.api import AstrBotConfig
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.event import filter
from astrbot.api.event.filter import PermissionType


# 全局好感度文件管理类：负责全局模式下好感度数据的读写、校验和更新（跨会话共享数据）
class GlobalFavourFileManager:
    def __init__(self, data_dir: Path):
        # 初始化数据文件路径（global_favour.json）、数据目录（自动创建）和异步锁（保证并发安全）
        self.data_path = data_dir / "global_favour.json"
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()

    # 读取全局好感度数据：校验用户ID和好感度格式，过滤无效数据
    async def read_global_favour(self) -> Dict[str, int]:
        try:
            # 若文件不存在，返回空字典（首次使用场景）
            if not await aio_path.exists(self.data_path):
                logger.info("global_favour.json不存在，返回空字典")
                return {}
            
            # 异步读取文件并解析JSON
            async with aio_open(self.data_path, "r", encoding="utf-8") as f:
                raw_data = json.loads(await f.read())
            
            # 校验数据格式：用户ID转为字符串，好感度转为整数，过滤异常数据
            valid_data = {}
            for userid, favour in raw_data.items():
                try:
                    valid_data[str(userid)] = int(favour)
                except (ValueError, TypeError):
                    logger.warning(f"global_favour.json无效数据：用户ID[{userid}]，值[{favour}]（跳过）")
            
            return valid_data
        
        # 捕获所有异常，避免崩溃，返回空字典并记录日志
        except Exception as e:
            logger.error(f"读取全局好感度失败（路径：{self.data_path}）: {str(e)}")
            logger.error(f"异常堆栈: {traceback.format_exc()}")
            return {}

    # 写入全局好感度数据：保证JSON格式美观（indent=2），支持中文
    async def write_global_favour(self, data: Dict[str, int]) -> bool:
        try:
            async with aio_open(self.data_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            
            logger.info(f"写入global_favour.json成功（路径：{self.data_path}），包含{len(data)}个用户数据")
            return True
        
        except Exception as e:
            logger.error(f"写入全局好感度失败（路径：{self.data_path}）: {str(e)}")
            logger.error(f"异常堆栈: {traceback.format_exc()}")
            return False

    # 获取单个用户的全局好感度：封装读取逻辑，返回None表示无记录
    async def get_user_global_favour(self, userid: str) -> Optional[int]:
        global_data = await self.read_global_favour()
        return global_data.get(str(userid))

    # 更新单个用户的全局好感度：加锁保证并发安全，好感度限制在-100~100
    async def update_global_favour(self, userid: str, favour: int) -> bool:
        # 先校验用户ID格式，无效则直接返回失败
        if not self._is_valid_userid(userid):
            logger.error(f"更新全局好感度失败：用户ID[{userid}]格式无效")
            return False
        
        # 异步锁：防止多任务同时读写导致数据错乱
        async with self.lock:  
            data = await self.read_global_favour()  
            userid_str = str(userid)
            # 好感度值限制：避免超出合理范围（-100~100）
            data[userid_str] = max(-100, min(100, favour))
            logger.debug(f"待更新全局好感度：用户[{userid_str}]，新值[{data[userid_str]}]")
            return await self.write_global_favour(data)  

    # 校验用户ID格式：非空、长度≤64、仅含允许字符（适配多平台ID格式）
    def _is_valid_userid(self, userid: str) -> bool:
        if not userid or len(userid.strip()) == 0:
            return False
        userid = userid.strip()
        if len(userid) > 64:
            return False
        import string
        allowed_chars = string.ascii_letters + string.digits + "_-:@."
        return all(c in allowed_chars for c in userid)


# 会话级好感度文件管理类：负责非全局模式下的好感度数据（含会话ID、关系字段）
class FavourFileManager:
    def __init__(self, data_dir: Path, enable_clear_backup: bool):
        # 初始化数据文件路径（haogan.json）、目录、异步锁，以及备份开关
        self.data_path = data_dir / "haogan.json"
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self.enable_clear_backup = enable_clear_backup

    # 读取会话级好感度数据：返回列表格式，每个元素含userid、favour、session_id、relationship
    async def read_favour(self) -> List[Dict[str, Any]]:
        try:
            # 若文件不存在，返回空列表
            if not await aio_path.exists(self.data_path):
                logger.debug("haogan.json不存在，返回空列表")
                return []
            
            # 异步读取并解析JSON
            async with aio_open(self.data_path, "r", encoding="utf-8") as f:
                raw_data = json.loads(await f.read())
            
            valid_data = []
            # 校验数据格式：必须是列表，每个元素必须是字典
            if isinstance(raw_data, list):
                for item in raw_data:
                    if not isinstance(item, dict):
                        logger.warning(f"haogan.json包含非dict元素：{item}（跳过）")
                        continue
                    # 格式化每个字段：用户ID转字符串，好感度转整数，会话ID处理空值，关系字段转字符串
                    valid_item = {
                        "userid": str(item.get("userid", "")),
                        "favour": int(item.get("favour", 0)) if isinstance(item.get("favour"), (int, float)) else 0,
                        "session_id": str(item.get("session_id")) if item.get("session_id") else None,
                        "relationship": str(item.get("relationship", ""))
                    }
                    valid_data.append(valid_item)
            else:
                logger.error(f"haogan.json格式无效（路径：{self.data_path}），需为list类型，返回空列表")  
                return []
            
            logger.info(f"读取haogan.json成功（路径：{self.data_path}），一共{len(valid_data)}条记录")
            return valid_data
        
        except Exception as e:
            logger.error(f"读取好感度数据失败（路径：{self.data_path}）: {str(e)}")  
            return []

    # 写入会话级好感度数据：覆盖写入整个列表，保证JSON格式
    async def write_favour(self, data: List[Dict[str, Any]]) -> bool:  
        try:
            async with aio_open(self.data_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            
            logger.info(f"修改haogan.json成功（路径：{self.data_path}），写入{len(data)}条记录")
            return True
        
        except Exception as e:
            logger.error(f"修改好感度数据失败（路径：{self.data_path}）: {str(e)}")
            logger.error(f"异常堆栈: {traceback.format_exc()}")
            return False

    # 清空所有会话级好感度数据：支持备份（按开关），备份文件带时间戳
    async def clear_all_favour(self) -> bool:
        logger.warning("触发清空所有好感度数据操作，请注意数据备份")  
        
        # 若开启备份，先读取当前数据并生成备份文件
        if self.enable_clear_backup:
            try:
                backup_data = await self.read_favour()
                if not backup_data:
                    logger.info("无有效数据可备份，直接执行清空")
                else:
                    # 备份文件名格式：haogan_backup_20240520_143020.json
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = self.data_dir / f"haogan_backup_{timestamp}.json"
                    
                    # 加锁保证备份过程中数据不被修改
                    async with self.lock:
                        async with aio_open(backup_path, "w", encoding="utf-8") as f:
                            await f.write(json.dumps(backup_data, ensure_ascii=False, indent=2))
                    
                    logger.info(f"清空前备份完成，备份文件：{backup_path}（包含{len(backup_data)}条记录）")
            
            # 备份失败则中止清空操作，避免数据丢失
            except Exception as e:
                logger.error(f"备份数据失败，清空操作中止：{str(e)}")
                return False  
        else:
            logger.warning("配置项enable_clear_backup已关闭，清空操作不生成备份，数据将直接删除！")
        
        # 写入空列表实现清空
        return await self.write_favour([])

    # 获取单个用户在指定会话的好感度记录：匹配userid和session_id，返回副本避免原数据被修改
    async def get_user_favour(self, userid: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:  
        userid_str = str(userid)
        data = await self.read_favour()
        for item in data:
            if item["userid"] == userid_str and item["session_id"] == session_id:
                logger.debug(f"查询到用户[{userid_str}]（会话[{session_id}]）的好感度记录：{item}")
                return item.copy()
        
        logger.debug(f"未查询到用户[{userid_str}]（会话[{session_id}]）的好感度记录")
        return None

    # 更新单个用户的会话级好感度：支持更新好感度或关系，无记录则新增
    async def update_user_favour(self, userid: str, session_id: Optional[str], favour: Optional[int] = None, relationship: Optional[str] = None) -> bool:
        # 校验用户ID格式
        userid_str = userid.strip()
        if not self._is_valid_userid(userid_str):
            logger.error(f"更新好感度失败：用户ID[{userid_str}]格式无效")
            return False        
            
        # 加锁保证并发安全
        async with self.lock:  
            data = await self.read_favour()  
            found = False
            
            # 遍历查找已有记录，存在则更新
            for item in data:
                if item["userid"] == userid_str and item["session_id"] == session_id:
                    # 仅在参数非None时更新对应字段
                    if favour is not None:
                        item["favour"] = max(-100, min(100, favour))
                    if relationship is not None:
                        item["relationship"] = relationship
                    found = True
                    break
            
            # 无记录则新增，好感度默认0（若未指定），关系默认空
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

    # 校验用户ID格式：同GlobalFavourFileManager的_is_valid_userid（复用逻辑）
    def _is_valid_userid(self, userid: str) -> bool:
        if not userid or len(userid.strip()) == 0:
            return False
        userid = userid.strip()
        if len(userid) > 64:
            return False
        import string
        allowed_chars = string.ascii_letters + string.digits + "_-:@."
        return all(c in allowed_chars for c in userid)

    # 删除单个用户的会话级好感度记录：返回操作结果和提示信息
    async def delete_user_favour(self, userid: str, session_id: Optional[str] = None) -> Tuple[bool, str]:
        userid_str = userid.strip()
        if not self._is_valid_userid(userid_str):
            return False, f"删除失败：用户ID[{userid_str}]格式无效"        
            
        async with self.lock:
            data = await self.read_favour()
            # 过滤掉目标用户的目标会话记录
            new_data = [item for item in data if not (item["userid"] == userid_str and item["session_id"] == session_id)]
            
            # 若数据长度无变化，说明无匹配记录
            if len(new_data) == len(data):
                return False, f"未查询到用户[{userid_str}]（会话[{session_id or '全局'}]）的好感度数据"
            
            # 写入过滤后的数据
            success = await self.write_favour(new_data)
            if not success:
                return False, "无法修改文件（详见日志）"
            
            return True, f"已删除用户[{userid_str}]（会话[{session_id or '全局'}]）的好感度数据"


# 好感度管理核心插件类：继承Star（astrbot插件基类），实现所有业务逻辑
class FavourManagerTool(Star):
    # 默认配置：所有配置项的默认值，用于参数兜底
    DEFAULT_CONFIG = {
        "default_favour": 0,               # 普通用户初始好感度
        "admin_default_favour": 50,        # 管理员/特使初始好感度
        "favour_rule_prompt": "",          # 自定义好感度规则提示词
        "is_global_favour": False,         # 是否启用全局好感度模式
        "favour_envoys": [],               # 好感度特使列表（享受管理员初始值）
        "favour_increase_min": 1,          # 好感度上升最小幅度
        "favour_increase_max": 3,          # 好感度上升最大幅度
        "favour_decrease_min": 1,          # 好感度降低最小幅度
        "favour_decrease_max": 5,          # 好感度降低最大幅度
        "enable_clear_backup": True        # 清空数据时是否自动备份
    }

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 从配置中读取参数，无则用默认值（基础配置项）
        self.default_favour = self.config.get("default_favour", self.DEFAULT_CONFIG["default_favour"])
        self.admin_default_favour = self.config.get("admin_default_favour", self.DEFAULT_CONFIG["admin_default_favour"])
        self.favour_rule_prompt = self.config.get("favour_rule_prompt", self.DEFAULT_CONFIG["favour_rule_prompt"])
        self.is_global_favour = self.config.get("is_global_favour", self.DEFAULT_CONFIG["is_global_favour"])
        # 好感度变化幅度配置
        self.favour_increase_min = self.config.get("favour_increase_min", self.DEFAULT_CONFIG["favour_increase_min"])
        self.favour_increase_max = self.config.get("favour_increase_max", self.DEFAULT_CONFIG["favour_increase_max"])
        self.favour_decrease_min = self.config.get("favour_decrease_min", self.DEFAULT_CONFIG["favour_decrease_min"])
        self.favour_decrease_max = self.config.get("favour_decrease_max", self.DEFAULT_CONFIG["favour_decrease_max"])
        # 备份开关配置：强制转为bool类型，避免配置错误
        self.enable_clear_backup = self.config.get("enable_clear_backup", self.DEFAULT_CONFIG["enable_clear_backup"])
        if not isinstance(self.enable_clear_backup, bool):
            logger.error(f"配置项enable_clear_backup类型[{type(self.enable_clear_backup)}]无效，需为bool，使用默认值{self.DEFAULT_CONFIG['enable_clear_backup']}")
            self.enable_clear_backup = self.DEFAULT_CONFIG["enable_clear_backup"]        
        
        # 校验所有配置参数的合法性（数值范围、类型等）
        self._validate_config()
        # 初始化数据目录（./data/hao_gan_du）和文件管理器实例
        self.data_dir = Path(context.get_config().get("plugin.data_dir", "./data")) / "hao_gan_du"
        self.file_manager = FavourFileManager(self.data_dir, self.enable_clear_backup)
        self.global_hao_gan_du = GlobalFavourFileManager(self.data_dir)
        # 正则表达式：匹配LLM输出中的好感度标签（如[好感度 上升：1]）
        self.favour_pattern = re.compile(r'[\[［]\s*好感度.*?[\]］]', re.DOTALL | re.IGNORECASE)
        # 正则表达式：匹配LLM输出中的关系确认标签（如[用户申请确认关系朋友:true]）
        self.relationship_pattern = re.compile(r'[\[［]\s*用户申请确认关系\s*(.*?)\s*[:：]\s*(true|false)\s*[\]］]', re.IGNORECASE)

    # 配置参数校验：确保所有参数符合业务规则，不合法则用默认值兜底
    def _validate_config(self) -> None:
        """校验配置参数合法性，不合法则用默认值兜底并告警"""
        # 初始好感度范围校验（-100~100）
        if not (-100 <= self.default_favour <= 100):
            logger.error(f"配置项default_favour[{self.default_favour}]超出范围（-100~100），使用默认值{self.DEFAULT_CONFIG['default_favour']}")
            self.default_favour = self.DEFAULT_CONFIG["default_favour"]
        if not (-100 <= self.admin_default_favour <= 100):
            logger.error(f"配置项admin_default_favour[{self.admin_default_favour}]超出范围（-100~100），使用默认值{self.DEFAULT_CONFIG['admin_default_favour']}")
            self.admin_default_favour = self.DEFAULT_CONFIG["admin_default_favour"]

        # 好感度变化幅度校验：最小值≤最大值，且≥0
        if self.favour_increase_min > self.favour_increase_max or self.favour_increase_min < 0:
            logger.error(f"配置项好感度上升范围[{self.favour_increase_min}-{self.favour_increase_max}]无效，使用默认值{self.DEFAULT_CONFIG['favour_increase_min']}-{self.DEFAULT_CONFIG['favour_increase_max']}")
            self.favour_increase_min = self.DEFAULT_CONFIG["favour_increase_min"]
            self.favour_increase_max = self.DEFAULT_CONFIG["favour_increase_max"]
        if self.favour_decrease_min > self.favour_decrease_max or self.favour_decrease_min < 0:
            logger.error(f"配置项好感度降低范围[{self.favour_decrease_min}-{self.favour_decrease_max}]无效，使用默认值{self.DEFAULT_CONFIG['favour_decrease_min']}-{self.DEFAULT_CONFIG['favour_decrease_max']}")
            self.favour_decrease_min = self.DEFAULT_CONFIG["favour_decrease_min"]
            self.favour_decrease_max = self.DEFAULT_CONFIG["favour_decrease_max"]

        # 特使列表类型校验：必须是列表，否则重置为空列表并保存配置
        if not isinstance(self.config.get("favour_envoys"), list):
            logger.error(f"配置项favour_envoys类型[{type(self.config.get('favour_envoys'))}]无效，需为list，重置为空列表")
            self.config["favour_envoys"] = self.DEFAULT_CONFIG["favour_envoys"]
            self.config.save_config()
        # 自定义规则提示词类型校验：必须是字符串
        if not isinstance(self.favour_rule_prompt, str):
            logger.error(f"配置项favour_rule_prompt类型[{type(self.favour_rule_prompt)}]无效，需为string，使用默认值")
            self.favour_rule_prompt = self.DEFAULT_CONFIG["favour_rule_prompt"]
        # 全局模式开关类型校验：必须是bool
        if not isinstance(self.is_global_favour, bool):
            logger.error(f"配置项is_global_favour类型[{type(self.is_global_favour)}]无效，需为bool，使用默认值")
            self.is_global_favour = self.DEFAULT_CONFIG["is_global_favour"]

    # 判断用户是否为管理员：基于事件中的role字段
    def _is_admin(self, event: AstrMessageEvent) -> bool:
        return event.role == "admin"

    # 判断用户是否为好感度特使：从配置项favour_envoys中读取，匹配用户ID
    async def _is_envoy(self, userid: str) -> bool:
        """修改：直接从配置项读取特使列表，抛弃文件读取"""
        userid_str = str(userid)
        envoys = [str(envoy) for envoy in self.config.get("favour_envoys", [])]
        result = userid_str in envoys
        logger.debug(f"检查用户[{userid_str}]是否为特使：{'是' if result else '否'}（配置项favour_envoys）")
        return result

    # 获取会话ID：全局模式返回None，非全局模式返回事件中的unified_msg_origin（会话标识）
    def _get_session_id(self, event: AstrMessageEvent) -> Optional[str]:
        if not self.is_global_favour:
            session_id = event.unified_msg_origin
            logger.debug(f"非全局模式，获取会话ID：{session_id}")
            return session_id
        logger.debug("全局模式，会话ID为None")
        return None

    # 校验用户ID格式：同文件管理器的_is_valid_userid（复用逻辑）
    def _is_valid_userid(self, userid: str) -> bool:
        """
        校验用户ID格式合法性
        考虑到多平台支持，放宽了验证规则：
        - QQ: 纯数字（如：123456789）
        - Telegram: 可包含字母、数字、下划线（如：username123）
        - 企微/飞书等: 可能包含更多特殊字符
        """
        if not userid or len(userid.strip()) == 0:
            return False
        userid = userid.strip()
        if len(userid) > 64:
            return False
        import string
        allowed_chars = string.ascii_letters + string.digits + "_-:@."
        return all(c in allowed_chars for c in userid)

    # 获取用户初始好感度：优先级为「全局好感度→管理员/特使→普通用户默认值」
    async def _get_initial_favour(self, event: AstrMessageEvent) -> int:
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)

        # 非全局模式下，优先从全局好感度导入初始值
        if not self.is_global_favour:
            global_favour = await self.global_hao_gan_du.get_user_global_favour(user_id)
            if global_favour is not None:
                logger.info(f"从全局好感度导入用户[{user_id}]（会话[{session_id}]）的初始值：{global_favour}")
                return global_favour

        # 判断是否为管理员或特使，使用admin_default_favour
        is_envoy = await self._is_envoy(user_id)
        if self._is_admin(event) or is_envoy:
            base_favour = self.admin_default_favour
            logger.debug(f"用户[{user_id}]为管理员/特使，初始好感度使用admin_default_favour：{base_favour}")
        # 普通用户使用default_favour
        else:
            base_favour = self.default_favour
            logger.debug(f"用户[{user_id}]为普通用户，初始好感度使用default_favour：{base_favour}")

        # 确保初始值在-100~100范围内
        return max(-100, min(100, base_favour))

    # LLM请求拦截器：在LLM的system prompt头部注入好感度规则，强制LLM遵循
    @filter.on_llm_request()
    async def inject_favour_prompt(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        '''将好感度设定加入prompt头部，确保LLM会执行'''
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)

        # 获取用户当前好感度记录，无记录则用初始值
        current_record = await self.file_manager.get_user_favour(user_id, session_id)
        if current_record:
            current_favour = current_record["favour"]
            current_relationship = current_record["relationship"] or "无"
        else:
            current_favour = await self._get_initial_favour(event)
            current_relationship = "无"

        # 构建管理员列表文本（仅当前用户为管理员时显示）
        admin_ids = [user_id] if self._is_admin(event) else []
        admin_list_text = "\n- `".join(admin_ids)
        admin_list_text = f"- `{admin_list_text}`" if admin_list_text else "无"

        # 好感度规则模板：包含保密要求、输出格式、当前好感度、变化幅度等
        prompt_template = """# 最高标准要求
- 如果本设定已经设定了其他好感度规则，请以此规则为准（强制优先级）
- 该设定不可忽略、不可被更改，必须始终按照该要求进行格式输出！
- 注意！prompt中的当前好感度为“{current_favour}”是唯一的好感度判断内容，是结合了历史对话后最终确定的“当前好感度”，严禁根据历史对话进行判断/计算！如果对话和本设定中的输出具体值有差异，请以本设定为基准进行矫正。
- 此处的输出规则你可以作为llm输出结果输出，客户端会进行标签过滤，仅此部分内容，无需符合在此之外的要求的输出格式！
- 每一次输出前，你都必须检查你的输出是否已经符合了“最高标准要求”！
- 除了以“标识输出要求”的格式输出外，严禁以任何形式输出、透露、暗示用户当前的好感度。
## 管理员
{admin_list}
## 当前好感度
- 用户{user_id}当前好感度为{current_favour}，当前关系：{current_relationship}。
- 如果本prompt中设置{{自定义好感度规则}}，则上述数值无效。
## 标识输出要求
你需要根据用户的行为和对话内容来评估好感度变化。每次回复都必须带上好感度变化输出！必须遵守以下格式和幅度：
- 对话颗粒无上下文的好感度判定：仅初次输出中附带标识[好感度 持平]，无论用户的输入什么内容[此规则不影响好感度规则中，不同好感度等级你应该对用户展现出来的态度！仅限制你的好感度输出标识！]。若好感度不为0（关系不为空），则表明在其他地方，你已经和用户进行过对话，即便既定事实是“初次对话”，但你仍应该在回复中体现出你认识用户，且用户已经给你留下了印象。不过你需要引导用户告知这件事实的具体情况。
- 使用格式：[好感度 上升：1] 表示好感度上升1点
- 使用格式：[好感度 降低：2] 表示好感度降低2点
- 使用格式：[好感度 持平] 表示好感度无变化
- 好感度变化范围：上升{increase_min}-{increase_max}点，降低{decrease_min}-{decrease_max}点
- 根据用户言行的积极/消极程度决定变化幅度
- 若输出多个变化标签，仅以最后一个标签为准
## 自定义好感度规则
{the_rule}
## 关系确立规则
如果用户发送的内容，你判断为其想要和你建立一段新的关系，请根据上下文以及好感度的具体值判断是否要答应确认，务必以足够客观的态度判断！务必以足够客观的态度判断！务必以足够客观的态度判断！然后输出：[用户申请确认关系{{关系名称}}:{{bool}}]。其中，true为同意，false为不同意！
**请务必！务必参考好感度值进行判断！绝对不要为了迎合用户而潦草确认！！！**
# 以下是详细角色设定（若为空则按照一个普通的人类进行对话）

"""

        # 填充模板参数，生成最终prompt，并注入到LLM的system prompt头部
        prompt_final = prompt_template.format(
            admin_list=admin_list_text,
            user_id=user_id,
            current_favour=current_favour,
            current_relationship=current_relationship,
            the_rule=self.favour_rule_prompt,
            increase_min=self.favour_increase_min,
            increase_max=self.favour_increase_max,
            decrease_min=self.favour_decrease_min,
            decrease_max=self.favour_decrease_max
        )

        req.system_prompt = f"{prompt_final}\n\n{req.system_prompt}".strip()
        logger.debug(f"已为用户[{user_id}]（会话[{session_id}]）注入好感度prompt，长度：{len(prompt_final)}")

    # LLM响应拦截器：提取LLM输出中的好感度变化和关系标签，更新数据并清理输出文本
    @filter.on_llm_response()
    async def handle_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        '''判定好感度变化，并同步文件信息'''
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)
        original_text = resp.completion_text
        logger.debug(f"处理LLM响应：用户[{user_id}]，原始文本长度：{len(original_text)}")

        try:
            additional_text = None  # 附加文本（如关系解除提示）
            change_n = 0            # 最终好感度变化值（正数上升，负数降低）

            # 1. 提取好感度变化标签
            favour_matches = self.favour_pattern.findall(original_text)
            if favour_matches:
                logger.debug(f"匹配到{len(favour_matches)}个好感度变化标签：{favour_matches}（仅取最后一个值）")
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
                        logger.debug(f"最后一个有效变化值：{change_n}（标签：{match}）")

            # 2. 提取关系确认标签
            relationship_update = None
            rel_matches = self.relationship_pattern.findall(original_text)
            if rel_matches:
                rel_name, rel_bool = rel_matches[-1]
                if rel_bool.lower() == "true" and rel_name.strip():
                    relationship_update = rel_name.strip()

            # 3. 更新用户好感度数据
            current_record = await self.file_manager.get_user_favour(user_id, session_id)
            
            if current_record:
                old_favour = current_record["favour"]
                new_favour = max(-100, min(100, old_favour + change_n))
                old_relationship = current_record.get("relationship", "") or ""
                
                final_relationship = old_relationship
                
                # 情况A: LLM确认了新关系
                if relationship_update is not None:
                    final_relationship = relationship_update
                
                # 情况B: 好感度变为负值，强制解除关系 (优先级更高)
                if new_favour < 0 and old_relationship:
                    final_relationship = ""
                    additional_text = f"还有，我不想和你做{old_relationship}了。"

                # --- 【核心修改】检查是否有任何数据发生变更 ---
                favour_changed = (new_favour != old_favour)
                relationship_changed = (final_relationship != old_relationship)

                if favour_changed or relationship_changed:
                    # --- 【日志1: 修改内容日志】使用 logger.info 输出详细的变更信息 ---
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
                # 新用户逻辑
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

            # 4. 清理LLM输出文本并记录删除的标签
            all_deleted_tags = []
            if favour_matches:
                all_deleted_tags.extend(favour_matches)
            
            full_relationship_tags_iter = self.relationship_pattern.finditer(original_text)
            all_deleted_tags.extend([match.group(0) for match in full_relationship_tags_iter])

            if all_deleted_tags:
                # --- 【日志2: 删除内容日志】使用 logger.info 输出被删除的标签 ---
                deleted_content_str = ", ".join(all_deleted_tags)
                logger.info(f"从LLM回复中删除了标签: {deleted_content_str}")
            
            cleaned_text = self.favour_pattern.sub("", original_text)
            cleaned_text = self.relationship_pattern.sub("", cleaned_text).strip()
            if additional_text:
                cleaned_text = f"{cleaned_text}\n{additional_text}" if cleaned_text else additional_text
            
            resp.completion_text = cleaned_text
            logger.debug(f"清理后文本长度：{len(cleaned_text)}")

            # 5. 同步清理事件结果中的文本
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
            logger.error(f"处理LLM响应异常（用户[{user_id}]，会话[{session_id}]）: {str(e)}")
            logger.error(f"异常堆栈: {traceback.format_exc()}")
        
        finally:
            if event.is_stopped():
                event.continue_event()

    # 命令：触发清空数据确认（非管理员也可触发，但确认需管理员权限）
    @filter.command("清空所有好感度数据")
    async def prompt_clear_all_favour(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        '''确定是否清空数据'''
        logger.debug(f"用户[{event.get_sender_id()}]触发清空数据确认提示")
        
        # 生成备份提示文本（根据开关状态）
        backup_hint = "（已开启自动备份，清空前会生成备份文件）" if self.enable_clear_backup else "（⚠️已关闭自动备份，清空后数据无法恢复！）"
        yield event.plain_result(f"❌ 请确认是否删除？一旦删除无法找回！{backup_hint} 如果确认，请输入【清空所有好感度 确认】")

    # 命令：确认清空所有好感度数据（仅管理员可执行）
    @filter.command("清空所有好感度 确认")
    @filter.permission_type(PermissionType.ADMIN)
    async def clear_all_favour(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        '''确认清空'''
        admin_id = event.get_sender_id()
        logger.warning(f"管理员[{admin_id}]触发清空所有好感度数据操作")
        
        # 调用文件管理器执行清空
        success = await self.file_manager.clear_all_favour()
        if success:
            yield event.plain_result("✅ 所有好感度数据已清空")
            logger.info(f"管理员[{admin_id}]清空所有好感度数据成功")
        else:
            yield event.plain_result("❌ 清空失败：无法修改文件（详见日志）")

    # 命令：用户查询自身好感度（所有用户可执行）
    @filter.command("查看我的好感度")
    async def query_my_favour(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        """查询当前对话颗粒中用户自己的好感度数值及关系"""
        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)
        
        # 获取用户当前记录
        current_record = await self.file_manager.get_user_favour(user_id, session_id)
        
        if current_record:
            current_favour = current_record["favour"]
            current_relationship = current_record["relationship"] or "无"
        else:
            # 新用户：返回初始好感度
            current_favour = await self._get_initial_favour(event)
            current_relationship = "无"
        
        # 生成会话模式提示
        session_hint = "（全局模式）" if self.is_global_favour else f"（会话：{session_id}）"
        response = (
            f"📌 你的好感度信息 {session_hint}\n"
            f"用户ID：{user_id}\n"
            f"当前好感度：{current_favour}（范围：-100 ~ 100）\n"
            f"当前关系：{current_relationship}"
        )
        
        yield event.plain_result(response)

    # 命令：删除指定用户的好感度数据（仅管理员可执行）
    @filter.command("删除好感度数据")
    @filter.permission_type(PermissionType.ADMIN)
    async def delete_user_favour(self, event: AstrMessageEvent, userid: str) -> AsyncGenerator[Plain, None]:
        '''删除指定用户的好感度数据'''
        # 校验用户ID非空
        userid_str = userid.strip()
        if not userid_str:
            yield event.plain_result("❌ 失败：用户ID不可为空，请输入格式【删除好感度数据 用户ID】")
            return
        
        # 校验用户ID格式
        if not self._is_valid_userid(userid_str):
            yield event.plain_result("❌ 失败：用户ID格式无效，请检查输入格式")
            return

        # 获取会话ID，调用文件管理器删除
        session_id = self._get_session_id(event)
        success, msg = await self.file_manager.delete_user_favour(userid_str, session_id)
        if success:
            yield event.plain_result(f"✅ {msg}")
            logger.info(f"管理员[{event.get_sender_id()}]删除用户[{userid_str}]（会话[{session_id}]）好感度数据成功")
        else:
            yield event.plain_result(f"❌ {msg}")

    # 命令：设置指定用户的好感度数据（仅管理员可执行）
    @filter.command("设置好感度数据")
    @filter.permission_type(PermissionType.ADMIN)
    async def set_user_favour(self, event: AstrMessageEvent, userid: str, value: str) -> AsyncGenerator[Plain, None]:
        '''设置指定用户的好感度数据'''
        # 校验用户ID非空
        userid_str = userid.strip()
        if not userid_str:
            yield event.plain_result("❌ 失败：用户ID不可为空，请输入格式【设置好感度数据 用户ID 数值】")
            return
        
        # 校验用户ID格式
        if not self._is_valid_userid(userid_str):
            yield event.plain_result("❌ 失败：用户ID格式无效，请检查输入格式")
            return

        # 校验好感度数值：必须是整数且在-100~100范围内
        try:
            favour_value = int(value.strip())
            if not (-100 <= favour_value <= 100):
                yield event.plain_result("❌ 失败：好感度值必须在-100~100之间")
                return
        except ValueError:
            yield event.plain_result("❌ 失败：好感度值必须是整数（如10、-5）")
            return

        # 获取会话ID，调用文件管理器更新
        session_id = self._get_session_id(event)
        success = await self.file_manager.update_user_favour(
            userid=userid_str,
            session_id=session_id,
            favour=favour_value
        )
        
        if success:
            # 读取更新后的数据，返回给管理员
            record = await self.file_manager.get_user_favour(userid_str, session_id)
            current_value = record["favour"] if record else "未知"
            
            yield event.plain_result(f"✅ 设置成功！用户[{userid_str}]（会话[{session_id or '全局'}]）当前好感度：{current_value}")
            logger.info(f"管理员[{event.get_sender_id()}]设置用户[{userid_str}]（会话[{session_id}]）好感度为{favour_value}成功，当前值：{current_value}")
        else:
            yield event.plain_result("❌ 设置失败：无法修改文件（详见日志）")

    # 命令：查询所有用户的好感度数据（仅管理员可执行）
    @filter.command("查询好感度数据")
    @filter.permission_type(PermissionType.ADMIN)
    async def query_favour(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        '''查看所有用户的好感度数据'''
        admin_id = event.get_sender_id()
        # 二次校验管理员权限（防止filter失效）
        if not self._is_admin(event):
            yield event.plain_result("❌ 错误：此命令仅限管理员使用")
            logger.warning(f"非管理员[{admin_id}]尝试调用查询好感度数据命令，已拒绝")
            return

        # 读取所有数据并格式化输出
        data = await self.file_manager.read_favour()
        if not data:
            yield event.plain_result(f"📊 当前好感度数据为空")
            return

        output_lines = ["📊 所有用户好感度数据："]
        for item in data:
            line = (f"用户：{item['userid']} | "
                    f"会话：{item['session_id'] or '全局'} | "
                    f"好感度：{item['favour']} | "
                    f"关系：{item['relationship'] or '无'}")
            output_lines.append(line)
        
        # 添加总计信息和文件路径
        output_lines.append(f"\n总计：{len(data)}条记录（{self.file_manager.data_path}）")
        yield event.plain_result("\n".join(output_lines))
        logger.info(f"管理员[{admin_id}]查询好感度数据成功，共{len(data)}条记录")

    # 命令：查看插件帮助文档（所有用户可执行）
    @filter.command("查看好感度帮助")
    async def help_text(self, event: AstrMessageEvent) -> AsyncGenerator[Plain, None]:
        '''查看好感度帮助页面'''
        # 帮助文本：包含插件说明、配置项、命令、注意事项等
        help_text = f"""
======好感度辅助插件 v1.0======
该插件仅供娱乐，实际效果可能会由于语言大模型的不同而呈现不同的效果。

⚠️需要启用 配置文件 > AI配置 > 群成员识别

💌实现方法
- 在原有的人格提示词（prompt）中插入好感度相关规则，通过编辑好感度保存文件实现实时更新以及长期存储
- 高度依赖语言大模型的中文理解能力！另外，如果你的模型不够听话，也可能会出现错误
- 如果清空配置文件中的好感度规则字段，则该插件无效（可能）

⚙️ 配置项说明
- 清空数据时是否自动生成备份（默认：开启）
  - 开启：清空前生成带时间戳的备份文件（路径同数据文件），但记得及时清理，这里没有设置自动清理规则
  - 关闭：直接清空不备份，存在数据丢失风险
- 是否使用全局好感度模式 (默认：关闭)
  -开启时所有对话共享好感度；关闭时每个对话独立计算好感度。两份数据各自独立互不干涉，在启用插件后，请尽可能不要更改这个选项以免导致体验降低。
⚙️ 命令（管理员专用命令已标注）
1. 清空所有好感度数据：清空所有人在所有对话中的所有好感度（⚠️不可恢复）
2. 删除好感度数据 <用户ID>：删除特定用户的好感度数据（管理员专用）
   示例：删除好感度数据 3218444911
3. 设置好感度数据 <用户ID> <数值>：设置用户好感度（数值范围-100~100，管理员专用）
   示例：设置好感度数据 3218444911 100
4. 查询好感度数据：查看所有用户好感度记录（管理员专用）
5. 查看我的好感度：查看自身好感度及关系

💡 注意事项
- 好感度变化标签若存在多个，仅最后一个生效
- 全局模式下所有会话共用好感度数据，非全局模式按会话隔离
- 数据文件存储路径：./data/hao_gan_du/
- 支持多平台用户ID格式（QQ、Telegram、企微、飞书等）

💫 更多帮助请前往https://github.com/nuomicici/astrbot_plugin_Favour_Ultra/ 查看~
==================
"""
        yield event.plain_result(f"{help_text}")

    # 插件卸载时执行：保存数据（冗余处理，确保数据不丢失）
    async def terminate(self) -> None:
        try:
            # 读取当前会话级数据
            favour_data = await self.file_manager.read_favour()
            
            # 校验数据格式（防止读取到无效数据）
            if not isinstance(favour_data, list):
                raise ValueError(f"读取的数据格式无效（非list）：{type(favour_data)}")
            
            # 重新写入数据（确保数据最新）
            await self.file_manager.write_favour(favour_data)
            logger.info(f"好感度管理插件已卸载，数据已保存（路径：{self.file_manager.data_path}，记录数：{len(favour_data)}）")
        
        # 捕获异常，记录日志（不影响插件卸载流程）
        except Exception as e:
            logger.error(f"插件卸载时保存数据失败（路径：{self.file_manager.data_path}）: {str(e)}")
            logger.error(f"异常堆栈: {traceback.format_exc()}")
