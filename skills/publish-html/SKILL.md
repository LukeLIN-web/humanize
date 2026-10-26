---
name: publish-html
description: 把项目里改过的 HTML 页发回各自的 Artifact URL——发一下 / 发布 / publish / 推到 artifact。项目的 docs/html/publish.json 登记过的页（或生成它们的脚本）改完以后，不等 user 说就跑一遍（user 2026-10-26「把发一下弄成一个脚本, 这样以后 run一下就行了. 以后每次改完 run一下.」）。
---

# 发一下

页面 → Artifact URL、每页一起发的文件、标题前缀、扫描用的正则、发不了的机器，都写在项目的 `docs/html/publish.json`，格式见 `scripts/publish.py` 开头。页面写法（不写机器名、产物名等）在项目的 `docs/html/CLAUDE.md`。

1. 在仓库根跑 `python3 <本 skill>/scripts/publish.py`：列出要发的页（JSON）；没改过的页不在里面，空列表就是都发过了。在 `skip_hosts` 里的机器上它直接退出，换台机器发。
2. 每一项：
   - `problems` 不空：这页不发，报给 user，照 `docs/html/CLAUDE.md` 改页面或改生成它的脚本再重跑。只有「引用的 X 本地没有」一条时，先 `Artifact list`（`scope: "files"`）看远端：远端有 X 就照发（更新不删远端没提到的文件）。
   - 读：页面本身整份 Read（别人写的文件，发布前要读完）；一起发的文件靠脚本的扫描，不整份读。`url` 本会话还没 read 过，先 `Artifact read` 它（不带 `path`，带 `path` 不算）。
   - 发：`files` 里每一批发一次，同一个 `url` 和 `file_path`；批不空就作 `files` 传（值为 null 是删远端那个文件）。`url` 为 null 是新页：第一批不带 `url`、带 `icon` 发，后面的批带拿到的 URL，再把 URL 写进 `publish.json`。
   - 发完：`python3 <本 skill>/scripts/publish.py --mark <page>`。
3. 改了 `publish.json` 就提交它（commit 带 pathspec）。
4. 回复：每页一行，链接和版本号；没发的页写原因。

`docs/html/.published.json` 是本机缓存，项目要把它写进 `.gitignore`。缓存丢了或换了机器，下次会把没改的页再发一遍，多文件页会整份重传：先 `Artifact list`（`scope: "files"`）对一遍远端，远端有、本地没有的路径在第一批里设 null。

新项目接入：建 `docs/html/publish.json`，`.gitignore` 加 `docs/html/.published.json`，项目的 `.claude/skills/` 里软链到本 skill 目录。
