"""Plan the Artifact publishes of a repo's HTML pages: which pages changed since this checkout last published them,
which of their files to upload or remove, and which page rules a page breaks.

The Artifact tool exists only inside a Claude session, so this script does not publish: the publish-html skill runs it,
publishes each item, then records it with --mark. The pages and their URLs live in the repo's docs/html/publish.json;
what was published is a local cache next to it (.published.json, untracked), and losing it only makes the next run
publish unchanged pages again. Run from the repo root.

    python3 publish.py              # JSON list, one item per page to publish
    python3 publish.py --mark PAGE  # after publishing PAGE: record what is now live

publish.json:
    {"title_prefix": "Yaofen_",                # every page's <title> starts with it
     "leak": "nnmc\\d+|/shared/|outputs/",      # regex a page read outside the lab must not match
     "skip_hosts": ["hostA"],                   # hosts whose claude.ai account cannot reach these URLs
     "accounts": {"hostB": {"docs/html/idea.html": "https://claude.ai/artifact/…"}},
                                                # a host whose account keeps its own copies: only these pages, to these URLs
     "pages": {"docs/html/idea.html": {"url": "https://claude.ai/artifact/…"},
               "code/vis/index.html": {"url": "…", "files": ["data.js", "media/*.mp4"]},
               "docs/html/todo.html": {"url": "…", "public": false},
               "docs/html/new.html": {"url": null}}}
    files are globs relative to the page's directory; url null publishes a new artifact. An accounts copy takes its
    files from the page's entry in pages and is cached apart (page@host), so the two accounts never mark each other done.
"""
import glob
import hashlib
import json
import os
import re
import socket
import sys

MANIFEST = os.path.join("docs", "html", "publish.json")
STATE = os.path.join("docs", "html", ".published.json")
TEXT = (".html", ".js", ".css", ".json", ".svg")
BATCH_BYTES = 60e6  # one publish carries at most 64 MB and 255 files


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def files_of(page, globs):
    """published path -> source path; the page itself under its own name"""
    d = os.path.dirname(page)
    out = {os.path.basename(page): page}
    for g in globs:
        for p in sorted(glob.glob(os.path.join(d, g))):
            out[os.path.relpath(p, d)] = p
    return out


def problems(cfg, page, files, public):
    src = read(page)
    out = []
    if not src.startswith('<meta charset="utf-8">'):
        out.append('第一行不是 <meta charset="utf-8">')
    if f"<title>{cfg['title_prefix']}" not in src:
        out.append(f"<title> 不以 {cfg['title_prefix']} 开头")
    # an update keeps the artifact's other files, so a reference missing here is fine if the live page already has it
    out += [f"引用的 {ref} 本地没有：远端已有就照发，没有就别发" for ref in re.findall(r'\bsrc="([^":?#$]+)"', src)
            if ref not in files]
    if public:
        leak = re.compile(cfg["leak"])
        for pub, path in files.items():
            hits = sorted(set(leak.findall(read(path)))) if path.endswith(TEXT) else []
            if hits:
                out.append(f"{pub} 带机器名、本机路径或产物名：{', '.join(hits)}")
    return out


def batches(files):
    """split {published path: source or None} into publishes under the size and count limits"""
    out, cur, size = [], {}, 0
    for pub, path in files.items():
        n = os.path.getsize(path) if path else 0
        if cur and (size + n > BATCH_BYTES or len(cur) >= 250):
            out.append(cur)
            cur, size = {}, 0
        cur[pub] = path
        size += n
    return out + [cur]


def load():
    return json.loads(read(STATE)) if os.path.exists(STATE) else {}


def target(cfg):
    """the pages this host publishes, with its account's URLs, and the suffix its cache entries carry"""
    host = socket.gethostname()
    own = cfg.get("accounts", {}).get(host)
    if own is not None:
        return {page: {**cfg["pages"][page], "url": url} for page, url in own.items()}, "@" + host
    if host in cfg.get("skip_hosts", []):
        sys.exit(f"{host} 登录的 claude.ai 账号看不到这些 URL，换台机器发")
    return cfg["pages"], ""


def plan(cfg, pages, key):
    state, items = load(), []
    for page, p in pages.items():
        files = files_of(page, p.get("files", []))
        was = state.get(page + key, {})
        now = {pub: sha(path) for pub, path in files.items()}
        name = os.path.basename(page)
        upload = {pub: os.path.abspath(files[pub]) for pub in now if pub != name and was.get(pub) != now[pub]}
        removed = {pub: None for pub in was if pub not in now}
        if was.get(name) == now[name] and not upload and not removed:
            continue
        items.append({"page": page, "url": p["url"], "file_path": os.path.abspath(page),
                      "files": batches({**upload, **removed}),
                      "problems": problems(cfg, page, files, p.get("public", True))})
    return items


def mark(pages, key, page):
    state = load()
    state[page + key] = {pub: sha(path) for pub, path in files_of(page, pages[page].get("files", [])).items()}
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    cfg = json.loads(read(MANIFEST))
    pages, key = target(cfg)
    if sys.argv[1:2] == ["--mark"]:
        for page in sys.argv[2:]:
            mark(pages, key, page)
    else:
        print(json.dumps(plan(cfg, pages, key), ensure_ascii=False, indent=1))
