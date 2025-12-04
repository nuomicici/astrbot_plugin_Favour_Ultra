import re
import shutil
import traceback
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, AsyncGenerator, Optional, List

from astrbot.api import logger
from astrbot.core.message.components import Plain, At
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api.star import Star, register, Context
from astrbot.api import AstrBotConfig
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.event import filter

# 导入拆分后的模块
from .const import DEFAULT_CONFIG, EXCLUSIVE_RELATIONSHIPS, FAVOUR_PATTERN, RELATIONSHIP_PATTERN, PROMPT_TEMPLATE
from .utils import is_valid_userid, format_timedelta
from .permission import PermLevel, PermissionManager
from .storage import FavourFileManager, GlobalFavourFileManager

@register("favour_ultra", "Soulter", "好感度/关系管理(重构版)", "1.2.0")
class FavourManagerTool(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 读取配置，使用 const.py 中的默认值作为 fallback
        self.default_favour = self.config.get("default_favour", DEFAULT_CONFIG["default_favour"])
        self.admin_default_favour = self.config.get("admin_default_favour", DEFAULT_CONFIG["admin_default_favour"])
        self.favour_rule_prompt = self.config.get("favour_rule_prompt", DEFAULT_CONFIG["favour_rule_prompt"])
        self.is_global_favour = self.config.get("is_global_favour", DEFAULT_CONFIG["is_global_favour"])
        self.favour_increase_min = self.config.get("favour_increase_min", DEFAULT_CONFIG["favour_increase_min"])
        self.favour_increase_max = self.config.get("favour_increase_max", DEFAULT_CONFIG["favour_increase_max"])
        self.favour_decrease_min = self.config.get("favour_decrease_min", DEFAULT_CONFIG["favour_decrease_min"])
        self.favour_decrease_max = self.config.get("favour_decrease_max", DEFAULT_CONFIG["favour_decrease_max"])
        self.enable_clear_backup = self.config.get("enable_clear_backup", DEFAULT_CONFIG["enable_clear_backup"])
        
        self.cold_violence_threshold = self.config.get("cold_violence_threshold", DEFAULT_CONFIG["cold_violence_threshold"])
        self.cold_violence_duration_minutes = self.config.get("cold_violence_duration_minutes", DEFAULT_CONFIG["cold_violence_duration_minutes"])
        
        default_replies = DEFAULT_CONFIG["cold_violence_replies"]
        self.cold_violence_replies = self.config.get("cold_violence_replies", default_replies)
        # 补全可能缺失的回复配置
        for key, value in default_replies.items():
            if key not in self.cold_violence_replies:
                self.cold_violence_replies[key] = value

        self._validate_config()
        
        # 初始化权限管理
        self.admins_id = context.get_config().get("admins_id", [])
        self.perm_level_threshold = self.config.get("level_threshold", DEFAULT_CONFIG["level_threshold"])
        PermissionManager.get_instance(superusers=self.admins_id, level_threshold=self.perm_level_threshold)
        
        # 初始化数据目录
        base_data_dir = Path(context.get_config().get("plugin.data_dir", "./data"))
        self.data_dir = base_data_dir / "plugin_data" / "astrbot_plugin_favour_ultra"
        self._migrate_old_data(base_data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 初始化文件管理器
        self.file_manager = FavourFileManager(self.data_dir, self.enable_clear_backup)
        self.global_hao_gan_du = GlobalFavourFileManager(self.data_dir)
        
        mode_text = "全局模式" if self.is_global_favour else "对话隔离模式"
        logger.info(f"好感度插件已初始化 - {mode_text}")
        
        self.pending_updates = {}
        self.cold_violence_users: Dict[str, datetime] = {}

    def _migrate_old_data(self, base_data_dir: Path):
        """处理旧数据迁移"""
        old_data_dir = base_data_dir / "hao_gan_du"
        if old_data_dir.exists() and not self.data_dir.exists():
            logger.warning(f"检测到旧版数据 {old_data_dir}，正在迁移...")
            try:
                self.data_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(old_data_dir, self.data_dir)
                trash_dir = base_data_dir / "hao_gan_du_old"
                if trash_dir.exists(): shutil.rmtree(trash_dir)
                old_data_dir.rename(trash_dir)
                logger.info("数据迁移成功")
            except Exception as e:
                logger.error(f"迁移失败: {e}")

    def _validate_config(self):
        if not (-100 <= self.default_favour <= 100):
            self.default_favour = DEFAULT_CONFIG["default_favour"]
        # ... 其他简单的校验省略，保持代码整洁

    def _get_target_uid(self, event: AstrMessageEvent, text_arg: str) -> Optional[str]:
        """解析目标用户ID，支持@和纯数字，过滤机器人自己"""
        bot_self_id = None
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'self_id'):
            bot_self_id = str(event.message_obj.self_id)

        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message'):
            for component in event.message_obj.message:
                if isinstance(component, At):
                    uid = str(component.qq)
                    # 如果At的是机器人自己，则跳过
                    if bot_self_id and uid == bot_self_id:
                        continue
                    return uid
        
        cleaned_arg = text_arg.strip()
        if is_valid_userid(cleaned_arg):
            return cleaned_arg
        return None

    async def _get_user_display_name(self, event: AstrMessageEvent, user_id: Optional[str] = None) -> str:
        """获取用户昵称"""
        target_user_id = user_id or str(event.get_sender_id())
        group_id = event.get_group_id()
        
        if group_id:
            try:
                info = await event.bot.get_group_member_info(
                    group_id=int(group_id), user_id=int(target_user_id), no_cache=True
                )
                return info.get("card", "") or info.get("nickname", "") or target_user_id
            except Exception:
                pass
        
        try:
            info = await event.bot.get_stranger_info(user_id=int(target_user_id))
            return info.get("nickname", "") or target_user_id
        except Exception:
            return target_user_id

    async def _check_permission(self, event: AstrMessageEvent, required_level: int) -> bool:
        if str(event.get_sender_id()) in self.admins_id:
            return True
        if not isinstance(event, AiocqhttpMessageEvent):
            return False
        user_level = await PermissionManager.get_instance().get_perm_level(event, event.get_sender_id())
        return user_level >= required_level

    def _get_session_id(self, event: AstrMessageEvent) -> Optional[str]:
        return None if self.is_global_favour else event.unified_msg_origin

    async def _get_initial_favour(self, event: AstrMessageEvent) -> int:
        user_id = str(event.get_sender_id())
        if not self.is_global_favour:
            gf = await self.global_hao_gan_du.get_user_global_favour(user_id)
            if gf is not None: return gf
            
        is_envoy = str(user_id) in [str(e) for e in self.config.get("favour_envoys", [])]
        is_owner_or_admin = await self._check_permission(event, PermLevel.OWNER)
        
        base = self.admin_default_favour if (is_owner_or_admin or is_envoy) else self.default_favour
        return max(-100, min(100, base))

    # ================= 核心 Hooks =================

    @filter.on_llm_request()
    async def inject_favour_prompt(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        user_id = str(event.get_sender_id())
        
        # 1. 冷暴力拦截
        if user_id in self.cold_violence_users:
            exp_time = self.cold_violence_users[user_id]
            if datetime.now() < exp_time:
                time_str = format_timedelta(exp_time - datetime.now())
                reply = self.cold_violence_replies.get("on_message", "").format(time_str=time_str)
                await event.send(event.plain_result(reply))
                event.stop_event()
                return
            else:
                del self.cold_violence_users[user_id]

        # 2. 准备 Prompt 数据
        session_id = self._get_session_id(event)
        record = await self.file_manager.get_user_favour(user_id, session_id)
        if record:
            curr_fav = record["favour"]
            curr_rel = record["relationship"] or "无"
        else:
            curr_fav = await self._get_initial_favour(event)
            curr_rel = "无"

        # 3. 唯一关系检查
        exclusive_addon = ""
        if not self.is_global_favour and session_id:
            all_data = await self.file_manager.read_favour()
            session_data = [i for i in all_data if i["session_id"] == session_id]
            exists = []
            for item in session_data:
                rel = item.get("relationship", "")
                if rel and any(k in rel for k in EXCLUSIVE_RELATIONSHIPS):
                    exists.append(f"{rel}{item['userid']}")
            if exists:
                exclusive_addon = f"\n本群聊中已有唯一关系：{'，'.join(exists)}（如果有更多，用并列方式列出），任何想要成为类似关系的请求你都必须据此拒绝。"

        # 4. 构造 Prompt
        prompt = PROMPT_TEMPLATE.format(
            user_id=user_id,
            admin_status="Bot管理员" if self._is_admin(event) else "普通用户",
            current_favour=curr_fav,
            current_relationship=curr_rel,
            the_rule=self.favour_rule_prompt,
            exclusive_prompt_addon=exclusive_addon,
            increase_min=self.favour_increase_min,
            increase_max=self.favour_increase_max,
            decrease_min=self.favour_decrease_min,
            decrease_max=self.favour_decrease_max,
            cold_violence_threshold=self.cold_violence_threshold
        )
        req.system_prompt = f"{prompt}\n{req.system_prompt}".strip()

    @filter.on_llm_response()
    async def handle_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        if not hasattr(event, 'message_obj') or not hasattr(event.message_obj, 'message_id'):
            return
        
        msg_id = str(event.message_obj.message_id)
        text = resp.completion_text
        
        update_data = {'change': 0, 'rel_update': None}
        has_tag = False
        
        matches = FAVOUR_PATTERN.findall(text)
        if matches:
            has_tag = True
            valid_changes = []
            for m in matches:
                m_str = m.lower().strip()
                num_match = re.search(r'(\d+)', m_str)
                val = abs(int(num_match.group(1))) if num_match else 0
                
                change = 0
                if re.search(r'[降低]', m_str):
                    change = -max(self.favour_decrease_min, min(self.favour_decrease_max, val))
                elif re.search(r'[上升]', m_str):
                    change = max(self.favour_increase_min, min(self.favour_increase_max, val))
                elif re.search(r'[持平]', m_str):
                    change = 0
                else:
                    continue
                valid_changes.append(change)
            
            if valid_changes:
                update_data['change'] = valid_changes[-1]

        rel_matches = RELATIONSHIP_PATTERN.findall(text)
        if rel_matches:
            name, boolean = rel_matches[-1]
            if boolean.lower() == "true" and name.strip():
                update_data['rel_update'] = name.strip()

        if has_tag or update_data['rel_update']:
            self.pending_updates[msg_id] = update_data

    @filter.on_decorating_result(priority=100)
    async def cleanup_and_update_favour(self, event: AstrMessageEvent) -> None:
        result = event.get_result()
        if not result or not result.chain: return

        # 1. 更新逻辑
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message_id'):
            msg_id = str(event.message_obj.message_id)
            data = self.pending_updates.pop(msg_id, None)
            
            if data:
                change = data['change']
                rel_up = data['rel_update']
                uid = str(event.get_sender_id())
                sid = self._get_session_id(event)
                
                try:
                    record = await self.file_manager.get_user_favour(uid, sid)
                    if not record:
                        init_fav = await self._get_initial_favour(event)
                        record = {"favour": init_fav, "relationship": ""}
                    
                    old_fav = record["favour"]
                    new_fav = max(-100, min(100, old_fav + change))
                    
                    old_rel = record.get("relationship", "") or ""
                    new_rel = rel_up if rel_up is not None else old_rel
                    
                    if new_fav < 0 and old_rel:
                        new_rel = ""
                    
                    if new_fav != old_fav or new_rel != old_rel:
                        await self.file_manager.update_user_favour(uid, sid, new_fav, new_rel)
                        logger.info(f"更新用户[{uid}]: {old_fav}->{new_fav}, Rel: {old_rel}->{new_rel}")

                    if new_fav <= self.cold_violence_threshold and change < 0:
                        self.cold_violence_users[uid] = datetime.now() + timedelta(minutes=self.cold_violence_duration_minutes)
                        trig_msg = self.cold_violence_replies.get("on_trigger")
                        if trig_msg:
                            result.chain.append(Plain(f"\n{trig_msg}"))

                except Exception as e:
                    logger.error(f"更新异常: {e}")

        # 2. 清洗逻辑
        new_chain = []
        cleaned = False
        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                orig = comp.text
                new_t = FAVOUR_PATTERN.sub("", orig)
                new_t = RELATIONSHIP_PATTERN.sub("", new_t).strip()
                if orig != new_t: cleaned = True
                if new_t: new_chain.append(Plain(new_t))
            else:
                new_chain.append(comp)
        
        if cleaned:
            result.chain = new_chain

    # ================= 指令系统 =================

    async def _respond_favour_info(self, event: AstrMessageEvent, target_uid: str):
        """共用的好感度响应生成器"""
        viewer_id = str(event.get_sender_id())
        # 如果是查询者自己，检查冷暴力
        if viewer_id == target_uid and viewer_id in self.cold_violence_users:
            exp = self.cold_violence_users[viewer_id]
            if datetime.now() < exp:
                t_str = format_timedelta(exp - datetime.now())
                reply = self.cold_violence_replies.get("on_query", "").format(time_str=t_str)
                yield event.plain_result(reply)
                return
            else:
                del self.cold_violence_users[viewer_id]

        sid = self._get_session_id(event)
        rec = await self.file_manager.get_user_favour(target_uid, sid)
        
        if rec:
            fav, rel = rec["favour"], rec["relationship"] or "无"
        else:
            # 查询他人且无记录时，尝试获取全局数据或默认值
            fav = self.default_favour
            if not self.is_global_favour:
                gf = await self.global_hao_gan_du.get_user_global_favour(target_uid)
                if gf is not None: fav = gf
            rel = "无"

        nick = await self._get_user_display_name(event, target_uid)
        mode = "全局" if self.is_global_favour else f"会话: {sid}"
        
        txt = f"用户：{nick} ({target_uid})\n模式：{mode}\n───\n好感度：{fav} / 100\n关系：{rel}"
        try:
            url = await self.text_to_image(f"# 好感度查询\n\n{txt}")
            yield event.image_result(url)
        except:
            yield event.plain_result(txt)

    @filter.command("查看我的好感度", alias={'我的好感度'})
    async def query_my_favour(self, event: AstrMessageEvent):
        async for x in self._respond_favour_info(event, str(event.get_sender_id())): yield x

    @filter.command("查看他人好感度", alias={'查询他人好感度', 'ta的好感度', '查看用户好感度', '查询用户好感度', '好感度查询','查看好感度','查询好感度','查好感度'})
    async def query_other_favour(self, event: AstrMessageEvent, target: str):
        uid = self._get_target_uid(event, target)
        if not uid:
            yield event.plain_result("无法识别目标用户，请艾特或输入ID")
            return
        async for x in self._respond_favour_info(event, uid): yield x

    @filter.command("修改好感度")
    async def modify_favour(self, event: AstrMessageEvent, target: str, val: str):
        if not await self._check_permission(event, PermLevel.ADMIN):
            yield event.plain_result("权限不足，需要管理员权限")
            return
        
        uid = self._get_target_uid(event, target)
        if not uid:
            yield event.plain_result("无效用户")
            return
            
        try:
            v = int(val)
            if not -100 <= v <= 100: raise ValueError
        except:
            yield event.plain_result("数值必须在 -100 到 100 之间")
            return
            
        await self.file_manager.update_user_favour(uid, self._get_session_id(event), favour=v)
        # 获取更新后的值以确认
        rec = await self.file_manager.get_user_favour(uid, self._get_session_id(event))
        curr = rec["favour"] if rec else "未知"
        yield event.plain_result(f"已将用户 {uid} 好感度修改为 {v} (当前: {curr})")

    @filter.command("删除好感度数据")
    async def delete_user_favour(self, event: AstrMessageEvent, target: str):
        if not await self._check_permission(event, PermLevel.ADMIN):
            yield event.plain_result("权限不足，需要管理员权限")
            return
            
        uid = self._get_target_uid(event, target)
        if not uid:
            yield event.plain_result("无效用户")
            return
            
        success, msg = await self.file_manager.delete_user_favour(uid, self._get_session_id(event))
        yield event.plain_result(msg)

    @filter.command("查询好感度数据", alias={'查看好感度数据', '本群好感度查询', '查看本群好感度', '本群好感度'})
    async def query_favour_data(self, event: AstrMessageEvent):
        if not await self._check_permission(event, PermLevel.ADMIN):
            yield event.plain_result("权限不足")
            return
        
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此命令仅限群聊")
            return

        sid = self._get_session_id(event)
        data = await self.file_manager.read_favour()
        session_data = [i for i in data if i["session_id"] == sid]
        
        if not session_data:
            yield event.plain_result("当前会话暂无数据")
            return

        # 批量获取信息逻辑保持一致
        async def get_info(u_id: str):
            try:
                info = await event.bot.get_group_member_info(group_id=int(group_id), user_id=int(u_id), no_cache=True)
                return info.get("card", "") or info.get("nickname", ""), info.get("nickname", u_id)
            except:
                return "未知/退群", "未知"

        tasks = [get_info(item['userid']) for item in session_data]
        infos = await asyncio.gather(*tasks)

        lines = [f"# 当前会话好感度数据 (会话: {sid or '全局'})\n\n| 群昵称 | 用户 (ID) | 好感度 | 关系 |\n|----|----|----|----|"]
        for i, item in enumerate(session_data):
            gnic, pnic = infos[i]
            lines.append(f"| {gnic} | {pnic} ({item['userid']}) | {item['favour']} | {item['relationship'] or '无'} |")
        
        lines.append(f"\n总计：{len(session_data)}条")
        txt = "\n".join(lines)
        try:
            url = await self.text_to_image(txt)
            yield event.image_result(url)
        except:
            yield event.plain_result(txt)

    @filter.command("查询全部好感度", alias={'查看全部好感度', '查询全局好感度', '查看全局好感度', '查询好感度全局'})
    async def query_all_favour(self, event: AstrMessageEvent):
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足，需要Bot管理员权限")
            return
            
        data = await self.file_manager.read_favour()
        if not data:
            yield event.plain_result("数据为空")
            return
            
        # 按会话分组
        groups = {}
        for item in data:
            sid = item["session_id"] or "全局"
            if sid not in groups: groups[sid] = []
            groups[sid].append(item)
            
        lines = ["📊 全部好感度数据："]
        for sid, items in groups.items():
            gid = None
            is_group = False
            if sid and isinstance(sid, str):
                parts = sid.split('/')
                if len(parts) == 3 and parts[1] == 'group':
                    is_group = True
                    gid = parts[2]
            
            async def get_info(u_id: str):
                try:
                    if is_group and gid:
                        info = await event.bot.get_group_member_info(group_id=int(gid), user_id=int(u_id), no_cache=True)
                        return info.get("card", "") or info.get("nickname", u_id)
                    else:
                        info = await event.bot.get_stranger_info(user_id=int(u_id))
                        return info.get("nickname", u_id)
                except:
                    return "未知"

            tasks = [get_info(item['userid']) for item in items]
            infos = await asyncio.gather(*tasks)
            
            lines.append(f"\n# 会话：{sid}\n| 昵称 | 用户ID | 好感度 | 关系 |\n|---|---|---|---|")
            for i, item in enumerate(items):
                lines.append(f"| {infos[i]} | {item['userid']} | {item['favour']} | {item['relationship'] or '无'} |")
                
        txt = "\n".join(lines)
        try:
            url = await self.text_to_image(txt)
            yield event.image_result(url)
        except:
            yield event.plain_result(txt)

    @filter.command("清空当前好感度")
    async def clear_conversation_favour_prompt(self, event: AstrMessageEvent):
        if not await self._check_permission(event, PermLevel.OWNER):
            yield event.plain_result("权限不足，需要群主权限")
            return
        hint = "（已自动备份）" if self.enable_clear_backup else "（⚠️无备份）"
        yield event.plain_result(f"确认清空当前会话数据？{hint}\n确认请发送：【清空当前好感度 确认】")

    @filter.command("清空当前好感度 确认")
    async def clear_conversation_favour(self, event: AstrMessageEvent):
        if not await self._check_permission(event, PermLevel.OWNER):
            yield event.plain_result("权限不足")
            return
        
        sid = self._get_session_id(event)
        async with self.file_manager.lock:
            data = await self.file_manager.read_favour()
            new_data = [i for i in data if i["session_id"] != sid]
            success = await self.file_manager.write_favour(new_data)
        yield event.plain_result("已清空" if success else "清空失败")

    @filter.command("清空全局好感度数据")
    async def clear_global_favour_prompt(self, event: AstrMessageEvent):
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足，需要Bot管理员权限")
            return
        hint = "（已自动备份）" if self.enable_clear_backup else "（⚠️无备份）"
        yield event.plain_result(f"确认清空所有数据？{hint}\n确认请发送：【清空全局好感度数据 确认】")

    @filter.command("清空全局好感度数据 确认")
    async def clear_global_favour(self, event: AstrMessageEvent):
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("权限不足")
            return
        success = await self.file_manager.clear_all_favour()
        yield event.plain_result("已清空全局数据" if success else "清空失败")

    @filter.command("取消冷暴力", alias={'解除冷暴力'})
    async def cancel_cold(self, event: AstrMessageEvent, target: str):
        if not await self._check_permission(event, PermLevel.SUPERUSER):
            yield event.plain_result("需要Bot管理员权限")
            return
        uid = self._get_target_uid(event, target)
        if not uid:
            yield event.plain_result("无效用户")
            return
            
        if uid in self.cold_violence_users:
            del self.cold_violence_users[uid]
            yield event.plain_result(f"已解除用户 {uid} 的冷暴力")
        else:
            yield event.plain_result("该用户未处于冷暴力状态")

    @filter.command("查看好感度帮助", alias={'好感度帮助', '好感度插件帮助'})
    async def help_text(self, event: AstrMessageEvent):
        """查看帮助文档"""
        current_mode = "全局模式（共享）" if self.is_global_favour else "对话隔离模式（独立）"
        is_admin = await self._check_permission(event, PermLevel.OWNER)

        if is_admin:
            help_msg = f"""
======⭐ 好感度插件 - 管理员帮助 ⭐======

📌 当前模式：{current_mode}

⚙️ 权限等级：Bot管理员 > 群主 > 群管理员 > 高等级成员 > 普通用户

📋 用户命令
1. 查看我的好感度
2. 查看他人好感度 @用户 (或输入ID)

🔑 管理员命令 (目标均支持 @用户 或 ID)
1. 修改好感度 @用户 <数值> - (群管及以上)
2. 删除好感度数据 @用户 - (群管及以上)
3. 查询好感度数据 - (群管及以上, 本群列表)
4. 清空当前好感度 - (群主及以上, 清空本群)
5. 查询全部好感度 - (Bot管理员, 全局列表)
6. 清空全局好感度数据 - (Bot管理员, 全局清空)
7. 取消冷暴力 @用户 - (Bot管理员)

⚠️ 数据文件在 data/plugin_data/astrbot_plugin_favour_ultra/
==================================
"""
        else:
            help_msg = f"""
====== 好感度帮助 ======

📋 可用命令
1. 查看我的好感度 : 查看自己的好感度
2. 查看他人好感度 @用户 : 查看TA的好感度
3. 查看好感度帮助 : 显示此信息

==========================
"""
        yield event.plain_result(help_msg.strip())

    async def terminate(self) -> None:
        pass
