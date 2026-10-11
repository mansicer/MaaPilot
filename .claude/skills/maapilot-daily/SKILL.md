---
name: maapilot-daily
description: 明日方舟日常 via MaaPilot —— 前台跑一次日常（daily run：公招、基建、信用商店、剿灭、领奖励、刷理智）、定时日常里的目标部分（刷目标缺的材料、养成、更新目标、写汇报）、"今天做了什么"、改日常的时间和内容。Use when the user says 跑一次日常 / 重新做日常 / 今天做了什么 / 日常改成几点 / 不做某项, or when a scheduled daily run starts you for the goals part (daily / routine / farming / report).
---

# maapilot-daily：日常

日常 = MAA 日常项 + 刷理智 + 领奖励 + 对账 + 汇报。MAA 日常项由程序按 `private/config.yaml` 依次跑；刷理智时有进行中的目标就由你来决定刷什么、做什么养成。

## MAA 日常项

| 项 | 做什么 | 说给用户 |
|---|---|---|
| `startup` | 启动游戏、回首页 | 启动游戏 |
| `recruit` | 公开招募：收已完成的、按标签开新的（不用加急许可） | 公招 |
| `infrast` | 基建：换班、收制造站 / 贸易站产物、线索、会客室 | 基建换班收菜 |
| `mall` | 信用商店买东西、访问好友拿信用 | 信用商店 |
| `annihilation` | 当期剿灭打 1 次；本周合成玉拿满后自动跳过 | 剿灭 |
| `box` | 识别干员练度（接了森空岛用 `maapilot state skland`，不行再用 `maapilot state box` 的 MAA 干员识别，更新 `private/state/box.json`；森空岛没成会在汇报里告警，token 失效要请用户在终端重跑 `maapilot skland token`）：其余项之后、刷理智之前，每个游戏日（04:00 换日）只读一次，今天读过就跳过；`daily.read_box: false` 关掉 | 识别干员练度 |
| `award` | 领任务奖励和邮件（总在刷理智之后） | 领奖励 |

`box` 和刷理智（`farm`）不写在 `daily.tasks` 里，但 `--only` 里可以写（例如 `daily run --only box` 只重读练度）。`box` 失败了重跑用 `maapilot state box`（它不是 maa 任务）。

## 用户说"跑一次日常"

1. 先 `maapilot daily status`：有没有正在跑、离下次定时多久。定时点前后 20 分钟内就告诉用户"马上会自动跑"，问还要不要现在跑。
2. `maapilot daily run`，用 Bash 的后台运行（要 20–60 分钟），告诉用户大概多久。只补几项用 `--only recruit,mall`。
3. 结束后看 `data`：
   - 退出码 1、`error.code` 是 `handoff`：日常停在失败的那一项交给你（`data.handoff`：哪一项、为什么、`rest` 是它和后面还没跑的、
     `evidence` 是失败那一刻的截图和 MAA 自己存的截图）。马上按 `maapilot-recovery`「先看」：看这几张截图、`maapilot maa why` 看经过、判断原因，再看屏幕处理；
     被登出 / 掉线按 `maapilot-recovery`「掉线、被登出」（先问用户是不是在别的设备上登着，同时计时 5 分钟，没回复就重新登录接着跑）；
     处理好后 `maapilot daily run --only <rest 用逗号连起来>` 接着跑，一轮下来直到跑完；
   - `failed` 里有失败的项（定时运行里 agent 也没救回来的）：同样按 `maapilot-recovery` 处理，处理完 `daily run --only <这几项>` 补跑；
   - `goals_pending: true`：程序把刷理智交回给你，按下面「目标部分」做；`award_pending: true` 时领奖励也留给你（奖励要在刷理智之后领）。
     做完目标部分：`maapilot maa award`（`award_pending` 时）→ `maapilot guard check` → `maapilot report write <run_id> --append "<目标部分做了什么、花了什么>"`，再一起汇报；
   - 退出码 3：货币对账不过，转述给用户（`maapilot-recovery`「货币对账停机」）；退出码 4：模拟器被占用。
4. 汇报（见「汇报」）。

## 目标部分（定时运行叫起你，或 `goals_pending`）

1. `maapilot goals list`：只看"进行中"的。`problems` 里有写错的目标，照着修好那几行（`goals update`）。
2. `maapilot state sanity` 看理智；`state show depot` 的时间是今天这次日常之前的，就先 `state depot`。
3. 逐个目标（按文件里的顺序；"分解与进展"里写了先后的照写的）：
   - 读它的"分解与进展"，接着上次做；
   - `maapilot calc need "<对象>"`（技能等级、专精按笔记里记的加 `--from`）：`short` 为空 → 现在就做（`maapilot-training`）；缺的能合成 → `maapilot-base` 合出来再做；
   - `关卡`、`其他` 类目标按笔记里的分解做（`maapilot-battle`）。
