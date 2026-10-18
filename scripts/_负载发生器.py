# -*- coding: utf-8 -*-
"""_负载发生器.py —— 烧机小程序：把一个 CPU 线程压到 100%

用法:
    python _负载发生器.py [秒数]      默认 5 秒

被 10-性能自检.py / 11-满载温度60秒.py 反复拉起 —— 起几个进程就是几个核满载。
本身不做任何测量，只负责制造负载。

来源：agent 工作区的 _burn2.py（2026-10-18 收录，未改逻辑）。
"""
import sys
import time

end = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 5)
x = 0.0
while time.time() < end:
    for _ in range(200000):
        x = x * 1.000001 + 1.0
print(x)
