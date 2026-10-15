---
name: todo-dispatch
description: One tick of the todo dispatcher loop, run as `/loop 30m /todo-dispatch` — read the repo's todo ledger, open a new claude-board card (a fresh Claude Code session) for every open item nobody has claimed, let each session claim, do and tick its own item; meanwhile watch the collaboration friction between the parallel sessions and fix it in the process (merge queue, SemVer, CI/CD), and push the shared default branch through the gate. Triggers: 看 todo 派活、没人做的开 session 去做、todo 派单循环、观察协作摩擦、dispatcher loop.
argument-hint: "[可选：只看 todo 的哪一节]"
---

# todo 派单循环（一轮）

user 的原话（2026-10-14）：「你的任务就是看 todo，然后把没人做的，你新建 claude board card claude session 去做，没别的了，
他们做完应该自己标注 todo done。你要研究的是观察协作摩擦、在流程上解决，考虑 merge queue、SemVer、CI/CD。」

定时跑：`/loop 30m /todo-dispatch`（插件装法下叫 `/humanize:todo-dispatch`）。每轮按下面六步走完再停，不在两轮之间常驻。

## 角色

你是派单员和流程维护者，**不做条目本身的活**。四件事：派活、看摩擦、修流程、推共享默认分支。
条目的认领、进度、做完都由派出去的会话自己写；你只读台账、不替它们改条目。

## 先读的（每轮重读，不凭上一轮的记忆）

- 目标仓库根目录的 `CLAUDE.md`、`CONTRIBUTING.md`：待办台账在哪、认领规则、认领工具、门禁命令、提交与推送规则。
- 摩擦台账（目标仓库的 `docs/collab-friction.md`，没有就建）：上一轮记到哪、哪些修补在「待核」。
- `git log` 自上一轮以来的提交（新会话没有「上一轮」：从摩擦台账最后一行的时间算起）、`gh run list -R <origin>` 最近的 CI、`ListAgents` 和 board 上的卡片。

## 一轮六步

### 1. 分类台账里的 open 条目

读**工作树**里的台账（会话的进度常常还没提交）；某条在工作树和 HEAD 里不一样，说明有人正在改它，这一轮跳过。
逐条读全文（含子条目），还要读别的条目里提到它的话（交接、先后常写在另一条里）。只认这六类：

| 类 | 判据 | 动作 |
|---|---|---|
| 已认领 | 开始标签上有认领标记 | 不碰。认领的会话像是死了（卡片不在、transcript 很久不动）也不接手，记一条摩擦并告诉 user |
| 等 user | 写着「做不做 user 定」「由 user 拍板」，或 agent 按停止判据（CLAUDE.md 第 4 条）判过、等 user 定——且后面没有记下 user 的裁定 | 不派，汇报里列出来；重捡要 user 重新拍板 |
| 已交接 | 台账里任何一处（含别的条目）写着它交给某个 issue / 某个会话 | 不派 |
| 排队 | 写着排在别的条目之后、等某个在跑的作业或某个前置 | 不派 |
| 总条目 | 下面有子条目（open 的也算） | 自己永远不派，只看它的叶子条目 |
| 空闲 | 以上都不是 | 派 |

拿不准归哪类，按「等 user」处理并在汇报里问。

### 2. 派活：每个空闲条目开一张新卡

- **选机器**：要 Isaac / L40S 的评测、要某台机器上的服务栈的，开在那台；零 GPU 的读数、判断、写文档开在本机。
- **开卡**：把派单写进 scratchpad 的一个文件，跑 `python3 <本 skill>/scripts/dispatch.py <host> <仓库路径> <派单文件>`，
  它在 board 上开卡、等卡出现、把派单打进输入框，打印卡的 key 和会话名。一条一卡，不把两条塞给一个会话。
- **派单**（自包含、单段，不带换行；措辞照下面的骨架填）：
  「我是 <你的会话名>（<主机>），按 user 的 /loop 把 todo 上没人认领的条目派给新会话。派给你的是「<条目标题>」那一条（<在哪一节>）。
  ① 先按根目录 CLAUDE.md 的认领规则用 <认领工具> 认领；那条已经有认领就别做，直接停。
  ② 照条目写的做，按仓库 CLAUDE.md、CONTRIBUTING.md 和 memory 的规则跑。<条目特有的边界：不碰哪些在跑的作业、预算闸、和哪一条不叠加>
  ③ 做完用 <认领工具> 把它标成做完；判成不做或撒手，把判断写进条目、放掉认领。要 user 拍板的直接问 user。」
- **验收**（下一轮第 1 步，不在本轮里等）：那条有没有认领标记、`git log` 有没有认领提交。没认上的卡记一条摩擦，再派一次。

### 3. 看摩擦

两路来源，缺一不可：会话发回来的消息和报告里的「摩擦」节；你从事件里量的。每轮至少查这些：

