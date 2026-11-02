"""风扇 A/B 实验：同样负载下比较「原生 0x80」与「本工具曲线」。

用法：
    python fan_ab_test.py ours      # 只读 status/*.json，普通权限即可
    python fan_ab_test.py native    # 停服务 + 写 0x2F=0x80，需要管理员

两个模式都是 4 个进程 x 100 秒负载，前后各有空载观察段，结束后无条件恢复服务。
安全阀：EC >= 95°C 或 DTS >= 99°C 立即撤负载。

2026-11-02 实测结果（本机）：
    native : 空载 3891 RPM / EC 58-62°C；负载 40 秒 EC 92°C、DTS 95°C，转速只到 3896
    ours   : 负载稳态 EC 69-71°C、DTS 75°C、7 档 4392 RPM、PL1 28W 稳 2.6-2.9GHz
"""
import os
import sys
import json
import time
import subprocess
import multiprocessing

BASE = r"C:\Users\Ting\AppData\Roaming\T14scontrol"
SVC = "T14sFanSvc"
NPROC = 4
LOAD_S = 100
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def _burn(t_end):
    x = 12345
    while time.time() < t_end:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF


def sc(*a):
    return subprocess.run(["C:\\Windows\\System32\\sc.exe"] + list(a),
                          capture_output=True, text=True, errors="replace",
                          creationflags=0x08000000).stdout


def svc_state():
    o = sc("query", SVC)
    return "RUNNING" if "RUNNING" in o else ("STOPPED" if "STOPPED" in o else "?")


def wait_state(want, secs=20):
    t0 = time.time()
    while time.time() - t0 < secs:
        if svc_state() == want:
            return True
        time.sleep(0.5)
    return svc_state() == want


def jread(name):
    try:
        with open(os.path.join(BASE, "status", name), encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return {}


def run(kind):
    lines = []

    def w(s):
        lines.append(str(s))
        print(s, flush=True)

    ec = None
    if kind == "native":
        sys.path.insert(0, os.path.join(BASE, "cpu"))
        import CpuPLFix as C           # noqa: N813  只有这个模式需要 EC 直读
        if not C.ensure_driver(verbose=False):
            w("!! 驱动不可用；native 模式需要管理员")
            return 1
        ec = C

    def sample(tag):
        f = jread("fan.json")
        c = jread("cpu.json")
        rpm = f.get("rpm")
        hfsp = f.get("hfsp")
        if ec is not None:
            # native 模式：风扇服务已停，fan.json 会过期 -> 温度取 cpu.json（CPU 服务一直在写）
            h = ec.ec_read_ok(0x2F, tries=2)
            lo = ec.ec_read_ok(0x84, tries=2)
            hi = ec.ec_read_ok(0x85, tries=2)
            hfsp = h
            rpm = (hi << 8 | lo) if (lo is not None and hi is not None) else None
            t, d = c.get("ec"), c.get("dts")
        else:
            # ours 模式：服务在跑，fan.json 是最新快照
            t, d = f.get("ec") or c.get("ec"), f.get("dts") or c.get("dts")
        w("%-8s %s  hfsp=%-4s RPM=%-5s EC=%-4s DTS=%-4s PKG=%-5s freq=%s"
          % (tag, time.strftime("%H:%M:%S"), hfsp, rpm, t, d,
             f.get("pkg"), c.get("freq_mhz")))
        return t, d

    def phase(tag, secs):
        t0 = time.time()
        while time.time() - t0 < secs:
            time.sleep(10)
            t, d = sample(tag)
            if (t is not None and t >= 95) or (d is not None and d >= 99):
                w("!! 安全阀触发 EC=%s DTS=%s" % (t, d))
                return True
        return False

    w("=== 风扇 A/B（%s）  %s ===" % (kind, time.strftime("%Y-%m-%d %H:%M:%S")))
    w("负载 = %d 进程 / %d 线程，%d 秒" % (NPROC, multiprocessing.cpu_count(), LOAD_S))
    procs = []
    try:
        sample("基线")
        if kind == "native":
            w("\n--- 停服务 + 写 0x2F=0x80 ---")
            sc("stop", SVC)
            if not wait_state("STOPPED", 20):
                w("!! 停不下来")
                return 1
            assert ec is not None
            w("写 0x80 -> %s" % ec.ec_write(0x2F, 0x80))
            phase("idle", 40)
        else:
            w("\n[空载 40s]")
            phase("idle", 40)

        w("\n[加负载 %d 秒]" % LOAD_S)
        t_end = time.time() + LOAD_S
        procs = [multiprocessing.Process(target=_burn, args=(t_end,)) for _ in range(NPROC)]
        for p in procs:
            p.start()
        phase("load", LOAD_S)
        for p in procs:
            p.terminate()
        procs = []

        w("\n[撤负载 60s]")
        phase("after", 60)
    except Exception:
        import traceback
        w("!! 异常:\n" + traceback.format_exc())
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        if kind == "native":
            w("\n--- 恢复服务 ---")
            for _ in range(3):
                sc("start", SVC)
                if wait_state("RUNNING", 15):
                    break
                time.sleep(1)
            w("服务: " + svc_state())
        out = os.path.join(OUT_DIR, "风扇AB-%s-%s.txt"
                           % (kind, time.strftime("%Y%m%d-%H%M%S")))
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        print("WROTE", out)
    return 0


if __name__ == "__main__":
    mode = (sys.argv[1] if len(sys.argv) > 1 else "ours").lower()
    if mode not in ("ours", "native"):
        print("用法: python fan_ab_test.py [ours|native]")
        sys.exit(2)
    sys.exit(run(mode))
