import sys
import types
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Stub optional Home Assistant dependencies not required for tests
sys.modules.setdefault("psutil_home_assistant", types.ModuleType("psutil_home_assistant"))

fnv_module = types.ModuleType("fnv_hash_fast")
fnv_module.fnv1a_32 = lambda data: 0
sys.modules.setdefault("fnv_hash_fast", fnv_module)
