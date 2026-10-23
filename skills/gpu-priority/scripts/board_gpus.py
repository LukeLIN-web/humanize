"""One table of every card on every host: who is on it (project, board card, command) and
who is still waiting for one. Reads the gpu_snapshot.py files and, when the board is
reachable, its cards (session id -> card name).

    python3 board_gpus.py [--dir /shared/user62/workspace/juyi/gpu-board] [--stale-min 15]
"""
import argparse
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../todo-dispatch/scripts"))

PROJECT = re.compile(r"/workspace/(?:juyi/(?:robot/)?)?([\w.-]+)|/tmp/([\w.-]+)|/(volo)/")


def board_names() -> dict[str, str]:
    try:
        import dispatch  # the board client todo-dispatch already uses
        return {w["session_id"]: f'{w.get("name") or w["key"]}@{w["host"]}'
                for w in dispatch.call("GET", "/api/windows")["windows"] if w.get("session_id")}
    except Exception as e:  # board down: the table still stands on the snapshots
        print(f"(board unreachable: {e}; no card names)", file=sys.stderr)
        return {}


def label(p: dict, names: dict[str, str]) -> str:
    if p.get("pid") is None:
        return p["cmd"]
    m = PROJECT.search(p["cwd"] + " " + p["cmd"])
    project = next((g for g in m.groups() if g), "?") if m else "?"
    argv = p["cmd"].split(" <- ")[-1].split()
    if "serve" in argv:  # a model server: the model and port say whose it is
        k = argv.index("serve")
        port = argv[argv.index("--port") + 1] if "--port" in argv[:-1] else "?"
        what = f'serve {os.path.basename(argv[k + 1].rstrip("/")) if k + 1 < len(argv) else "?"} :{port}'
    else:
        i = next((k for k, a in enumerate(argv) if a.endswith((".py", ".sh"))), 0)
        what = " ".join(argv[i:i + 3])
    who = names.get(p.get("session"), "") if p.get("session") else ""
    via = f' ssh<{p["ssh_from"]}' if p.get("ssh_from") else ""
    return f'{project:<14} {what[-70:]:<70} {p["age_min"]}m{via} {who}'.rstrip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="/shared/user62/workspace/juyi/gpu-board")
    ap.add_argument("--stale-min", type=float, default=15)
    args = ap.parse_args()
    names = board_names()
    for path in sorted(glob.glob(os.path.join(args.dir, "*.json"))):
        snap = json.load(open(path))
        age = (time.time() - os.path.getmtime(path)) / 60
        stale = f"  STALE {age:.0f} min" if age > args.stale_min else ""
        print(f'== {snap["host"]}  {snap["time"]}{stale}')
        for c in snap["cards"]:
            used = f'{(c["used_mb"] or 0) / 1024:5.1f}/{(c["total_mb"] or 0) / 1024:.0f}G' if c["total_mb"] else "  N/A"
            procs = c["procs"] or [None]
            for k, p in enumerate(procs):
                util = c["util"] if c["util"] is not None else "-"
                head = f'  {c["index"]} {used} {util:>3}%' if k == 0 else " " * 20
                print(f'{head}  {label(p, names) if p else "-- free --"}')
        # lock names carry CUDA ordinals, which differ from the nvidia-smi index above on
        # boxes with a CUDA-hidden card, so they are listed by holder rather than placed on a row
        holders: dict[str, list[str]] = {}
        for name, lk in snap["locks"].items():
            if lk["held"]:
                holders.setdefault(" ".join(lk["note"].split()[1:4])[:70], []).append(name[3:-5])
        for note, ords in holders.items():
            print(f'  LOCK cuda {",".join(ords)}  {note}')
        for w in snap["waiting"]:
            m = re.search(r"--need-gb\s+(\S+)", w["cmd"])
            n = re.search(r"--num-gpus\s+(\S+)", w["cmd"])
            print(f'  WAIT need {m.group(1) if m else "?"}G x{n.group(1) if n else 1}  {label(w, names)}')


if __name__ == "__main__":
    main()
