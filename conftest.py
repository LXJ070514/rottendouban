"""让 `import crawler` 在 pytest 下可用（测试直接跑源码，无需 pip install）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
