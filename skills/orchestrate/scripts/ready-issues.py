#!/usr/bin/env python3
"""ready-issues.py — 里程碑模式的派活表：milestone issue 清单里每个 issue 现在是什么状态、哪些能派。

    ready-issues.py -R <owner/repo> <milestone issue 号> [--sync] [--json]

读 milestone issue 正文里的任务清单行。point 的 issue 号必须紧跟在勾选框后面：`- [ ] #12 feat: …`；
砍掉的 point 整段加删除线：`- [ ] ~~#12 feat: …~~ 原因` 或（没开过 issue 的）`- [ ] ~~feat: …~~ 原因`；
挪到别的 milestone 的也划掉（`~~#12 …~~ → #<新 milestone>`），issue 不关、把它正文的 `Milestone:` 行改成新的。
行里别处出现的 #N（「照 #7 的决定」）不算。对每个 #N 读状态、关闭原因、label 和正文里的 `Blocked-by:` 行
（`Blocked-by: #3 #4`，没有依赖写 `Blocked-by: none`），分成：

  closed       做完关了
  cut          砍掉或挪走了：清单里划掉了，或 issue 以 not planned / duplicate 关掉
  in-progress  开着、带 in-progress label（已派出去了）
  ready        开着、没派、Blocked-by 全做完了：现在就能派，一条消息里并行发
  blocked      开着、还有没做完的 Blocked-by（列出来）
  no-issue     清单行里还没有 #N（point 还没开 issue）

只剩 closed 和 cut 时，milestone 的 point 就都结了。

错误（退出 1）：依赖成环；Blocked-by 指向的 issue 不存在；Blocked-by 指向砍掉的 issue（not planned / duplicate
关掉的不算做完：改依赖，或者这个 point 也砍掉）。警告：清单的勾或删除线和 issue 状态对不上；开着的 issue 没写
Blocked-by 行（当作没有依赖，但要补上）。Blocked-by 可以指向清单外的 issue，照样读它的状态。

  --sync   把清单的勾按状态改对（closed、cut → [x]，其余 → [ ]），有改动才写回 milestone issue
  --json   输出 JSON，不输出表

退出码：0 正常；1 有错误（见上）；2 用法错或 gh 出错。
"""
import argparse
import json
import re
import subprocess
import sys

