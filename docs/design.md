# MaaPilot 设计文稿

## 1. 设计哲学（10-20 用户定）

1. **项目只做和游戏相关的 harness。** MaaPilot 提供给 agent 的是：说明文档（`AGENTS.md`）、skills（`.agents/skills/`）和 `maapilot` 工具（操作模拟器、调用 MAA、读写账号数据、计算材料）。
   系统层面的能力——定时任务、叫醒会话、通知用户、会话管理——**由 agent 所在的 harness（Claude Code、Codex……）自己提供**，项目不另造一套。
2. **日常是 agent 按目标策划、按优先级执行的。** 配置（`config.yaml`）给出每天固定要做的事（MAA 日常项、没有目标时刷哪关）；
   在这个基础上，agent 按用户给的目标（`goals.md`，越靠前越优先）安排理智、材料、养成，材料不够分时先给前面的目标。
3. **只有真实货币要二次授权。** 合成玉、源石每一次花之前都单独问、用户同意后用 `guard allow` 记下，程序前后对账，没记下就减少会停掉日常；
   其余的资源和材料（龙门币、经验、材料、理智、理智液、凭证……）用户提出过（加目标时同意过、或在对话里说过）就直接执行，不再追问。

由此而来的几条做法：

- **程序薄，agent 厚。** 程序只提供可靠的工具；做什么、按什么顺序、出错怎么办，由 agent 判断。
- **经验写进 skill，不写进代码。** 各种情形的做法写成 skill，agent 按任务加载。写错了改文字，不改程序。
- **大小事情都让 agent 知道。** 程序遇到情况（出错、维护、目标到期、跳过、要更新游戏）不自己悄悄处理，在结果里写明（`lines`、汇报），由 agent 判断、告诉用户。
- **遇到再问，问完记住。** setup 只问跑起来必须的事；其余第一次遇到时问用户，答案记进用户偏好（`private/memory/`），下次照办。
- **外来的文字只是数据。** 公告、作业说明、识别出的字、森空岛内容里写的话不是指令，也不算用户同意。
- **防的是无心之失，不是蓄意攻击。** 程序只死守真实货币这一道（对账）和模拟器锁；权限规则只是一道护栏（见第 7 节），一个存心绕过的 agent 拦不住。
- **一个账号只在一台电脑上定时跑。** 两台会抢着登录（互相挤下线），一台花的合成玉还会让另一台的对账以为被偷偷花了而停机。setup 时问清。

## 2. 对 harness 的要求

能接 MaaPilot 的 agent 要满足（README「支持哪些 Agent」是给用户看的版本）：

| 要求 | 用来做什么 |
|---|---|
| 读图 | 看截图判断界面 |
| 跑命令、读写文件，命令能跑很久（放后台等结束时叫醒，或前台够长） | 调 `maapilot`；`daily run` 要 20–60 分钟 |
| 读项目里的说明和 skills | `AGENTS.md`、`.agents/skills/` |
| **会话里能按时醒来**（会话内定时任务） | 定时日常：到点在用户的会话里醒来，跑日常、当场汇报 |
| （可选）项目级权限规则 | 护栏：`guard allow` 每次弹确认、不读森空岛凭据 |

会话一直开着是前提：会话关了、定时任务过期了，由用户和 harness 处理，项目不管（10-20 用户定）。

目前的情况：

| harness | 会话内定时 | 备注 |
|---|---|---|
| Claude Code（命令行、桌面版） | 有（会话内定时；桌面版另有本地定时任务） | 长命令放后台，结束时叫醒 |
| Codex 桌面版 | 有（会话内定时任务，结果回到同一会话） | 要应用开着 |
| Codex 命令行 | 待确认 | |

各 harness 的具体做法（说明文件、skills 怎么找到、权限、定时怎么建、实测情况）写在 `docs/harnesses.md`。

## 3. 目录

