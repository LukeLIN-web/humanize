"""Write this host's GPU state — each card, who is on it, who holds its gpu_wait lock,
who is still waiting for a card — to <out>/<hostname>.json. Read-only; stdlib only.

    python3 -I gpu_snapshot.py [--out DIR]

The file is named after the host the script actually ran on, so a pane that silently
fell back to the launching machine shows up as the wrong file, not as wrong data.
"""
import argparse
import json
import os
import socket
import subprocess
import time

LOCK_DIR = "/tmp/gpu_wait_locks"  # same box-local dir both gpu_wait copies use
CLK = os.sysconf("SC_CLK_TCK")


def smi(kind: str, fields: str) -> list[list[str]]:
    out = subprocess.run(["nvidia-smi", f"--query-{kind}={fields}", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return [[c.strip() for c in line.split(",")] for line in out.splitlines() if line.strip()]


def num(x: str) -> int | None:
    return int(x) if x.isdigit() else None  # "[N/A]" on a card CUDA can't see


def read(pid: int, name: str) -> bytes:
    try:
        with open(f"/proc/{pid}/{name}", "rb") as f:
            return f.read()
    except OSError:
        return b""


def proc(pid: int, uptime: float) -> dict:
    env = dict(kv.split("=", 1) for kv in read(pid, "environ").decode(errors="replace").split("\0") if "=" in kv)
    stat = read(pid, "stat").decode(errors="replace")
    rest = stat[stat.rfind(")") + 2:].split() if stat else []
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = ""
    cmd = read(pid, "cmdline").replace(b"\0", b" ").decode(errors="replace").strip()
    if rest and "/" not in cmd:  # a renamed worker (VLLM::EngineCore): who started it is in the parent
        cmd += " <- " + read(int(rest[1]), "cmdline").replace(b"\0", b" ").decode(errors="replace").strip()
    return {
        "pid": pid,
        "pgid": int(rest[2]) if rest else None,
        "age_min": round((uptime - int(rest[19]) / CLK) / 60) if rest else None,
        "cwd": cwd,
        "cmd": cmd[:400],
        "session": env.get("CLAUDE_CODE_SESSION_ID"),  # set when a Claude Code Bash tool launched it on this host
        "ssh_from": env.get("SSH_CONNECTION", "").split(" ")[0] or None,  # launched through a remote-tmux ssh
    }


def lock_holders() -> dict[str, dict]:
    """gpu_wait lock file -> its note and whether a live process still holds the flock."""
    held = set()
    with open("/proc/locks") as f:
        for line in f:
            parts = line.split()
            if len(parts) > 5 and parts[1] == "FLOCK":
                held.add(parts[5])  # maj:min:inode
    out = {}
    if not os.path.isdir(LOCK_DIR):
        return out
    for name in sorted(os.listdir(LOCK_DIR)):
        path = os.path.join(LOCK_DIR, name)
        st = os.stat(path)
        with open(path, errors="replace") as f:
            note = f.read(300).strip()
        out[name] = {"held": f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}" in held, "note": note}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="/shared/user62/workspace/juyi/gpu-board")
    args = ap.parse_args()
    with open("/proc/uptime") as f:
        uptime = float(f.read().split()[0])

    cards = [{"index": int(i), "uuid": u, "used_mb": num(used), "total_mb": num(total), "util": num(util), "procs": []}
             for i, u, used, total, util in smi("gpu", "index,uuid,memory.used,memory.total,utilization.gpu")]
    by_uuid = {c["uuid"]: c for c in cards}
    for uuid, pid, mb in smi("compute-apps", "gpu_uuid,pid,used_memory"):
        p = proc(int(pid), uptime) if pid.isdigit() else {"pid": None, "cmd": "(pid hidden: another container)"}
        by_uuid[uuid]["procs"].append(dict(p, used_mb=num(mb)))

    # a gpu_wait still polling; after a claim the wrapper stays as the job's parent, so drop lock holders
    waiting = []
    claimed_pids = {int(n["note"].split()[0][4:]) for n in lock_holders().values()
                    if n["held"] and n["note"].startswith("pid=")}
    for d in os.listdir("/proc"):
        if d.isdigit():
            cmd = read(int(d), "cmdline").split(b"\0")
            if any(a.endswith(b"gpu_wait.py") for a in cmd[:3]) and int(d) not in claimed_pids:
                waiting.append(proc(int(d), uptime))

    host = socket.gethostname().split(".")[0]
    os.makedirs(args.out, exist_ok=True)
    tmp = os.path.join(args.out, f".{host}.json.tmp")
    with open(tmp, "w") as f:
        json.dump({"host": host, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cards": cards,
                   "locks": lock_holders(), "waiting": waiting}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(args.out, f"{host}.json"))
    print(f"GPU_SNAPSHOT_OK {host}")


if __name__ == "__main__":
    main()
