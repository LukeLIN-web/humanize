"""Read a Claude Code TUI pane: capture it and parse the bottom of the screen.

Imported by tick-state.py and inject.py. Run directly to print one parse:
    python3 pane.py [-S <tmux-socket>] <pane>
"""
import json
import re
import subprocess
import sys

RULE = re.compile(r"^─{8,}")  # composer rules; the top one may carry the session title
POPUP = re.compile(r"\b0: Dismiss\b")
LIMIT = re.compile(r"hit your [\w ]*limit[^\n]*?resets ([^\n·]+)", re.I)


def tmux(socket, *args, check=True):
    cmd = ["tmux"] + (["-S", socket] if socket else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r.stdout


def capture(socket, pane):
    return tmux(socket, "capture-pane", "-p", "-J", "-t", pane)


def squash(s):
    """Text with all whitespace removed: the composer wraps long lines at its own width."""
    return re.sub(r"\s+", "", s or "")


def parse(text):
    """What the bottom of a Claude Code screen shows.

    composer: text after the ❯ prompt between the last two rules (None when the last
              rule pair holds no prompt, e.g. a decision menu replaced it)
    popup:    the rating / "Heads up" / "Dismissed." rows just above the composer
    busy:     a tool or turn is running ("esc to interrupt" in the status line)
    menu, permission, usage_limit: the stall markers the skill's §5.2 / §5.5 / §5.6 key on
    """
    lines = [l.rstrip() for l in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    rules = [i for i, l in enumerate(lines) if RULE.match(l)]
    out = {"tui": False, "composer": None, "popup": None, "busy": False, "menu": False,
           "permission": False, "usage_limit": None, "status": ""}
    tail = "\n".join(lines[-15:])
    out["menu"] = "Enter to select" in tail
    out["permission"] = "Do you want to proceed?" in tail
    m = LIMIT.search("\n".join(lines[-25:]))
    out["usage_limit"] = m.group(1).strip() if m else None
    if len(rules) >= 2:
        top, bot = rules[-2], rules[-1]
        body = lines[top + 1:bot]
        if body and body[0].startswith("❯") and not (out["menu"] or out["permission"]):
            first = body[0][1:].strip()
            rest = [l.strip() for l in body[1:]]
            out["composer"] = "\n".join([first] + rest).strip("\n")
            above = [l.strip() for l in lines[max(0, top - 6):top] if l.strip()]
            hits = [j for j, l in enumerate(above) if POPUP.search(l)]
            if hits:
                i = hits[-1]
                # "1: Learn more … 0: Dismiss" belongs to the header row above it;
                # "✦ Dismissed.  1: … 0: Dismiss" is a whole popup on one row
                head = [above[i - 1]] if i and above[i][:1].isdigit() else []
                out["popup"] = " / ".join(head + [above[i]])
        below = [l.strip() for l in lines[bot + 1:] if l.strip()]
        out["status"] = below[0] if below else ""
        out["busy"] = any("esc to interrupt" in l for l in below)
    out["tui"] = out["composer"] is not None or out["menu"] or out["permission"]
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    sock = None
    if a[:1] == ["-S"]:
        sock, a = a[1], a[2:]
    if len(a) != 1:
        sys.exit("usage: pane.py [-S <tmux-socket>] <pane>")
    print(json.dumps(parse(capture(sock, a[0])), ensure_ascii=False, indent=1))
