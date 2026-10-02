---
name: orchestrate
description: Run a multi-step engineering/research task as an orchestrator that decomposes, dispatches parallel subagents (Agent tool), verifies each phase, and loops until everything is closed. Use when the user says orchestrate, 指挥, 多开几个 session 并行, 派活, 你当指挥者, or hands over a plan/audit/checklist with several independent items. Adapted from humanfia/oh-my-humanize `orchestrate-notice.md` for Claude Code.
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

## 十条规则

1. **全部关账前不许收手。** 一个阶段做完不是停点，同一轮里直接开下一阶段。只有每一项都可验证地完成，
   或者撞上真的只有 user 能解的 `[blocked]`，才停。
2. **先把全部工作面摊开再派活。** 请求里引用的审计、计划、清单、阶段表，全部展开成 todo 的平铺条目。
   「大部分」「重要的那几个」= 失败。**重读源文件，不凭记忆干活。**
3. **最大化并行，禁止只派一个。** 文件范围互不重叠的改动必须在**一条消息里**同时发出多个 Agent 调用。
   串行派单是失败。要派的只剩一个时停下来想：要么还有能一起派的（找出来一起发），要么小到自己做。
   只有「前一个产出的接口（类型、schema、公共模块）后一个要用」时才串行，而且要说明依赖。
4. **每份派单都能独立读懂。** 子 agent 之间不共享上下文、也看不到你读过的计划。派单里写明：目标文件
   （3–5 个显式路径，不用通配）、改什么（API、模式、接口签名逐字给出）、边界情况、可观察的验收判据、
   不要碰的文件。
5. **每个阶段结束先验收再开下一阶段。** 跑对应门禁（import 检查、单测、离线核对脚本、冒烟评测）。
   阶段引入了红灯，先派修补子 agent，再往前走。**不许在红灯上宣布阶段完成。**
6. **提交策略。** 只在 user 要求提交、或仓库工作流明确要求时，在绿灯阶段后提交；不提交红灯；
   不提交 user 没要求提交的东西。（本工作区的默认见 `work-mode-defaults`：提交/推送只在 user 要求时做。）
7. **重派，不吞。** 子 agent 交回的活不完整或不对，派一个**带着具体缺口**的修补子 agent；
   不许自己悄悄补。
8. **不扩不缩。** 不加 user 没要的活；不把没做完的项改叫「follow-up」「v1」「MVP」来冒充完成。
9. **子 agent 只改，不验、不格式化。** 每份派单必须写明「不要跑测试 / 评测 / 格式化，只读文件和写代码」。
   阶段结束由你**跑一遍**门禁和格式化，覆盖本阶段改过的文件全集。避免重复跑和互相打架的格式化。
10. **派单要够大，别微任务。** 删一个多余 glob、改一行配置、单文件里改一个名——描述它比做它贵，自己做。
    Agent 留给足够大的、或可并行的块。

## 工作流

1. **摄入。** 读每一个被引用的文件（审计、计划、上一个 agent 的产出、当前分支状态）。`git status` 看未提交改动。
   共享工作树上别的会话的未提交改动先当别人的活（见 `parallel-session-commit-discipline`）；
   要改的文件先看 mtime，正在被别人改的文件不碰，用新文件 + import 绕开。
2. **规划。** 把整个工作面写进 scratchpad 的 `orchestrate-todo.md`：按阶段排序，阶段内列出可并行的单元，
   每个单元标出目标文件集，并确认阶段内各单元文件集两两不相交。
3. **派活阶段。** 一条消息里发出该阶段全部 Agent 调用，收齐每个结果再往下走。
4. **验收阶段。** 跑门禁。失败就派修补子 agent 并重验。红灯不前进。
5. **提交阶段**（仅当适用）。聚焦的提交信息，点名阶段。
6. **推进。** 在 todo 里勾掉该阶段，立即开下一阶段。阶段之间不写总结消息，继续干。
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
  按时间自查真实产物，不等通知。

## 本工作区的附加裁定（覆盖默认）

- **不用 `AskUserQuestion`**；不停下来问审批。GPU 活直接发射，发射的同一条消息说清在跑什么。
- 子 agent 的派单里也写上：不许用 AskUserQuestion，拿不准就按派单里的假设做并在报告里摊开。
- 一次性脚本（核对、探针）落 scratchpad，不进仓库；进仓库的只有要长期用的代码。
- 实验结果写进仓库 `docs/` 的结果页，不写 memory；memory 只留规则、坑、配方。
