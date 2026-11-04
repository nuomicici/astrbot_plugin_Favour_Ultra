# storage.py
import os
import re
import time
import json
import asyncio
import functools
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from aiofiles import open as aio_open
from aiofiles.os import path as aio_path
from sqlmodel import SQLModel, Field, select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text, Column, DateTime
from sqlalchemy.exc import OperationalError as SAOperationalError, IntegrityError as SAIntegrityError
from astrbot.api import logger
from .utils import is_valid_userid


def _retry_on_locked(max_retries: int = 3, base_delay: float = 0.3):
    """装饰器：在遇到 SQLite database is locked 时自动重试。"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except (SAOperationalError, Exception) as e:
                    err_str = str(e).lower()
                    if 'database is locked' in err_str and attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"[DB重试] database is locked，{delay:.1f}s 后重试 ({attempt+1}/{max_retries})")
                        await asyncio.sleep(delay)
                    else:
                        raise
        return wrapper
    return decorator

# 定义数据库模型
class FavourRecord(SQLModel, table=True):
    __tablename__ = "favour_records"
    __table_args__ = {"extend_existing": True}
    
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    session_id: str = Field(default="global", index=True) # "global" 表示全局，或者具体的 session_id
    favour: int = Field(default=0)
    relationship: str = Field(default="")
    is_unique: bool = Field(default=False)
    username: str = Field(default="")  # 用户昵称，供 WebUI 数据管理展示
    #################
    created_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, default=datetime.now))
    updated_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, default=datetime.now, onupdate=datetime.now))
    last_interaction: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, default=datetime.now))


class FavourDBManager:
    """基于SQLite的好感度数据库管理器"""
    def __init__(self, data_dir: Path, min_val: int = -100, max_val: int = 100):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "favour.db"
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.min_val = min_val
        self.max_val = max_val
        
        # 创建异步引擎（优化 SQLite 并发：限制连接池 + busy timeout）
        self.engine = create_async_engine(
            self.db_url, 
            echo=False,
            connect_args={"timeout": 30},  # SQLite busy timeout 30秒，避免 database is locked
            pool_size=1,       # SQLite 单文件数据库，限制为单连接
            max_overflow=2,    # 允许少量溢出以应对突发并发
        )
        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self._initialized = False
        self._init_lock = asyncio.Lock()
        # 由插件注入：返回当前确定存在的平台 ID 集合，用于识别“平台已不存在”的孤儿别名记录；
        # 返回 None/空集合表示无法确定，此时不会认领孤儿别名（避免串号）。
        self.platform_resolver: Optional[Callable[[], Optional[set]]] = None

    def set_limits(self, min_val: int, max_val: int) -> None:
        """热更新好感度边界（供 WebUI 配置保存后调用）。"""
        self.min_val = min_val
        self.max_val = max_val
        logger.debug(f"[DB边界] 好感度上下限已更新为 [{min_val}, {max_val}]")

    async def init_db(self):
        """初始化数据库表并执行必要的迁移"""
        if self._initialized:
            return
            
        async with self._init_lock:
            if self._initialized:
                return
                
            try:
                async with self.engine.begin() as conn:
                    # 启用 WAL 模式（提升并发读写性能，减少 database is locked）
                    await conn.execute(text("PRAGMA journal_mode=WAL"))
                    await conn.execute(text("PRAGMA busy_timeout=30000"))
                    
                    # 检查表是否存在
                    result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='favour_records'"))
                    table_exists = result.scalar() is not None

                    if not table_exists:
                        await conn.run_sync(SQLModel.metadata.create_all)
                    else:
                        # 检查并添加缺失的字段
                        result = await conn.execute(text("PRAGMA table_info(favour_records)"))
                        columns = [row[1] for row in result.fetchall()]
                        if "created_at" not in columns:
                            logger.info("正在升级数据库：添加 created_at 字段...")
                            await conn.execute(text("ALTER TABLE favour_records ADD COLUMN created_at DATETIME"))
                            await conn.execute(text("UPDATE favour_records SET created_at = updated_at WHERE created_at IS NULL"))
                            logger.info("数据库升级完成（created_at）。")
                        if "last_interaction" not in columns:
                            logger.info("正在升级数据库：添加 last_interaction 字段...")
                            await conn.execute(text("ALTER TABLE favour_records ADD COLUMN last_interaction DATETIME"))
                            await conn.execute(text("UPDATE favour_records SET last_interaction = updated_at WHERE last_interaction IS NULL"))
                            logger.info("数据库升级完成（last_interaction）。")
                        if "username" not in columns:
                            logger.info("正在升级数据库：添加 username 字段...")
                            await conn.execute(text("ALTER TABLE favour_records ADD COLUMN username VARCHAR(128) DEFAULT ''"))
                            logger.info("数据库升级完成（username）。")
                            #################

                        # 添加唯一索引防止重复行（修复 #26：并发 select-then-insert 可能撞出重复）
                        result = await conn.execute(text(
                            "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_user_session'"
                        ))
                        if result.scalar() is None:
                            # 先清理可能已存在的重复行（保留 id 最大的那条）
                            await conn.execute(text(
                                "DELETE FROM favour_records WHERE id NOT IN ("
                                "  SELECT MAX(id) FROM favour_records GROUP BY user_id, session_id"
                                ")"
                            ))
                            await conn.execute(text(
                                "CREATE UNIQUE INDEX uq_user_session ON favour_records(user_id, session_id)"
                            ))
                            logger.info("数据库升级完成（UNIQUE INDEX uq_user_session）。")

                self._initialized = True
                logger.info(f"好感度数据库已初始化: {self.db_path}")
            except Exception as e:
                logger.error(f"数据库初始化失败: {e}")

    async def migrate_from_json(self, json_path: Path, is_global: bool = False):
        """从旧版JSON文件迁移数据"""
        await self.init_db()
        if not await aio_path.exists(json_path):
            return

        try:
            async with aio_open(json_path, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)

            count = 0
            async with self.async_session() as session:
                if is_global:
                    if isinstance(data, dict):
                        for uid, fav in data.items():
                            stmt = select(FavourRecord).where(
                                FavourRecord.user_id == str(uid),
                                FavourRecord.session_id == "global"
                            )
                            result = await session.execute(stmt)
                            if not result.scalars().first():
                                record = FavourRecord(
                                    user_id=str(uid),
                                    session_id="global",
                                    favour=int(fav)
                                )
                                session.add(record)
                                count += 1
                else:
                    if isinstance(data, list):
                        for item in data:
                            uid = str(item.get("userid", ""))
                            sid = str(item.get("session_id", "")) or "global"
                            if not uid: continue
                            
                            stmt = select(FavourRecord).where(
                                FavourRecord.user_id == uid,
                                FavourRecord.session_id == sid
                            )
                            result = await session.execute(stmt)
                            if not result.scalars().first():
                                record = FavourRecord(
                                    user_id=uid,
                                    session_id=sid,
                                    favour=int(item.get("favour", 0)),
                                    relationship=str(item.get("relationship", "")),
                                    is_unique=bool(item.get("is_unique", False))
                                )
                                session.add(record)
                                count += 1
                
                await session.commit()
            
            if count > 0:
                logger.info(f"成功从 {json_path.name} 迁移了 {count} 条数据到数据库")
                backup_path = json_path.with_suffix(".json.bak")
                import shutil
                shutil.move(json_path, backup_path)
                logger.info(f"旧文件已备份为: {backup_path.name}")

        except Exception as e:
            logger.error(f"迁移数据失败 {json_path}: {str(e)}")

    async def backup_data(self, records: List[FavourRecord], prefix: str) -> Optional[str]:
        """备份指定记录到JSON文件"""
        if not records:
            return None
        try:
            backup_dir = self.data_dir / "backups"
            backup_dir.mkdir(exist_ok=True)
            # 清理文件名中的非法字符（Windows 不允许 : / \ 等）
            safe_prefix = re.sub(r'[<>:"/\\|?*]', '_', prefix)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = backup_dir / f"{safe_prefix}_{timestamp}.json"
            
            data_to_save = []
            for r in records:
                d = r.dict()
                d['created_at'] = d['created_at'].isoformat() if d.get('created_at') else None
                d['updated_at'] = d['updated_at'].isoformat() if d.get('updated_at') else None
                d['last_interaction'] = d['last_interaction'].isoformat() if d.get('last_interaction') else None
                data_to_save.append(d)
                
            async with aio_open(filename, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data_to_save, ensure_ascii=False, indent=2))
            return str(filename)
        except Exception as e:
            logger.error(f"备份数据失败: {e}")
            return None

    @staticmethod
    def is_shared_session_id(session_id: Optional[str]) -> bool:
        """判断 session_id 是否为共享/全局记录。

        独立会话 UMO 格式为三段 'platform:MessageType:target_id'（含至少 2 个 ':'）。
        共享会话格式包括：
          - 新版平台全局：'platform:user_id'（如 'aiocqhttp:123456'，含 1 个 ':'）
          - 旧版适配器前缀：'platform'（如 'aiocqhttp'，不含 ':'）
          - 旧版全局：'global'（不含 ':'）
        """
        sid = (session_id or "").strip()
        if not sid:
            return True
        return sid.count(":") < 2

    # ==================== 旧版共享记录兼容（alias / global / 孤儿别名） ====================
    #
    # 历史上共享（全局）记录的 session_id 出现过三种形态：
    #   1. 'global'            —— v4.2.1 及以前的跨平台共享池
    #   2. '<平台ID>'          —— v4.2.2~v4.4.11 的适配器共享池（平台 ID 即 UMO 第一段，可被用户改名）
    #   3. '<平台ID>:<用户ID>' —— v4.4.12 起的新格式
    # 读取/写入新格式 key 时，若未命中，必须按下列优先级“认领”旧记录，绝不能当作新用户从 0 创建：
    #   alias（同平台旧前缀，直接改名迁移） > global（复制为种子，保留原行） > 孤儿别名（平台已不存在，复制，需能确认平台列表）

    def _known_platforms(self, current: str) -> Optional[set]:
        """返回当前确定存在的平台 ID 集合；无法可靠判断时返回 None。"""
        if not self.platform_resolver:
            return None
        try:
            ids = self.platform_resolver()
        except Exception as e:
            logger.debug(f"[旧数据兼容] 获取平台列表失败: {e}")
            return None
        if not ids:
            return None
        return {str(i) for i in ids if i} | {current}

    def _active_other_platforms(self, current: str) -> set:
        """返回除 current 以外当前确定存活的其他平台 ID 集合（无则返回空集合）。"""
        known = self._known_platforms(current)
        if not known:
            return set()
        return {p for p in known if p and p != current and p != "global"}

    async def _find_legacy_rows(self, session, platform: str, user_id: str):
        """查找某用户在当前平台之外可继承的旧版共享记录，返回 (alias_row, global_row, orphan_rows)。"""
        alias = (await session.execute(select(FavourRecord).where(
            FavourRecord.user_id == user_id, FavourRecord.session_id == platform
        ))).scalars().first()
        glob = (await session.execute(select(FavourRecord).where(
            FavourRecord.user_id == user_id, FavourRecord.session_id == "global"
        ))).scalars().first()

        orphans: List[FavourRecord] = []
        other_active = self._active_other_platforms(platform)
        rows = (await session.execute(select(FavourRecord).where(
            FavourRecord.user_id == user_id,
            FavourRecord.session_id.not_like("%:%:%"),
        ))).scalars().all()
        for r in rows:
            sid = r.session_id or ""
            if sid in ("global", platform) or sid == f"{platform}:{user_id}":
                continue
            prefix = sid.split(":", 1)[0]
            if not prefix or prefix in other_active:
                continue
            # 只接受 '别名' 或 '别名:本用户ID' 两种形态（如 '涩涩号'、'涩涩号:123456'、'default'）
            if ":" in sid and sid != f"{prefix}:{user_id}":
                continue
            orphans.append(r)
        return alias, glob, orphans

    @staticmethod
    def _latest_row(rows: List[FavourRecord]) -> Optional[FavourRecord]:
        if not rows:
            return None
        return max(
            rows,
            key=lambda r: (
                r.favour,
                r.last_interaction or r.created_at or datetime.min,
            ),
        )

    async def _claim_legacy(self, session, platform: str, user_id: str, target_sid: str) -> Optional[FavourRecord]:
        """把旧版共享记录认领为 target_sid（'platform:user_id'）并落库，返回新记录；无旧记录返回 None。"""
        alias, glob, orphans = await self._find_legacy_rows(session, platform, user_id)
        now = datetime.now()
        if alias is not None:
            # 若同时还存在 global 或孤儿别名，将其中更高的好感/关系合并进 alias
            for extra in [glob, *orphans]:
                if extra is not None:
                    self._merge_into(alias, extra, now)
            # 同平台旧前缀：整行改名，保留全部字段与主键
            alias.session_id = target_sid
            alias.updated_at = now
            session.add(alias)
            try:
                await session.commit()
            except SAIntegrityError:
                await session.rollback()
                return (await session.execute(select(FavourRecord).where(
                    FavourRecord.user_id == user_id, FavourRecord.session_id == target_sid
                ))).scalars().first()
            logger.info(f"[旧数据认领] 用户 {user_id}: '{platform}' → '{target_sid}'")
            return alias

        candidates = [r for r in [*orphans, glob] if r is not None]
        if not candidates:
            return None
        primary = self._latest_row(candidates)
        origin = primary.session_id
        new_rec = FavourRecord(
            user_id=user_id,
            session_id=target_sid,
            favour=max(self.min_val, min(self.max_val, primary.favour)),
            relationship=primary.relationship or "",
            is_unique=bool(primary.is_unique),
            username=primary.username or "",
            created_at=primary.created_at or now,
            updated_at=now,
            last_interaction=primary.last_interaction or now,
        )
        for extra in candidates:
            if extra is not primary:
                self._merge_into(new_rec, extra, now)
        new_rec.favour = max(self.min_val, min(self.max_val, new_rec.favour))
        session.add(new_rec)
        try:
            await session.commit()
        except SAIntegrityError:
            await session.rollback()
            return (await session.execute(select(FavourRecord).where(
                FavourRecord.user_id == user_id, FavourRecord.session_id == target_sid
            ))).scalars().first()
        logger.info(f"[旧数据认领] 用户 {user_id}: 复制 '{origin}' → '{target_sid}'（保留原行作为种子）")
        return new_rec

    async def _peek_legacy(self, user_id: str, platform: str) -> Optional[FavourRecord]:
        """只读兜底：认领写库失败时仍返回可用的旧记录，防止被当成新用户重置。"""
        try:
            async with self.async_session() as session:
                alias, glob, orphans = await self._find_legacy_rows(session, platform, user_id)
                return alias or glob or self._latest_row(orphans)
        except Exception as e:
            logger.warning(f"[旧数据兼容] 只读回退查询失败: {e}")
            return None

    async def get_favour(self, user_id: str, session_id: Optional[str] = None) -> Optional[FavourRecord]:
        """获取好感度记录。

        查询新版平台全局键 'platform:user_id' 未命中时，会按 alias > global > 孤儿别名 的顺序认领旧记录，
        避免旧版共享数据在升级后被当成新用户。
        """
        await self.init_db()
        sid = session_id if session_id else "global"
        async with self.async_session() as session:
            rec = (await session.execute(select(FavourRecord).where(
                FavourRecord.user_id == user_id,
                FavourRecord.session_id == sid
            ))).scalars().first()
            if rec is not None or sid.count(":") != 1:
                return rec
            platform = sid.split(":", 1)[0]
            if not platform or platform == "global":
                return None
            try:
                return await self._claim_legacy(session, platform, user_id, sid)
            except Exception as e:
                logger.warning(f"[旧数据认领] 写库失败，改为只读回退: {e}")
        return await self._peek_legacy(user_id, platform)

    @_retry_on_locked()
    async def update_favour(
        self, 
        user_id: str, 
        session_id: Optional[str], 
        favour: Optional[int] = None, 
        relationship: Optional[str] = None, 
        is_unique: Optional[bool] = None,
        touch_interaction: bool = True  # 是否刷新最后互动时间
    ) -> bool:
        """更新好感度记录（并发安全：唯一索引 + IntegrityError 回退更新）"""
        await self.init_db()
        if not is_valid_userid(user_id):
            return False
            
        sid = session_id if session_id else "global"
        
        try:
            async with self.async_session() as session:
                stmt = select(FavourRecord).where(
                    FavourRecord.user_id == user_id,
                    FavourRecord.session_id == sid
                )
                result = await session.execute(stmt)
                record = result.scalars().first()

                # 兼容旧版共享记录：alias 改名 / global 与孤儿别名复制，保证写入基于旧数据而非从 0 创建
                if not record and sid.count(":") == 1:
                    platform_prefix = sid.split(":", 1)[0]
                    if platform_prefix and platform_prefix != "global":
                        record = await self._claim_legacy(session, platform_prefix, user_id, sid)

                now = datetime.now()
                if not record:
                    init_favour = max(self.min_val, min(self.max_val, favour)) if favour is not None else 0
                    record = FavourRecord(
                        user_id=user_id,
                        session_id=sid,
                        favour=init_favour,
                        relationship=relationship or "",
                        is_unique=is_unique if is_unique is not None else False,
                        last_interaction=now
                    )
                    session.add(record)
                    try:
                        await session.commit()
                    except SAIntegrityError:
                        # 并发插入冲突：回滚后走更新路径
                        await session.rollback()
                        result = await session.execute(
                            select(FavourRecord).where(
                                FavourRecord.user_id == user_id,
                                FavourRecord.session_id == sid
                            )
                        )
                        record = result.scalars().first()
                        if record:
                            if favour is not None:
                                record.favour = max(self.min_val, min(self.max_val, favour))
                            if relationship is not None:
                                record.relationship = relationship
                            if is_unique is not None:
                                record.is_unique = is_unique
                            record.updated_at = now
                            if touch_interaction:
                                record.last_interaction = now
                            session.add(record)
                            await session.commit()
                else:
                    if favour is not None:
                        record.favour = max(self.min_val, min(self.max_val, favour))
                    if relationship is not None:
                        record.relationship = relationship
                    if is_unique is not None:
                        record.is_unique = is_unique
                    record.updated_at = now
                    if touch_interaction:
                        record.last_interaction = now
                    session.add(record)
                    await session.commit()
                
                return True
        except Exception as e:
            logger.error(f"更新数据库失败: {str(e)}")
            return False

    @_retry_on_locked()
    async def update_user_all_records(
        self, 
        user_id: str, 
        favour: Optional[int] = None, 
        relationship: Optional[str] = None, 
        is_unique: Optional[bool] = None
    ) -> int:
        """更新某用户在所有会话中的记录（全局修改）"""
        await self.init_db()
        if not is_valid_userid(user_id):
            return 0
            
        try:
            async with self.async_session() as session:
                values = {"updated_at": datetime.now()}
                if favour is not None:
                    values["favour"] = max(self.min_val, min(self.max_val, favour))
                if relationship is not None:
                    values["relationship"] = relationship
                if is_unique is not None:
                    values["is_unique"] = is_unique
                
                stmt = update(FavourRecord).where(FavourRecord.user_id == user_id).values(**values)
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount
        except Exception as e:
            logger.error(f"全局更新失败: {str(e)}")
            return 0

    @_retry_on_locked()
    async def delete_favour(self, user_id: str, session_id: Optional[str] = None) -> Tuple[bool, str]:
        """删除单条记录。

        删除平台全局键 'platform:user_id' 时，会连同该用户的旧版 alias / global / 孤儿别名行一并清除，
        否则被删记录会在下次读取时从旧数据“复活”。
        """
        await self.init_db()
        sid = session_id if session_id else "global"
        try:
            async with self.async_session() as session:
                targets: Dict[int, FavourRecord] = {}
                exact = (await session.execute(select(FavourRecord).where(
                    FavourRecord.user_id == user_id,
                    FavourRecord.session_id == sid
                ))).scalars().first()
                if exact is not None:
                    targets[exact.id] = exact

                if sid.count(":") == 1:
                    platform_prefix = sid.split(":", 1)[0]
                    if platform_prefix and platform_prefix != "global":
                        alias, glob, orphans = await self._find_legacy_rows(session, platform_prefix, user_id)
                        for r in [alias, glob, *orphans]:
                            if r is not None:
                                targets[r.id] = r

                if not targets:
                    return False, "未找到记录"

                for r in targets.values():
                    await session.delete(r)
                await session.commit()
                return True, "删除成功"
        except Exception as e:
            logger.error(f"删除记录失败: {str(e)}")
            return False, f"数据库错误: {str(e)}"

    async def get_all_in_session(self, session_id: Optional[str] = None) -> List[FavourRecord]:
        """获取某会话下的所有记录。

        传入独立会话 UMO 或 'global'：精确匹配。
        传入平台名或 'platform:uid'：返回该平台的“平台视图”——
          汇总新版 'platform:uid'、旧版 'platform'、旧版 'global' 种子以及非其他存活平台的孤儿别名记录，按用户去重。
        """
        await self.init_db()
        sid = session_id if session_id else "global"
        async with self.async_session() as session:
            if sid == "global" or sid.count(":") >= 2:
                stmt = select(FavourRecord).where(FavourRecord.session_id == sid)
                result = await session.execute(stmt)
                return list(result.scalars().all())

            platform_prefix = sid.split(":", 1)[0]
            other_active = self._active_other_platforms(platform_prefix)
            all_shared = list((await session.execute(select(FavourRecord).where(
                FavourRecord.session_id.not_like("%:%:%")
            ))).scalars().all())

        # 过滤掉明确属于“其他当前存活平台”的记录，其余（当前平台 + global + 孤儿别名）均纳入当前平台视图
        candidate_rows: List[FavourRecord] = []
        for r in all_shared:
            s = r.session_id or ""
            if s == "global" or s == platform_prefix or s.startswith(f"{platform_prefix}:"):
                candidate_rows.append(r)
                continue
            prefix = s.split(":", 1)[0]
            if prefix and prefix not in other_active:
                candidate_rows.append(r)

        # 优先级：platform:uid (3) > platform (2) > 孤儿别名 (1) > global (0)
        def _rank(r: FavourRecord) -> Tuple[int, int]:
            s = r.session_id or ""
            if s.startswith(f"{platform_prefix}:"):
                tier = 3
            elif s == platform_prefix:
                tier = 2
            elif s == "global":
                tier = 0
            else:
                tier = 1
            return (tier, r.favour)

        dedup: Dict[str, FavourRecord] = {}
        for r in candidate_rows:
            prev = dedup.get(r.user_id)
            if prev is None or _rank(r) > _rank(prev):
                dedup[r.user_id] = r
        return list(dedup.values())

    async def get_global_records(self) -> List[FavourRecord]:
        """获取所有共享记录（旧版 'global'、旧版适配器前缀 'aiocqhttp'、以及新版 'aiocqhttp:123456'）。
        共享记录的 session_id 少于 2 个 ':'（不含 '%:%:%'）。"""
        await self.init_db()
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(
                FavourRecord.session_id.not_like('%:%:%')
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_non_global_records(self) -> List[FavourRecord]:
        """获取所有独立会话记录（session_id 包含至少 2 个 ':'，如 'aiocqhttp:GroupMessage:123'）"""
        await self.init_db()
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(
                FavourRecord.session_id.like('%:%:%')
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_all_records(self) -> List[FavourRecord]:
        """获取全部记录（供 WebUI 数据管理使用）"""
        #################
        await self.init_db()
        async with self.async_session() as session:
            stmt = select(FavourRecord).order_by(FavourRecord.session_id, FavourRecord.user_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    @_retry_on_locked()
    async def update_record(self, record_id: int, **kwargs) -> bool:
        """更新指定记录的字段（favour, relationship, username 等）"""
        #################
        await self.init_db()
        try:
            async with self.async_session() as session:
                stmt = update(FavourRecord).where(FavourRecord.id == record_id).values(**kwargs)
                await session.execute(stmt)
                await session.commit()
            return True
        except Exception as e:
            logger.error(f"更新记录 {record_id} 失败: {e}")
            return False

    @_retry_on_locked()
    async def delete_record(self, record_id: int) -> bool:
        """删除指定记录"""
        #################
        await self.init_db()
        try:
            async with self.async_session() as session:
                stmt = delete(FavourRecord).where(FavourRecord.id == record_id)
                await session.execute(stmt)
                await session.commit()
            return True
        except Exception as e:
            logger.error(f"删除记录 {record_id} 失败: {e}")
            return False

    @_retry_on_locked()
    async def clear_session(self, session_id: Optional[str] = None) -> bool:
        """清空某会话记录。

        平台视图（平台名或 'platform:uid'）会清空该平台全部共享记录，并清除视图内用户对应的 'global' 种子行，
        否则被清空的用户会从 global 种子重新认领回来。
        """
        await self.init_db()
        sid = session_id if session_id else "global"
        try:
            async with self.async_session() as session:
                if sid == "global" or sid.count(":") >= 2:
                    await session.execute(delete(FavourRecord).where(FavourRecord.session_id == sid))
                else:
                    platform_prefix = sid.split(":", 1)[0]
                    plat_filter = (
                        (FavourRecord.session_id == platform_prefix)
                        | (
                            FavourRecord.session_id.like(f"{platform_prefix}:%")
                            & FavourRecord.session_id.not_like("%:%:%")
                        )
                    )
                    plat_rows = (await session.execute(select(FavourRecord).where(plat_filter))).scalars().all()
                    uids = {r.user_id for r in plat_rows}
                    # 视图内尚未认领、仅存在于 global 的用户也属于“当前平台视图”
                    glob_rows = (await session.execute(select(FavourRecord).where(
                        FavourRecord.session_id == "global"
                    ))).scalars().all()
                    uids |= {r.user_id for r in glob_rows}
                    await session.execute(delete(FavourRecord).where(plat_filter))
                    if uids:
                        await session.execute(delete(FavourRecord).where(
                            FavourRecord.session_id == "global",
                            FavourRecord.user_id.in_(list(uids)),
                        ))
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"清空会话记录失败: {str(e)}")
            return False

    @_retry_on_locked()
    async def clear_all(self) -> bool:
        """清空所有记录"""
        await self.init_db()
        try:
            async with self.async_session() as session:
                stmt = delete(FavourRecord)
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"清空所有记录失败: {str(e)}")
            return False

    async def get_decay_candidates(
        self, 
        inactive_days: int = None, 
        decay_config: dict = None
    ) -> List[Tuple[FavourRecord, int, int]]:
        """
        获取需要进行衰减的记录。
        
        线性模式：使用 inactive_days 作为统一阈值。
        分级模式：使用 decay_config['advanced_rules'] 按好感度区间匹配。
        
        Returns: List of (record, inactive_days_for_this_record, decay_amount)
        """
        await self.init_db()
        results: List[Tuple[FavourRecord, int, int]] = []
        
        mode = decay_config.get("mode", "linear") if decay_config else "linear"
        floor_favour = decay_config.get("floor_favour") if decay_config else None
        
        try:
            async with self.async_session() as session:
                # 获取所有好感度高于 min_val 的记录
                stmt = select(FavourRecord).where(FavourRecord.favour > self.min_val)
                result = await session.execute(stmt)
                all_records = list(result.scalars().all())
            
            for record in all_records:
                if mode == "linear":
                    if inactive_days is None:
                        inactive_days = 7
                    cutoff = datetime.now() - timedelta(days=inactive_days)
                    if record.last_interaction and record.last_interaction < cutoff:
                        # 检查底线
                        eff_floor = floor_favour if floor_favour is not None else self.min_val
                        if record.favour > eff_floor:
                            decay_amt = decay_config.get("decay_amount", 5) if decay_config else 5
                            results.append((record, inactive_days, decay_amt))
                else:
                    # 分级模式：按 advanced_rules 匹配
                    rules = decay_config.get("advanced_rules", []) if decay_config else []
                    if not rules:
                        continue
                    # 按 min_favour 降序排列以优先匹配高区间
                    rules_sorted = sorted(rules, key=lambda r: r.get("min_favour", 0), reverse=True)
                    matched_rule = None
                    for rule in rules_sorted:
                        r_min = rule.get("min_favour", -999)
                        r_max = rule.get("max_favour", 999)
                        if r_min <= record.favour <= r_max:
                            matched_rule = rule
                            break
                    
                    if matched_rule:
                        days = matched_rule.get("inactive_days", 7)
                        cutoff = datetime.now() - timedelta(days=days)
                        if record.last_interaction and record.last_interaction < cutoff:
                            eff_floor = matched_rule.get("floor", floor_favour)
                            if eff_floor is None:
                                eff_floor = floor_favour if floor_favour is not None else self.min_val
                            if record.favour > eff_floor:
                                decay_amt = matched_rule.get("decay_amount", 5)
                                results.append((record, days, decay_amt))
        except Exception as e:
            logger.error(f"查询衰减候选记录失败: {e}")
        
        return results

    @_retry_on_locked()
    async def apply_decay(self, user_id: str, session_id: str, decay_amount: int, floor: int = None) -> Optional[int]:
        """
        对指定记录应用衰减，返回衰减后的好感度值。
        若已达到底线则不再衰减，返回 None 表示无变化。
        """
        record = await self.get_favour(user_id, session_id)
        if not record:
            return None
        
        eff_floor = floor if floor is not None else self.min_val
        if record.favour <= eff_floor:
            return None
        
        new_favour = max(eff_floor, record.favour - decay_amount)
        await self.update_favour(user_id, session_id, favour=new_favour, touch_interaction=False)
        return new_favour

    async def auto_backup(self) -> Optional[str]:
        """自动备份所有记录"""
        records = await self.get_all_records()
        return await self.backup_data(records, "auto")

    async def list_backups(self) -> List[dict]:
        """列出所有备份文件"""
        backup_dir = self.data_dir / "backups"
        if not backup_dir.exists():
            return []
        
        result = []
        for f in backup_dir.iterdir():
            if f.is_file() and f.suffix == ".json":
                stat = f.stat()
                # 从文件名解析时间戳，格式: prefix_YYYYMMDD_HHMMSS.json
                created_iso = ""
                try:
                    parts = f.stem.rsplit("_", 2)
                    if len(parts) >= 2:
                        date_part = parts[-2]
                        time_part = parts[-1]
                        if len(date_part) == 8 and len(time_part) == 6:
                            dt = datetime.strptime(f"{date_part}_{time_part}", "%Y%m%d_%H%M%S")
                            created_iso = dt.isoformat()
                except Exception:
                    pass
                
                result.append({
                    "filename": f.name,
                    "size_kb": round(stat.st_size / 1024, 2),
                    "created": created_iso,
                    "path": str(f),
                })
        
        result.sort(key=lambda x: x["filename"], reverse=True)
        return result

    @_retry_on_locked()
    async def restore_backup(self, filename: str) -> Tuple[bool, str]:
        """从备份文件恢复数据"""
        # 验证文件名安全性
        if ".." in filename or "/" in filename or "\\" in filename or not filename.endswith(".json"):
            return False, "不安全的文件名"
        
        backup_path = self.data_dir / "backups" / filename
        if not backup_path.exists():
            return False, f"备份文件不存在: {filename}"
        
        try:
            async with aio_open(backup_path, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)
            
            if not isinstance(data, list):
                return False, "备份文件格式无效"
            
            await self.init_db()
            async with self.async_session() as session:
                async with session.begin():
                    # 删除所有现有记录
                    await session.execute(delete(FavourRecord))
                    
                    # 插入备份中的记录
                    for item in data:
                        record = FavourRecord(
                            id=item.get("id"),
                            user_id=item.get("user_id", ""),
                            session_id=item.get("session_id", "global"),
                            favour=item.get("favour", 0),
                            relationship=item.get("relationship", ""),
                            is_unique=item.get("is_unique", False),
                            username=item.get("username", ""),
                            created_at=datetime.fromisoformat(item["created_at"]) if item.get("created_at") else datetime.now(),
                            updated_at=datetime.fromisoformat(item["updated_at"]) if item.get("updated_at") else datetime.now(),
                            last_interaction=datetime.fromisoformat(item["last_interaction"]) if item.get("last_interaction") else datetime.now(),
                        )
                        session.add(record)
            
            return True, f"restored {len(data)} records"
        except Exception as e:
            logger.error(f"恢复备份失败: {e}")
            return False, f"恢复失败: {str(e)}"

    async def delete_backup(self, filename: str) -> Tuple[bool, str]:
        """删除指定备份文件"""
        # 验证文件名安全性
        if ".." in filename or "/" in filename or "\\" in filename or not filename.endswith(".json"):
            return False, "不安全的文件名"
        
        backup_path = self.data_dir / "backups" / filename
        if not backup_path.exists():
            return False, f"备份文件不存在: {filename}"
        
        try:
            backup_path.unlink()
            return True, "deleted"
        except Exception as e:
            logger.error(f"删除备份失败: {e}")
            return False, f"删除失败: {str(e)}"

    async def cleanup_old_backups(self, max_age_hours: int = 24):
        """清理超过指定时间的旧备份文件"""
        backup_dir = self.data_dir / "backups"
        if not backup_dir.exists():
            return
        
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        cleaned = 0
        
        for f in backup_dir.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    age = now - os.path.getmtime(str(f))
                    if age > max_age_seconds:
                        f.unlink()
                        cleaned += 1
                except Exception as e:
                    logger.warning(f"清理备份文件失败 {f.name}: {e}")
        
        if cleaned > 0:
            logger.info(f"[备份清理] 已清理 {cleaned} 个过期备份文件（超过 {max_age_hours}h）")

    # ==================== 会话列表 / 迁移 / 复制 ====================

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有有数据的会话及其记录数（同平台的全局记录聚合为平台全局池）。"""
        await self.init_db()
        records = await self.get_all_records()
        counts: Dict[str, int] = {}
        for r in records:
            sid = r.session_id or "global"
            if self.is_shared_session_id(sid) and sid != "global":
                # 将 'aiocqhttp:123456' 与 'aiocqhttp' 统一归入平台级全局键 'aiocqhttp'
                sid = sid.split(":", 1)[0]
            counts[sid] = counts.get(sid, 0) + 1
        result = []
        for sid, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            is_shared = self.is_shared_session_id(sid)
            parts = sid.split(":", 2)
            result.append({
                "session_id": sid,
                "count": cnt,
                "platform": parts[0] if parts else sid,
                "session_type": "" if is_shared else (parts[1] if len(parts) >= 2 else ""),
                "session_target": "" if is_shared else (parts[2] if len(parts) >= 3 else ""),
                "is_shared": is_shared,
            })
        return result

    async def preview_session(self, session_id: str) -> Dict[str, Any]:
        """预览某会话下的用户记录摘要。"""
        await self.init_db()
        records = await self.get_all_in_session(session_id)
        users = []
        for r in records:
            users.append({
                "user_id": r.user_id,
                "username": r.username or r.user_id,
                "favour": r.favour,
                "relationship": r.relationship or "",
                "is_unique": r.is_unique,
                "last_interaction": r.last_interaction.isoformat() if r.last_interaction else "",
            })
        return {"session_id": session_id, "count": len(users), "users": users}

    @_retry_on_locked()
    async def copy_session(
        self,
        source_sid: str,
        target_sid: str,
        mode: str = "merge",
    ) -> Tuple[bool, str, int]:
        """将源会话数据复制到目标会话（session_id 以外字段全部迁移）。

        mode:
          - merge: 按 user_id 覆盖写入目标（目标中不存在的用户新增，存在的用源数据覆盖）
          - replace: 先清空目标会话再完整复制
        Returns: (success, message, affected_count)
        """
        await self.init_db()
        source_sid = (source_sid or "").strip()
        target_sid = (target_sid or "").strip()
        if not source_sid or not target_sid:
            return False, "源/目标会话 UMO 不能为空", 0
        if source_sid == target_sid:
            return False, "源与目标会话不能相同", 0

        try:
            source_records = await self.get_all_in_session(source_sid)
            if not source_records:
                return False, f"源会话无数据: {source_sid}", 0

            is_target_platform_global = (
                self.is_shared_session_id(target_sid)
                and target_sid != "global"
                and ":" not in target_sid
            )

            async with self.async_session() as session:
                if mode == "replace":
                    if is_target_platform_global:
                        await session.execute(
                            delete(FavourRecord).where(
                                (FavourRecord.session_id == target_sid)
                                | (
                                    FavourRecord.session_id.like(f"{target_sid}:%")
                                    & FavourRecord.session_id.not_like("%:%:%")
                                )
                            )
                        )
                    else:
                        await session.execute(
                            delete(FavourRecord).where(FavourRecord.session_id == target_sid)
                        )

                affected = 0
                now = datetime.now()
                for src in source_records:
                    eff_target_sid = f"{target_sid}:{src.user_id}" if is_target_platform_global else target_sid
                    stmt = select(FavourRecord).where(
                        FavourRecord.user_id == src.user_id,
                        FavourRecord.session_id == eff_target_sid,
                    )
                    result = await session.execute(stmt)
                    existing = result.scalars().first()
                    if not existing and is_target_platform_global:
                        res_legacy = await session.execute(
                            select(FavourRecord).where(
                                FavourRecord.user_id == src.user_id,
                                FavourRecord.session_id == target_sid,
                            )
                        )
                        existing = res_legacy.scalars().first()
                        if existing:
                            existing.session_id = eff_target_sid
                    if existing:
                        existing.favour = src.favour
                        existing.relationship = src.relationship or ""
                        existing.is_unique = src.is_unique
                        existing.username = src.username or existing.username or ""
                        existing.updated_at = now
                        existing.last_interaction = src.last_interaction or existing.last_interaction
                        session.add(existing)
                    else:
                        session.add(FavourRecord(
                            user_id=src.user_id,
                            session_id=eff_target_sid,
                            favour=src.favour,
                            relationship=src.relationship or "",
                            is_unique=src.is_unique,
                            username=src.username or "",
                            created_at=src.created_at or now,
                            updated_at=now,
                            last_interaction=src.last_interaction or now,
                        ))
                    affected += 1

                await session.commit()

            msg = f"已将 {affected} 条记录从 {source_sid} 复制到 {target_sid}（模式={mode}）"
            logger.info(f"[会话复制] {msg}")
            return True, msg, affected
        except Exception as e:
            logger.error(f"复制会话失败: {e}")
            return False, f"复制失败: {e}", 0

    @_retry_on_locked()
    async def migrate_session(
        self,
        source_sid: str,
        target_sid: str,
        mode: str = "merge",
    ) -> Tuple[bool, str, int]:
        """迁移会话：复制到目标后清空源会话。"""
        ok, msg, count = await self.copy_session(source_sid, target_sid, mode=mode)
        if not ok:
            return ok, msg, count
        cleared = await self.clear_session(source_sid)
        if not cleared:
            return False, f"复制成功但清空源会话失败: {source_sid}（目标已有 {count} 条）", count
        full_msg = f"{msg}；已清空源会话 {source_sid}"
        logger.info(f"[会话迁移] {full_msg}")
        return True, full_msg, count

    # ==================== 启动迁移（旧版共享记录 → platform:user_id） ====================

    @staticmethod
    def _merge_into(dst: "FavourRecord", src: "FavourRecord", now: datetime) -> None:
        """把旧记录 src 合并进目标记录 dst：
        - 好感度：若 src 好感度高于 dst（或 dst 为 0 而 src 非 0），取 src.favour，以修复升级后被重置为初值的情况；
        - 关系/唯一/昵称：保留非空有值的一方；
        - 时间：创建时间取较早者，最后互动时间取较晚者。
        """
        if src.favour > dst.favour or (dst.favour == 0 and src.favour != 0):
            dst.favour = src.favour
        src_rel = src.relationship if (src.relationship and src.relationship != "无") else ""
        dst_rel = dst.relationship if (dst.relationship and dst.relationship != "无") else ""
        if src_rel and (not dst_rel or src.favour >= dst.favour):
            dst.relationship = src_rel
        dst.is_unique = bool(dst.is_unique or src.is_unique)
        if not dst.username and src.username:
            dst.username = src.username
        if src.created_at and (not dst.created_at or src.created_at < dst.created_at):
            dst.created_at = src.created_at
        if src.last_interaction and (not dst.last_interaction or src.last_interaction > dst.last_interaction):
            dst.last_interaction = src.last_interaction
        dst.updated_at = now

    async def _snapshot_before_migration(self, tag: str) -> Optional[str]:
        """迁移前整库快照：任何改写数据的迁移之前必须先留底，出错可由 WebUI「备份快照」一键恢复。"""
        try:
            records = await self.get_all_records()
            return await self.backup_data(records, f"pre_migrate_{tag}")
        except Exception as e:
            logger.error(f"[全局迁移] 迁移前快照失败: {e}")
            return None

    @_retry_on_locked()
    async def auto_migrate_global_to_adapters(
        self, known_platforms: Optional[set] = None
    ) -> Tuple[int, int]:
        """启动或首次发现平台时，把旧版共享记录规范化/合并为 'platform:user_id'（如 'aiocqhttp:123456'）。

        设计原则：**只合并、不丢弃**。任何无法确定归属的记录都原样保留，由运行时 get_favour 按需认领。

        迁移步骤（均在迁移前先做整库快照）：
          1. 平台别名记录（session_id 不含 ':'，且不是 'global'，如 'aiocqhttp' / '涩涩号'）
             → 改写为 '<别名>:<user_id>'；目标已存在则合并后删除旧行。
          2. 跨池恢复与单平台收口：
             - 若某用户已存在当前存活平台的 '<platform>:<user_id>' 记录，同时还残留 'global' 或孤儿别名记录，
               将旧记录合并进 '<platform>:<user_id>'（自动修复因早期未回退而新建的 0 好感记录）。
             - 若当前仅有一个存活平台 `only`，将所有剩余 'global' 与孤儿别名记录直接迁入 `only:<user_id>`。
             - 若存在多个平台或平台尚未确定，保留 'global' 行供运行时按需复制认领。

        Returns: (migrated_count, kept_count)  kept_count 为保留待运行时认领的记录数。
        """
        await self.init_db()
        migrated = 0
        kept = 0

        try:
            platforms = {str(p) for p in (known_platforms or []) if p and str(p) != "global"}
            if not platforms and self.platform_resolver:
                try:
                    resolved = self.platform_resolver() or set()
                    platforms = {str(p) for p in resolved if p and str(p) != "global"}
                except Exception:
                    platforms = set()

            async with self.async_session() as session:
                alias_rows = list((await session.execute(select(FavourRecord).where(
                    FavourRecord.session_id.not_like("%:%"),
                    FavourRecord.session_id != "global",
                ))).scalars().all())
                global_rows = list((await session.execute(select(FavourRecord).where(
                    FavourRecord.session_id == "global"
                ))).scalars().all())
                colon_shared_rows = list((await session.execute(select(FavourRecord).where(
                    FavourRecord.session_id.like("%:%"),
                    FavourRecord.session_id.not_like("%:%:%"),
                ))).scalars().all())

            # 识别孤儿别名行（仅当已知存活平台集合非空时，不属于任何存活平台的 prefix:uid）
            orphan_colon_rows = [
                r for r in colon_shared_rows
                if platforms and (r.session_id or "").split(":", 1)[0] not in platforms
            ]

            if not alias_rows and not global_rows and not orphan_colon_rows:
                return 0, 0

            snap = await self._snapshot_before_migration("v4_4_13")
            if snap:
                logger.info(f"[全局迁移] 已在迁移前创建整库快照: {snap}")
            else:
                logger.warning("[全局迁移] 未能创建迁移前快照，为数据安全本次跳过迁移（运行时仍会按需认领旧记录）。")
                return 0, len(alias_rows) + len(global_rows)

            now = datetime.now()
            async with self.async_session() as session:
                # ---- Step 1：0 冒号别名记录原地改名为 alias:uid ----
                for rec in alias_rows:
                    new_sid = f"{rec.session_id}:{rec.user_id}"
                    target = (await session.execute(select(FavourRecord).where(
                        FavourRecord.user_id == rec.user_id,
                        FavourRecord.session_id == new_sid,
                    ))).scalars().first()
                    if target is not None:
                        self._merge_into(target, rec, now)
                        session.add(target)
                        await session.delete(rec)
                    else:
                        rec.session_id = new_sid
                        rec.updated_at = now
                        session.add(rec)
                    migrated += 1
                if alias_rows:
                    await session.commit()

            # ---- Step 2：global 记录与孤儿别名记录合并/收口 ----
            async with self.async_session() as session:
                all_shared_now = list((await session.execute(select(FavourRecord).where(
                    FavourRecord.session_id.not_like("%:%:%")
                ))).scalars().all())

                if len(platforms) == 1:
                    only = next(iter(platforms))
                    legacy_sources = [
                        r for r in all_shared_now
                        if r.session_id == "global" or (r.session_id or "").split(":", 1)[0] != only
                    ]
                    for src_rec in legacy_sources:
                        new_sid = f"{only}:{src_rec.user_id}"
                        if src_rec.session_id == new_sid:
                            continue
                        target = (await session.execute(select(FavourRecord).where(
                            FavourRecord.user_id == src_rec.user_id,
                            FavourRecord.session_id == new_sid,
                        ))).scalars().first()
                        if target is not None:
                            self._merge_into(target, src_rec, now)
                            session.add(target)
                            await session.delete(src_rec)
                        else:
                            src_rec.session_id = new_sid
                            src_rec.updated_at = now
                            session.add(src_rec)
                        migrated += 1
                    if legacy_sources:
                        await session.commit()
                        logger.info(
                            f"[全局迁移] 单平台 '{only}'：已将 {len(legacy_sources)} 条旧 'global'/旧别名记录迁入或合并到 '{only}:UID'。"
                        )
                else:
                    # 多平台或平台尚未加载：若用户在已知存活平台下已有记录，将 global 记录合并进去修复好感/关系
                    active_by_user: Dict[str, List[FavourRecord]] = {}
                    curr_globals: List[FavourRecord] = []
                    for r in all_shared_now:
                        if r.session_id == "global":
                            curr_globals.append(r)
                        elif ":" in (r.session_id or ""):
                            plat_p = (r.session_id or "").split(":", 1)[0]
                            if not platforms or plat_p in platforms:
                                active_by_user.setdefault(r.user_id, []).append(r)

                    for g in curr_globals:
                        targets = active_by_user.get(g.user_id, [])
                        if not targets:
                            kept += 1
                            continue
                        for t in targets:
                            self._merge_into(t, g, now)
                            session.add(t)
                        if platforms and len(targets) >= len(platforms):
                            await session.delete(g)
                        else:
                            kept += 1
                        migrated += 1
                    await session.commit()

            if migrated or kept:
                logger.info(f"[全局迁移] 完成：迁移/合并 {migrated} 条，保留待认领 {kept} 条。")
            return migrated, kept

        except Exception as e:
            logger.error(f"[全局迁移] 自动迁移失败（数据未丢失，已有快照可恢复）: {e}")
            return 0, 0
