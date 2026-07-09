# config_manager.py
"""
配置管理器 —— 绕过 AstrBot 框架配置逻辑，自行管理配置文件。
配置文件保存于插件目录的上上级的 plugin_data 中。
安装时若检测到旧版框架配置则迁移，否则按默认配置生成。
"""
import json
import shutil
import traceback
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime

from astrbot.api import logger

# ==================== 默认配置 ====================

DEFAULT_CONFIG: Dict[str, Any] = {
    # 基础配置
    "favour_mode": "galgame",
    "is_global_favour": False,
    "group_sort_by": "default",
    "enable_cold_violence": True,
    "enable_relationship_table": True,
    "min_favour_value": -200,
    "max_favour_value": 1000,
    "default_favour": 0,

    # 好感度分级配置（独立项目，默认7个分级）
    #################
    "favour_levels": [
        {"min": -200, "max": -151, "name": "极度厌恶", "desc": "当前等级下，无论用户说什么内容，你都会感到十分嫌弃。如果没有其他人请求，该等级用户无法提升好感度。"},
        {"min": -150,  "max": -51, "name": "厌恶",     "desc": "当前等级下，你对用户抱有很强烈的抗拒感，你会想尽办法不想与其发生交流。此该等级用户可以通过正向互动稍微恢复好感度。"},
        {"min": -50,  "max": -1,  "name": "反感",     "desc": "当前等级下，你对用户的行为感到反感，会明显表现出自己对TA没有好感。此等级用户可以正常通过对话进行好感度增减。"},
        {"min": 0,    "max": 149,  "name": "普通",     "desc": "当前等级下，你对用户的态度为\u201c普通朋友\u201d。你对于用户的态度很平淡，不会主动表现出喜爱或者厌恶情绪。但是如果用户对你做出冒犯的时，你会反感（降低好感度）。"},
        {"min": 150,   "max": 299,  "name": "喜欢",     "desc": "当前等级下，你对于用户有一定的喜欢。用户的言语、动作主要不是特别冒犯，你都不会介意。此等级用户可以正常通过对话进行好感度增减。"},
        {"min": 300,   "max": 449,  "name": "亲密",     "desc": "当前等级下，你几乎不会对用户的行为感到不适。用户做什么都会引起你的\u201c喜欢\u201d。（除非太过强硬，明显违背你的意愿）此等级用户可以正常通过对话进行好感度增减。"},
        {"min": 450,  "max": 500, "name": "挚爱",     "desc": "此等级为最高等级。你对用户抱有极深的感情，极为重视用户的每一句话。"},
    ],

    # 好感度衰减配置
    "favour_decay": {
        "enabled": False,
        "mode": "linear",  # "linear" 线性衰减 / "advanced" 分级衰减
        # 衰减底线：好感度降低到此值后不再衰减（None 则使用 min_favour_value）
        "floor_favour": None,
        # --- 线性模式 ---
        "inactive_days": 7,    # 无互动 N 日后触发衰减
        "decay_amount": 5,     # 每次减少 int 点
        # --- 分级模式（高级） ---
        # 按好感度区间配置不同衰减速度和底线。规则按 min_favour 从高到低匹配。
        "advanced_rules": [
            {"min_favour": 90, "max_favour": 100, "inactive_days": 1, "decay_amount": 5, "floor": 80},
            {"min_favour": 70, "max_favour": 89,  "inactive_days": 2, "decay_amount": 3, "floor": 60},
            {"min_favour": 0,  "max_favour": 69,  "inactive_days": 7, "decay_amount": 2, "floor": -50},
        ],
    },

    # 主动搭话配置
    "active_chat": {
        "enabled": False,
        "time_start": "08:00",     # 允许搭话的时间范围起点
        "time_end": "23:30",       # 允许搭话的时间范围终点
        "interval_hours": 2,       # 每 N 小时检查一次
        # 按好感度区间分配触发概率（百分比），按 min_favour 从高到低匹配
        "rules": [
            {"min_favour": 90, "max_favour": 100, "probability": 15},
            {"min_favour": 70, "max_favour": 89,  "probability": 8},
            {"min_favour": 50, "max_favour": 69,  "probability": 3},
        ],
        # LLM 搭话提示词。可用占位符：{current_time}=当前时间, {last_interaction_ago}=距上次互动时长, {favour}=好感度, {relationship}=关系, {user_name}=用户ID
        "llm_prompt": (
            "现在时间是 {current_time}，距离上次互动已经 {last_interaction_ago}。\n"
            "请以自然、不经意的方式向用户 {user_name} 发起聊天。\n"
            "当前好感度：{favour}，关系：{relationship}。\n"
            "请按照你的人设对用户发起对话\n"
            "注意：这是一条系统触发的主动搭话，不是用户要求你发送的。"
        ),
    },

    # 好感度查询权限开关
    "query_permission": {
        "group_normal_user": True,   # 群聊普通用户可否查询
        "private_normal_user": True, # 私聊普通用户可否查询
    },

    # 高级配置
    "advanced_config": {
        "admin_default_favour": 50,
        "favour_envoys": [],
        "favour_increase_min": 1,
        "favour_increase_max": 3,
        "favour_decrease_min": 1,
        "favour_decrease_max": 5,
        "level_threshold": 50,
        "blocked_sessions": [],
        "allowed_sessions": [],
        # 修改好感度指令的最低权限：superuser=仅Bot管理员, owner=群主及以上, admin=管理员及以上
        "modify_favour_permission": "admin",
    },

    # 冷暴力配置
    "cold_violence_config": {
        "consecutive_decrease_threshold": 3,
        "duration_minutes": 30,
        "is_global": False,
        "auto_blacklist_on_min": False,  # 好感度达到最低值时拉黑（不再监听该用户信息）
        "replies": {
            "on_trigger": "......（我不想理你了。）",
            "on_message": "[自动回复]不想理你,{time_str}后再找我",
            "on_query": "冷暴力呢，看什么看，{time_str}之后再找我说话"
        }
    },
}

