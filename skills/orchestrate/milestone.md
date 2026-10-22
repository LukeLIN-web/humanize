# 里程碑模式

触发：user 给一个 milestone（「这个 milestone 要做 X」「开个 milestone」「按 milestone 走」），要你从 brainstorm 一路推到合进
默认分支。它是协作 PR 模式（`collab-pr.md`）在前面加了一段规划、在后面换了门禁：

| | 协作 PR 模式 | 里程碑模式 |
|---|---|---|
| 规划 | 每项一个 issue | milestone issue → decision issue → feature / infra issue，带 `Blocked-by:` 依赖 |
| todo | scratchpad 的 `orchestrate-todo.md` | milestone issue 的 Points 清单（不另写本地 md） |
| 作者的 PR 进 | 默认分支 | `milestone/<ms>` dev 分支 |
| 合进去之前 | 合并队列在合并结果上跑门禁 | review LGTM + 作者自测；不等 dev 上的 CI |
| 测试谁跑、何时跑 | 作者跑快门禁，队列每批跑一次 | 作者跑 unit + 自己 feature 的 system；全量（静态检查 + unit + system）每次 push dev 时异步跑，dev → 默认分支时挡合并；heavy 夜跑 |
| 作者 | 写代码、跑快门禁 | 端到端：实现 + 验证，和便宜的别家模型（advisor）结伴 |

照搬协作 PR 模式的：CI/CD 体检、所有 gh 命令带 `-R $R`、作者各自的 worktree、零上下文交叉 review（只用 `--comment`，第一行 LGTM / CHANGES）、
广播、摩擦台账（scratchpad 的 `orchestrate-friction.md`，照它的「摩擦」一节看、记、修）、`scripts/merge-queue.sh` 和 SemVer（只用在进默认分支的 PR）。
下面只写不一样的。占位符：`<skill>` 是本 skill 目录，`<humanize>` 是它往上两级（插件根），派单里都写成绝对路径；
`<ms>` 是 milestone 的短名（dev 分支 `milestone/<ms>`）；`<slug>` 是单个 feature 的短名（分支 `feat/N-<slug>`）。

## 第 0 步：一个 session 一个 milestone

- 本会话只管一个 milestone。user 一次给了几个：其余的每个开一个新 session，不在一个会话里混。开法是 claude-board 卡片：
  `python3 <humanize>/skills/todo-dispatch/scripts/dispatch.py <host> <cwd> <prompt 文件>`，`<host>` 是 board 上那台机器的标签
  （`/api/windows` 的 `host` 字段，例如 `71`），prompt 写「orchestrate 里程碑模式：<这个 milestone>」。
- 照 collab-pr.md 第 0 步体检，另外查四件事；缺的开成 infra point（第 1 步），在依赖图里排最前：
  - CI 在 `milestone/**` 的 push 上跑不跑（第 5 步的异步测试靠它）；
  - 测试分不分得出 unit / system / heavy，能不能各用一条命令跑（第 3、7、8 步靠它）；
  - 只改文档的提交跑不跑测试（第 6 步）；
  - 仓库许不许 merge commit（`gh api repos/$R --jq .allow_merge_commit`）：不许就第 4 步用 `--squash`，first-parent 二分照样成立。
- 标签：`for l in milestone decision feature infra bug in-progress; do gh label create "$l" -R "$R" 2>/dev/null; done`。
- dev 分支：`git fetch -q origin && git push origin origin/<默认分支>:refs/heads/milestone/<ms>`。主工作树不切到它（别的会话在用）；
  要在 dev 上跑东西（二分、复现）用 scratchpad 下的 worktree：`git worktree add --detach <scratch>/dev origin/milestone/<ms>`。

## 第 1 步：定 milestone

- **Brainstorm**（需求不清时先用 brainstorming skill 和 user 过一遍）：目标、完成判据（可观察的）、不做什么。把要做的拆成 point：
  - **feature**：一个用户或调用方看得见的能力，实现和验证都归它，一个作者端到端交付；
  - **infra**：CI、测试骨架、静态检查、脚本、共享工具——几个 feature 都要用的地基。

  一个 point 要一个作者、一个 PR 装得下；装不下再拆。
- 开 milestone issue（label `milestone`），正文：

  ```
  ## 目标
  ## 完成判据
  ## 不做
  ## Points
  - [ ] #12 feat: <一句话>        ← issue 号紧跟勾选框；还没开 issue 的先写 - [ ] feat: <一句话>
  - [ ] infra: <一句话>
  ## Decisions
  - #7 <一句话结论>
  ```

