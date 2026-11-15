---
name: cross-repo-sync
description: 跨仓库 memory 同步，按 repo 关系图做的跨仓库操作，约 14 天一轮（SessionStart 钩子到期提醒）——按关系图让每个仓库只有一个 memory store、各机各 cwd 的 project 键都链到它；几个仓库共有的 memory 只在一个 topic store 里有正本，订阅它的仓库放软链、索引里有它一节；碎片和分叉的副本合并回正本；顺着图的 uses 边把上游的接口变化带进下游的 memory。Triggers: 跨仓库同步 memory、mem sync、memory 分叉、各机 memory 不一样、通用 memory 合并、repo 关系图、cross-repo watcher.
argument-hint: "[可选：只看哪个仓库或 topic]"
---

# 跨仓库 memory 同步（一轮）

**约 14 天一轮，不常驻**（user 2026-11-14：「不用启动监视器。就和 memory 一样定期提醒开一次就行，比如 14 天」）。到期提醒来自 `memsync.py due`：把它挂成某个常用仓库的 SessionStart 钩子，上一轮（hub `log.md` 最后一行的日期）满 14 天就往会话里塞一句提醒，全机队一天最多一次。收到提醒或 user 叫跑时，在 hub（关系图所在的 git 仓库）里开一个会话跑 `/cross-repo-sync`（插件装法下 `/humanize:cross-repo-sync`），按下面六步走完就关。

## 模型

- **关系图** `graph.yaml`（`$HUMANIZE_REPO_GRAPH`，没设就是当前 checkout 根下的 `graph.yaml`）：仓库（代码路径、memory store、订阅哪些 topic、`uses` 哪些仓库）和 topic（hub 里的 store、在订阅者索引里的节标题）。仓库要加进来、换 store、改订阅，都只改这个文件。
- **两条不变式**和它们怎么查、怎么修：`scripts/memsync.py` 开头。一句话：每个仓库一个 store，每个 memory 只有一份实体文件；topic 的文件在订阅者那里是软链。
- **会话怎么写**：订阅者索引里每个 topic 节的第一行注释写着正本在哪。会话改 topic 里的 memory 时 Claude Code 拒绝写软链、报出目标路径，改动自然落进正本；新写的 memory 落在会话自己仓库的 store，是不是通用由 watcher 判。
- 公开仓库（包括本仓库）的 memory 不进它自己的仓库，store 在 hub 里。

## 一轮六步

### 1. 看现状

```bash
python3 <本 skill>/scripts/memsync.py status     # 在 hub 里跑；! 要判断，+ apply 会修，~ 建议
```

### 2. 处理 `!`（要判断的）

| 现象 | 做法 |
|---|---|
| 某机某键是实体目录，里面有 store 没有的 memory（碎片） | 逐条并进这个仓库的 store（下面「合并规则」）；本来就是通用的，并进对应 topic 的正本。并完删掉碎片里那一条；碎片里只剩索引时 apply 把整个目录挪成 `memory.pre-graph-<日期>` 并接上链。两个目录的碎片讲的是同一个项目（上层目录和里面的代码仓库），在图里并成一个仓库、另一个路径写进 `keys`，apply 自己改链 |
| 订阅者自己的副本和 topic 的正本不一样 | 把副本多出来的事实并进正本，只对这个仓库成立的拆进仓库自己的 memory；删副本，apply 换成软链 |
| 仓库自己的文件挂在 topic 节下 / 仓库新写的 memory 是通用的 | 通用的 `memsync.py promote <仓库> <文件> <topic>`（文件和索引行一起搬，再 apply）；只对本仓库成立的把索引行挪到仓库自己的节 |
| 没有索引行 | 按文件 frontmatter 补一行：`- [标题](文件.md) — 钩子` |

### 3. 落盘

```bash
python3 <本 skill>/scripts/memsync.py apply
```

按它最后打印的每个 checkout 一条 pathspec 提交，再加上第 2 步你自己改的文件；只提交自己动过的路径，按各仓库自己的提交规则（推送等 user 要求）。hub 每轮提交一次。再跑一遍 status，`!` 和 `+` 都清了这一步才算完。

### 4. 新 memory 归类

自上一轮（hub `log.md` 最后一行的时间）以来，各仓库 store 里新出现或改过的实体文件（`git log --since` 加工作树里没提交的；hub 里的 store 看 mtime）逐条读：对订阅同一 topic 的其他仓库也成立的，promote；只对本仓库成立的不动。拿不准的不动，汇报里列出来。

