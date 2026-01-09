import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from aiofiles import open as aio_open
from aiofiles.os import path as aio_path
from sqlmodel import SQLModel, Field, select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
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
    updated_at: datetime = Field(default_factory=datetime.now)

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

    async def init_db(self):
        """初始化数据库表"""
        if self._initialized:
            return
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self._initialized = True
        logger.info(f"好感度数据库已初始化: {self.db_path}")

    async def migrate_from_json(self, json_path: Path, is_global: bool = False):
        """从旧版JSON文件迁移数据"""
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

    async def get_favour(self, user_id: str, session_id: Optional[str] = None) -> Optional[FavourRecord]:
        """获取好感度记录"""
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
        is_unique: Optional[bool] = None
    ) -> bool:
        """更新好感度记录"""
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

                if not record:
                    init_favour = max(self.min_val, min(self.max_val, favour)) if favour is not None else 0
                    record = FavourRecord(
                        user_id=user_id,
                        session_id=sid,
                        favour=init_favour,
                        relationship=relationship or "",
                        is_unique=is_unique if is_unique is not None else False
                    )
                    session.add(record)
                else:
                    if favour is not None:
                        record.favour = max(self.min_val, min(self.max_val, favour))
                    if relationship is not None:
                        record.relationship = relationship
                    if is_unique is not None:
                        record.is_unique = is_unique
                    record.updated_at = datetime.now()
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
        if not is_valid_userid(user_id):
            return 0
            
        try:
            async with self.async_session() as session:
                # 构建更新字典
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
        sid = session_id if session_id else "global"
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(FavourRecord.session_id == sid)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_global_records(self) -> List[FavourRecord]:
        """仅获取全局记录"""
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(FavourRecord.session_id == "global")
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_non_global_records(self) -> List[FavourRecord]:
        """获取所有非全局记录"""
        async with self.async_session() as session:
            stmt = select(FavourRecord).where(FavourRecord.session_id != "global")
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def clear_session(self, session_id: Optional[str] = None) -> bool:
        """清空某会话记录"""
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
        try:
            async with self.async_session() as session:
                stmt = delete(FavourRecord)
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"清空所有记录失败: {str(e)}")
            return False