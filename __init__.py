import importlib
import sys

# 1. 定义子模块列表
# ⚠️ 注意顺序：被依赖的模块放前面，依赖别人的模块放后面
# 顺序：常量 -> 工具 -> 权限 -> 存储 -> 主逻辑
_SUB_MODULES = [
    ".const",
    ".utils",
    ".permission",
    ".storage",
    ".main"
]

# 2. 强制热重载子模块
# 当 AstrBot 重载这个插件包时，这段代码会执行，强制刷新 sys.modules 中的缓存
for module in _SUB_MODULES:
    # 拼接完整的模块名，例如: astrbot_plugin_favour_ultra.const
    full_name = f"{__name__}{module}"
    
    if full_name in sys.modules:
        try:
            # 强制重载已加载的模块
            importlib.reload(sys.modules[full_name])
        except Exception as e:
            # 打印错误但不阻断加载（防止因语法错误导致彻底崩溃）
            print(f"[FavourUltra] Warning: Failed to reload module {full_name}: {e}")

# 3. 正常导出插件主类
from .main import FavourManagerTool
