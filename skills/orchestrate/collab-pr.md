# 协作 PR 模式

触发：user 给几项工作（「来，做 idea 1、2、3」），要你**并行派 agent、走标准多人协作流程（PR）推进仓库**，
并且**观察协作中的摩擦、在流程上解决**；常带的词有 merge queue、SemVer、CI/CD。

这个模式有两份交付，第二份是主要的：

1. 每项工作以 issue → 分支 → PR → 交叉 review → 合并队列 →（有版本号时）SemVer 发版 的流程合进默认分支。
2. **摩擦台账**：子 agent 并行协作时哪里互相卡、为什么卡、你在流程上改了什么、改完还复发没有。
   修的是流程（流程文件、CI、队列参数、派单模板），不是哪一个 PR。

## 角色

| 谁 | 做什么 | 不做什么 |
|---|---|---|
| 你（指挥者） | 体检 CI/CD、开 issue、定文件归属、派作者和审阅、跑合并队列、广播、记摩擦、提流程修补 PR | 不写功能代码；不替作者解冲突、不替作者改 review 意见（规则 7：重派，不吞） |
| 作者 agent（每项一个） | 独立 worktree、独立分支；写代码、跑快门禁、开 PR；改 review 意见；被队列弹回后自己修 | 不推默认分支、不合并、不改版本号和 CHANGELOG |
| 审阅 agent（每个 PR 一个，零上下文） | 只读 PR，按清单给意见 | 不改代码 |
| 合并队列 `scripts/merge-queue.sh` | 默认分支只前进到「门禁在合并结果上绿过」的提交 | 不改写任何 PR 分支 |

子 agent 之间不能直接说话，所有协调都经过你——**这本身就是最大的摩擦源**，所以「广播」是你的固定动作，不是可选项。

## 第 0 步：CI/CD 体检（派活之前）

逐项查，结果写进 todo，第一条消息里给 user 一张表（项 / 现状 / 对并行 PR 的影响 / 本轮修还是交 user）。

先定 `R`（origin 的 owner/repo），**之后所有 gh 命令都带 `-R $R`，派单里也写死**：有 upstream 的 fork 里，gh 自己挑的默认仓库常是
upstream，PR 会开到别人的仓库上、体检也查错仓库。

```bash
R=$(git remote get-url origin | sed -E 's#^[a-z+]+://([^@/]*@)?github\.com/##; s#^[^@/]*@github\.com:##; s#\.git$##')
gh repo view "$R" --json nameWithOwner,isPrivate,owner,defaultBranchRef
gh api repos/$R --jq '{owner_type: .owner.type, allow_auto_merge, delete_branch_on_merge}'
gh api repos/$R/branches/<默认分支>/protection   # 404 = 没保护
gh api repos/$R/rulesets
ls .github/workflows/ && grep -n -A4 '^on:' .github/workflows/*.yml
gh run list -R "$R" -L 20 --json event,headBranch,workflowName,conclusion,createdAt,updatedAt   # 基线、时长
gh api repos/$R/actions/runners --jq '.runners[] | {name,status,labels:[.labels[].name]}'
git ls-remote --tags --refs origin 'v*' | tail -3; ls package.json pyproject.toml Cargo.toml .claude-plugin/plugin.json VERSION 2>/dev/null
```

要回答的问题：

- **原生 merge queue 有没有**：只有组织名下的仓库有（公开仓，或 Enterprise Cloud 的私有仓）；`owner.type == User` 就没有，
  用本 skill 的 `scripts/merge-queue.sh`。有原生的就用原生的：workflow 必须加 `merge_group:` 触发（不加，队列永远等不到检查），
  进队用 `gh pr merge N -R "$R" --auto --merge`。
- **CI 在哪些事件上跑**：`push` 到任意分支 ⇒ PR 分支已有 CI，缺的只是「合并结果」那一次，队列 `--gate ci` 推 `mq/*` 分支正好补上
  （它要求 PR head 上跑过的每个 workflow 都在合并结果上跑一遍）；有的 workflow 只在 `pull_request` / 默认分支上跑 ⇒ 它不会在 `mq/*`
  上触发，队列等不到就退出 4——用 `--gate local:<和 CI 同一条命令>`，或给那个 workflow 加 `mq/**` 的 push 触发。
  已有 push 全分支触发时**别再加 `pull_request` 触发**：同一个提交会跑两遍，单 runner 时直接翻倍排队。