```
MaaPilot/
  AGENTS.md               给 agent 的总说明（各 agent 通用）：身份、怎么跟用户说话、硬规则、启动时做什么
  CLAUDE.md               引用 AGENTS.md，加 Claude Code 专用的几条（后台叫醒、超时）
  .claude/settings.json   Claude Code 的权限、启动钩子（由 maapilot/policy.py 生成）
  .codex/rules/           Codex 的命令规则（同上生成）
  .agents/skills/         各 skill（见第 6 节；.claude/skills 是指向它的链接）
  maapilot/               Python 包：命令行工具
  bin/session-start       启动钩子：把 .venv/bin 加进会话的 PATH，再做启动检查
  templates/config.yaml   配置模板
  .tools/                 项目自己下载的 maa-cli、adb（不入库，`maapilot tools install`）
  private/                用户数据（不入库）：config.yaml、goals.md、快照、日志、汇报、用户偏好
  docs/                   本文稿、命令说明（cli.md）、harness 说明（harnesses.md）
```

## 4. 工具：`maapilot` 命令（输出一律 JSON）

| 命令 | 做什么 |
|---|---|
| `doctor` | 检查 adb、截图、分辨率、maa-cli 与资源、游戏是否在前台，逐项给修复建议；`--brief` 是会话开头的启动检查 |
| `tools status / install` | 项目 `.tools/` 里的 maa-cli、adb：装了什么、从官方发布下载安装（maa-cli 默认装实测过的版本） |
| `screen shot / ocr` | 截图、识别文字（截图存进 `private/shots/`，agent 用读图看） |
| `input tap / swipe / back / text` | 点击、滑动、返回、输入 |
| `maa <任务> [参数]` | 调 MAA：启动、公招、基建、信用商店、领奖励、刷关、剿灭、作业（copilot）；失败时当场截图。`maa why` 只读 MAA 的日志，把失败那段时间的事实整理出来，由 agent 判断 |
| `state depot / box / sanity` | 用 MAA 的识别读仓库、干员练度、理智，存一份带时间的快照 |
| `state skland`、`skland token / status` | 从森空岛直接读干员练度、理智、本周剿灭进度，不碰模拟器（`skland token` 由用户自己在终端里跑） |
| `copilot find / show / strip` | 查 MAA 作业站的作业，逐个干员对照练度标出缺谁、差多少（只摆事实） |
| `data …` | 游戏数据：同步；查养成消耗、合成配方、关卡掉率；活动关 MAA 能不能导航 |
| `calc need / craft / stages / event` | 纯计算：目标要多少材料、缺多少、哪些能合成、哪几关最省理智、活动关掉什么 |
| `goals list / add / remove / update / move / expire` | 目标清单的增删改；`move` 调先后 = 优先级 |
| `op upgrade / mastery`、`craft` | 养成、合成的快捷方式：能成就省事，失败了 agent 自己在界面上做 |
| `guard snapshot / check / allow` | 真实货币对账；`allow` 记下用户同意的一次花费 |
| `daily run` | 日常的固定流程：按配置依次跑 MAA 日常项，出错交回在场的 agent；跑完交给 agent 做目标部分（见第 7 节） |
| `daily done` | agent 交上这次日常目标部分的总结，程序放进汇报 |
| `report show / latest / seen` | 读汇报；记下哪次已经告诉过用户 |
| `news list / show / told` | 国服游戏内公告里的维护和活动日程；记下哪个时间点已经告诉过用户 |
| `game version / update` | 模拟器上的游戏版本和官网最新版；下载、覆盖安装（只有官服） |
| `reset [--dry-run] [--memory]` | 从头设置：`private/` 整个改名成备份（不删任何东西） |
| `dev rules / review` | 开发用：生成权限文件；看各 agent 哪里用错了命令 |

所有操作模拟器的命令自动拿锁，同一时间只有一个；只读的截图、识字不拿锁，随时能看。

