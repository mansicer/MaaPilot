# 各 harness 怎么接 MaaPilot

MaaPilot 只做和游戏相关的部分（`AGENTS.md`、skills、`maapilot` 工具）；定时、叫醒会话、通知用户由 agent 所在的 harness 提供（CLAUDE_DEV.md「设计哲学」第 1 条）。
`AGENTS.md` 和 skills 只写各 harness 通用的做法；每个 harness 自己的做法（定时工具叫什么、长命令怎么等、权限、setup 时要多做什么）写在这个目录里它自己的文件。
接一个新的 harness 要改哪些文件，见最后一节「接一个新的 harness」。

| harness | 文件 |
|---|---|
| Claude Code（命令行、桌面版） | [`claude-code.md`](claude-code.md) |
| Codex（桌面版、命令行） | [`codex.md`](codex.md) |
| OpenClaw | [`openclaw.md`](openclaw.md) |
| pi | [`pi.md`](pi.md) |

## 要 harness 提供什么

| 要求 | 用来做什么 |
|---|---|
| 读图 | 看截图判断界面 |
| 跑命令、读写文件，命令能跑很久（放后台等结束时叫醒，或前台够长） | 调 `maapilot`；`daily run` 要 20–60 分钟 |
| 读项目里的说明和 skills | `AGENTS.md`、`.agents/skills/` |
| **能按时叫醒 agent**（定时任务） | 定时日常：到点醒来，跑日常、汇报 |
| （可选）项目级权限规则 | 护栏：`guard allow` 每次弹确认、不读森空岛凭据 |

1–3 是在对话里用的前提，第 4 条是定时日常的前提。harness 一直运行是前提：会话关了、定时过期了，由用户和 harness 处理，项目不管。

## 定时日常怎么定（通用）

- **一次性定时接力**（有一次性定时、而且可靠的都这样）：始终只留一个"下一次日常"的一次性定时，时间是 `config.yaml` 的 `schedule` 里离现在最近的下一个时间点。
  setup 时建第一个；每次定时日常醒来、跑 `daily run` 之前先建下一个，这样这次出错、没收尾，下一次也照样会来。改时间：删掉还没到的那个，建新的。
  建之前先列一遍已有的定时，别建重复的。
- **每天重复的定时**（没有一次性定时、或一次性的不可靠）：每个时间点建一个，醒来时只确认各时间点都在、不再建；会过期的，在 harness 自己的文件里写清多久要续。
- **提示要自足**：到点时前面的对话可能已经被压缩掉了，单看这一句就要知道做什么。推荐：
  `定时日常（04:40）：按 skill maapilot-daily 做；先定好下一次`（括号里写这一次的时间；用重复定时的把"先定好下一次"换成"这是每天重复的定时，不用再建"）。
- 分钟别选 :00、:30（有的 harness 会提前一两分钟叫醒）；04:40、16:40 这样的就好。
- 同一时间只在一个地方定（一个会话或一个 harness 的任务列表），同一个账号只在一台电脑上定。

## 接一个新的 harness

### 要新增、修改的文件

**一定要改的**

- **新增 `docs/harnesses/<名字>.md`**：这个 harness 的全部做法都写在这里，照 `claude-code.md` 的样子：
  - 开头一张表：说明文件（它读哪个文件）、skills（怎么找到）、启动检查（有没有启动钩子，没有就靠 `AGENTS.md` 让 agent 自己跑）、权限（有没有项目规则、拦得住什么）、PATH（`maapilot` 怎么写）、定时（用什么工具、一次性还是重复）、长命令（能不能放后台等它叫醒）。
  - 「定时日常」：工具叫什么、怎么建、怎么列、怎么删，提示怎么写，有什么坑（过期、提前或推迟响、要应用开着）。
  - 「setup 时要多做的」（有才写）：例如信任文件夹、批准联网、注册 agent。
  - 「实测」：测过什么、哪天、结论；没测的标"待实测"。
- **改 `docs/harnesses/README.md`**：开头的对应表加一行。
- **改 `AGENTS.md`**：「你所在的 harness」的对应表加一行。别的地方不写这个 harness 的名字和做法。
- **改 `README.md`**（给用户看的）：
  - 「支持哪些 Agent」的表加一行（对话里用、定时日常支不支持）；
  - 开头的徽章、介绍和"需要"里的 agent 列表；
  - 加一节"用 XX"：怎么在项目文件夹里打开它、第一次要点什么、定时日常有什么前提。

**一般不改的**

- **`.agents/skills/*/SKILL.md`**：skills 只写通用规则。这个 harness 和通用规则对不上时，把通用规则补全（例如"没有一次性定时、或一次性的不可靠的，用每天重复的定时"），不写"某某例外"；它独有的做法写进 `<名字>.md`。
- **日常汇报的格式**：所有 harness 都一样，不为某个 harness 改。

**harness 不读我们的文件时**

- **新增说明文件**（例如 Claude Code 读的 `CLAUDE.md`）：harness 不读 `AGENTS.md`、只读别的文件名时才要。内容只有一行引入 `AGENTS.md`，不加专用内容。
- **新增 skills 链接**（例如 `.claude/skills`）：harness 不读 `.agents/skills/` 时才要，建一个指向 `.agents/skills` 的链接。Windows 上 git 可能没建成链接，在 `maapilot/tools/doctor.py` 里让 `doctor --fix` 补上，像 `skills_link` 那一项。

**harness 往项目里写东西、或要用户改它的配置时**

- **改 `.gitignore`**：harness 在项目文件夹里写本机状态的（像 Codex 的 `.codex/config.toml`），加进去，别提交到仓库。
- **新增 `templates/<名字>/`**：接入要用户改 harness 自己的配置时（例如注册 agent、建定时任务的配置片段），片段和说明放这里，由用户或 agent 照着做；项目不替用户改它的配置。

**harness 有项目级权限规则时**（例如 Claude Code 的 `.claude/settings.json`、Codex 的 `.codex/rules/`）

- **改 `maapilot/policy.py`**：加上生成这个 harness 规则文件的部分，按 `CLAUDE_DEV.md` 的做法重新生成；`tests/test_policy.py` 核对。

**规则要用户先在 harness 里做一步才生效时**（例如 Codex 要先信任这个文件夹）

- **改 `maapilot/hosts.py`**：加上它的名字和常见安装位置，让程序找得到它。
- **改 `maapilot/tools/host_cmd.py`**：让 `host install <名字>` 替用户做这一步（agent 先在对话里问过用户）。
- **跟着改**：`docs/cli.md` 的「host」一节，`tests/test_hosts.py`。

只靠读 `AGENTS.md`、skills 就能用的 harness，这两组都不用动。

定时不写进代码（设计哲学第 1 条）：定时由 harness 自己建，项目只在 `<名字>.md` 里写做法。

### 步骤

1. **核对要求**：对照上面「要 harness 提供什么」。没有定时的只能在对话里用。
2. 按上面「要新增、修改的文件」加文件、改文件。`docs/harnesses/<名字>.md` 里先把还没测的标"待实测"。
3. **实测**，结论写进 `<名字>.md`：
   - 定时按时醒来：有没有一次性的、可不可靠，重复的会不会过期；
   - `maapilot` 命令放行、`guard allow` 弹确认（拦不住的写明）；
   - 长命令怎么等结束；读图；
   - 真跑一次完整的定时日常。
4. 全部测试通过（`tests/test_docs_consistency.py` 也会检查 `<名字>.md` 里写的 `maapilot` 命令）。
