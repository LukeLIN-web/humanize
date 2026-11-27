#!/usr/bin/env python3
"""Type one steer into a Claude Code composer and submit it (SKILL.md §5, §5.0).

usage: inject.py [-S <tmux-socket>] <pane> <<'EOF'
       <steer text>
       EOF

Steps, each reported on one stdout line:
  1. A popup over the composer (a row ending "0: Dismiss"): at most two dismiss keys.
     A key that landed in the composer is removed with one BSpace; the composer must
     then read as before.
  2. Two captures 3 s apart; a composer that changed means a human may be typing.
  3. A non-empty composer gets one probe space. Text gone -> a prompt suggestion: the
     probe is removed and typing replaces it. Text still there -> a human draft: the
     probe is removed, the draft is checked unchanged, and nothing is typed.
  4. The text (whitespace runs, newlines included, become one space) is typed
     literally and must land exactly; then Enter, and the composer must let go of it.
     Text that did not land is erased again with BSpace.
Exit codes:
  0  submitted (stdout says when a prompt suggestion was replaced)
  2  no composer on the pane (gone, decision menu, permission prompt): nothing sent
  3  a human is typing, or a human draft is in the composer: nothing typed
  4  the popup stayed up after two dismiss keys: nothing typed
  5  stopped midway (text did not land, Enter did not submit, or the composer
     changed under us); the last line says what the composer holds now
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pane import capture, parse, squash, tmux  # noqa: E402

GAP = 3.0  # seconds between the two "is a human typing" captures


def say(msg):
    print(f"inject: {msg}", flush=True)


class Pane:
    def __init__(self, sock, target):
        self.sock, self.target = sock, target

    def read(self):
        return parse(capture(self.sock, self.target))

    def send(self, *keys, literal=False, repeat=None):
        args = ["send-keys", "-t", self.target] + (["-l"] if literal else []) + \
               (["-N", str(repeat)] if repeat else []) + list(keys)
        tmux(self.sock, *args)

    def settle(self, before, timeout=2.0, until=None):
        """Capture until the screen differs from `before` (or `until(state)` holds), then once more."""
        end = time.time() + timeout
        st = self.read()
        while time.time() < end:
            done = until(st) if until else (st["composer"], st["popup"]) != (before["composer"], before["popup"])
            if done:
                time.sleep(0.2)
                return self.read()
            time.sleep(0.15)
            st = self.read()
        return st


def main():
    a = sys.argv[1:]
    sock = None
    if a[:1] == ["-S"]:
        sock, a = a[1], a[2:]
    if len(a) != 1 or a[0].startswith("-"):
        sys.exit(__doc__)
    target = a[0]
    text = " ".join(sys.stdin.read().split())
    if not text:
        sys.exit("inject: no steer text on stdin")
    p = Pane(sock, target)

    try:
        st = p.read()
    except RuntimeError as e:
        say(f"cannot capture {target}: {e}")
        return 2
    if st["composer"] is None:
        what = "a decision menu" if st["menu"] else "a permission prompt" if st["permission"] else "no composer"
        say(f"{what} on {target}; nothing sent")
        return 2

    before = st["composer"]
    keys = 0
    while st["popup"] and keys < 2:
        say(f"popup over the composer: {st['popup']}; sending its dismiss key 0")
        p.send("0", literal=True)
        keys += 1
        st = p.settle(st)
        if st["composer"] != before:
            landed = st["composer"]
            say(f"the key landed in the composer ({landed!r}); removing it with one BSpace")
            p.send("BSpace")
            st = p.settle(st)
            # typing over a prompt suggestion replaces it, so after the BSpace it may stay gone
            if not (st["composer"] == before or (landed == "0" and st["composer"] == "")):
                say(f"the composer did not come back: now {st['composer']!r}, before {before!r}")
                return 5
            before = st["composer"]
    if st["popup"]:
        say(f"popup still up after {keys} dismiss keys: {st['popup']}; nothing typed")
        return 4

    first = st["composer"]
    time.sleep(GAP)
    st = p.read()
    if st["composer"] != first:
        say(f"the composer changed within {GAP:.0f} s ({first!r} -> {st['composer']!r}): a human may be typing; nothing typed")
        return 3

    replaced = False
    if squash(first):
        p.send(" ", literal=True)
        # a suggestion vanishes under the space; a draft just gains an (invisible, trimmed) trailing space
        probe = p.settle(st, timeout=1.5, until=lambda s: squash(s["composer"]) != squash(first))
        if squash(probe["composer"]) == "":
            p.send("BSpace")
            st = p.settle(probe, until=lambda s: s["composer"] in ("", first))
            if st["composer"] not in ("", first):
                say(f"after the probe the composer reads {st['composer']!r}; stopping")
                return 5
            say(f"prompt suggestion in the composer ({first!r}); typing replaces it")
            replaced = True
        elif squash(probe["composer"]) == squash(first):
            p.send("BSpace")
            st = p.settle(probe, until=lambda s: s["composer"] == first)
            if st["composer"] != first:
                say(f"the human draft changed after removing the probe: now {st['composer']!r}")
                return 5
            say("a human draft is in the composer; left as it was, nothing typed")
            return 3
        else:
            say(f"after the probe the composer reads {probe['composer']!r} (was {first!r}); stopping")
            return 5

    want = squash(text)
    p.send(text, literal=True)
    st = p.settle(st, timeout=3 + len(text) / 100, until=lambda s: squash(s["composer"]) == want)
    if squash(st["composer"]) != want:
        say(f"the text did not land exactly: composer reads {st['composer']!r}; erasing it")
        p.send("BSpace", repeat=len(text) + 2)
        st = p.settle(st, timeout=3, until=lambda s: s["composer"] in ("", first))
        say(f"composer now {st['composer']!r}")
        return 5

    p.send("Enter")
    st = p.settle(st, timeout=5, until=lambda s: squash(s["composer"]) != want)
    if squash(st["composer"]) == want:
        say("Enter did not submit; the text is still in the composer, no more keys sent")
        return 5
    say("submitted" + (", replacing a prompt suggestion" if replaced else "") +
        f"; composer now {st['composer']!r}, {'busy' if st['busy'] else 'idle'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
