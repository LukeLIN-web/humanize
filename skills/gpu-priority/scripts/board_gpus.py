"""One table of every GPU on every board host: who is on each card (board card
name, else project and command), the gpu_wait claims, and who is still queued
for a card. Reads the `gpus` rows of the board's /api/windows (claude-board
core/gpus.py), the same data the board's GPUs panel draws.

    python3 board_gpus.py
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../todo-dispatch/scripts"))
import dispatch  # noqa: E402  the board client todo-dispatch already uses

PROJECT = re.compile(r"/workspace/(?:juyi/(?:robot/)?)?([\w.-]+)|/tmp/([\w.-]+)|/(volo)/")


def label(p: dict, names: dict[str, str], age: bool = True) -> str:
    if p["pid"] is None:
        return "(another container)"
    if p.get("card"):
        who = names.get(p["card"], p["card"])
    else:
        m = PROJECT.search(p["cwd"] + " " + p["cmd"])
        who = next((g for g in m.groups() if g), "?") if m else "?"
    argv = p["cmd"].split(" <- ")[-1].split()
    if "serve" in argv:  # a model server: the model and port say whose it is
        k = argv.index("serve")
        port = argv[argv.index("--port") + 1] if "--port" in argv[:-1] else "?"
        what = f'serve {os.path.basename(argv[k + 1].rstrip("/")) if k + 1 < len(argv) else "?"} :{port}'
    else:
        i = next((k for k, a in enumerate(argv) if a.endswith((".py", ".sh"))), 0)
        what = " ".join(argv[i:i + 3])
    when = f'{(time.time() - p["started_at"]) / 60:.0f}m' if p.get("started_at") else "?"
    via = f' ssh<{p["ssh_from"]}' if p.get("ssh_from") else ""
    return f'{who:<18} {what[-64:]:<64} {when if age else ""}{via}'


def main() -> None:
    snap = dispatch.call("GET", "/api/windows")
    names = {w["key"]: w.get("name") or w["key"] for w in snap["windows"]}
    for h in snap.get("gpus", []):
        print(f'== {h["host"]}{"  STALE" if h["stale"] else ""}')
        for c in h["cards"]:
            used = f'{c["used_mb"] / 1024:5.1f}/{c["total_mb"] / 1024:.0f}G' if c["total_mb"] else "  N/A"
            util = c["util"] if c["util"] is not None else "-"
            for k, p in enumerate(c["procs"] or [None]):
                head = f'  {c["index"]} {used} {util:>3}%' if k == 0 else " " * 20
                print(f'{head}  {label(p, names) if p else "-- free --"}')
        # CUDA ordinals, which differ from the nvidia-smi index above on a box
        # with a card CUDA can't see, so claims are listed by holder; one line
        # per kind of job, a card it holds k slots of repeated k times
        claims: dict[str, list[int]] = {}
        for cl in h["claims"]:
            claims.setdefault(label(cl, names, age=False), []).extend(cl["cuda"])
        for who, ords in claims.items():
            print(f'  CLAIM cuda {",".join(map(str, sorted(ords)))}  {who}')
        waits: dict[str, int] = {}
        for w in h["waiting"]:
            m = re.search(r"--need-gb\s+(\S+)", w["cmd"])
            n = re.search(r"--num-gpus\s+(\S+)", w["cmd"])
            key = f'need {m.group(1) if m else "?"}G x{n.group(1) if n else 1}  {label(w, names, age=False)}'
            waits[key] = waits.get(key, 0) + 1
        for key, k in waits.items():
            print(f'  WAIT {k} job{"s" if k > 1 else ""} {key}')


if __name__ == "__main__":
    main()