## 5. 数据（`private/`）

| 文件 | 内容 |
|---|---|
| `config.yaml` | 模拟器地址、服务器、日常时间、要做的 MAA 日常项、没有目标时刷哪关（默认 1-7）、游戏更新后的用户协议能不能直接点同意 |
| `goals.md` | 进行中的目标（格式见下） |
| `goals-done.md` | 完成或放弃的目标，整段挪过来，写上结束日期和原因 |
| `memory/` | 用户偏好和对这个账号试出来的经验（一事一文件 + `MEMORY.md` 目录；不依赖哪个 agent 的记忆功能，启动检查带上目录） |
| `state/` | 仓库、干员、理智快照；剿灭的本周记录；官方公告缓存；外部数据源的格式健康（`sources.json`） |
| `guard/`、`lock.json` | 货币对账、模拟器锁（只由程序写） |
| `log/`、`reports/` | 每次运行、每次操作的记录；每次日常的汇报 |
| `setup.json` | setup 是否完成 |

**目标清单格式**（半结构化 Markdown）：目标的先后就是优先级（越靠前越高）。每个目标一个二级标题，下面固定 5 行字段（限时活动另有一行"期限"），程序只读这几行；
再往下是 agent 自由写的"分解与进展"。缺多少、刷几天这类会变的数不当事实存，每次用 `calc` 现算。

```markdown
## 遥 精2
- 状态：进行中
- 对象：干员 遥 → 精2 1级
- 原话：把遥练到精2（2026-09-30）
- 同意：2026-09-30 21:40，已报：龙门币约 59 万、经验约 36 万、聚合剂 2、提纯源岩 9…
- 花合成玉/源石：否

分解与进展（agent 自由写）：
- 缺辅助芯片，靠 PR-C 刷；合成要换加工站干员
- 10-01：合成做了一半，差聚合剂 2
```

"同意"那一行就是用户对这个目标所需花费的同意（哲学第 3 条），执行时不再问；"花合成玉/源石"为"是"的，每次执行到那一步仍要再问。
对象可以是 `干员`、`材料`、`关卡`、`活动`（限时活动，必须写期限）、`其他`；过了期限的目标"到期"，日常开头自动挪进 `goals-done.md`。

## 6. Skills

| skill | 什么时候用 | 写什么 |
|---|---|---|
| `maapilot-setup` | 第一次打开，或用户说要重新设置 | ① 模拟器：没装就按 MAA 文档引导；装好就找 ADB 地址、写配置。② `tools install` 装 maa-cli 和 adb，跑 `doctor` 排查到全部通过；试跑"启动游戏回首页"；读一次仓库和干员。③ 问日常：每天几次、几点；做哪些 MAA 日常项；没有目标时刷哪关。④ 写配置，**用 harness 自己的定时功能在会话里定好日常**（各家做法见 `docs/harnesses.md`），写 `setup.json` |
| `maapilot-daily` | 定时日常醒来时；用户说"跑一次日常" | 定时日常的整套做法（第 7 节）；有目标时按优先级策划理智和养成（`calc stages` 给建议），没有就刷默认关；更新目标进度；汇报格式和必说的内容 |
| `maapilot-goals` | 用户说要练谁、要打哪关、不要某个目标了；日常里核对进度 | 怎么分解：练度 → 材料；关卡 → 需要的练度 → 材料。能马上做完就直接做；不能就报"要花什么、够不够、差多少、大约几天"，用户同意后写入 |
| `maapilot-training` | 升级、精英化、升技能、专精、模组 | 页面路线、确认框上看什么、常见卡点；先试 `op` 快捷命令，失败就截图自己点；做完核对 |
| `maapilot-base` | 合成材料、制造站改产、基建 | 加工站路线、心情消耗、换心情满的干员、合成顺序；制造站改产和改回 |
| `maapilot-battle` | 打某一关、剿灭、推图、模组任务 | 先按练度判断打不打得过；找作业、自动编队 + 作业打；剿灭用 MAA；打完核对 3 星；打不过就说缺什么、问要不要加成长期目标 |
| `maapilot-events` | 日常开头或启动检查发现新公告、维护结束、活动开始；用户问活动、维护 | 维护后更新游戏、第一次启动、`data sync`；活动开始时读公告、查活动关和商店、对照目标，跟用户说值得拿什么、问要不要改安排；说完 `news told` |
| `maapilot-recovery` | MAA 任务失败、页面卡住、掉线 | 先看失败那一刻的截图和 `maa why` 的事实，自己判断原因；常见卡点（弹窗、公告、掉线重登、维护）；处理完让日常接着跑；处理不了写清卡在哪 |

