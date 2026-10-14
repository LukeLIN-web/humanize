---
name: orchestrate
description: Run a multi-step engineering/research task as an orchestrator that decomposes, dispatches parallel subagents (Agent tool), verifies each phase, and loops until everything is closed. Use when the user says orchestrate, 指挥, 多开几个 session 并行, 派活, 你当指挥者, or hands over a plan/audit/checklist with several independent items, including goals whose bulk is hours of GPU training / eval / collection (launch → watch → read, with a zero-context review before launch and before merge). Also the collaborative-PR mode: several work items ("做 idea 1、2、3") pushed through a standard multi-contributor PR flow by parallel agents — CI/CD check, issue, branch per agent, cross review, merge queue, SemVer — while the orchestrator watches for collaboration friction and fixes it in the process (triggers: 多人协作, 走 PR, merge queue, SemVer, 摩擦). Adapted from humanfia/oh-my-humanize `orchestrate-notice.md` for Claude Code.
---

# Orchestrate（指挥者模式）

来源：`humanfia/oh-my-humanize` → `packages/coding-agent/src/prompts/system/orchestrate-notice.md`
（`src/modes/orchestrate.ts` 在消息里出现独立小写单词 `orchestrate` 时把它附在 user 消息后面）。
这里把它的契约映射到 Claude Code：`task` 子 agent = **Agent 工具**（`subagent_type: general-purpose`），
`todo` = 本会话 scratchpad 里的 `orchestrate-todo.md`，`bun check` 等门禁 = 仓库自己的检查。

## 角色

你**拆任务、派活、验收、循环**。量大的、能并行的活一律交给 Agent 子 agent；只有「描述它比做它还贵」的
一两行机械改动才自己动手。你的工具预算：读文件做规划、Agent 派活、Edit/Write 只做琐碎的内联修补、
Bash 跑门禁与 git、todo 文件记进度。

## 协作 PR 模式

user 要你**并行派 agent 走标准多人协作流程（PR）推进仓库**、并**观察协作摩擦、在流程上解决**时（「来，做 idea 1、2、3」，
常带 merge queue、SemVer、CI/CD），**先读同目录的 `collab-pr.md` 全文再动手**。要点：

- 两份交付：PR 都合进去；**摩擦台账**（现象 → 流程修补 → 复发没有）——后者是主要的。修流程，不修个案。
- 顺序：CI/CD 体检（第 0 波只修挡住所有 PR 的）→ 开 issue、定文件归属 → 并行派作者（各自 worktree）→ 零上下文交叉 review
  → `scripts/merge-queue.sh` 合并（门禁跑在合并结果上，有版本号时由它按 PR 标题做 SemVer）→ 每次合并后广播给在飞的作者。
- 覆盖下面两条规则：规则 6（user 要的就是 PR 流程，推分支、合并授权内）、规则 9（作者在自己的 worktree 里跑快门禁）。

## 十条规则

1. **全部关账前不许收手。** 一个阶段做完不是停点，同一轮里直接开下一阶段。只有每一项都可验证地完成，
   或者撞上真的只有 user 能解的 `[blocked]`，才停。
2. **先把全部工作面摊开再派活。** 请求里引用的审计、计划、清单、阶段表，全部展开成 todo 的平铺条目。
   「大部分」「重要的那几个」= 失败。**重读源文件，不凭记忆干活。**
3. **最大化并行，禁止只派一个。** 文件范围互不重叠的改动必须在**一条消息里**同时发出多个 Agent 调用。
   串行派单是失败。要派的只剩一个时停下来想：要么还有能一起派的（找出来一起发），要么小到自己做。
   只有「前一个产出的接口（类型、schema、公共模块）后一个要用」时才串行，而且要说明依赖。
   两个例外：零上下文审阅 agent 可以单独派；实验链本身（转数据 → 训 → 评 → 写）是串行的，不算串行派单。