- 这个 issue 就是本模式的 todo，不写 `orchestrate-todo.md`。user 的裁定当场改进去。砍掉的 point 整段加删除线、后面写原因，不删行：
  `- [ ] ~~#12 feat: …~~ <原因>`（开了 issue 的再 `gh issue close 12 -R $R --reason "not planned" --comment <原因>`）。
  挪到下个 milestone 的也这样划掉，后面写 `→ #<新 milestone>`；issue 不关，把它正文的 `Milestone:` 行改成新的，并加进新 milestone 的清单。
  user 中途问的问题记在 scratchpad，收尾逐条答。
- 清单的勾只由 `scripts/ready-issues.py --sync` 打，不手改。

## 第 2 步：每个 point 先做 plan

先 plan，再派作者。plan 里的 decision、tradeoff、research 都落在 issue 里，历史追得到、原因写得清；
**不写本地 md**——仓库里只进代码、测试和长期文档。

**影响微架构的，开 decision issue**（label `decision`）。判据：改了它，别的 point 的代码或测试要跟着改——模块边界、对外接口和协议、
数据格式和存储布局、并发和时序模型、几个 feature 共用的抽象。正文：

```
## 背景        要定什么，为什么现在定
## 选项        每个选项一节：做法、好处、代价
## Research    实测数字、原型结论、参考链接（研究 agent 的完整结论贴成评论，这里留摘要）
## 决定        选了哪个，为什么不选别的；接口类的决定把签名逐字写在这里
## 后果        谁要照它改，什么变难了，什么情况下要重新考虑
```

- 选项要的事实（跑基准、查文档、一次性原型）派零上下文研究 agent 去拿，几个选项并行；结论回帖到 decision issue，不写文件。
  代价大到要 user 拍板的，列出选项和你的推荐交给 user；其余你定。
- 定了就写「决定」节并关掉 issue。以后推翻：开新的 decision issue，正文写 `Supersedes #7`，在旧的下面评论 `Superseded by #M`；
  旧的正文不改，历史就是这样追出来的。

**不影响微架构的小取舍**写进 feature issue 的「方案」节，带一句理由。

**decision 定了，再开依赖它的 feature / infra issue**（label `feature` / `infra`）。正文：

```
Milestone: #5
Decisions: #7            （没有就删这行）
Blocked-by: #12 #13      （没有依赖写 Blocked-by: none）

## 功能        做完以后，用户或调用方能做什么
## 目标        可观察的验收判据
## 方案        怎么实现；小取舍和理由
## 测试        unit 测什么；system 走哪条端到端路径；要不要单独搭 unit testbench 可以写建议，作者定
## 文件        预计改、新建的文件（文件归属：能同时派的 issue 两两不相交）
```

- 开完就把 milestone 清单那行改成 `- [ ] #N …`。
- **依赖**：`Blocked-by:` 是依赖的唯一正本，只写真依赖（要用对方产出的代码、接口或数据）。能靠先定接口解开的就解开：
  接口写进 decision issue，双方各自对着接口搭 unit testbench，不再互等。依赖越少，能并行的越多。
- 跑 `python3 <skill>/scripts/ready-issues.py -R $R <milestone 号>`：每个 point 一行状态（ready / blocked / in-progress / closed / cut / no-issue）。
  退出 1（依赖成环、Blocked-by 悬空或指向砍掉的 issue）就先改 issue，不派活。

## 第 3 步：派作者

- `ready` 的那一批**一条消息里全部派出**（`isolation: "worktree"`）。派出前给每个 issue 打 `in-progress`、评论一行
  「派给 <agent 名>，分支 feat/N-<slug>」——这就是领取，ready-issues.py 据此不再把它列成 ready。
  每个作者在你的 scratchpad 下分一个自己的目录 `<scratch>/feat-N/`（放日志、PR 正文这些不进仓库的东西），派单里写绝对路径。
