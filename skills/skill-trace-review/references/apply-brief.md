# 照审稿报告改 skill

先读 skill-trace-review 的 SKILL.md 第 4、5、6 节，照着做。

- 运行目录：{RUN}
  - 审稿报告：`audit-*.md`
  - trace：`<skill>.txt`
  - 上一轮的目录：{PREVIOUS}
- 你负责的 skill 和仓库：{SKILLS}
- 审稿报告：{AUDITS}
- 判「重复」和「和 user 裁定冲突」时要查的：项目规则文件、memory 目录 {RULES}（memory 用 `grep -R`，里面很多是软链）。
- 本组要注意的：{NOTES}

只改报告里把握高、而且当场核过的条目。每个 skill 一个提交，只带自己改的路径，不推送，不用 stash、amend。

## 交回

写进 `<run>/applied-<负责的 skill，用 + 连>.md`，最后的回复里给同样的内容。每个 skill 一节：

- 正文字节：改前 → 改后。
- 改了的：`位置 | 改了什么 | 证据 | 下一轮 trace 该看到什么 | 提交`。
- 没改的：`报告条目 | 原因（等 user／太新／核不过／别人在改／要写代码）| 一句话说明`。
- 要 user 定的：每条一句，写清两个选项和你的建议。
