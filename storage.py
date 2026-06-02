# storage.py
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from aiofiles import open as aio_open
from aiofiles.os import path as aio_path
from sqlmodel import SQLModel, Field, select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from astrbot.api import logger
from .utils import is_valid_userid

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
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_interaction: datetime = Field(default_factory=datetime.now)  # 最后互动时间，用于衰减


class FavourDBManager:
    """基于SQLite的好感度数据库管理器"""
    def __init__(self, data_dir: Path, min_val: int = -100, max_val: int = 100):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "favour.db"
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.min_val = min_val
        self.max_val = max_val
        
        # 创建异步引擎
        self.engine = create_async_engine(self.db_url, echo=False)
        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self._initialized = False
        self._init_lock = asyncio.Lock()

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
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = backup_dir / f"{prefix}_{timestamp}.json"
            
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

    async def get_favour(self, user_id: str, session_id: Optional[str] = None) -> Optional[FavourRecord]:
        """获取好感度记录"""
        await self.init_db()
        sid = session_id if session_id else "global"
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(
                FavourRecord.user_id == user_id,
                FavourRecord.session_id == sid
            )
            result = await session.execute(stmt)
            return result.scalars().first()

    async def update_favour(
        self, 
        user_id: str, 
        session_id: Optional[str], 
        favour: Optional[int] = None, 
        relationship: Optional[str] = None, 
        is_unique: Optional[bool] = None,
        touch_interaction: bool = True  # 是否刷新最后互动时间
    ) -> bool:
        """更新好感度记录"""
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

    async def delete_favour(self, user_id: str, session_id: Optional[str] = None) -> Tuple[bool, str]:
        """删除单条记录"""
        await self.init_db()
        sid = session_id if session_id else "global"
        try:
            async with self.async_session() as session:
                stmt = select(FavourRecord).where(
                    FavourRecord.user_id == user_id,
                    FavourRecord.session_id == sid
                )
                result = await session.execute(stmt)
                record = result.scalars().first()
                
                if not record:
                    return False, "未找到记录"
                
                await session.delete(record)
                await session.commit()
                return True, "删除成功"
        except Exception as e:
            logger.error(f"删除记录失败: {str(e)}")
            return False, f"数据库错误: {str(e)}"

    async def get_all_in_session(self, session_id: Optional[str] = None) -> List[FavourRecord]:
        """获取某会话下的所有记录"""
        await self.init_db()
        sid = session_id if session_id else "global"
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(FavourRecord.session_id == sid)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_global_records(self) -> List[FavourRecord]:
        """仅获取全局记录"""
        await self.init_db()
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(FavourRecord.session_id == "global")
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_non_global_records(self) -> List[FavourRecord]:
        """获取所有非全局记录"""
        await self.init_db()
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(FavourRecord.session_id != "global")
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

    async def clear_session(self, session_id: Optional[str] = None) -> bool:
        """清空某会话记录"""
        await self.init_db()
        sid = session_id if session_id else "global"
        try:
            async with self.async_session() as session:
                stmt = delete(FavourRecord).where(FavourRecord.session_id == sid)
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"清空会话记录失败: {str(e)}")
            return False

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