- 不等整波：每合进一个 PR 就重跑 ready-issues.py，新变成 ready 的当场派。
- 唯一不进 dev 的 point 是第 8 步的夜跑 workflow：给它的派单里把起点和 `--base` 都换成默认分支，正文写 `Closes #N`，合并走合并队列。
- 作者派单（在 collab-pr.md 第 2 步的模板上改）：

  ```
  目标：端到端实现并验证 issue #N（gh issue view N -R <R>，正文就是需求；Decisions 行列出的 decision issue 也读）。
  工作区：你有自己的 git worktree（就是你的 cwd）。第一步：git fetch -q origin && git switch -c feat/N-<slug> origin/milestone/<ms>
          主工作树 <绝对路径> 一个字节都不碰。日志、PR 正文这类文件放 <你的目录>（绝对路径），不放 worktree 里。
  只拥有这些文件：<issue「文件」节>；<共享文件> 归 #M，只读。
  背景（你看不到别的上下文，以下是全部）：<decision 里的接口签名逐字、要复用的函数、解释器和数据的绝对路径>
  流程：
    1. 读 issue 和 decision，把方案（接口、数据结构、边界情况、测试计划）拿给 advisor 问漏了什么（见下），再动手。
    2. 先想测试：要不要单独搭 unit testbench 你定（issue「测试」节可能有建议）——你的模块要依赖别的在飞 feature、
       或者要靠别人的代码才测得到时就搭：邻居用 stub / fake，照 decision 里的接口。定了什么、为什么，写进 PR。
       system 测试只写本 feature 的端到端路径。
    3. 静态检查：<仓库的静态检查命令>，每次跑测试前先跑，红了先修。
    4. 测试：<unit 命令> 和 <本 feature 的 system 命令>，不跑 heavy。只改了非逻辑文件
       （bash <skill>/scripts/touches-logic.sh origin/milestone/<ms> 退出 1，没提交的改动也算在内）就不跑。
       不 fail-fast（不加 -x / --maxfail / -failfast），要逐条打印结果（pytest 加 -v -rfE），在快照里后台跑（Bash 工具 run_in_background）：
         bash <skill>/scripts/snapshot-run.sh [--link <被 gitignore 的依赖目录>] <你的目录>/test.log -- <测试命令>
       日志里一出现第一条失败（grep -n -m1 -E ' (FAILED|ERROR)( |$)'）就开始修——修的是你的 worktree，快照不受影响。
       跑完拿完整失败列表一起修；再只跑失败的那些（测试 ID 从日志取，可能带空格，一行一个读进数组：
       mapfile -t ids < <(sed -nE '/^(FAILED|ERROR) /{s/^(FAILED|ERROR) //;s/ - .*$//;p}' <你的目录>/test.log)，再 pytest -v -rfE "${ids[@]}"），
       最后全量再跑一遍，绿了才算。退出 125 是快照没建起来，看日志最后一行。
       失败看着和你的改动无关：在 origin/milestone/<ms> 上单跑那几条确认；本来就红的不修，列进报告。
    5. 把 diff 拿给 advisor 审，再推。同一个失败修了两次还没过，也先问 advisor。
    6. 小步提交，提交信息末尾带 (#N)；只 git add 具体文件，不用 git add -A（.humanize/ 是 advisor 的记录，不提交）。
    7. git push -u origin feat/N-<slug>；gh pr create -R <R> --base milestone/<ms> --title "<type>: …" --body-file <你的目录>/pr.md，正文：
       Refs #N（不写 Closes：PR 进的不是默认分支，Closes 不生效，issue 由指挥者关）；改了什么；unit testbench 搭没搭、为什么；
       怎么验的（静态检查、unit、system 各自的命令和结果）；Advisor：问了什么、采纳了什么、没采纳的为什么。
    8. 不改版本号文件和 CHANGELOG；不推 milestone/<ms> 和 <默认分支>；不合并。
    9. 合并前收到 review 意见或合并冲突：在同一个分支上改、推（要跟上 dev 就 git fetch && git merge origin/milestone/<ms>）。
       合并后收到「dev 红了，是你的 PR」：从最新的 origin/milestone/<ms> 开新分支 fix/N-<slug>，修完开新 PR 进 milestone/<ms>，正文 Refs #N。
    10. 不许用 AskUserQuestion；拿不准就按本派单的假设做，在报告里摊开。
  交回：PR 号、分支、head sha；测试（命令、通过 / 失败数、本来就红的有哪些）；testbench 搭没搭；advisor 问了几次、要点；
        **摩擦**（必填，没有就写「无」）：等了谁、等了什么；猜了什么；和谁的文件或接口撞了；环境缺什么；派单哪里不够。
  ```

本模式覆盖 SKILL.md 规则 9：测试归作者自己跑（在自己的 worktree 和快照里，不和别人打架）；GPU 和 heavy 照旧不跑。

## Advisor：和便宜的别家模型结伴

作者（Claude）配一个便宜的、最好是别家公司的模型当 advisor：训练数据和偏好不同，盲点就不同；便宜，才能在关键点上随手问。

