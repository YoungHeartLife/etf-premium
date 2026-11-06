#!/usr/bin/env python3
"""本地一键启动：数据库缺失时先全量采集，再启动 Web 服务。

等价于：
    python3 collect.py --full   # 仅首次
    python3 server.py

端口、数据库路径等通过环境变量覆盖（见 config.py / README）。
"""
from __future__ import annotations

import os
import subprocess
import sys

from config import BASE_DIR, DB_PATH

def main() -> None:
    os.chdir(BASE_DIR)
    if not os.path.exists(DB_PATH):
        print(f"数据库不存在，开始首次全量采集 -> {DB_PATH}")
        subprocess.run([sys.executable, "collect.py", "--full"], check=True)
    os.execv(sys.executable, [sys.executable, "server.py"])

if __name__ == "__main__":
    main()