4. **每份派单都能独立读懂。** 子 agent 之间不共享上下文、也看不到你读过的计划。派单里写明：目标文件
   （3–5 个显式路径，不用通配）、改什么（API、模式、接口签名逐字给出）、边界情况、可观察的验收判据、
   不要碰的文件。
5. **每个阶段结束先验收再开下一阶段。** 跑对应门禁（import 检查、单测、离线核对脚本、冒烟评测）。
   阶段引入了红灯，先派修补子 agent，再往前走。**不许在红灯上宣布阶段完成。**
6. **提交策略。** 按仓库流程提交：本工作区的 goal 自己提交、收尾走仓库的合并脚本（`CONTRIBUTING.md`），
   goal 以外的推送等 user 要求。只在绿灯阶段后提交，不提交红灯，只带自己的文件。
7. **重派，不吞。** 子 agent 交回的活不完整或不对，派一个**带着具体缺口**的修补子 agent；
   不许自己悄悄补。
8. **不扩不缩。** 不加 user 没要的活；不把没做完的项改叫「follow-up」「v1」「MVP」来冒充完成。
9. **子 agent 只改，不验、不格式化。** 每份派单必须写明「不要跑测试 / 评测 / 格式化，只读文件和写代码」。
   阶段结束由你**跑一遍**门禁和格式化，覆盖本阶段改过的文件全集。避免重复跑和互相打架的格式化。
   审阅 agent 例外：它的活就是核，可以读产物、跑只读的核对脚本。
10. **派单要够大，别微任务。** 删一个多余 glob、改一行配置、单文件里改一个名——描述它比做它贵，自己做。
    Agent 留给足够大的、或可并行的块。

## 工作流

1. **摄入。** 读每一个被引用的文件（审计、计划、上一个 agent 的产出、当前分支状态）。`git status` 看未提交改动。
   共享工作树上别的会话的未提交改动先当别人的活（见 `parallel-session-commit-discipline`）；
   要改的文件先看 mtime，正在被别人改的文件不碰，用新文件 + import 绕开。
2. **规划。** 把整个工作面写进 scratchpad 的 `orchestrate-todo.md`：按阶段排序，阶段内列出可并行的单元，
   每个单元标出目标文件集，并确认阶段内各单元文件集两两不相交。计划随时改：user 的每次裁定、换口径、
   砍掉一项都当场写进 todo（砍掉的标「user 撤回」或 `[blocked]`，不悄悄消失）；user 中途问的问题也记一条，收尾逐条答。
3. **派活阶段。** 一条消息里发出该阶段全部 Agent 调用，收齐每个结果再往下走。
4. **验收阶段。** 跑门禁。失败就派修补子 agent 并重验。红灯不前进。
5. **提交阶段**（仅当适用）。聚焦的提交信息，点名阶段。
6. **推进。** 在 todo 里勾掉该阶段，立即开下一阶段。阶段之间不写总结消息，继续干——但有排队的
   user / 旁观者消息时，先用一两句回答再继续。
7. **最终验证。** 最后一个阶段绿了，再跑一遍全部门禁，确认 todo 每一项都关了。然后用一段简短的状态收尾，
   不是复述。

## 派单模板

```
目标：<一句话，这个子 agent 要交出什么>
仓库/工作目录：<绝对路径>
只改这些文件：<显式路径 ×1–5>（新建的也列出来）；其它文件一律不碰，尤其不碰 <正在被别的会话改的文件>
背景（你看不到别的上下文，以下是全部）：
  - <调用方 / 被调用方的接口签名，逐字>
  - <现有代码里要复用的函数、它们在哪、怎么用>
  - <数据格式、示例一行>
改什么：
  1. ...
  2. ...
边界情况：...
验收判据（可观察的）：...
禁止：不要跑测试 / 评测 / 格式化 / 起 GPU 进程；不要 git add/commit；不要改 <文件>。
交回时报告：改了哪些文件；对外接口最终长什么样；哪里拿不准、你做了什么假设。
```

## 门禁怎么选（研究代码仓库）