- **runner 有几个**：只有一个 self-hosted runner ⇒ N 个 PR 的 CI 串行排队，队列要成批进（见第 4 步）。
- **默认分支基线绿不绿**：最近几次红 ⇒ 先查哪一步红，修绿作为**第 0 波的流程 PR**。红基线下每个 PR 都红，门禁没有信号。
- **门禁能不能本地一条命令复现**：不能 ⇒ 作者 agent 推之前没法自检，只能拿 CI 当编译器，队列弹出会很多。
- **还有谁在写默认分支**：别的会话、脚本直接推默认分支 ⇒ 绕过队列，默认分支可能被推红。**记录，不擅自开分支保护**
  （会打断别人在用的流程）；要不要开交 user 定。
- **有没有被消费的版本号**：包 manifest、插件 manifest、已有 `v*` tag ⇒ SemVer 发版由队列做（见下）；都没有就不新造 VERSION 文件。
  有 manifest 版本却没有 `vX.Y.Z` tag ⇒ 第 0 波先把现行版本打成 tag 推上去（队列从 origin 上的 tag 起算，没有 tag 就拒绝 `--semver`）。
- **有没有「每个 PR 自己升版本号」的 CI 检查**：有 ⇒ 并行 PR 必撞（都从 1.4.0 升到 1.5.0，先合的那个让后一个变成「没升」，版本号所在的每个文件都冲突）。
  这是第 0 波就要改的：改成版本号只由队列写，CI 检查改成核「PR 没碰版本号」或删掉。

**第 0 波只修确定会挡住所有 PR 的**（红基线、门禁不能复现、每 PR 自升版本号）。别的机制不预先加，等摩擦真出现（Occam）。
第 0 波的修补也是 PR、也走队列——它同时是队列本身的冒烟。

## 第 1 步：摊开工作面

- 每项工作一个 issue（仓库流程要求时；依赖登记 blocked-by）。一项太大就拆成几个**各自能独立合并**的 PR。
- **文件归属表**：每项预计改哪些文件。两项都要改的文件就是热点，当场处理：
  共享接口先由一个「接口 PR」落（contract first），其它项只 import；集中的注册表、索引、导出列表能改成「一项一个文件」就先改。
- 分支名 `<type>/<issue>-<slug>`；PR 标题用 Conventional Commits（`feat:` / `fix:` / `feat!:` …）。
  **标题是这个 PR 的 SemVer 级别的唯一声明处**，别处不再写。

## 第 2 步：派作者（一条消息里并行发出）

Agent 调用带 `isolation: "worktree"`。worktree 里没有被 gitignore 的东西（venv、数据、产物），派单里给解释器和数据的绝对路径——
但**被测的代码必须来自 worktree**：主工作树的 venv 若是 editable install，`import` 拿到的是主工作树的源码，门禁在 worktree 里怎么跑都绿。
门禁命令里设 `PYTHONPATH=$PWD`（src 布局用 `$PWD/src`），或先断言 `python -c 'import pkg; print(pkg.__file__)'` 在 `$PWD` 下。