同一条规矩常以不同的文件名散在几个仓库里（「术语盲读只在 CI 跑」在三个仓库各叫一个名字），所以判之前按内容的关键词 grep 所有 store 和 topic，不只比文件名；找到同义的就并成一条。

### 5. 顺着 `uses` 边

对每条 `A uses B`：

- **上游改了接口**：B 自上一轮以来的提交里改了 A 会用到的东西（脚本名和参数、API 路径、配置键、文件位置），grep A 的 store 和 A 订阅的 topic 里提到它的 memory，过时的改正本（topic 的改 hub 里那份）。
- **下游记着上游的坑**：A 的 memory 里有「B 的某处要绕着走」这类条目，在 B 里修掉比每个下游各记一份好。不替 B 改代码；在汇报里列出来（哪条 memory、建议在 B 里怎么修），由 user 派。

### 6. 记账和汇报

hub 的 `log.md` 追加一行，以 `- YYYY-MM-DD` 开头（`due` 按它算下一次到期）：时间、合并了哪些、promote 了哪些、uses 边上改了哪些、留给 user 的。汇报就是这一行加上要 user 定的事，不用 AskUserQuestion。

## 合并规则

合并是这个 skill 唯一需要判断的活，也最容易丢东西：

- **不丢事实**。每份副本里的每个事实，最后要么在正本里，要么在某个仓库自己的 memory 里，要么被另一份里更新、带日期的事实取代（取代的要能说出是哪一句）。
- **user 的裁定原话和日期照搬**，不改写、不合并成概括。只有引了 user 原话或写明「user 裁定」的才算裁定；会话从一次事故里自己总结的教训不算，它和 user 的裁定冲突时不按日期取舍，两句并列交 user。
- **两个仓库对同一件事的裁定不同**：日期新的那条可能只是对它自己的仓库说的。问 user 是不是每个仓库都这样，定了写成 topic 里的一条、带原话和日期；没问清之前两边各留各的。
- **复述根 `CLAUDE.md`、代码或 README 的 memory 删掉**，链接改指那一处，写明是哪个仓库的哪个文件，让没有那一处的订阅仓库也找得到；这条 memory 里有那一处没写的事实，先把事实并到别的 memory 再删。
- **topic 正本里不写「本项目」「本仓库」**，也不写只对某个仓库成立的句子；那些拆进那个仓库自己的 memory（并进一条合适的，或新开一条并补索引行）。
- **`[[name]]` 只指向同一 topic 或订阅者都有的 topic 里的 memory**；指向某仓库自己的 memory 的，改成「见 <仓库> 的 `<文件>`」。status 的 `~` 会列出 topic 里指出去的链。
- **会话在并发地写**。合并前记下每份副本的 sha256，删副本前再比一次；变了就把新改动也并进来再删。
- 合并完的正本照所在 store 的 `CLAUDE.md`（没有就照写入方仓库的）写 memory 的规矩：只记下次还成立的、不写写死的现状和相对时间、一事一处。

## 新仓库进图（第一次同步）

一个仓库刚加进图，或第一次跑这个 skill，碎片和分叉的副本可能上百条，一轮做不完就分批：

- 动手前把各机碎片目录打包进 hub 的 `.backup/`（gitignored）。
- 先合并分叉的副本（定 topic 正本），再并碎片：碎片里讲通用事实的，要拿去和已经定下的正本比。
- 并行派 agent 时按 topic 分批，每批只写正本、拆残余、删副本，**不碰任何 `MEMORY.md`**（会话和别的 agent 同时在写）；索引行写进报告，由你汇总写进 topic 的 `MEMORY.md` 和仓库的索引。
- 副本删了、软链还没建的这段时间，会话读不到这些 memory：合并收齐就立刻 apply。
- 碰到图之外的 store（老 clone 里的 `docs/memory` 之类）不动，汇报里列出来。

## 不做

- 不改仓库根的 `CLAUDE.md`、代码、结果页——只动 memory store（含 store 里的 `CLAUDE.md`）和 `graph.yaml`。
- 不删 topic 里的 memory，除非它整条不再成立（和 memory 体检的判据一样）。
- 不推送，不改写历史，不提交别人的改动。
- 图上没有的仓库不碰；要加进图的，汇报里提出来由 user 定。
