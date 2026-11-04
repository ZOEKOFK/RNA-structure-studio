"""让 pytest 在项目根目录下可以直接 import backend 包。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