```
目标：<issue #N 的 goal 原文>
工作区：你有自己的 git worktree（就是你的 cwd）。第一步：git fetch -q origin && git switch -c <分支> origin/<默认分支>
        主工作树 <绝对路径> 一个字节都不碰。
只拥有这些文件：<路径 ×1–5>；<共享文件> 归 <另一个 PR>，只读。
背景（你看不到别的上下文，以下是全部）：<接口签名逐字、要复用的函数、数据格式、解释器/数据的绝对路径>
流程：
  1. 小步提交，提交信息末尾带 (#N)。
  2. 推之前跑门禁：<和 CI 同一条命令>，不 fail-fast，逐条打印结果（pytest 加 -v -rfE；-q 要到跑完才列失败），在快照里后台跑：
       bash <本 skill 目录>/scripts/snapshot-run.sh <你自己的日志路径，不放 worktree 里> -- <命令>
     日志里一出现第一条失败（grep -n -m1 -E ' (FAILED|ERROR)( |$)'）就开始修；跑完照脚本开头的命令只重跑失败的，最后全量再跑一遍。
     退出 125 是快照没建起来，看日志最后一行。只改了非逻辑文件（bash <本 skill 目录>/scripts/touches-logic.sh origin/<默认分支> 退出 1）就不跑。红灯不推。
  3. git push -u origin <分支>；gh pr create -R <owner/repo> --title "<type>: …" --body-file <文件>，正文写：Closes #N；改了什么；怎么验的；
     对共享接口的影响（有破坏性改动就在标题用 `type!:` 并写出谁受影响；级别只看标题）。所有 gh 命令都带 -R <owner/repo>。
  4. 不改版本号文件和 CHANGELOG（队列写）。不推 <默认分支>、不合并、不 approve。
  5. 之后会收到 review 意见或队列弹回的消息：在同一个分支上改、推；需要跟上默认分支时 git fetch && git merge origin/<默认分支>。
  6. 不许用 AskUserQuestion；拿不准就按本派单的假设做，在报告里摊开。
交回：PR 号、分支、head sha、门禁结果；
      **摩擦**（必填，没有就写「无」）：等了谁/什么；猜了什么；和谁的文件或接口撞了；环境缺什么；派单哪里不够。
```

本模式覆盖 SKILL.md 规则 9：作者在**自己的 worktree** 里跑快门禁（和 CI 同一条命令、不重写文件的检查），不会和别人打架；
GPU、评测这类慢门禁照旧不跑。规则 6 也被覆盖：user 要的就是 PR 流程，作者推自己的分支、队列推默认分支都是授权内的。

## 第 3 步：交叉 review

每个 PR 一开，就派一个零上下文的审阅 agent（多个 PR 一条消息并行发）：

```
只读审 PR #N（gh pr view N -R <owner/repo>、gh pr diff N -R <owner/repo>、需要时读仓库文件）。不改代码、不 checkout。要跑探针只在自己的临时 worktree 或沙箱里，只停自己记下的 pid（不用 `pkill -f`、`killall`），不用 `--force`（worktree、push、rm 都一样）。
清单：1. 做的是不是 issue #<M> 要的，有没有多做少做；2. 改了归属表以外的文件没有：<该 PR 拥有的文件>；
3. 改了共享接口却没在标题用 `!`；4. 正确性和边界情况；5. 验证方式够不够。
用 gh pr review N -R <owner/repo> --comment --body-file <文件> 发出，正文第一行只写 LGTM 或 CHANGES，后面逐条列意见（文件:行 + 怎么改）。
交回：第一行同上；**摩擦**（必填）：哪里看不懂、缺什么上下文。
```

所有 agent 共用一个 GitHub 身份，GitHub 不许自己 approve 或 request changes 自己的 PR——所以只用 `--comment`，
第一行的 LGTM / CHANGES 就是 review 结论的记录。CHANGES ⇒ 用 SendMessage 把意见转给**原作者 agent**（它带着上下文），
改完只复审改动部分；两轮还不收敛，你看分歧点定，并记一条摩擦。

## 第 4 步：合并队列

进队条件：review 第一行 LGTM、分支 CI 绿、标题是合法的 Conventional Commit。

```bash
bash <本 skill 目录>/scripts/merge-queue.sh --gate ci [--semver [--version-cmd '<命令>']] 12 15   # 在仓库里跑
```

它在 `.git/mq-worktree` 里合并，不碰你的工作树；`git push origin` 要能非交互地推通（先手推一次试）。

