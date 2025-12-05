import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from aiofiles import open as aio_open
from aiofiles.os import path as aio_path
from astrbot.api import logger
from .utils import is_valid_userid

class AsyncJsonFileManager:
    """异步JSON文件管理基类"""
    def __init__(self, data_dir: Path, filename: str):
        self.data_path = data_dir / filename
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()

    async def _read_json(self) -> Any:
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

    async def _write_json(self, data: Any) -> bool:
        """写入JSON文件"""
        try:
            async with aio_open(self.data_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception as e:
            logger.error(f"写入{self.data_path.name}失败: {str(e)}")
            return False

    def _get_default_value(self) -> Any:
        """获取默认值，子类需要重写"""
        raise NotImplementedError

class GlobalFavourFileManager(AsyncJsonFileManager):
    def __init__(self, data_dir: Path, min_val: int = -100, max_val: int = 100):
        super().__init__(data_dir, "global_favour.json")
        self.min_val = min_val
        self.max_val = max_val

    def _get_default_value(self) -> Dict[str, int]:
        return {}

    async def read_global_favour(self) -> Dict[str, int]:
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

    async def write_global_favour(self, data: Dict[str, int]) -> bool:
        """写入全局好感度数据"""
        if not isinstance(data, dict):
            logger.error("写入数据格式无效，需为dict类型")
            return False
        
        success = await self._write_json(data)
        if success:
            logger.info(f"写入global_favour.json成功，包含{len(data)}个用户数据")
        return success

    async def get_user_global_favour(self, userid: str) -> Optional[int]:
        """获取用户全局好感度"""
        global_data = await self.read_global_favour()
        return global_data.get(str(userid))

    async def update_global_favour(self, userid: str, favour: int) -> bool:
        """更新用户全局好感度"""
        if not is_valid_userid(userid):
            logger.error(f"更新全局好感度失败：用户ID[{userid}]格式无效")
            return False
        
        async with self.lock:
            data = await self.read_global_favour()
            userid_str = str(userid)
            data[userid_str] = max(self.min_val, min(self.max_val, favour))
            return await self.write_global_favour(data)

class FavourFileManager(AsyncJsonFileManager):
    def __init__(self, data_dir: Path, enable_clear_backup: bool, min_val: int = -100, max_val: int = 100):
        super().__init__(data_dir, "haogan.json")
        self.enable_clear_backup = enable_clear_backup
        self.min_val = min_val
        self.max_val = max_val

    def _get_default_value(self) -> List[Dict[str, Any]]:
        return []

    async def read_favour(self) -> List[Dict[str, Any]]:
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
                "relationship": str(item.get("relationship", "")),
                "is_unique": bool(item.get("is_unique", False))
            }
            valid_data.append(valid_item)
        
        logger.info(f"读取haogan.json成功，一共{len(valid_data)}条记录")
        return valid_data

    async def write_favour(self, data: List[Dict[str, Any]]) -> bool:
        """写入会话级好感度数据"""
        if not isinstance(data, list):
            logger.error("写入数据格式无效，需为list类型")
            return False
        
        success = await self._write_json(data)
        if success:
            logger.info(f"修改haogan.json成功，写入{len(data)}条记录")
        return success

    async def clear_all_favour(self) -> bool:
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

    async def get_user_favour(self, userid: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """获取用户好感度记录"""
        userid_str = str(userid)
        data = await self.read_favour()
        for item in data:
            if item["userid"] == userid_str and item["session_id"] == session_id:
                logger.debug(f"查询到用户[{userid_str}]（会话[{session_id}]）的好感度记录")
                return item.copy()
        
        logger.debug(f"未查询到用户[{userid_str}]（会话[{session_id}]）的好感度记录")
        return None

    async def update_user_favour(self, userid: str, session_id: Optional[str], favour: Optional[int] = None, relationship: Optional[str] = None, is_unique: Optional[bool] = None) -> bool:
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
                        item["favour"] = max(self.min_val, min(self.max_val, favour))
                    if relationship is not None:
                        item["relationship"] = relationship
                    if is_unique is not None:
                        item["is_unique"] = is_unique
                    found = True
                    break
            
            if not found:
                init_favour = max(self.min_val, min(self.max_val, favour)) if favour is not None else 0
                init_relation = relationship or ""
                init_unique = is_unique if is_unique is not None else False
                data.append({
                    "userid": userid_str,
                    "session_id": session_id,
                    "favour": init_favour,
                    "relationship": init_relation,
                    "is_unique": init_unique
                })
            
            return await self.write_favour(data)

    async def delete_user_favour(self, userid: str, session_id: Optional[str] = None) -> Tuple[bool, str]:
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