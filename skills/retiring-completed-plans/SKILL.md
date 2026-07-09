---
name: retiring-completed-plans
description: Use when asked to clean up docs/exec-plan(清理/清库/归档 exec-plan、把做完的 plan 删了),or right after a plan's result page is settled and the plan file should be retired
---

# exec-plan 清库(做完的 plan 退役)

## 核心原则

做完的 plan 唯一去向是删除(git 历史即归档,仓库先例措辞「已完成并删」)。
**「结果页存在」不等于「做完」**——判完成的唯一标准:结果页把 plan 预注册的问题全部答上了。

## 判定流程(每个 .md 逐个跑)

1. 列清单:`ls docs/exec-plan/*.md`;`CLAUDE.md` 与 `archived/` 不在处置范围。
2. 找对应结果页:`grep -rl "<plan文件名>" docs/eval_res docs/paper-writing`,或按名近似搜 docs/eval_res 子目录。
3. **完成三查**(全过才算做完):
   - 结果页开头「问题 1/2/3 的答案」全部落数,plan 核心问题无空档;
   - 对结果页 `grep -n "待补\|TBD\|待跑"`——plan 对应的行/表无占位符(实测教训:落账节已写、但主表该 plan 的行仍是 `<待补跑>` = 未完成,不能删);
   - plan 自述开头与 c2f-router-lineage.md 花名册无「待命 / 未启动 / 现在不跑」标记。
4. 分类处置:
   - **做完** → `git rm`;
   - **做完、但结果页按名把内容外包给它**(典型:gates 预声明,结果页写「门与口径 → X.md」现读不复述)→ `git mv` 到该结果页同目录(docs/ 引用约定是裸文件名,移动不断链);
   - **未完成 / 待命 / 在跑 / 想法池(ideas)/ 论文加固清单** → 保留,一个字不动。
5. 修指针:全仓 `grep -rn "<被删文件名>" docs scripts src`;
   - 活指针(「plan → X.md」「对应 exec-plan:X.md」)原地加注 `(已完成并删,落账见本页)`;
   - 落账偏离表里的历史引语(「plan(X.md)怎么说」)是引述,不动。
6. 提交:一次原子 commit(删+移+指针同批,任一时刻无悬空引用);**只 add 本次清理涉及的路径**——并行会话常有在途改动,禁 `git add -A` / `git commit -a`;消息按仓库中文 conventional 风格列明删/移清单。

## 常见错判

| 症状 | 纠正 |
|---|---|
| 结果页存在/有落账节就判做完 | 三查全过才算;占位符 = 未完成 |
| 把 gates/口径预声明当普通 plan 删掉 | 结果页现读它们,须 `git mv` 到结果页同目录 |
| 想法池、加固清单、CLAUDE.md 被当 plan 处置 | 非 plan,保留 |
| 删后留悬空「plan → X.md」 | 逐个加注「已完成并删」 |
| `git add -A` 吞进并行会话的在途文件 | 只 add 清理路径,提交后 `git show --name-status HEAD` 复核 |