- **谁**：默认 `<humanize>/scripts/ask-gemini.sh --no-web-research "<问题>"`（gemini-3.7-flash）；退出 126（额度用完）或没装 gemini，
  换 `<humanize>/scripts/ask-codex.sh --codex-model gpt-6-astra:low --codex-sandbox read-only "<问题>"`。问题整段作为一个加引号的参数传。
- **何时**（三个点，不是每改一行）：动手前审方案；同一个失败修了两次还没过；推之前审 diff。
- **怎么问**：自带上下文（issue 要点、相关接口、diff 全文贴进问题，不指望它自己读仓库）；问具体的
  （「这个方案漏了哪些边界情况」「这个 diff 里有哪些 bug，给文件:行」），不问「好不好」。
- **它只给意见**，不改文件；采纳与否作者定，写进 PR 的 Advisor 节。它是第二双眼睛，不是门禁，零上下文 review 照旧。
- 你在第 2 步定 decision 时也可以问它一句「这几个选项还漏了什么」。

## 第 4 步：review → 合进 dev，不等测试

- 每个 PR 一开就派零上下文审阅（collab-pr.md 第 3 步的模板），清单多三条：issue「测试」节写的测试真写了没有；
  Advisor 节里没采纳的意见，理由站不站得住；有没有 decision 里没写的接口改动（有 ⇒ 先补 decision issue 再合）。
- 合进 dev 的条件：review 第一行 LGTM、作者报告里静态检查和测试绿、和 dev 不冲突。**不等 dev 上的 CI。**
- 合：`gh pr merge N -R $R --merge`（仓库不许 merge commit 就 `--squash`；不带 `--delete-branch`：作者的 worktree 还检出着这个分支，收尾统一删）。
  GitHub 报冲突 ⇒ SendMessage 给作者：merge origin/milestone/<ms>、解冲突、重跑测试、推。记摩擦。
- 合完马上：
  1. `gh issue close <issue> -R $R --comment "合进 milestone/<ms>：#<PR> @ <sha>"`，摘掉 `in-progress`；
  2. `ready-issues.py -R $R <milestone 号> --sync`：清单打勾，同时看哪些新 ready 了，当场派；
  3. 广播给在飞的作者（collab-pr.md 第 4 步的广播，「默认分支」换成 `milestone/<ms>`）。

## 第 5 步：dev 分支异步测试

- CI 在每次 push 到 `milestone/**` 时跑全量：静态检查 + unit + system（不含 heavy）。体检时没有这个触发，就是第一个 infra point。
- **绿 / 红只看测试 job 的结论**，不看整次 run：`gh run view <id> -R $R --json jobs` 里测试 job 是 `success` 才算绿、`failure` 才算红；
  测试 job 被第 6 步跳过（只改了文档）的 run 不算绿也不算红，找「上次绿」时越过它。
- 你盯 dev 的 CI（Monitor 盯 `gh run list -R $R -b milestone/<ms>` 的变化），但**不拿它挡合并**。
- dev 红了：
  1. 找是谁：上次绿的提交到这次红的之间合了哪些 PR（`git log --first-parent --oneline <绿>..<红>`）。只有一个就是它；
     几个就二分：先把 dev worktree 拉到最新（`git -C <scratch>/dev fetch -q origin && git -C <scratch>/dev checkout -q --detach origin/milestone/<ms>`），
     再沿 first-parent 二分（`git bisect start --first-parent <红> <绿>`，`git bisect run` 跑红的那个测试）。
  2. SendMessage 给那个作者（它带着上下文）修：从最新 dev 开 `fix/N-<slug>`，修补 PR 进 dev；作者已经不在了，
     就派新的修补 agent，派单写清红的测试和是哪个 PR 引入的。
  3. 修补 PR 优先 review、优先合。dev 红着的时候别的 PR 照常合（不冻结），但 dev 不绿不开 dev → 默认分支。
  4. 记摩擦。同一类红第二次出现就修流程：多半是 unit testbench 没覆盖到的接口假设（补进 decision 和 testbench），
     或者该有一条静态检查（写进仓库的检查脚本）。

## 第 6 步：不碰代码逻辑的提交不跑任何测试

- 判据只有一个：`<skill>/scripts/touches-logic.sh`。退出 1 = 改的文件全在非逻辑清单里（默认 `*.md`、`docs/*`、`LICENSE*` 等），不跑任何测试；
  退出 0 = 要跑；拿不准一律算要跑。本地用一个参数（`touches-logic.sh origin/milestone/<ms>`：从分叉点到工作树，没提交的改动和没 add 的新文件都算），
  CI 用两个参数（两个提交之间）。作者、你、CI 用同一个判据。
