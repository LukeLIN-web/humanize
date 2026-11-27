#!/usr/bin/env python3
"""Wait on a long job under a Monitor: poll its logs and print a line only when the agent has
something to act on, so the session wakes only then.

    watch-job.py --log 'run/logs/*.log' --log 'run/services/*.log' --match 'gate .*done' \\
        --ignore 'carb::windowing|services/proxy.*: Traceback' --pid 12345 --state run/watch.state \\
        --done 'test $(ls run/out/*.json | wc -l) -ge 120'
    watch-job.py --check --log ... --match ... --ignore ...     # dry run over the whole logs

Prints (stdout; each line wakes the agent, lines of one round arrive as one notification):
- a new log line matching --match or a failure signature (ALERT), unless `<path>: <line>`
  matches --ignore; the same line of the same file only once; at most PER_FILE per file and
  ROUND_MAX per round;
- DONE when the --done shell command exits 0 (exit 0); EXITED when every --pid is gone first
  (exit 2); --pid only sees processes on this host;
- STALL once for each --log glob whose files have not grown for --stall seconds; again only
  after it grows.
After --for seconds it exits 1 with nothing on stdout (a progress line goes to stderr):
re-arm it. Under a Monitor: timeout_ms 1800000 with the default --for 1740.

Re-arm with the same --state file: it carries on where the last run stopped, so lines written
between two runs are neither lost nor replayed and a stall is not reported twice. Without
--state (or on its first run) it starts at the current end of every log. Globs are expanded
again every round; a file that appears later is read from its start, one that shrank from 0.
It reads appended bytes, not inotify, so logs another host writes over NFS are seen.

--check reads every matching file from its start once and prints each kind of line it would
wake on (words with digits folded, first 100 characters) with how many lines and files, and
every glob that matches no file. Run it on the smoke run's logs before launching the full
job: every failure the smoke hit must be listed; lines a healthy job prints go into --ignore;
then --check again.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

# "ECC error", not a bare "ECC": Isaac Sim logs "ECC is enabled on physical device" at every start.
ALERT = (r"Traceback|Error|FAILED|Killed|OOM|out of memory|core dumped|Segmentation fault"
         r"|ECC error|Xid|\brc=[1-9]")
PER_FILE = 5
ROUND_MAX = 20
SEEN_MAX = 5000
KIND_CHARS = 100


def emit(line):
    print(line, flush=True)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def files_of(pattern):
    return sorted(glob.glob(pattern))


def size_of(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def newest_mtime(paths):
    times = []
    for p in paths:
        try:
            times.append(os.path.getmtime(p))
        except OSError:
            pass
    return max(times) if times else None


def check(a, wanted):
    kinds, globs_empty, n_files, n_lines = {}, [], set(), 0
    for g in a.log:
        paths = files_of(g)
        if not paths:
            globs_empty.append(g)
        for path in paths:
            if path in n_files:
                continue
            n_files.add(path)
            with open(path, "rb") as f:
                for raw in f:
                    n_lines += 1
                    ln = raw.decode(errors="replace").strip()
                    if not wanted(path, ln):
                        continue
                    k = kinds.setdefault(re.sub(r"\w*\d[\w,.:-]*", "N", ln)[:KIND_CHARS], [0, set(), path, ln])
                    k[0] += 1
                    k[1].add(path)
    print(f"CHECK: {len(n_files)} files, {n_lines} lines, {len(kinds)} kinds of line would wake you")
    ranked = sorted(kinds.values(), key=lambda k: -k[0])
    for count, paths, first, ln in ranked[:40]:
        print(f"{count:6d} lines {len(paths):4d} files | {ln[:240]} | first in {os.path.basename(first)}")
    if len(ranked) > 40:
        print(f"(+{len(ranked) - 40} more kinds)")
    for g in globs_empty:
        print(f"NO FILES: {g}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", action="append", default=[], help="glob, repeatable; each one is a group for STALL")
    ap.add_argument("--match", help="regex of milestone lines to print as well as the failure signatures")
    ap.add_argument("--ignore", help="regex of lines never to print (what a healthy job prints anyway), matched against `<path>: <line>`")
    ap.add_argument("--done", help="shell command; exit 0 means finished (judge by the job's products)")
    ap.add_argument("--pid", type=int, action="append", default=[], help="pid on this host, repeatable")
    ap.add_argument("--state", help="file that carries offsets, printed lines and stalls across re-arms")
    ap.add_argument("--every", type=float, default=60, help="seconds between rounds")
    ap.add_argument("--stall", type=float, default=1800, help="seconds without log growth that make a STALL")
    ap.add_argument("--for", dest="duration", type=float, default=1740, help="seconds before exiting 1 to be re-armed")
    ap.add_argument("--check", action="store_true", help="dry run over the whole logs, then exit")
    a = ap.parse_args()
    pattern = re.compile(f"{a.match}|{ALERT}" if a.match else ALERT)
    ignore = re.compile(a.ignore) if a.ignore else None

    def wanted(path, ln):
        return bool(pattern.search(ln)) and not (ignore and ignore.search(f"{path}: {ln}"))

    if a.check:
        check(a, wanted)
        return

    state = {}
    if a.state and os.path.exists(a.state):
        with open(a.state) as f:
            state = json.load(f)
    resumed = bool(state)
    offsets = state.get("offsets", {})
    seen = dict.fromkeys(state.get("seen", []))
    stalled = set(state.get("stalled", []))
    if not resumed:
        offsets = {p: size_of(p) or 0 for g in a.log for p in files_of(g)}
    start = time.time()
    new_lines, latest = 0, ""
    while True:
        printed, held, held_files = 0, 0, set()
        for path in sorted({p for g in a.log for p in files_of(g)}):
            size = size_of(path)
            if size is None:
                continue
            off = offsets.get(path, 0)
            if size < off:
                off = 0
            if size == off:
                offsets[path] = off
                continue
            with open(path, "rb") as f:
                f.seek(off)
                chunk = f.read(size - off)
            complete = chunk.rfind(b"\n") + 1
            offsets[path] = off + complete
            lines = [ln.strip() for ln in chunk[:complete].decode(errors="replace").splitlines()]
            if not lines:
                continue
            new_lines += len(lines)
            latest = f"{path}: {lines[-1][:200]}"
            hits = [ln for ln in dict.fromkeys(lines) if wanted(path, ln) and f"{path}\t{ln}" not in seen]
            for ln in hits:
                seen[f"{path}\t{ln}"] = None
            for i, ln in enumerate(hits):
                if i < PER_FILE and printed < ROUND_MAX:
                    emit(f"{path}: {ln[:300]}")
                    printed += 1
                else:
                    held += 1
                    held_files.add(path)
        if held:
            emit(f"(+{held} more lines in {len(held_files)} files)")
        now = time.time()
        for g in a.log:
            last = newest_mtime(files_of(g))
            if last is None:
                continue
            if now - last > a.stall:
                if g not in stalled:
                    emit(f"STALL: {g} has not grown for {(now - last) / 60:.0f} min")
                    stalled.add(g)
            else:
                stalled.discard(g)
        if a.state:
            tmp = a.state + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"offsets": offsets, "seen": list(seen)[-SEEN_MAX:], "stalled": sorted(stalled)}, f)
            os.replace(tmp, a.state)
        if a.done and subprocess.run(["bash", "-c", a.done], capture_output=True).returncode == 0:
            emit(f"DONE: {a.done}")
            sys.exit(0)
        if a.pid and not any(alive(p) for p in a.pid):
            emit(f"EXITED: pid {' '.join(map(str, a.pid))} gone" + (", --done not met" if a.done else "")
                 + f"; last: {latest}")
            sys.exit(2)
        if now - start > a.duration:
            print(f"still running after {(now - start) / 60:.0f} min: +{new_lines} log lines; last: {latest}",
                  file=sys.stderr)
            sys.exit(1)
        time.sleep(a.every)


if __name__ == "__main__":
    main()