CHECK_RE = re.compile(r"^(\s*[-*]\s+\[)([ xX])(\]\s+)(.*)$")
REF_RE = re.compile(r"#(\d+)\b")
POINT_RE = re.compile(r"^(~~)?\s*#(\d+)\b")
CUT_REASONS = {"NOT_PLANNED", "DUPLICATE"}
BLOCKED_RE = re.compile(r"^\s*Blocked-by:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
MILESTONE_RE = re.compile(r"^\s*Milestone:\s*#(\d+)", re.IGNORECASE | re.MULTILINE)
IN_PROGRESS = "in-progress"


class GhError(Exception):
    pass


def gh(*args: str, stdin: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        raise GhError(r.stderr.strip() or f"gh {' '.join(args)} exited {r.returncode}")
    return r.stdout


def checklist(body: str) -> list[dict]:
    """milestone 正文里的任务清单行：行号、勾没勾、划没划掉、紧跟勾选框的 #N（没有为 None）、原文。"""
    items = []
    for i, line in enumerate(body.splitlines()):
        m = CHECK_RE.match(line)
        if m:
            text = m.group(4).strip()
            ref = POINT_RE.match(text)
            items.append({"line": i, "checked": m.group(2) != " ", "struck": text.startswith("~~"),
                          "number": int(ref.group(2)) if ref else None, "text": text})
    return items


def is_closed(iss: dict) -> bool:
    return iss["state"].upper() == "CLOSED"


def is_cut(iss: dict) -> bool:
    return is_closed(iss) and (iss.get("stateReason") or "").upper() in CUT_REASONS


def blockers(body: str) -> list[int] | None:
    """Blocked-by 行里的 issue 号；没有这一行为 None（`none` 是空表）。"""
    lines = BLOCKED_RE.findall(body or "")
    if not lines:
        return None
    return sorted({int(n) for line in lines for n in REF_RE.findall(line)})


def find_cycles(edges: dict[int, list[int]], open_nodes: set[int]) -> list[list[int]]:
    """只在开着的 issue 之间找环（关了的 issue 不再挡人）。每个环报一次。"""
    cycles, seen, color = [], set(), {}
    stack: list[int] = []

    def visit(n: int) -> None:
        color[n] = 1
        stack.append(n)
        for b in edges.get(n, []):
            if b not in open_nodes:
                continue
            if color.get(b) == 1:
                cyc = stack[stack.index(b):]
                key = frozenset(cyc)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cyc + [b])
            elif color.get(b) is None:
                visit(b)
        stack.pop()
        color[n] = 2

    for n in sorted(open_nodes):
        if color.get(n) is None:
            visit(n)
    return cycles


def moved_away(iss: dict, milestone: int | None) -> bool:
    """issue 正文的 Milestone: 行指向别的 milestone（挪走了）。"""
    m = MILESTONE_RE.search(iss.get("body") or "")
    return milestone is not None and m is not None and int(m.group(1)) != milestone


def classify(milestone_body: str, issues: dict[int, dict | None], milestone: int | None = None) -> dict:
    """issues：号 → gh issue view 的 JSON（不存在为 None），要含清单里的和 Blocked-by 指到的。
    milestone：这个 milestone issue 的号，用来认出挪到别的 milestone 的 point。"""
    rows, warnings, errors = [], [], []
    edges: dict[int, list[int]] = {}
    open_nodes = set()
    for item in checklist(milestone_body):
        n = item["number"]
        if n is None:
            status = "cut" if item["struck"] else "no-issue"
            rows.append({"number": None, "status": status, "title": item["text"], "blocked_by": []})
            continue
        iss = issues.get(n)
        if iss is None:
            errors.append(f"清单里的 #{n} 不存在")
            continue
        closed = is_closed(iss)
        cut = item["struck"] or is_cut(iss)
        if item["struck"] and not closed and not moved_away(iss, milestone):
            warnings.append(f"#{n} 清单里划掉了，issue 却还开着：砍掉就 gh issue close {n} --reason \"not planned\"；"
                            f"挪到别的 milestone 就把它的 Milestone: 行改成新的")
        if is_cut(iss) and not item["struck"]:
            warnings.append(f"#{n} 以 {iss['stateReason'].lower()} 关掉了，清单里却没划掉（划掉并写原因）")
        if (closed or cut) != item["checked"]:
            warnings.append(f"#{n} {'已结' if closed or cut else '开着'}，清单里却{'没勾' if closed or cut else '勾了'}（--sync 改）")
        labels = {lb["name"] for lb in iss.get("labels", [])}
        bl = blockers(iss.get("body", ""))
        if bl is None:
            bl = []
            if not closed and not cut:
                warnings.append(f"#{n} 没写 Blocked-by 行（当作没有依赖；没有就写 Blocked-by: none）")
        edges[n] = bl
        pending = []
        for b in bl:
            dep = issues.get(b)
            if dep is None:
                errors.append(f"#{n} 的 Blocked-by #{b} 不存在")
                pending.append(b)
            elif is_cut(dep):
                if not closed and not cut:
                    errors.append(f"#{n} 的 Blocked-by #{b} 被砍掉了（{dep['stateReason'].lower()}）：改 #{n} 的依赖，或把 #{n} 也砍掉")
                pending.append(b)
            elif not is_closed(dep):
                if not closed and not cut and moved_away(dep, milestone):
                    warnings.append(f"#{n} 等的 #{b} 挪到别的 milestone 了：#{n} 也挪走，或者把 #{b} 挪回来")
                pending.append(b)
        if cut:
            status = "cut"
        elif closed:
            status = "closed"
        else:
            open_nodes.add(n)
            status = "in-progress" if IN_PROGRESS in labels else ("blocked" if pending else "ready")
        rows.append({"number": n, "status": status, "title": iss.get("title", ""), "blocked_by": pending})
    # 清单外、被 Blocked-by 指到的开着的 issue 也参与找环
    for n, iss in issues.items():
        if iss is not None and n not in edges and not is_closed(iss):
            open_nodes.add(n)
            edges[n] = blockers(iss.get("body", "")) or []
    for cyc in find_cycles(edges, open_nodes):
        errors.append("依赖成环：" + " → ".join(f"#{c}" for c in cyc))
    return {"rows": rows, "warnings": warnings, "errors": errors}


def synced_body(milestone_body: str, issues: dict[int, dict | None]) -> str:
    lines = milestone_body.splitlines(keepends=True)
    for item in checklist(milestone_body):
        iss = issues.get(item["number"]) if item["number"] is not None else None
        if item["struck"]:
            mark = "x"
        elif iss is None:
            continue
        else:
            mark = "x" if is_closed(iss) else " "
        raw = lines[item["line"]]
        end = raw[len(raw.rstrip("\r\n")):]
        m = CHECK_RE.match(raw.rstrip("\r\n"))
        lines[item["line"]] = m.group(1) + mark + m.group(3) + m.group(4) + end
    return "".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-R", "--repo", required=True, help="owner/repo（origin 的，不让 gh 自己挑）")
    ap.add_argument("milestone", type=int)
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cache: dict[int, dict | None] = {}

    def fetch(n: int) -> dict | None:
        if n not in cache:
            try:
                cache[n] = json.loads(gh("issue", "view", str(n), "-R", a.repo,
                                         "--json", "number,title,state,stateReason,labels,body"))
            except GhError as e:
                if "Could not resolve" not in str(e):
                    raise
                cache[n] = None
        return cache[n]

    try:
        ms = fetch(a.milestone)
        if ms is None:
            print(f"milestone issue #{a.milestone} 不存在", file=sys.stderr)
            return 2
        body = ms.get("body", "")
        for item in checklist(body):
            if item["number"] is not None and fetch(item["number"]) is not None:
                for b in blockers(cache[item["number"]].get("body", "")) or []:
                    fetch(b)
        result = classify(body, cache, a.milestone)
        if a.sync:
            new = synced_body(body, cache)
            result["synced"] = new != body
            if new != body:
                gh("issue", "edit", str(a.milestone), "-R", a.repo, "--body-file", "-", stdin=new)
    except GhError as e:
        print(f"gh 出错：{e}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        for r in result["rows"]:
            num = f"#{r['number']}" if r["number"] is not None else "--"
            tail = "  ← " + " ".join(f"#{b}" for b in r["blocked_by"]) if r["blocked_by"] else ""
            print(f"{num:<6} {r['status']:<12} {r['title']}{tail}")
        for w in result["warnings"]:
            print(f"警告：{w}")
        for e in result["errors"]:
            print(f"错误：{e}")
        if result.get("synced"):
            print(f"已把 #{a.milestone} 的清单勾选改成和 issue 状态一致")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