每个 skill 末尾有"要问用户的情形"：用户没提过的花费、第一次遇到而记忆里没有答案的事。合成玉和源石不在任何 skill 里放行，永远单独问。

## 7. 运行方式

**首次启动**：Claude Code 的启动钩子跑 `maapilot doctor --hook`，没 setup 时在屏幕上提示"随便说一句就开始"；别的 harness 由 `AGENTS.md` 让 agent 自己先跑 `doctor --brief`。
用户一开口就进入 `maapilot-setup`。

**自由对话**：用户直接说要做的事，agent 加载对应 skill 去做，出错当场处理（`maapilot-recovery`）。例如"把某某练到精2" → `maapilot-goals` 判断能不能马上做，能就 `maapilot-training` 直接做，不能就报数后写进目标。

**定时日常**（harness 触发）：setup 时 agent 用 harness 的会话内定时，在用户的会话里定好每天的时间，提示词写明"这是定时日常，按 `maapilot-daily` 做"。到点 agent 在同一个会话里醒来：

1. 跑 `maapilot daily run`（长命令，按 harness 的方式放后台等结束）。程序做开头的检查（到期的目标挪走；公告旧了重拉，正在维护就不跑；游戏版本旧了交给 agent 更新），再按配置依次跑 MAA 日常项；
   某项失败就停下交回在场的 agent，agent 按 `maapilot-recovery` 处理后让它接着跑。
2. 固定流程跑完，agent 按 `maapilot-daily` 做目标部分：按目标的优先级用理智刷材料、做养成和合成、更新目标进度；用户提过的花费直接做，没提过的写进汇报"要你决定的事"。
3. agent 用 `daily done` 交上总结，程序前后货币对账、写汇报。
4. agent 在会话里把结果和目标进度告诉用户（按用户偏好决定详略），`report seen`。

用户可能在场也可能不在：**定时日常运行期间 `guard allow` 一律被程序拒绝**（程序自己记着日常在跑，不看 agent 怎么说），合成玉、源石的花费只能等用户回来、在对话里同意后再做。
怎么可靠地认出"这是定时日常期间"是实现时要先定的一点。

会话开头的启动检查列出进行中的目标、官方日程；还没告诉过用户的问题（`unseen_problems`）和到点还没说的日程（`news.due`），第一句就说。

**长期目标**：靠每天日常推进；材料够了就养成，完成或用户放弃就移除。

## 8. 审批与权限

- **合成玉、源石：每一次都问。** 用户在对话里同意后 agent 用 `guard allow` 记下（宿主再弹一次确认），对账时放行这一笔；没记下的减少 → 停掉日常、告诉用户。
  不因为以前同意过、记忆里有、目标里写了而省掉这一问。
- **其余资源和材料：用户提出过就做。** 加目标时报过数、用户同意了（"同意"那一行），执行这个目标要花的龙门币、经验、材料、理智都不再问；
  用户在对话里说过的（"快到期的理智液可以用"）记进用户偏好，照办。没提过的才问，问完记下。
