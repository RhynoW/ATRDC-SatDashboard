import sys
from pathlib import Path

# 讓 tests 能直接 import scenario04（不需安裝套件）
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