- 清理类提交：`git log` 里的「退回」「误带」「revert」「冲突取」，以及**别人**做的「并入 origin」（第 5 步脚本自己的合并提交消息正好是 `并入 origin/<分支>`，不算）；
- 默认分支上的 CI 红：哪次红、红了多久、红着时有几次推；
- 本地默认分支和 origin 的差距：`git rev-list --left-right --count <分支>...origin/<分支>`；
- 认领碰撞：同一条被两个会话认领、认领后卡片没了、认领提交里带了别人的改动（`git show --stat` 看认领提交是不是只改一行）；
- 热点文件：`git log --since=<上一轮> --name-only` 计数，最高的几个；
- 修补后的复发：台账里每条「待核」的修补，这一轮同类现象出现没有。

每条写进摩擦台账一行：时间 / 涉及谁 / 现象（可观察的）/ 代价 / 根因 / 流程修补（落在哪个文件、哪个 PR）/ 复发没有。

### 4. 修流程

照 orchestrate 的 `collab-pr.md`「摩擦：怎么看、怎么记、怎么修」：

- **修流程不修个案**；同类第二次出现、或一次就代价大，才修。先删后加。
- 修补落在流程的唯一正本（目标仓库的 CLAUDE.md / CONTRIBUTING / CI / 门禁脚本 / 工具脚本，或本 skill、orchestrate 的派单模板），不在派单里口头约定。
- 修补走 PR：作者 agent 在自己的 worktree 里写（`isolation: "worktree"`），零上下文审阅（轮数上限照目标仓库 CONTRIBUTING 和 collab-pr.md），
  `orchestrate/scripts/merge-queue.sh --gate ci <PR>` 合进 origin；不手工并回共享工作树，第 5 步的脚本会并；合进去后广播。
- **转派审阅意见只转审阅原文和它验证过的改法**，不自己补措辞：指挥者顺手加的一句没经审，最容易出事。
- 派单里写清**对手操作**：共享文件的每一条写入路径（会话的 Edit/Write 不拿 git 的锁、CI 机器人直推、别的脚本），作者才知道要防谁。
- 审阅附的原型代码必须和审阅正文一致，或只给正文。
- **SemVer** 只在目标仓库有被消费的版本号时才做（包 / 插件 manifest、`v*` tag），由合并队列按 PR 标题算；没有就不新造版本号文件。
- 改别的会话也在用的规则（分支保护、推送权、认领规则），先问 user；user 定了就照做，并把裁定写进流程正本。

### 5. 推共享默认分支

user 2026-10-14：共享工作树的本地默认分支由派单员推。每轮跑一次：

```bash
bash <本 skill>/scripts/push-shared-main.sh            # 在目标仓库里跑；门禁默认 bash scripts/ci_check.sh，别的用 --gate '<命令>'
```

它并上 origin、在 HEAD 的临时 detached worktree 里过门禁（只查已提交的内容）、非强制地推。退出码和动作：

| 退出 | 意思 | 动作 |
|---|---|---|
| 0 | 推上去了，或没有要推的 | — |
| 3 | 别的会话没提交的改动挡着并 origin（暂存的、没做完的合并、或 origin 要动的文件上有改动） | 下一轮重跑 |
| 4 | 门禁红 | 不推；从打印的待推区间按提交消息认出是哪个会话的活（作者全是同一个账号，看不出），本机的 SendMessage 给它，跨机的记摩擦、告诉 user |
| 5 | 并 origin 真冲突（已 abort） | 不会自己消失：告诉 user，别每轮重试 |
| 6 | 推被拒，origin 刚前进 | 下一轮重跑；永远不 `--force` |
| 2 | 推失败但不是因为 origin 前进（例如分支保护） | 看报错，告诉 user |

### 6. 汇报

一段短的：这一轮派了谁（卡、会话名、条目）、哪些条目在等 user（每条一句为什么）、新记的摩擦和修补进度、推送结果。
**派出去的会话问 user 的话只落在它自己的 pane 里**，user 多半看不到：每轮读每张派出去的卡最近一条输出
（board 的 timeline，或 `~/.claude/sessions/<pid>.json` 取 sessionId 再读 transcript 末尾），在等 user 定的问题连同它给的选项和建议原样转给 user，
并说明它停着占了什么（卡、服务）。
user 已经裁定过的事不再问。不用 AskUserQuestion。

## 不做

- 不做条目本身的活，不替会话改条目、不替它标做完。
- 不接手已认领的条目，不重派判过「不做」或等 user 的条目。
- 不强推、不改写共享分支的历史、不在共享工作树里切分支。

## agent-harness 的接线

台账、认领工具在根目录 CLAUDE.md 第 9 节，门禁、推送、分支保护在 CONTRIBUTING §2，体检结果在 `docs/collab-friction.md` 开头；这里只记它们没写的：

- board 跑在 nnmc71，派单员也在 nnmc71 上跑（别处跑要设 `CLAUDE_BOARD_URL`、`CLAUDE_BOARD_TOKEN`）；它同时汇总 `61`、`63` 的卡。
- RoboLab / Isaac 的活开在 `63`，RoboCasa（MuJoCo，H100）和零 GPU 的开在 `71`。