- **改设置、改日常时间、从头设置：** 用户在对话里同意就做，程序不另设关卡。
- **权限规则只是护栏**（10-20 用户定，只留两条，开发后期再收）：`guard allow` 询问；`private/skland.json`（森空岛凭据）拒绝读写。
  `maapilot` 命令整个放行；不直接跑 `maa` / `adb`、不碰 `private/guard/` 和锁靠 `AGENTS.md` 硬规则、模拟器锁和对账。
  规则只在 `maapilot/policy.py` 写一份，`maapilot dev rules` 生成 `.claude/settings.json` 和 `.codex/rules/`。

## 9. 现状与迁移（10-20）

第 7 节的"harness 触发"是定下的方向，代码里还是旧的做法，按下面迁移（实测和步骤见 roadmap）：

| 旧的（还在代码里） | 去向 |
|---|---|
| 系统定时（`maapilot/oscompat/sched.py`、`launch.py`；`daily schedule / unschedule`） | 去掉，改由 harness 的会话内定时 |
| 定时日常里无头叫起 agent（`config.yaml` 的 `agent`、`maapilot/hosts.py` 的调用和读输出） | 去掉，醒来的 agent 自己做；`daily done` 保留 |
| 把结果推进会话（`codex queue`、记录会话编号）、`daily watch` | 去掉，结果本来就在会话里 |
| 靠"是不是被日常进程叫起的"认出无人值守（`maapilot/tools/confirm.py`） | 改成按"定时日常期间"拒绝 `guard allow`；`report seen` / `news told` 不再拦 |
| `host status / install` | 留下 Codex 信任文件夹这部分（规则要信任后才加载） |

迁移前先实测：Claude Code（这台 Mac）、Codex 桌面版（Windows）的会话内定时能不能在项目里醒来、跑长命令、守住项目规则；实测通过后实现，旧的系统定时留一个版本作备用。

## 10. 验收

1. 打开项目、启动 agent，说第一句话就进入 setup；确认模拟器、确认日常内容、定好定时后 setup 完成。
2. 自由对话里能做到：跑一次日常；练干员（等级、技能、专精）；合成指定材料；复杂任务（模组任务、剿灭）。
3. 定时日常到点在会话里醒来，按目标优先级做完，当场汇报；MAA 出错时 agent 当场接管。
4. 没经用户同意的合成玉、源石减少会被发现并停下；定时日常期间 `guard allow` 被拒绝。

## 11. 识别回归测试

别的测试都喂假的 OCR 结果；真机上的错多半出在识别这一层（首页理智 130 读成 29、专精确认框 57/15 读成 5715）。
`tests/test_screens.py` 在真机截图（`tests/fixtures/screens/`）上跑真的 MaaFramework OCR 和读页面的函数，对照人眼看出的答案。
不用模拟器：`maapilot.crew.core.ImageGame` 是不连设备的 Game，截图就是给的图片，点击只记在 `actions` 里，
`state.home_sanity`、`mastery.open_dialog`、`craft.read_count` 这类函数可以原样在图上跑。OCR 模型（`resource/model/ocr/`）不在时整个文件跳过。

**修识别的毛病，先加出错的截图**：

1. 找到出错那一刻的截图（`private/shots/`、`private/debug/`），用 `maapilot screen ocr --image <图> --roi …` 看 OCR 实际读出了什么。
2. 做成样本：`.venv/bin/python tests/fixtures/screens/make.py <图> <名> --keep X Y W H …`。只留被测函数读的区域，其余涂黑；
   做完用读图工具看一眼，留下的部分不能有昵称、ID（首页左边）、好友名。文件名带上正确答案（`home_130`、`craft_全新装置_16-16`）。
3. 在 `tests/test_screens.py` 加一条，断言正确答案，先看它失败；再改读法（不是改测试），全部样本都要过。
   一时修不了的标 `xfail(strict=True, reason=…)` 写清读成了什么；修好了它会报 XPASS，提醒去掉标记。