- 清单放在仓库的 `.github/non-logic-paths`（一行一个 bash 通配，`*` 跨目录），没有就用脚本里的默认表。
  **测试会读 md 的仓库**（prompt、模板、skill 仓库）要把 `*.md` 从清单里去掉——那里的 md 就是逻辑。
- CI：把脚本拷进仓库（例如 `scripts/ci/touches-logic.sh`），加一个分类 job，测试 job 依赖它。比 `paths-ignore` 好：清单只有一份，本地也能用。

  ```yaml
  jobs:
    classify:
      runs-on: <和仓库其它 job 一样>
      outputs:
        logic: ${{ steps.c.outputs.logic }}
      steps:
        - uses: actions/checkout@v4
          with: { fetch-depth: 0 }
        - id: c
          run: |
            rc=0; bash scripts/ci/touches-logic.sh "${{ github.event.pull_request.base.sha || github.event.before }}" "$GITHUB_SHA" || rc=$?
            if [ "$rc" = 1 ]; then echo logic=false; else echo logic=true; fi >> "$GITHUB_OUTPUT"
    test:
      needs: classify
      if: needs.classify.outputs.logic == 'true'
      # ...
  ```

- review 照常：文档也要审，只是不跑测试。

## 第 7 步：dev → 默认分支

- 条件：ready-issues.py 里只剩 closed 和 cut；dev 最近一次测试 job 的结论是 success（第 5 步的口径）；没有在飞的修补 PR。
- `gh pr create -R $R --base <默认分支> --head milestone/<ms> --title "<type>: <milestone 标题>"`，正文：`Closes #<milestone>`；
  每个 point 一行（#issue → #PR，砍掉的写原因）；decision 列表（#、一句结论）。标题的 type 就是这次 SemVer 的级别：哪个 point 改了对外接口就用 `type!:`。
- 零上下文收尾审（SKILL.md「两道零上下文审」的收尾那道）：milestone 的完成判据逐条对实现；跨 feature 的接口和 decision 一致；
  每个 point 的测试都在，都放进了对应的层。
- 门禁是**全量测试**（静态检查 + unit + system，不含 heavy），在合并结果上跑、挡合并：
  `bash <skill>/scripts/merge-queue.sh --gate ci [--semver …] <PR 号>`（或 `--gate local:<全量命令>`），退出码照 collab-pr.md 第 4 步的表处理。
  这里红了就是 dev 的问题：照第 5 步找人修进 dev，再进队。
- 合进去以后：`Closes` 关掉 milestone issue；在它下面评论最终状态（版本号、最近一次 heavy 夜跑的结果、留到下个 milestone 的 issue）；
  删 dev 分支、feat / fix 分支和 scratchpad 里的 dev worktree。

## 第 8 步：heavy 测试夜跑

- heavy：一次跑十来分钟以上的、要 GPU 或大数据的、长回归和随机压力测试。打上标记（pytest marker `heavy`，或放进 `tests/heavy/`），
  unit / system 的命令显式排除它。heavy 不进任何 PR 的门禁。
- 夜跑：仓库有 CI runner 就加一个 `schedule:` 的 workflow。cron 只认**默认分支**上的 workflow 文件，所以这个 infra point 是例外：
  它的 PR 直接进默认分支（走合并队列），不进 dev；workflow 里跑默认分支，再加上 `git ls-remote --heads origin 'milestone/*'` 列出的每条分支
  （matrix 或循环，各自 checkout）。没有 runner 就在盒子上挂 crontab，做法相同（`setsid`，照 SKILL.md「长 GPU 作业」）。
- 夜跑红了：开 bug issue（label `bug`，正文 `Milestone: #<当前>`、`Blocked-by: none`），加进当前 milestone 的清单，走第 3 步，不在 dev 上直接改。
  清单没结完不开 dev → 默认分支，所以它自然挡在合并前面；要留到下个 milestone，由 user 定，照第 1 步「挪到下个 milestone」的写法。

## 收尾

- milestone issue 关了；每个 feature / infra / bug issue 关了，或明确交 user（`[blocked]` 写原因）；每个 decision issue 都有「决定」节；
  dev 分支、feat / fix 分支、`mq/*` 分支、worktree 都清掉。
- 给 user 的报告：
  - point 表：# / 标题 / PR / review 轮数 / advisor 次数 / 引起 dev 变红的次数；
  - decision 表：# / 一句结论 / 有没有被推翻；
  - dev 变红的记录：几次、谁引入的、多久修好；
  - 摩擦台账（照 collab-pr.md 收尾）。