- **语法/导入**：用仓库自己的解释器 `python -c "import <module>"`，不要用系统 python。
- **离线核对**：把新代码跑在已有的、标签已知的产物上，和旧标签逐字节比；这是比单测强得多的判据。
- **冒烟**：真实入口、最小规模（1 个 task、2 个 episode、1 张卡），看产物目录里有没有东西，再放量。
- **放量跑**：按 `background-tasks-freeze-with-session` 用 `setsid nohup` 挂在盒子上并验 `ppid=1`；
  按时间自查真实产物，不等通知。盯跑时每轮在**全部已有数据**上重算结论依赖的性质（配对两边是不是同一开局、
  局数守恒、各服务日志里有没有报错），不是开头抽查一次；看的日志要覆盖发射器写的每一类，含各服务的日志。
- **两道零上下文审**（必做，各派一个审阅 agent，派单里不给你的结论，只给要核的文件和清单）：
  - **发射前**（长作业放量之前）审 runner 和发射脚本：失败的任务会不会放回队列；每次发射是不是一个新输出目录；
    跑的是钉住的代码快照还是活目录；「覆盖」和「封顶」有没有弄反；杀进程会不会留下还在写的孤儿；冒烟有没有走到每个分支。
  - **收尾**（合并之前）审结果页对产物：每个数能不能从产物现算出来；配对、对照是不是同开局、同代码；
    判据和冻结的口径一致；措辞不比数据强。
  - 结论评论到 goal 的 issue 上：首行 `复审：LGTM` 或 `复审：CHANGES`，后面写核了什么；CHANGES 修完再审一轮，最多审 3 轮：第 3 轮还是 CHANGES，修完就收尾、不再审（user 2026-10-14）。
    本工作区的合并脚本见到结果页改过、issue 上最后一条复审不是 LGTM，会拒绝合并。
  旁观者（monitor-claude-goal）是第三道，不是门禁：它每小时才看一次，抓到时 GPU 已经烧了。

## 长 GPU 作业（发射 → 盯 → 读）

goal 的主体是几小时的训练 / 评测 / 采集时，按这个形状跑（规则 3、10 照上面的例外放宽）：

1. **搭**：runner、分析脚本、结果页骨架并行派（文件不相交）。子 agent 不发 GPU 作业、不连远端机器。
2. **发射前审**（见门禁），然后真入口冒烟，每个分支至少一局。
3. **发射归你**：`setsid` 挂在盒子上、验 `ppid=1`；每次发射一份代码快照、一个新输出目录；记下进程组号，停作业用它，
   不按命令行匹配进程。
4. **盯**：一个 Monitor，`timeout_ms` 给满 1800000、到期重挂；过滤器覆盖进度里程碑和每一种失败（Traceback、服务退出、
   OOM、ECC），只在变化时吐行；管道里的过滤写 `command grep --line-buffered`（Bash 工具里的 `grep` 是包 ugrep 的函数，
   在常驻管道里不逐行吐）。另外每小时读一次真实产物、重算不变量。
5. **等的时候不空等**：派不在关键路径上的活（分析脚本、结果页骨架、下一阶段的准备）；不在前台 sleep。
6. **读数 → 收尾审 → 合并。**

## 本工作区的附加裁定（覆盖默认）

- **不用 `AskUserQuestion`**；不停下来问审批。GPU 活直接发射，发射的同一条消息说清在跑什么。
- 子 agent 的派单里也写上：不许用 AskUserQuestion，拿不准就按派单里的假设做并在报告里摊开。
- **等待不占前台**：超过约一分钟的等待放后台（`run_in_background` 的 until 循环或 Monitor），前台 Bash 不 sleep 等，
  这样 user 和旁观者的消息不会被压住。
- 一次性脚本（核对、探针）落 scratchpad，不进仓库；进仓库的只有要长期用的代码。
- 实验结果写进仓库 `docs/` 的结果页，不写 memory；memory 只留规则、坑、配方。
