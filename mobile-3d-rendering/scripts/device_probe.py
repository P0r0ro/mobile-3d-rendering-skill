#!/usr/bin/env python3
"""Android soak probe for 3D games: fps/jank, memory trend (leaks), thermals, CPU clocks.

Engine-agnostic: it reads the OS (SurfaceFlinger, dumpsys meminfo, thermalservice), so it works
for Godot, Unity, Unreal, Bevy, WebView/three.js... Optionally parses the engine's own log line.

  python3 device_probe.py --package com.example.game --seconds 120 --launch
  python3 device_probe.py -p com.example.game -s 600 --interval 10 --csv soak.csv \
      --log-regex 'fps=(?P<fps>\\d+).*draws=(?P<draws>\\d+)'

Exit code: 0 PASS, 1 WARN, 2 FAIL (process died, memory leaking, fps far below target).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

# ----------------------------------------------------------------------------- adb


def find_adb(explicit: str | None) -> str:
    for c in (explicit, os.environ.get("ADB"),
              os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
              os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
              shutil.which("adb")):
        if c and os.path.exists(c):
            return c
    sys.exit("adb not found: pass --adb or set $ADB")


class Adb:
    def __init__(self, path: str, serial: str | None):
        self.base = [path] + (["-s", serial] if serial else [])

    def sh(self, cmd: str, timeout: float = 15.0) -> str:
        try:
            r = subprocess.run(self.base + ["shell", cmd], capture_output=True, text=True,
                               timeout=timeout, errors="replace")
            return r.stdout
        except subprocess.TimeoutExpired:
            return ""

    def run(self, *args: str, timeout: float = 30.0) -> str:
        r = subprocess.run(self.base + list(args), capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return r.stdout


# ----------------------------------------------------------------------------- readers

MEM_ROWS = {  # label in output -> regex on dumpsys meminfo (first number = PSS KB)
    "pss": r"^\s*TOTAL(?: PSS)?:?\s+(\d+)",
    "native": r"^\s*Native Heap\s+(\d+)",
    "gl": r"^\s*GL mtrack\s+(\d+)",
    "egl": r"^\s*EGL mtrack\s+(\d+)",
    "gfx": r"^\s*Graphics:\s+(\d+)",
}


def read_mem(adb: Adb, pkg: str) -> dict[str, float]:
    out = adb.sh(f"dumpsys meminfo {pkg}", timeout=20)
    res: dict[str, float] = {}
    for key, rx in MEM_ROWS.items():
        m = re.search(rx, out, re.M)
        if m:
            res[key] = int(m.group(1)) / 1024.0  # MB
    return res


def read_thermal(adb: Adb) -> dict[str, float]:
    out = adb.sh("dumpsys thermalservice")
    res: dict[str, float] = {}
    m = re.search(r"Thermal Status:\s*(\d+)", out)
    if m:
        res["tstatus"] = int(m.group(1))
    # prefer the live HAL section, fall back to cached
    sec = out.split("Current temperatures from HAL:")[-1]
    for name, key in (("TYPE_CPU", "t_cpu"), ("TYPE_GPU", "t_gpu"), ("TYPE_SKIN", "t_skin"),
                      ("TYPE_BATTERY", "t_batt")):
        m = re.search(r"mValue=(-?[\d.]+), mType=\d+, mName=" + name, sec)
        if m and float(m.group(1)) > -50:
            res[key] = float(m.group(1))
    if "t_batt" not in res:
        m = re.search(r"temperature:\s*(\d+)", adb.sh("dumpsys battery"))
        if m:
            res["t_batt"] = int(m.group(1)) / 10.0
    return res


_CPU_MAX: dict[int, int] = {}


def read_cpu(adb: Adb) -> dict[str, float]:
    """Big-cluster clock as % of its max (throttling shows up here first)."""
    if not _CPU_MAX:
        out = adb.sh("for c in /sys/devices/system/cpu/cpu[0-9]*; do echo ${c##*cpu} "
                     "$(cat $c/cpufreq/cpuinfo_max_freq 2>/dev/null); done")
        for line in out.split("\n"):
            p = line.split()
            if len(p) == 2 and p[1].isdigit():
                _CPU_MAX[int(p[0])] = int(p[1])
    if not _CPU_MAX:
        return {}
    top = max(_CPU_MAX.values())
    big = [c for c, f in _CPU_MAX.items() if f == top]
    out = adb.sh(" ; ".join(f"cat /sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq "
                            "2>/dev/null" for c in big))
    cur = [int(x) for x in out.split() if x.isdigit()]
    res = {"cpu_big_pct": 100.0 * max(cur) / top} if cur else {}
    gpu = adb.sh("cat /sys/kernel/gpu/gpu_clock /sys/class/kgsl/kgsl-3d0/gpuclk "
                 "/sys/class/devfreq/*mali*/cur_freq 2>/dev/null").split()
    if gpu and gpu[0].isdigit():
        v = int(gpu[0])
        res["gpu_mhz"] = v / 1e6 if v > 1e5 else float(v)
    return res


def find_layer(adb: Adb, pkg: str) -> str | None:
    out = adb.sh("dumpsys SurfaceFlinger --list")
    lines = [ln.strip() for ln in out.split("\n") if pkg in ln]
    for pref in ("SurfaceView", "BLAST", ""):
        for ln in lines:
            if pref in ln and "Background" not in ln:
                return ln
    return None


def read_frames(adb: Adb, layer: str) -> list[int]:
    """Present timestamps (ns) of the last ~127 frames of the layer."""
    out = adb.sh(f"dumpsys SurfaceFlinger --latency '{layer}'")
    ts: list[int] = []
    for ln in out.split("\n")[1:]:
        p = ln.split()
        if len(p) == 3 and p[1].isdigit():
            v = int(p[1])
            if 0 < v < 2**62:
                ts.append(v)
    return ts


def frame_stats(ts: list[int], target: float) -> dict[str, float]:
    ts = sorted(set(ts))
    if len(ts) < 10:
        return {}
    d = [(b - a) / 1e6 for a, b in zip(ts, ts[1:]) if b > a]
    span = (ts[-1] - ts[0]) / 1e9
    budget = 1000.0 / target
    return {"fps": (len(ts) - 1) / span if span > 0 else 0.0,
            "p95_ms": sorted(d)[int(len(d) * 0.95) - 1],
            "max_ms": max(d),
            "jank_pct": 100.0 * sum(1 for x in d if x > budget * 1.5) / len(d)}


def pid_of(adb: Adb, pkg: str) -> str:
    return adb.sh(f"pidof {pkg}").strip()


# ----------------------------------------------------------------------------- analysis


def slope_per_min(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return 0.0
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return 60.0 * sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--package", required=True)
    ap.add_argument("-s", "--seconds", type=float, default=120)
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--warmup", type=float, default=20, help="seconds ignored for leak slope")
    ap.add_argument("--target-fps", type=float, default=60)
    ap.add_argument("--launch", action="store_true", help="force-stop, clear logcat, launch")
    ap.add_argument("--log-regex", help="regex with named groups (e.g. fps, draws) over logcat")
    ap.add_argument("--leak-warn", type=float, default=3.0, help="MB/min")
    ap.add_argument("--leak-fail", type=float, default=12.0, help="MB/min")
    ap.add_argument("--csv")
    ap.add_argument("--adb")
    ap.add_argument("--serial")
    a = ap.parse_args()

    adb = Adb(find_adb(a.adb), a.serial)
    dev = adb.sh("getprop ro.product.model").strip()
    gpu = adb.sh("getprop ro.hardware.egl").strip()
    sdk = adb.sh("getprop ro.build.version.sdk").strip()
    if not dev:
        sys.exit("no device (adb devices is empty or unauthorized)")
    print(f"# device {dev}  gpu-driver={gpu}  sdk={sdk}  pkg={a.package}")

    if a.launch:
        adb.sh(f"am force-stop {a.package}")
        adb.run("logcat", "-c")
        adb.sh(f"monkey -p {a.package} -c android.intent.category.LAUNCHER 1")
        time.sleep(4)
    pid = pid_of(adb, a.package)
    if not pid:
        sys.exit(f"{a.package} is not running (use --launch)")
    layer = None
    for _ in range(5):
        layer = find_layer(adb, a.package)
        if layer:
            break
        time.sleep(1)
    print(f"# pid {pid}  layer={layer or 'not found (fps from --log-regex only)'}")

    cols = ["t", "fps", "p95_ms", "max_ms", "jank_pct", "pss", "native", "gl", "egl", "gfx",
            "tstatus", "t_cpu", "t_batt", "cpu_big_pct", "gpu_mhz"]
    print(" ".join(f"{c:>8}" for c in cols))
    rows: list[dict[str, float]] = []
    died = False
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        tick = time.time()
        if pid_of(adb, a.package) != pid:
            died = True
            print(f"!! process {pid} is gone at t={tick - t0:.0f}s (crash or low-memory kill)")
            break
        r: dict[str, float] = {"t": tick - t0}
        if layer:
            r.update(frame_stats(read_frames(adb, layer), a.target_fps))
        r.update(read_mem(adb, a.package))
        r.update(read_thermal(adb))
        r.update(read_cpu(adb))
        rows.append(r)
        print(" ".join(f"{r[c]:8.1f}" if c in r else f"{'-':>8}" for c in cols), flush=True)
        time.sleep(max(0.0, a.interval - (time.time() - tick)))

    if a.csv:
        with open(a.csv, "w") as f:
            f.write(",".join(cols) + "\n")
            for r in rows:
                f.write(",".join(f"{r[c]:.2f}" if c in r else "" for c in cols) + "\n")

    # ---- log regex (engine-side counters)
    log_vals: dict[str, list[float]] = {}
    if a.log_regex:
        rx = re.compile(a.log_regex)
        for ln in adb.run("logcat", "-d").split("\n"):
            m = rx.search(ln)
            if m:
                for k, v in (m.groupdict() or {"value": m.group(1)}).items():
                    try:
                        log_vals.setdefault(k, []).append(float(v))
                    except (TypeError, ValueError):
                        pass
    if died:
        kills = [ln for ln in adb.run("logcat", "-d", "-b", "all").split("\n")
                 if re.search(r"lowmemorykiller|lmkd|Kill '|FATAL|SIGSEGV|SIGABRT", ln)
                 and (a.package in ln or "lmk" in ln.lower())]
        print("\n".join(["# last kill/crash log lines:"] + kills[-8:]))

    # ---- verdict
    print("\n# summary")
    verdict = 0
    fails: list[str] = []
    warns: list[str] = []
    if died:
        fails.append("process died during the soak")
    warm = [r for r in rows if r["t"] >= a.warmup] or rows
    for key in ("pss", "native", "gl", "egl", "gfx"):
        pts = [(r["t"], r[key]) for r in warm if key in r]
        if len(pts) >= 3:
            s = slope_per_min([p[0] for p in pts], [p[1] for p in pts])
            print(f"  {key:7s} {pts[0][1]:8.1f} -> {pts[-1][1]:8.1f} MB   slope {s:+7.2f} MB/min")
            if key in ("pss", "native", "gl", "egl", "gfx") and s > a.leak_fail:
                fails.append(f"{key} grows {s:.1f} MB/min (leak)")
            elif s > a.leak_warn:
                warns.append(f"{key} grows {s:.1f} MB/min (watch it: run longer)")
    fps = [r["fps"] for r in warm if "fps" in r]
    if not fps and "fps" in log_vals:
        fps = log_vals["fps"]
    if fps:
        med = statistics.median(fps)
        low = min(fps)
        jank = statistics.fmean([r.get("jank_pct", 0.0) for r in warm])
        worst = max((r.get("max_ms", 0.0) for r in warm), default=0.0)
        print(f"  fps     median {med:.1f}  min {low:.1f}  jank {jank:.1f}% (>1.5x budget)  worst frame {worst:.0f} ms")
        if med < a.target_fps * 0.8:
            fails.append(f"median fps {med:.0f} < 80% of target {a.target_fps:.0f}")
        elif med < a.target_fps * 0.95 or jank > 5:
            warns.append(f"fps median {med:.0f}, jank {jank:.1f}%")
        if worst > 100:
            warns.append(f"hitch of {worst:.0f} ms (shader compile / load / GC?)")
        if len(fps) >= 6:
            first = statistics.fmean(fps[:3])
            last = statistics.fmean(fps[-3:])
            if last < first * 0.9:
                warns.append(f"fps fell {first:.0f} -> {last:.0f} over the run (thermal throttling?)")
    ts = [r["tstatus"] for r in rows if "tstatus" in r]
    if ts:
        print(f"  thermal status max {max(ts):.0f} (0 none,1 light,2 moderate,3 severe,4+ critical)")
        if max(ts) >= 3:
            fails.append("thermal status severe")
        elif max(ts) >= 2:
            warns.append("thermal status moderate: the OS is throttling")
    tc = [r["t_cpu"] for r in rows if "t_cpu" in r]
    if tc:
        print(f"  cpu temp {tc[0]:.1f} -> {tc[-1]:.1f} C (max {max(tc):.1f})")
    cb = [r["cpu_big_pct"] for r in warm if "cpu_big_pct" in r]
    if cb:
        print(f"  big-core clock median {statistics.median(cb):.0f}% of max")
    for k, v in log_vals.items():
        print(f"  log:{k:6s} median {statistics.median(v):.1f}  min {min(v):.1f}  max {max(v):.1f}  (n={len(v)})")
    for w in warns:
        print("  WARN", w)
    for f_ in fails:
        print("  FAIL", f_)
    verdict = 2 if fails else (1 if warns else 0)
    print(["PASS", "WARN", "FAIL"][verdict])
    if a.seconds < 60 and verdict == 0:
        print("  note: <60 s cannot rule out a slow driver leak; soak 2-10 min before shipping")
    return verdict


if __name__ == "__main__":
    sys.exit(main())
