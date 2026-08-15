"""向后兼容入口：能源分类实现已迁移到 energy_taxonomy_v2。"""

import sys
from pathlib import Path

# Studio 等独立工具会按文件路径加载本模块；此时先补 src 根，确保兼容导入可用。
_src_root = Path(__file__).resolve().parents[1]
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

from s01b_project_extractor.energy_taxonomy_v2 import *  # noqa: F401,F403,E402
