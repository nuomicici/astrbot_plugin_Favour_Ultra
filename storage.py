import json
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from aiofiles import open as aio_open
from aiofiles.os import path as aio_path
from datetime import datetime
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
        try:
            if not await aio_path.exists(self.data_path):
                return self._get_default_value()
            async with aio_open(self.data_path, "r", encoding="utf-8") as f:
                return json.loads(await f.read())
        except Exception as e:
            logger.error(f"读取{self.data_path.name}失败: {str(e)}")
            return self._get_default_value()

    async def _write_json(self, data: Any) -> bool:
        try:
            async with aio_open(self.data_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception as e:
            logger.error(f"写入{self.data_path.name}失败: {str(e)}")
            return False

    def _get_default_value(self) -> Any:
        raise NotImplementedError

class GlobalFavourFileManager(AsyncJsonFileManager):
    def __init__(self, data_dir: Path):
        super().__init__(data_dir, "global_favour.json")

    def _get_default_value(self) -> Dict[str, int]:
        return {}

    async def read_global_favour(self) -> Dict[str, int]:
        raw_data = await self._read_json()
        if not isinstance(raw_data, dict):
            return {}
        valid_data = {}
        for userid, favour in raw_data.items():
            try:
                valid_data[str(userid)] = int(favour)
            except (ValueError, TypeError):
                pass
        return valid_data

    async def write_global_favour(self, data: Dict[str, int]) -> bool:
        return await self._write_json(data)

    async def get_user_global_favour(self, userid: str) -> Optional[int]:
        global_data = await self.read_global_favour()
        return global_data.get(str(userid))

    async def update_global_favour(self, userid: str, favour: int) -> bool:
        if not is_valid_userid(userid):
            return False
        async with self.lock:
            data = await self.read_global_favour()
            data[str(userid)] = max(-100, min(100, favour))
            return await self.write_global_favour(data)

class FavourFileManager(AsyncJsonFileManager):
    def __init__(self, data_dir: Path, enable_clear_backup: bool):
        super().__init__(data_dir, "haogan.json")
        self.enable_clear_backup = enable_clear_backup

    def _get_default_value(self) -> List[Dict[str, Any]]:
        return []

    async def read_favour(self) -> List[Dict[str, Any]]:
        raw_data = await self._read_json()
        if not isinstance(raw_data, list):
            return []
        valid_data = []
        for item in raw_data:
            if not isinstance(item, dict):
                continue
            # [修改] 读取时增加新的唯一性字段，提供默认值
            valid_item = {
                "userid": str(item.get("userid", "")),
                "favour": int(item.get("favour", 0)) if isinstance(item.get("favour"), (int, float)) else 0,
                "session_id": str(item.get("session_id")) if item.get("session_id") else None,
                "relationship": str(item.get("relationship", "")),
                "is_unique": bool(item.get("is_unique", False)),
                "unique_category": str(item.get("unique_category", "")) if item.get("unique_category") else None
            }
            valid_data.append(valid_item)
        return valid_data

    async def write_favour(self, data: List[Dict[str, Any]]) -> bool:
        return await self._write_json(data)

    async def clear_all_favour(self) -> bool:
        if self.enable_clear_backup:
            try:
                backup_data = await self.read_favour()
                if backup_data:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = self.data_dir / f"haogan_backup_{timestamp}.json"
                    async with self.lock:
                        async with aio_open(backup_path, "w", encoding="utf-8") as f:
                            await f.write(json.dumps(backup_data, ensure_ascii=False, indent=2))
            except Exception as e:
                logger.error(f"备份失败: {str(e)}")
                return False
        return await self.write_favour([])

    async def get_user_favour(self, userid: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        userid_str = str(userid)
        data = await self.read_favour()
        for item in data:
            if item["userid"] == userid_str and item["session_id"] == session_id:
                return item.copy()
        return None

    # [修改] 增加 is_unique 和 unique_category 参数
    async def update_user_favour(self, userid: str, session_id: Optional[str], 
                               favour: Optional[int] = None, 
                               relationship: Optional[str] = None,
                               is_unique: Optional[bool] = None,
                               unique_category: Optional[str] = None) -> bool:
        userid_str = userid.strip()
        if not is_valid_userid(userid_str):
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
                    # [新增] 更新唯一性标志
                    if is_unique is not None:
                        item["is_unique"] = is_unique
                    if unique_category is not None:
                        item["unique_category"] = unique_category
                    found = True
                    break
            
            if not found:
                init_favour = max(-100, min(100, favour)) if favour is not None else 0
                init_relation = relationship or ""
                # [新增] 初始化唯一性标志
                item_data = {
                    "userid": userid_str,
                    "session_id": session_id,
                    "favour": init_favour,
                    "relationship": init_relation,
                    "is_unique": is_unique if is_unique is not None else False,
                    "unique_category": unique_category
                }
                data.append(item_data)
                
            return await self.write_favour(data)

    async def delete_user_favour(self, userid: str, session_id: Optional[str] = None) -> Tuple[bool, str]:
        userid_str = userid.strip()
        if not is_valid_userid(userid_str):
            return False, "格式无效"
        async with self.lock:
            data = await self.read_favour()
            new_data = [item for item in data if not (item["userid"] == userid_str and item["session_id"] == session_id)]
            if len(new_data) == len(data):
                return False, "未找到数据"
            success = await self.write_favour(new_data)
            return success, "删除成功" if success else "文件写入失败"