| 退出码 | 意思 | 你做什么 |
|---|---|---|
| 0 | 合进去了（有 `--semver` 时已打 tag） | 广播；核 `Closes` 的 issue 关了没有（合并提交里抄了 Closes 行）；被它挡着的 issue 就绪的当场派作者 |
| 3 | 和默认分支（或同批前面的 PR）冲突 | 批里不止一个 PR：先把被弹出的拿掉、其余的重跑合进去——冲突对象若是同批的 PR，不先合它，作者 merge 默认分支也拿不到冲突的另一半；然后 SendMessage 给被弹出的作者：merge 最新默认分支、解冲突、重跑门禁、推，改好再进队。记摩擦 |
| 4 | 合并结果上门禁红（各自绿、合起来红就是这种） | 批里不止一个 PR 就二分（前半批单独再跑）找出是谁；SendMessage 给那个作者。记摩擦 |
| 5 | 排队期间作者又推了 | 等作者停手，重跑 |
| 6 | 排队期间默认分支前进了 | 直接重跑（推是比较后交换：非强制推被拒就是这个信号） |
| 7 | 合进去了，但打 tag / 删分支 / 核 MERGED 有没做完的 | 照报错里的命令补；当成 0 继续 |
| 2 | 用法或前提不满足（draft、base 不对、没有 tag 却要 `--semver`、推不上去） | 看报错 |
| 其它 | 脚本自己出错 | 先看 `origin/<默认分支>` 有没有含上合并结果（含上了就当 7），再决定重跑 |

- **成批**：CI 慢（单 runner、一次十几分钟）时一批进几个 PR，一次 CI；红了二分。CI 快就一个一个进，弹出原因清楚。
- **广播**（每次合并后、每次流程修补合并后）：SendMessage 给所有还在飞的作者——「默认分支前进到 <sha>：合了 #N，改了 <共享文件/接口>；
  你下次推之前 merge origin/<默认分支>」。对已经交回的作者，续派时带上这句。
- **SemVer**（仓库有被消费的版本号时）：
  - 级别只看 PR 标题：`type!:` 是 major，`feat` 是 minor，其余 patch；一批取最高（正文里的 `BREAKING CHANGE:` 不算，免得两处声明打架）；
    0.x 期间 major 只升 minor（0.x 不承诺稳定，升到 1.0 是 user 的决定）。
  - 版本号文件和 CHANGELOG **只由队列写**：`--version-cmd` 在合并结果上改（拿到 `NEW_VERSION`、`BUMP`、`PRS`），
    和合并一起过门禁，合并后打 `vX.Y.Z` tag（起点是 origin 上已并入默认分支的最高 `vX.Y.Z`，预发布 tag 不算）。
    PR 一律不碰它们——这是并行 PR 最常见的冲突源。
  - 发 GitHub Release、推包到 registry 是对外动作：仓库原来就这么做才做，否则只打 tag。
  - 没有版本号的仓库，标题里的 `!` 照样有用：它告诉还在飞的 PR「我改了你们在用的接口」。破坏性的共享接口改动优先拆成
    expand → migrate → contract 三个 PR（先加新的不删旧的，各项迁过去，最后删旧的），在飞的 PR 就不会被弄坏。

## 摩擦：怎么看、怎么记、怎么修

**看**——两个来源，缺一不可：

1. agent 交回的「摩擦」节（派单里必填）。
2. 你从事件里量的：队列弹出（按退出码分类）、review 轮数、CI 排队时长对运行时长（`gh run view` 的 createdAt / startedAt / updatedAt）、
   续派次数和原因、**热点文件**——PR 一开就求两两交集，冲突发生之前就能看到：
   `for n in <open PRs>; do gh pr diff $n -R "$R" --name-only | sed "s/^/$n /"; done | sort -k2 | uniq -f1 -D`

**记**——scratchpad 里的 `orchestrate-friction.md`，一条一行：

| 时间 | 涉及 PR / agent | 现象（可观察的） | 代价（多的轮次、弹出、重派、等待分钟） | 根因 | 流程修补（落在哪个文件，PR 号） | 修补后复发？ |
|---|---|---|---|---|---|---|

**修**：

- **修流程不修个案**。同一类摩擦第二次出现、或一次就代价大，才做流程修补。修补落在流程的唯一正本：仓库的
  CONTRIBUTING / PR 模板 / CI workflow / 门禁脚本（仓库特有的），或本 skill 的派单模板、队列脚本（所有仓库通用的）。
  **不在派单里口头约定**——口头约定下一个 agent 看不到。
- **先删后加**：能靠删掉冲突点（共享计数器、手写索引、集中注册表、每 PR 自升的版本号）消掉的，不加协调机制。
- 流程修补也是 PR、也走队列；合进去后广播，后面的派单引用它。
- 改别的会话也在用的流程（开分支保护、禁止直推默认分支、改 issue 规则）要 user 拍板；本轮只记录，收尾时列出来。
- 收尾核复发：每条修补之后同类摩擦又出现没有。复发 = 修错了地方，重修。