# 配置文件名
CONFIG_FILENAME = "config.json"
PLUGIN_NAME_FOR_CONFIG = "astrbot_plugin_favour_ultra"


class PluginConfigManager:
    """插件配置管理器，绕过框架配置逻辑。"""

    def __init__(self, plugin_dir: Path, context_data_dir: Optional[Path] = None):
        """
        Args:
            plugin_dir: 插件自身目录 (如 .../astrbot_plugin_Favour_Ultra-main)
            context_data_dir: AstrBot 框架的 data 目录（用于检测旧配置）
        """
        self.plugin_dir = Path(plugin_dir)
        # 配置文件保存在插件目录的上上级的 plugin_data 中
        self.plugin_data_dir = self.plugin_dir.parent.parent / "plugin_data" / PLUGIN_NAME_FOR_CONFIG
        self.config_path = self.plugin_data_dir / CONFIG_FILENAME

        # 框架旧配置路径（用于迁移）
        # 兼容不同大小写：框架在不同版本/平台下可能生成不同大小写的文件名
        if context_data_dir:
            config_dir = Path(context_data_dir) / "config"
            candidates = [
                config_dir / "astrbot_plugin_Favour_Ultra_config.json",
                config_dir / "astrbot_plugin_favour_ultra_config.json",
                config_dir / "astrbot_plugin_Favour_Ultra-main_config.json",
                config_dir / "astrbot_plugin_favour_ultra-main_config.json",
            ]
            self.old_config_path = None
            for candidate in candidates:
                if candidate.exists():
                    self.old_config_path = candidate
                    break
        else:
            self.old_config_path = None

        self._config: Dict[str, Any] = {}
        self._migrated = False

    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """深度合并两个字典，override 覆盖 base。"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def load_or_create(self) -> Dict[str, Any]:
        """
        加载配置。若配置文件不存在：
          1. 检测旧版本框架配置（data/config/...）→ 有则迁移（仅迁移，之后不再读取）
          2. 无则按默认配置生成
        """
        if self._config:
            return self._config

        # 确保目录存在
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)

        if self.config_path.exists():
            # 已有配置文件，直接加载
            try:
                with open(self.config_path, "r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                loaded = self._normalize_config_after_load(loaded)
                self._config = self._deep_merge(DEFAULT_CONFIG.copy(), loaded)
                self._save()
                logger.info(f"已加载插件配置: {self.config_path}")
                return self._config
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}，将使用默认配置。")
                self._config = DEFAULT_CONFIG.copy()
                self._save()
                return self._config

        # 配置文件不存在 → 尝试迁移旧版框架配置（仅首次安装时）
        if self.old_config_path and self.old_config_path.exists():
            try:
                with open(self.old_config_path, "r", encoding="utf-8-sig") as f:
                    old_config = json.load(f)
                logger.info(f"检测到旧版框架配置，正在迁移: {self.old_config_path}")
                self._config = self._migrate_old_config(old_config)
                self._save()
                self._migrated = True
                # 迁移后备份旧文件
                backup_path = self.old_config_path.with_suffix(".json.v3_1_backup")
                shutil.copy(self.old_config_path, backup_path)
                logger.info(f"旧配置已备份至: {backup_path}，迁移完成。")
                return self._config
            except Exception as e:
                logger.error(f"迁移旧配置失败: {e}\n{traceback.format_exc()}，将使用默认配置。")

        # 无旧配置 → 按默认配置生成
        logger.info("未检测到旧配置，按默认配置生成新配置文件。")
        self._config = DEFAULT_CONFIG.copy()
        self._save()
        return self._config

    def _normalize_json_field(self, value: Any, default: Any) -> Any:
        """将可能来自 WebUI JSON 编辑器的字符串解析为 Python 对象。"""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                logger.warning(f"JSON 字段解析失败，使用默认值。")
                return default
        return value

    def _normalize_favour_levels(self, levels: Any) -> list:
        """将 favour_levels 规范化。"""
        return self._normalize_json_field(levels, DEFAULT_CONFIG["favour_levels"].copy())

    def _normalize_config_after_load(self, config: dict) -> dict:
        """加载配置后规范化所有可能的 JSON 编辑器字段。"""
        if "favour_levels" in config:
            config["favour_levels"] = self._normalize_favour_levels(config["favour_levels"])
        
        if "favour_decay" in config and isinstance(config["favour_decay"], dict):
            fd = config["favour_decay"]
            if "advanced_rules" in fd:
                fd["advanced_rules"] = self._normalize_json_field(
                    fd["advanced_rules"], 
                    DEFAULT_CONFIG["favour_decay"]["advanced_rules"]
                )
        
        if "active_chat" in config and isinstance(config["active_chat"], dict):
            ac = config["active_chat"]
            if "rules" in ac:
                ac["rules"] = self._normalize_json_field(
                    ac["rules"],
                    DEFAULT_CONFIG["active_chat"]["rules"]
                )
        
        return config

    def _migrate_old_config(self, old: Dict[str, Any]) -> Dict[str, Any]:
        """将旧版框架配置迁移为新版格式。"""
        new_config = DEFAULT_CONFIG.copy()

        # 基础字段直接迁移
        simple_keys = [
            "favour_mode", "is_global_favour", "group_sort_by",
            "enable_cold_violence", "enable_relationship_table",
            "min_favour_value", "max_favour_value", "default_favour"]
        for key in simple_keys:
            if key in old:
                new_config[key] = old[key]

        # 好感度分级（支持从 WebUI JSON 编辑器来的字符串）
        if "favour_levels" in old:
            new_config["favour_levels"] = self._normalize_favour_levels(old["favour_levels"])

        # 好感度衰减配置（兼容旧版线性 → 新版结构）
        if "favour_decay" in old:
            old_decay = old["favour_decay"]
            for k in new_config["favour_decay"]:
                if k in old_decay:
                    new_config["favour_decay"][k] = old_decay[k]
            # 旧版没有 mode 字段 → 默认 linear
            if "mode" not in old_decay:
                new_config["favour_decay"]["mode"] = "linear"

        # 主动搭话配置
        if "active_chat" in old:
            for k in new_config["active_chat"]:
                if k in old["active_chat"]:
                    new_config["active_chat"][k] = old["active_chat"][k]

        # 查询权限配置
        if "query_permission" in old:
            for k in new_config["query_permission"]:
                if k in old["query_permission"]:
                    new_config["query_permission"][k] = old["query_permission"][k]

        # 高级配置
        if "advanced_config" in old:
            adv = old["advanced_config"]
            for k in new_config["advanced_config"]:
                if k in adv:
                    new_config["advanced_config"][k] = adv[k]
        # 兼容：旧版没有 modify_favour_permission → 保持默认 "admin"
        if "modify_favour_permission" not in new_config.get("advanced_config", {}):
            new_config["advanced_config"]["modify_favour_permission"] = "admin"

        # 冷暴力配置
        if "cold_violence_config" in old:
            cv = old["cold_violence_config"]
            for k in new_config["cold_violence_config"]:
                if k in cv:
                    new_config["cold_violence_config"][k] = cv[k]

        # 规范化所有 JSON 编辑器字段
        new_config = self._normalize_config_after_load(new_config)
        logger.info("旧配置迁移完成（新增项使用默认值，可在 WebUI 中修改）。")
        return new_config

    def _save(self) -> None:
        """保存配置到文件（仅保存到 plugin_data 目录）。"""
        try:
            self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    def save(self) -> None:
        """公开的保存方法。"""
        self._save()

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项。"""
        if not self._config:
            self.load_or_create()
        return self._config.get(key, default)

    @property
    def config(self) -> Dict[str, Any]:
        """获取完整配置字典。"""
        if not self._config:
            self.load_or_create()
        return self._config

    def update_from_webui(self, webui_config: Dict[str, Any]) -> bool:
        """
        从 WebUI 接收配置更新并保存。
        验证分级配置合法性（至少3个分级，第8个起必填desc）。
        Returns: True 表示保存成功。
        """
        # 验证分级配置
        levels = webui_config.get("favour_levels", [])
        if len(levels) < 3:
            logger.error("好感度分级至少需要3个，保存失败。")
            return False

        for i, lv in enumerate(levels):
            if i >= 7:  # 第8个起（index 7+）
                if not lv.get("desc", "").strip():
                    logger.error(f"第 {i+1} 个分级（{lv.get('name', '未命名')}）的描述为必填项，保存失败。")
                    return False
            # 确保必要字段存在
            lv.setdefault("name", f"等级{i+1}")
            lv.setdefault("desc", "")

        self._config = self._deep_merge(self._config, webui_config)
        self._config["favour_levels"] = levels  # 确保分级完全按 WebUI 的来
        self._save()
        logger.info("配置已通过 WebUI 更新并保存。")
        return True