4. 刷理智：把所有目标还缺的基础材料合起来，`maapilot calc stages 固源岩=20 聚酸酯=8 …`，按 `plan` 刷（`maapilot maa fight <关卡> --times N`，只刷 `open_today` 的关）。
   **分批**：Bash 一条命令最长 10 分钟，一批只打大约 8 分钟的量（一般每批 4–6 次，按 `maa fight` 返回的 `duration_s` 调），Bash 超时设 600000 毫秒，一批一批接着打。
   多个目标抢同一种材料时，按目标顺序先满足前面的。目标都在等（训练室在练、材料在路上）、没东西可刷时，刷 `config.yaml` 的 `daily.default_stage`。
   `maa fight` 失败 `not_open` 就换建议里的下一关；打不了代理（没三星通关过）的关换一关，或按 `maapilot-battle` 处理。
5. 刷到材料够了就回到第 3 步做养成。
6. 每个目标做完一段就记一笔：`maapilot goals update <标题> --note "<做了什么、还差什么>"`。完成了（用 `calc need` 或屏幕核对确实达到）：`maapilot goals remove <标题> --status 完成 --reason "<核对依据>"`。
7. 写汇报（见下）。

**理智液**：默认不用（`--medicine` 不加）。先查自动记忆里用户对理智液（包括快过期的）怎么说：说过"快过期的可以用"就加 `--expiring-medicine`；
说过"每天可以用 N 瓶"就照办。记忆里没有 → 会话里问一次并记下；定时运行里不用，写进汇报"要你决定的事"。**源石永远不用来恢复理智。**

## 定时运行里（无人值守）

提示里写着这次运行的编号。用户不在场：
- 先 `maapilot lock status`：`inherited` 为 `true` 才说明日常把模拟器交给了你；不是就别操作，写进总结后结束。
- 不问问题；要确认的命令会被拒，别试。
- 命令都在前台跑、不放后台（Bash 超时设 600000 毫秒）：你一结束日常就收回模拟器，没跑完的会被拦下。刷图照上面分批。时间有上限（默认 90 分钟），先做最要紧的。
- 只做目标里同意过的花费；合成玉、源石一律不碰；第一次遇到、记忆里没答案的事不做，写进汇报。
- 不要用 `report write --append`：你最后的回复就是这一段汇报，程序会把它放进汇报（全程用中文，写给用户看）。

## 汇报

用玩家的话，先结果后问题：

```
今天 04:40 的日常做完了：公招 4 次、基建换班、信用商店、剿灭（本周合成玉已满）、领了奖励。
刷理智：PR-C-2 打了 8 次（给遥的辅助芯片），剩下的刷了 1-7。
目标：遥 精2 —— 辅助芯片够了，还差聚合剂 2 个，明天能合。
花费：龙门币约 3 万、理智 135；没花合成玉和源石。
要你决定的事：有 2 瓶理智液明天过期，要用掉吗？（用 → 多刷约 12 次 1-7；不用 → 过期作废）
```

- 数从 `data`（`depot_change`、`farm`、`tasks`）和你自己做的操作里来；龙门币说"约"。
- 失败了的项说清是什么、处理了没有、下次会不会自己好。
- 用户问"今天做了什么""上次日常怎么样"：`maapilot report show --last`（或指定哪次），转述，不整篇念。
- **主动汇报**：会话里一直在后台挂着 `maapilot daily watch`（见 CLAUDE.md「盯着日常」），定时日常一跑完它就结束，你马上告诉用户：
  有问题（退出码 1）就说清什么没做成、为什么、要用户决定什么；顺利的一两句（用户说过顺利时不用说就不说）。
  告诉用户之后 `maapilot report seen <运行编号>`（会话开头的 `unseen_problems` 就不会再提它），然后再挂一个 `maapilot daily watch`。

## 改日常

- 时间：改 `private/config.yaml` 的 `schedule`，然后 `maapilot daily schedule`（弹确认）。
- 做哪些项、没目标刷哪关、某项的 MAA 参数：改 `daily.tasks`、`daily.default_stage`、`daily.params`，改完 `maapilot doctor` 确认。只改用户说的。
- 不想要定时了：`maapilot daily unschedule`（弹确认）。

## 问用户的情形

- 理智液（包括快过期的）能不能用、每天用多少——记忆里没有时；
- 目标之外的花费（例如信用商店之外用凭证买东西）；
- 几个目标抢材料、而顺序看不出该给谁时；
- 刷的关打不了、目标卡住好几天没进展时，问要不要换做法或放一放。

## 记住什么（写进自动记忆）

- 用户对理智液、刷图、汇报详略、什么时候别打扰的回答，写明是用户哪天说的。
- 刷图、日常里摸出来的经验（某关打不了代理、某活动关的导航要怎么走）。
- 不记当天的数字和进度（进度写在目标的"分解与进展"里）。