**常见摩擦**（体检和规划时对照；「预置」的是在 agent 环境里必然发生的，开工前就落；其余等观察到再修）：

| 现象 | 根因 | 流程修补 | 何时 |
|---|---|---|---|
| agent 互相切走分支、index 里混进别人的改动 | 共用一个工作树 | 每个作者独立 worktree（`isolation: "worktree"`） | 预置 |
| review 发不出 approve / request changes | 所有 agent 同一 GitHub 身份 | 只用 `--comment`，第一行 LGTM / CHANGES | 预置 |
| 各自绿、合起来红 | 门禁只在 PR 分支上跑过 | 队列在合并结果上跑门禁 | 预置 |
| agent 用的是旧接口、不知道默认分支变了 | 没人告诉它 | 每次合并后广播 | 预置 |
| 版本号撞号、CHANGELOG 次次冲突、「没升版本」CI 红 | 版本号和发版说明由各 PR 写 | 只由队列写；级别在标题声明 | 体检（有版本号时） |
| 每个 PR 都红 | 默认分支基线就红 | 第 0 波先修绿 | 体检 |
| agent 推之前没法自检，队列弹出多 | 门禁不能本地复现 | 把 CI 步骤抽成一条脚本，CI 和作者都调它 | 体检 |
| worktree 里门禁跑不起来 | venv、数据被 gitignore，不进 worktree | 派单给解释器和数据的绝对路径；门禁脚本收解释器路径参数 | 观察到再修 |
| 门禁在 worktree 里永远绿 | 主 venv 是 editable install，测的是主工作树的源码 | 门禁设 `PYTHONPATH=$PWD`，或断言 `pkg.__file__` 在 `$PWD` 下 | 预置（Python 仓库） |
| PR 开到了 upstream、体检查错仓库 | 多 remote 时 gh 自己挑默认仓库 | 所有 gh 命令带 `-R <origin 的 owner/repo>`，派单里写死 | 预置（有 upstream 时） |
| 队列等 CI 很久 | 单 runner、一个 PR 一次 CI | 成批进队、红了二分；或 `--gate local` | 观察到再修 |
| 只改文档的 PR 也排队跑全量 CI | CI 不分改没改代码逻辑 | `touches-logic.sh` 分类 job（milestone.md 第 6 步） | 观察到再修 |
| 修一处、从头再跑、再红一处，来回好几趟 | 门禁 fail-fast，问题一次只露一个 | 不 fail-fast、快照里跑、第一处失败就开始修（SKILL.md「门禁怎么选」） | 预置 |
| 同一个 helper 写了两份；同一文件反复冲突 | 规划时没定归属 | 文件归属表；接口 PR 先合 | 规划时 |
| B 等 A 的接口，或 A 改接口弄坏 B | 依赖没登记、破坏性改动没声明 | issue blocked-by；`type!:`；expand → migrate → contract | 观察到再修 |
| 注册表、导出列表、文档索引次次冲突 | 一处集中手写的列表 | 改成一项一个文件或由生成器出（删掉冲突点，不加锁） | 观察到再修 |
| 格式化改动淹没 diff、互相改回去 | 各 agent 的格式化器或版本不同 | 只用仓库钉版的配置；门禁只检查不重写 | 观察到再修 |
| agent 停下来猜、交回的东西偏了 | 派单缺上下文 | 补本 skill 的派单模板（字段级） | 观察到再修 |
| 默认分支被直推红了 | 两条写默认分支的路径 | 记录，交 user 定要不要开保护 | 交 user |

## 收尾

- 每个 PR 合进去，或者明确交 user（`[blocked]` 写原因）；issue 关掉；分支、`mq/*` 分支、worktree 清掉。
- 给 user 的报告两张表：
  - PR 表：号 / 标题 / 级别 / 版本 / 弹出次数（按原因）/ review 轮数；
  - 摩擦台账：现象 → 流程修补（PR 号）→ 复发没有；末尾列出要 user 拍板的流程项。
- 仓库有结论页规矩的（例如 goal 要写 HTML），照仓库的来。
