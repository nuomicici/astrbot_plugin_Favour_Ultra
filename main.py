import re
import traceback
import shutil
from pathlib import Path
from typing import Dict, List, AsyncGenerator, Optional, Tuple, Any, Set
from datetime import datetime, timedelta
import asyncio

from astrbot.api import logger
from astrbot.core.message.components import Plain, At
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api.star import Star, register, Context
from astrbot.api import AstrBotConfig
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.event import filter

from .utils import is_valid_userid
from .permissions import PermLevel, PermissionManager
from .storage import FavourDBManager, FavourRecord

@register("astrbot_plugin_favour_ultra", "Soulter", "好感度插件(Ultra版)", "2.6.0", "https://github.com/Soulter/astrbot_plugin_favour_ultra")
class FavourManagerTool(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 基础配置
        self.favour_mode = self.config.get("favour_mode", "galgame")
        self.is_global_favour = self.config.get("is_global_favour", False)
        self.enable_cold_violence = self.config.get("enable_cold_violence", True) # 新增开关
        self.min_favour_value = self.config.get("min_favour_value", -100)
        self.max_favour_value = self.config.get("max_favour_value", 100)
        self.default_favour = self.config.get("default_favour", 0)
        self.favour_rule_prompt = self.config.get("favour_rule_prompt", "")

        # 高级配置
        adv_conf = self.config.get("advanced_config", {})
        self.admin_default_favour = adv_conf.get("admin_default_favour", 50)
        self.favour_envoys = adv_conf.get("favour_envoys", [])
        self.favour_increase_min = adv_conf.get("favour_increase_min", 1)
        self.favour_increase_max = adv_conf.get("favour_increase_max", 3)
        self.favour_decrease_min = adv_conf.get("favour_decrease_min", 1)
        self.favour_decrease_max = adv_conf.get("favour_decrease_max", 5)
        self.perm_level_threshold = adv_conf.get("level_threshold", 50)
        self.blocked_sessions = adv_conf.get("blocked_sessions", [])
        self.allowed_sessions = adv_conf.get("allowed_sessions", [])

        # 冷暴力配置
        cv_conf = self.config.get("cold_violence_config", {})
        self.cold_violence_threshold = cv_conf.get("threshold", -50)
        self.cold_violence_duration_minutes = cv_conf.get("duration_minutes", 60)
        self.cold_violence_is_global = cv_conf.get("is_global", False)
        self.cold_violence_replies = cv_conf.get("replies", {
            "on_trigger": "......（我不想理你了。）",
            "on_message": "[自动回复]不想理你,{time_str}后再找我",
            "on_query": "冷暴力呢，看什么看，{time_str}之后再找我说话"
        })

        self._validate_config()
        
        # 权限管理初始化
        self.admins_id = context.get_config().get("admins_id", [])
        PermissionManager.get_instance(
            superusers=self.admins_id,
            level_threshold=self.perm_level_threshold
        )

        # 数据库初始化
        self.data_dir = Path(context.get_config().get("plugin.data_dir", "./data")) / "plugin_data" / "astrbot_plugin_favour_ultra"
        self.db_manager = FavourDBManager(self.data_dir, self.min_favour_value, self.max_favour_value)
        
        # 异步初始化数据库和迁移数据
        asyncio.create_task(self._init_storage())

        # 正则表达式
        self.favour_pattern = re.compile(
            r'[\[［][^\[\]［］]*?(?:好.*?感|好.*?度|感.*?度)[^\[\]［］]*?[\]］]', 
            re.DOTALL | re.IGNORECASE
        )
        self.relationship_pattern = re.compile(
            r'[\[［]\s*用户申请确认关系\s*[:：]\s*(.*?)\s*[:：]\s*(true|false)(?:\s*[:：]\s*(true|false))?\s*[\]］]', 
            re.IGNORECASE
        )
        
        self.pending_updates = {}
        self.cold_violence_users: Dict[str, datetime] = {} # Key: user_id or session_id:user_id

    async def _init_storage(self):
        """初始化存储并迁移数据"""
        await self.db_manager.init_db()
        
        # 检查旧文件并迁移
        old_global = self.data_dir / "global_favour.json"
        old_local = self.data_dir / "haogan.json"
        
        if old_global.exists():
            logger.info("检测到旧版全局好感度文件，开始迁移...")
            await self.db_manager.migrate_from_json(old_global, is_global=True)
            
        if old_local.exists():
            logger.info("检测到旧版会话好感度文件，开始迁移...")
            await self.db_manager.migrate_from_json(old_local, is_global=False)

    def _validate_config(self) -> None:
        if self.min_favour_value >= self.max_favour_value:
             self.min_favour_value = -100
             self.max_favour_value = 100
        
        self.default_favour = max(self.min_favour_value, min(self.max_favour_value, self.default_favour))
        self.admin_default_favour = max(self.min_favour_value, min(self.max_favour_value, self.admin_default_favour))

    def _get_target_uid(self, event: AstrMessageEvent, text_arg: str) -> Optional[str]:
        """获取目标用户ID，支持At和纯文本"""
        # 1. 检查 At
        bot_self_id = None
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'self_id'):
            bot_self_id = str(event.message_obj.self_id)

        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message'):
            for component in event.message_obj.message:
                if isinstance(component, At):
                    uid = str(component.qq)
                    if bot_self_id and uid == bot_self_id:
                        continue
                    return uid
        
        # 2. 检查文本参数
        if text_arg:
            cleaned_arg = text_arg.strip()
            if is_valid_userid(cleaned_arg):
                return cleaned_arg
            
        return None

    def _get_session_id(self, event: AstrMessageEvent) -> Optional[str]:
        if self.is_global_favour:
            return "global"
        return event.unified_msg_origin

    async def _get_user_display_name(self, event: AstrMessageEvent, user_id: str) -> str:
        try:
            group_id = event.get_group_id()
            if group_id:
                info = await event.bot.get_group_member_info(group_id=int(group_id), user_id=int(user_id), no_cache=True)
                return info.get("card") or info.get("nickname") or user_id
            else:
                info = await event.bot.get_stranger_info(user_id=int(user_id))
                return info.get("nickname") or user_id
        except:
            return user_id

    async def _check_permission(self, event: AstrMessageEvent, required_level: int) -> bool:
        if str(event.get_sender_id()) in self.admins_id:
            return True
        if not isinstance(event, AiocqhttpMessageEvent):
            return False 
        perm_mgr = PermissionManager.get_instance()
        level = await perm_mgr.get_perm_level(event, event.get_sender_id())
        return level >= required_level

    async def _get_initial_favour(self, event: AstrMessageEvent) -> int:
        user_id = str(event.get_sender_id())
        
        if not self.is_global_favour:
            global_rec = await self.db_manager.get_favour(user_id, "global")
            if global_rec:
                return max(self.min_favour_value, min(self.max_favour_value, global_rec.favour))

        is_envoy = str(user_id) in [str(e) for e in self.favour_envoys]
        is_admin = await self._check_permission(event, PermLevel.OWNER) 
        
        base = self.admin_default_favour if (is_envoy or is_admin) else self.default_favour
        return max(self.min_favour_value, min(self.max_favour_value, base))

    def _get_cold_violence_key(self, user_id: str, session_id: Optional[str]) -> str:
        if self.cold_violence_is_global:
            return user_id
        return f"{session_id}:{user_id}" if session_id else user_id

    # ================= 事件处理 =================

    @filter.on_llm_request()
    async def inject_favour_prompt(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        session_id = self._get_session_id(event)
        user_id = str(event.get_sender_id())

        if session_id != "global":
            if self.allowed_sessions and session_id not in self.allowed_sessions:
                return
            if session_id in self.blocked_sessions:
                return

        # 检查冷暴力
        if self.enable_cold_violence:
            cv_key = self._get_cold_violence_key(user_id, session_id)
            if cv_key in self.cold_violence_users:
                expiry = self.cold_violence_users[cv_key]
                if datetime.now() < expiry:
                    remaining = expiry - datetime.now()
                    time_str = f"{int(remaining.total_seconds() // 60)}分"
                    reply = self.cold_violence_replies["on_message"].format(time_str=time_str)
                    await event.send(event.plain_result(reply))
                    event.stop_event()
                    return
                else:
                    del self.cold_violence_users[cv_key]

        record = await self.db_manager.get_favour(user_id, session_id)
        if record:
            curr_favour = record.favour
            curr_rel = record.relationship or "无"
        else:
            curr_favour = await self._get_initial_favour(event)
            curr_rel = "无"

        prompt = f"""
<Plugin_Favour>
当前用户ID: {user_id}
当前好感度: {curr_favour} (范围: {self.min_favour_value}~{self.max_favour_value})
当前关系: {curr_rel}
模式: {self.favour_mode}
规则: {self.favour_rule_prompt}
请根据好感度调整语气。如果用户行为导致好感度变化，请在回复末尾添加 [好感度 上升/降低/持平:数值]。
如果用户请求建立关系，请在回复末尾添加 [用户申请确认关系:关系名:true/false:是否唯一]。
</Plugin_Favour>
"""
        req.system_prompt += prompt

    @filter.on_llm_response()
    async def handle_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        if not hasattr(event, 'message_obj'): return
        msg_id = str(event.message_obj.message_id)
        text = resp.completion_text
        
        update_data = {'change': 0, 'rel': None, 'unique': None}
        
        matches = self.favour_pattern.findall(text)
        for m in matches:
            val = 0
            num = re.search(r'(\d+)', m)
            if num: val = int(num.group(1))
            
            if '降低' in m: update_data['change'] = -val
            elif '上升' in m: update_data['change'] = val
        
        rel_m = self.relationship_pattern.findall(text)
        if rel_m:
            last = rel_m[-1]
            if last[1].lower() == 'true':
                update_data['rel'] = last[0]
                update_data['unique'] = (last[2].lower() == 'true') if len(last) > 2 else False

        if update_data['change'] != 0 or update_data['rel']:
            self.pending_updates[msg_id] = update_data

    @filter.on_decorating_result(priority=100)
    async def update_data(self, event: AstrMessageEvent):
        if not hasattr(event, 'message_obj'): return
        msg_id = str(event.message_obj.message_id)
        data = self.pending_updates.pop(msg_id, None)
        
        if not data: return
        
        res = event.get_result()
        new_chain = []
        for comp in res.chain:
            if isinstance(comp, Plain):
                t = self.favour_pattern.sub("", comp.text)
                t = self.relationship_pattern.sub("", t)
                if t.strip(): new_chain.append(Plain(t))
            else:
                new_chain.append(comp)
        res.chain = new_chain

        user_id = str(event.get_sender_id())
        session_id = self._get_session_id(event)
        
        record = await self.db_manager.get_favour(user_id, session_id)
        old_fav = record.favour if record else await self._get_initial_favour(event)
        
        new_fav = old_fav + data['change']
        new_fav = max(self.min_favour_value, min(self.max_favour_value, new_fav))
        
        rel = data['rel'] if data['rel'] else (record.relationship if record else "")
        uniq = data['unique'] if data['unique'] is not None else (record.is_unique if record else False)
        
        if new_fav < 0 and rel:
            rel = ""
            uniq = False
            
        await self.db_manager.update_favour(user_id, session_id, new_fav, rel, uniq)
        
        # 检查冷暴力
        if self.enable_cold_violence and new_fav <= self.cold_violence_threshold and data['change'] < 0:
            cv_key = self._get_cold_violence_key(user_id, session_id)
            duration = timedelta(minutes=self.cold_violence_duration_minutes)
            self.cold_violence_users[cv_key] = datetime.now() + duration
            res.chain.append(Plain(f"\n{self.cold_violence_replies['on_trigger']}"))

    # ================= 1. 查询类型 =================

    # 1.1 查询好感度 (单人，纯文本)
    @filter.command("查询好感度", alias={'查好感度', '好感度查询', '查看好感度', '好感度'})
    async def query_favour(self, event: AstrMessageEvent, target: str = ""):
        """查询自己或他人的好感度"""
        target_uid = self._get_target_uid(event, target) or str(event.get_sender_id())
        session_id = self._get_session_id(event)
        
        record = await self.db_manager.get_favour(target_uid, session_id)
        fav = record.favour if record else (await self._get_initial_favour(event) if target_uid == str(event.get_sender_id()) else 0)
        rel = record.relationship if record else "无"
        uniq = " (唯一)" if record and record.is_unique else ""
        
        name = await self._get_user_display_name(event, target_uid)
        
        msg = f"🔍 用户：{name}\n🆔 ID：{target_uid}\n❤ 好感度：{fav}\n🔗 关系：{rel}{uniq}"
        yield event.plain_result(msg)

    # 1.2 查询当前好感度 (本群，T2I表格)
    @filter.command("查询当前好感度", alias={'查当前好感度', '查询本群好感度', '查本群好感度', '查群好感度', '查询群好感度', '当前好感度', '本群好感度', '群好感度'})
    async def query_current_session_favour(self, event: AstrMessageEvent):
        """查询当前会话的所有好感度记录"""
        if self.is_global_favour:
            yield event.plain_result("当前为全局模式，此命令无效。请使用【查询全局好感度】。")
            return
            
        session_id = self._get_session_id(event)
        records = await self.db_manager.get_all_in_session(session_id)
        
        if not records:
            yield event.plain_result("当前会话暂无好感度记录。")
            return
            
        # 构建 Markdown 表格
        md_lines = [
            f"# 📊 当前会话好感度列表",
            f"会话ID: {session_id}",
            "",
            "| 用户昵称 | 用户ID | 好感度 | 关系 | 唯一 |",
            "| :--- | :--- | :---: | :---: | :---: |"
        ]
        
        for r in records:
            name = await self._get_user_display_name(event, r.user_id)
            # 处理 Markdown 特殊字符，防止表格错乱
            name = name.replace("|", "\|").replace("\n", " ")
            rel = r.relationship or "无"
            uniq = "是" if r.is_unique else "否"
            md_lines.append(f"| {name} | {r.user_id} | {r.favour} | {rel} | {uniq} |")
            
        md_text = "\n".join(md_lines)
        
        try:
            url = await self.text_to_image(md_text)
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"生成好感度图片失败: {e}")
            yield event.plain_result("生成图片失败，请检查日志。")

    # 1.3 查询全部好感度 (非全局，T2I表格，按会话分组，显示前5后5)
    @filter.command("查询全部好感度", alias={'查全部好感度', '查看全部好感度', '全部好感度'})
    async def query_all_sessions_favour(self, event: AstrMessageEvent):
        """查询所有非全局会话的好感度 (仅Bot管理员)"""
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足！仅Bot管理员可用。")
            return
            
        records = await self.db_manager.get_non_global_records()
        if not records:
            yield event.plain_result("暂无非全局好感度记录。")
            return
            
        # 判断当前是否为私聊环境 (group_id 为空即为私聊)
        is_current_private = not event.get_group_id()
        
        # 按 session_id 分组
        session_groups = {}
        for r in records:
            if r.session_id not in session_groups:
                session_groups[r.session_id] = []
            session_groups[r.session_id].append(r)
            
        md_lines = [f"# 📊 全部会话好感度概览"]
        
        hidden_private_sessions = 0
        
        for sid, group_records in session_groups.items():
            # 判断该 session_id 是否为私聊会话
            # 依据 AstrBot 规范，私聊会话 ID 通常包含 'private'
            is_private_session = "private" in str(sid)
            
            # 如果是私聊会话，且当前不在私聊环境中 -> 隐藏并计数
            if is_private_session and not is_current_private:
                hidden_private_sessions += 1
                continue

            # 按好感度降序排序
            group_records.sort(key=lambda x: x.favour, reverse=True)
            
            md_lines.append(f"\n## 会话: {sid} (共 {len(group_records)} 人)")
            md_lines.append("| 用户ID | 好感度 | 关系 | 唯一 |")
            md_lines.append("| :--- | :---: | :---: | :---: |")
            
            count = len(group_records)
            if count <= 10:
                # 全部显示
                display_list = group_records
            else:
                # 显示前5和后5
                display_list = group_records[:5] + [None] + group_records[-5:]
                
            for r in display_list:
                if r is None:
                    md_lines.append("| ... | ... | ... | ... |")
                else:
                    rel = r.relationship or "无"
                    uniq = "是" if r.is_unique else "否"
                    md_lines.append(f"| {r.user_id} | {r.favour} | {rel} | {uniq} |")
        
        if hidden_private_sessions > 0:
            md_lines.append(f"\n> 另有 {hidden_private_sessions} 个私聊会话的数据已隐藏（仅在私聊查询时显示）。")
            
        md_text = "\n".join(md_lines)
        
        try:
            url = await self.text_to_image(md_text)
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"生成好感度图片失败: {e}")
            yield event.plain_result("生成图片失败，请检查日志。")

    # 1.4 查询全局好感度 (T2I表格)
    @filter.command("查询全局好感度", alias={'全局好感度', '查全局好感度', '查看全局好感度', '全局好感度查询'})
    async def query_global_favour(self, event: AstrMessageEvent):
        """查询全局模式下的好感度 (仅Bot管理员)"""
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足！仅Bot管理员可用。")
            return
            
        records = await self.db_manager.get_global_records()
        if not records:
            yield event.plain_result("暂无全局好感度记录。")
            return
            
        md_lines = [
            f"# 📊 全局好感度记录",
            "",
            "| 用户ID | 好感度 | 关系 | 唯一 |",
            "| :--- | :---: | :---: | :---: |"
        ]
        
        display_records = records[:100]
        
        for r in display_records:
            rel = r.relationship or "无"
            uniq = "是" if r.is_unique else "否"
            md_lines.append(f"| {r.user_id} | {r.favour} | {rel} | {uniq} |")
            
        if len(records) > 100:
            md_lines.append(f"\n> ...还有 {len(records)-100} 条记录未显示")
            
        md_text = "\n".join(md_lines)
        
        try:
            url = await self.text_to_image(md_text)
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"生成好感度图片失败: {e}")
            yield event.plain_result("生成图片失败，请检查日志。")

    # ================= 2. 修改类型 =================

    # 2.1 修改好感度
    @filter.command("修改好感度")
    async def modify_favour(self, event: AstrMessageEvent, target: str, value: int):
        """修改好感度: /修改好感度 @用户 50 (群管理员)"""
        if not await self._check_permission(event, PermLevel.ADMIN):
            yield event.plain_result("权限不足！需要群管理员及以上权限。")
            return
            
        uid = self._get_target_uid(event, target)
        if not uid:
            yield event.plain_result("未找到用户，请使用 @ 或输入 ID。")
            return
            
        session_id = self._get_session_id(event)
        await self.db_manager.update_favour(uid, session_id, favour=value)
        yield event.plain_result(f"已将用户 {uid} 的好感度修改为 {value}。")

    # 2.2 修改关系
    @filter.command("修改关系")
    async def modify_relationship(self, event: AstrMessageEvent, target: str, rel_name: str, is_unique: int):
        """修改关系: /修改关系 @用户 挚友 1 (群主)"""
        if not await self._check_permission(event, PermLevel.OWNER):
            yield event.plain_result("权限不足！需要群主权限。")
            return
            
        uid = self._get_target_uid(event, target)
        if not uid:
            yield event.plain_result("未找到用户。")
            return
            
        session_id = self._get_session_id(event)
        unique_bool = bool(is_unique)
        await self.db_manager.update_favour(uid, session_id, relationship=rel_name, is_unique=unique_bool)
        yield event.plain_result(f"已更新用户 {uid} 关系为 {rel_name} (唯一: {unique_bool})。")

    # 2.3 解除关系
    @filter.command("解除关系")
    async def clear_relationship(self, event: AstrMessageEvent, target: str):
        """解除关系: /解除关系 @用户 (群主)"""
        if not await self._check_permission(event, PermLevel.OWNER):
            yield event.plain_result("权限不足！需要群主权限。")
            return
            
        uid = self._get_target_uid(event, target)
        if not uid:
            yield event.plain_result("未找到用户。")
            return
            
        session_id = self._get_session_id(event)
        await self.db_manager.update_favour(uid, session_id, relationship="", is_unique=False)
        yield event.plain_result(f"已解除用户 {uid} 的所有关系。")

    # 2.4 全局修改/解除
    @filter.command("全局修改好感度")
    async def global_modify_favour(self, event: AstrMessageEvent, target: str, value: int):
        """全局修改好感度 (Bot管理员)"""
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足！仅Bot管理员可用。")
            return
        
        uid = self._get_target_uid(event, target)
        if not uid: return
        
        count = await self.db_manager.update_user_all_records(uid, favour=value)
        yield event.plain_result(f"已更新用户 {uid} 在所有会话中的好感度为 {value} (共 {count} 条记录)。")

    @filter.command("全局修改关系")
    async def global_modify_rel(self, event: AstrMessageEvent, target: str, rel_name: str, is_unique: int):
        """全局修改关系 (Bot管理员)"""
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足！仅Bot管理员可用。")
            return
        
        uid = self._get_target_uid(event, target)
        if not uid: return
        
        count = await self.db_manager.update_user_all_records(uid, relationship=rel_name, is_unique=bool(is_unique))
        yield event.plain_result(f"已更新用户 {uid} 在所有会话中的关系为 {rel_name} (共 {count} 条记录)。")

    @filter.command("全局解除关系")
    async def global_clear_rel(self, event: AstrMessageEvent, target: str):
        """全局解除关系 (Bot管理员)"""
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足！仅Bot管理员可用。")
            return
        
        uid = self._get_target_uid(event, target)
        if not uid: return
        
        count = await self.db_manager.update_user_all_records(uid, relationship="", is_unique=False)
        yield event.plain_result(f"已解除用户 {uid} 在所有会话中的关系 (共 {count} 条记录)。")

    # 2.5 跨会话修改
    @filter.command("跨会话修改")
    async def cross_session_modify(self, event: AstrMessageEvent, target_sid: str, operation: str, target_uid: str, arg1: str = "", arg2: str = ""):
        """
        跨会话修改数据 (Bot管理员)
        用法:
        /跨会话修改 <sid> 修改好感度 <uid> <数值>
        /跨会话修改 <sid> 修改关系 <uid> <关系名> <1/0>
        /跨会话修改 <sid> 解除关系 <uid>
        """
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足！仅Bot管理员可用。")
            return

        if not target_sid or not operation or not target_uid:
             yield event.plain_result("参数错误。请查看帮助。")
             return

        if not is_valid_userid(target_uid):
             yield event.plain_result(f"用户ID {target_uid} 格式无效。")
             return

        if operation == "修改好感度":
            try:
                val = int(arg1)
                await self.db_manager.update_favour(target_uid, target_sid, favour=val)
                yield event.plain_result(f"已将会话 {target_sid} 中用户 {target_uid} 的好感度修改为 {val}。")
            except ValueError:
                yield event.plain_result("数值必须为整数。")

        elif operation == "修改关系":
            if not arg1:
                yield event.plain_result("缺少关系名称。")
                return
            rel_name = arg1
            is_unique = bool(int(arg2)) if arg2.isdigit() else False
            await self.db_manager.update_favour(target_uid, target_sid, relationship=rel_name, is_unique=is_unique)
            yield event.plain_result(f"已更新会话 {target_sid} 中用户 {target_uid} 的关系为 {rel_name} (唯一: {is_unique})。")

        elif operation == "解除关系":
            await self.db_manager.update_favour(target_uid, target_sid, relationship="", is_unique=False)
            yield event.plain_result(f"已解除会话 {target_sid} 中用户 {target_uid} 的所有关系。")

        else:
            yield event.plain_result(f"未知操作: {operation}。支持的操作: 修改好感度, 修改关系, 解除关系")

    # ================= 3. 帮助类型 =================

    @filter.command("好感度帮助", alias={'查看好感度帮助'})
    async def help_menu(self, event: AstrMessageEvent):
        """显示可用命令菜单"""
        is_superuser = await self._check_permission(event, PermLevel.SUPERUSER)
        is_owner = await self._check_permission(event, PermLevel.OWNER)
        is_admin = await self._check_permission(event, PermLevel.ADMIN)
        
        msg = ["⭐ 好感度插件命令菜单 ⭐"]
        
        msg.append("\n[通用命令]")
        msg.append("- 查询好感度 [@用户]")
        msg.append("- 查询当前好感度")
        msg.append("- 好感度指令帮助")
        
        if is_admin or is_superuser:
            msg.append("\n[管理员命令]")
            msg.append("- 修改好感度 @用户 <数值>")
        
        if is_owner or is_superuser:
            msg.append("\n[群主命令]")
            msg.append("- 修改关系 @用户 <关系名> <1/0>")
            msg.append("- 解除关系 @用户")
            
        if is_superuser:
            msg.append("\n[Bot管理员命令]")
            msg.append("- 查询全部好感度")
            msg.append("- 查询全局好感度")
            msg.append("- 全局修改好感度 @用户 <数值>")
            msg.append("- 全局修改关系 @用户 <关系名> <1/0>")
            msg.append("- 全局解除关系 @用户")
            msg.append("- 跨会话修改 <sid> <操作> ...")
            
        yield event.plain_result("\n".join(msg))

    @filter.command("好感度指令帮助")
    async def help_usage(self, event: AstrMessageEvent):
        """显示详细指令用法"""
        msg = """⭐ 好感度指令用法示例 ⭐

1. 查询好感度
   用法: /查询好感度 [@用户]
   示例: /查询好感度
   示例: /查询好感度 @糯米茨

2. 修改好感度 (管理员)
   用法: /修改好感度 @用户 <数值>
   示例: /修改好感度 @糯米茨 60

3. 修改关系 (群主)
   用法: /修改关系 @用户 <关系名> <1/0>
   说明: 1代表唯一关系(如恋人)，0代表不唯一(如朋友)
   示例: /修改关系 @糯米茨 挚友 0
   示例: /修改关系 @小林 恋人 1

4. 解除关系 (群主)
   用法: /解除关系 @用户
   示例: /解除关系 @糯米茨

5. 全局操作 (Bot管理员)
   示例: /全局修改好感度 @糯米茨 100
   说明: 将修改该用户在所有群/私聊中的数据。

6. 跨会话修改 (Bot管理员)
   示例: /跨会话修改 group:123456 修改好感度 10001 50
"""
        yield event.plain_result(msg)