4. 改了读法的，合并后还要在模拟器上用只读命令核对一次（固定规则的识别要真机跑过才算数）。

**读法的共用件**（`maapilot/crew/reading.py`，不导入 MaaFramework，`tests/test_reading.py` 离线测）：
- 中文标签按词认：`Game.find_word(词, roi, vocab=…)` / `wait_word`，在已知词表里找编辑距离最近的（三个字以上差 1 个字也算，
  最近的不唯一就不认）。词表写同一处可能出现的别的词（全部配方名、全体干员名、训练位的几种状态、房间名），差一个字的别的词不会认错。
- "有/要"数字：原样读一遍，再二值化（`reading.binarize`，白底黑字）读几遍，`parse_have_need` 认斜杠的形近字，`vote` 投票；
  专精确认框还拿游戏数据里每一阶的"要"去对齐粘在一起的数字（`mastery.align_by_need`），切出来的"有"只用来认阶数，不用来算缺多少。
- 认得的遮挡（`maapilot/crew/overlays.py`）：`Game.wait` 等不到时先关掉表里的遮挡再等；只列点了不花东西的。
  进页面只走 `maapilot/crew/nav.py`（`goto`、`goto_room`），快捷操作里不自己点坐标导航。

## 12. 外部数据源

程序靠这些外部数据，格式随时会变（`maapilot/knowledge/sources.py`）：

| 来源（`sources.json` 的键） | 在哪解析 | 核对什么 | 对不上时 |
|---|---|---|---|
| 森空岛 `skland` | `crew/skland.py` | `chars[]`、`skills[]`、`equip[]`、`charInfoMap`；`status.ap`；`campaign.reward` | 练度：`state skland` 报错，日常改用 MAA 识别；理智：改用首页识别；剿灭：按自己的记录 |
| MAA 活动列表 `maa_activity` | `knowledge/maa_activity.py` | `<服务器>.sideStoryStage.*` 的 `Activity.UtcStartTime/UtcExpireTime`、`Stages[].Value` | 能解析的照用，导航不了时原因写明格式变了 |
| MAA 任务表 `maa_tasks` | 同上 | `{任务名: 对象}` | 活动关只认列表里写明的几关 |
| maa-cli 的输出 `maa_cli` | `tools/maa_cmd.py`、`tools/doctor.py` | Summary 任务行；刷图日志的 `Current sanity` / `Mission started`；`maa version` 的版本号 | 命令照常返回，`data.upstream_format` 写明 |
| 官方公告 `bulletin` | `knowledge/news.py` | 列表 `cid/title/displayTime/updatedAt/category`，全文 `content` 等 | 用上次的缓存，日常告警里写明 |
| 官网安装包地址 `game_download` | `tools/game.py` | 跳转后的文件名 `arknights-hg-<版本>.apk` | `game version` 报错，不更新 |
| 企鹅物流 `penguin`、一图流 `yituliu` | `knowledge/live.py` | matrix / stages、`data[].itemValueAp` | 不换缓存（旧的照用） |
| 游戏数据表 `gamedata` | `knowledge/datasync.py` | 每张表程序用到的字段（`TABLE_SCHEMA`） | 不替换本地的表 |

规矩：在解析的地方核对用到的字段，对不上抛 `UpstreamFormat`（错误码 `upstream_format`，消息写明来源和字段），不当成"没有数据"悄悄过去；
有正当退路的照样退，但要记下来。每个来源的健康记在 `private/state/sources.json`，`doctor --brief` 的 `sources` 和日常汇报的"要注意"里看得到；
用户的 agent 按 `maapilot-recovery` 用玩家的话告诉用户哪部分受影响，修是开发者的事。每个来源的真实响应样本在 `tests/fixtures/upstream/`，`tests/test_upstream.py` 核对。
已知的坑：森空岛的本周剿灭进度会滞后（换周后还报上周的数），不能无条件覆盖自己的记录（待修）。
