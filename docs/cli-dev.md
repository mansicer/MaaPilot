# `maapilot` 命令的开发文档

给开发者（和开发用的 agent）的：权限规则怎么生成、开发用的命令、反馈的上传格式、命令归谁负责。
**用户的 agent 不用读这个文件**：命令怎么用都在 [`cli.md`](cli.md) 里。设计依据见 [CLAUDE_DEV.md](../CLAUDE_DEV.md)「设计哲学」。

## 命令归谁负责

`maapilot/tools/` 下的命令组归 A（工具），`maapilot/flows/` 下的归 B（流程）；`maapilot/cli.py` 的 `GROUPS` 是全部命令组。
B 的命令：`daily done`、`daily run / status`、`daily unschedule`、`goals list / add / update / remove / move / expire`、`op upgrade / mastery / progress`、`craft`、`report show / latest / list / write`、`reset [--dry-run] [--memory]`。其余都是 A 的。

## dev —— 开发用（不给用户的 agent）

`maapilot dev rules [--check] [--root <目录>]`：按 `maapilot/policy.py` 生成两个宿主的权限规则文件和本文件里抄的设置（见下面「Claude Code 权限设置」）。
`data`：`{"files": [{"path", "changed"}], "written"}`；`--check` 只核对、不写，有不一样的退出码 1 `rules_out_of_date`（`data.files` 照样带上）。

`dev` 下的命令都不加权限规则（用户的 agent 用会弹确认），skill、`AGENTS.md` 和 `docs/harnesses/` 里不提，`maapilot --help` 里不列（`tests/test_docs_consistency.py` 的 `DEV_ONLY`）。

## 反馈的上传格式

客户端 `maapilot/tools/feedback.py`，接收端在另一个仓库 [mansicer/maapilot-feedback](https://github.com/mansicer/maapilot-feedback)（Cloudflare Worker + R2）。
两边的格式要一起改：接收端的测试用客户端真实生成的请求体，改了格式就重新生成那份样例。

**发送**：把 `bundle/` 打成 zip，`multipart/form-data` POST 到 `https://maapilot.sicer.top/feedback`（环境变量 `MAAPILOT_FEEDBACK_URL` 可以指到别处，开发用），
两个字段：`manifest`（JSON：`schema, id, created_at, category, maapilot, digest, bytes, preview, sha256, files`；`preview` 是脱敏后的用户原话开头 80 个字，服务器通知开发者时显示）和 `bundle`（zip）；
请求头 `X-MaaPilot-Feedback: 1`。服务器回 `{"ok": true, "id": "<同一个编号>"}` 才算发出去了（别的 2xx 回复不算）；同一份内容重发，服务器认得出、照样回成功。成功后 `data`：`{sent, id, server_id, sent_at, bytes}`，草稿标成 `sent`。

接收端的检查（任何一项不过都不落盘）：请求头和 multipart；大小不超过 5 MB；同一个 IP 60 秒内不超过 5 份（429 `rate_limited`）、当天不超过 200 份（429 `daily_limit`）；
清单的版本、编号格式和日期（离当天超过 3 天不收，所以客户端 48 小时后就不发了）、类别、文件名白名单；包是 zip、大小和 sha256 与清单一致、里面的文件和清单列的一样。
同一个编号原样重发回 200（`duplicate: true`），换了内容回 409 `id_taken`。4xx 带 `error` 的客户端报 `rejected`（不让 agent 原样重试）。

## Codex 命令规则

`.codex/rules/maapilot.rules`（入库，Starlark 的 `prefix_rule`）和下面 Claude Code 的权限出自同一份规则 `maapilot/policy.py`，由 `maapilot dev rules` 生成（见「Claude Code 权限设置」；不要手改）：
放行 → `allow`（在沙箱外跑），询问 → `prompt`，拒绝 → `forbidden`：`prompt` 只有 `guard allow`，没有 `forbidden`（10-20 用户定：权限只留两条，另一条是森空岛凭据文件，只有 Claude Code 拦得住）。
规则按命令的第一个词认程序：maapilot 的几种写法（`maapilot`、`.venv/bin/maapilot`、`maapilot.exe`、`.venv\Scripts\maapilot.exe`、`.venv/Scripts/maapilot.exe`）
放行 / 询问都一样。macOS 上 Codex 还会把绝对路径的程序按程序名认（`execpolicy check --resolve-host-executables` 实测），
Windows 上 Codex 经 PowerShell 跑命令，规则看到的是不是这样的写法还没核实。没列的命令在沙箱里跑（只能写仓库、没有网络，连不上模拟器）。
只有 Codex 信任这个文件夹时才加载（`host install codex`；`host status` 的 `hosts.codex.trusted`）。

**在 Codex 里防得住什么**：防得住 agent 的无心之失（用错命令、绕过 maapilot 直接点、记错同意），防不住一个被注入了指令、存心绕过的 agent：
- 规则只管命令，管不了文件：沙箱里写得了 `private/guard/`、`private/lock.json`，读得了 `private/skland.json`（Claude Code 是拒绝编辑 / 读取）；只靠 AGENTS.md 硬规则；
- 放行的 maapilot 命令在沙箱外跑，而仓库里的代码（`maapilot/`）沙箱里也写得了：改过的代码会以沙箱外的权限运行。
真实货币的对账照样在每次日常前后跑，花掉的会被发现（但不是拦在花之前）。

`daily run` 把锁交给它起的子进程用的环境变量 `MAAPILOT_LOCK_TOKEN` 名字里带 `TOKEN`：子进程是 `daily run` 自己起的，不经过 Codex 的环境变量策略，不受影响。

## Claude Code 权限设置

`.claude/settings.json`（入库，下面是它的全文；`tests/test_docs_consistency.py` 检查 skill 里写的每条命令都有放行或询问的规则）。

**权限规则只写一份**：`maapilot/policy.py`（每个 maapilot 子命令前缀的放行 / 询问 / 拒绝、别的程序 maa / adb / sleep、只有 Claude Code 有的文件规则 Read / Edit / WebFetch）。
`maapilot dev rules`（开发用）按它生成三处：`.claude/settings.json` 的 `permissions`（别的部分如 `hooks` 原样保留）、整份 `.codex/rules/maapilot.rules`、
本节下面抄的 settings.json 全文；`--check` 只核对，不一样就退出码 1 `rules_out_of_date`（`tests/test_policy.py` 也核对，并检查两边的结论一致）。这三处都不要手改。
两边的差别：Claude Code 的放行 / 询问只列本名 `maapilot`、拒绝的列全部写法，Codex 每条都列全部写法；`sleep 300` 在 Claude Code 里精确匹配、在 Codex 里是前缀；文件规则只有 Claude Code 有。

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(maapilot:*)",
      "Bash(sleep 300)",
      "Read(private/**)",
      "Edit(private/config.yaml)",
      "Edit(private/goals.md)",
      "Edit(private/goals-done.md)",
      "Edit(private/setup.json)",
      "Edit(private/shots/**)",
      "WebFetch(domain:prts.maa.plus)",
      "WebFetch(domain:prts.plus)",
      "WebFetch(domain:maa.plus)"
    ],
    "ask": [
      "Bash(maapilot guard allow:*)"
    ],
    "deny": [
      "Read(private/skland.json)",
      "Edit(private/skland.json)"
    ]
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/bin/session-start",
            "timeout": 300
          }
        ]
      }
    ]
  }
}
```

- `maa why` 只读，在 `maapilot maa:*` 的放行里。`news …` 只联网、写自己的缓存；`game update` 要下载、覆盖安装游戏，
  但只装官网的包（地址只认官方域名），所以都放行。`sleep 300` 是会话里掉线时等用户回复的 5 分钟计时器（skill `maapilot-recovery`「掉线、被登出」，后台运行）。
- 只有 `guard allow`（记下花合成玉 / 源石的同意）是 **ask**：真实货币是程序唯一死守的一道（见「命令总览」），再弹一次宿主的确认（远程也能点）；
  有日常在跑或没收尾时命令自己也拒绝，换了宿主也一样。
- `daily unschedule`、`reset`、`host install`、`lock release`、改 `private/config.yaml` 都放行：agent 在对话里问过、用户同意了就做（AGENTS.md 硬规则），
  程序不另设关卡。`reset` 日常在跑、锁被占着时自己不做；`lock release` 只删残留的锁。
- `maapilot` 整个放行（`Bash(maapilot:*)`），只列本名（启动钩子把 `.venv` 的程序目录放进了 PATH），别的写法按默认弹确认。
  直接跑 `maa` / `adb`、改 `private/guard/` 和锁都没有规则：靠 AGENTS.md 硬规则、锁和对账（10-20 用户定，开发后期再收）。
- 森空岛凭据：`private/skland.json` agent 读不了、改不了（拒绝；Codex 拦不住读文件，见「Codex 命令规则」）。
  `skland token` 让用户自己在终端里跑是 AGENTS.md 的约定，不是权限规则。
- `guard snapshot` 可以放行：上次对账之后跑过可能花东西的命令时它改做对账（见「guard」），不能拿来抹掉花费。
- 启动钩子 `bin/session-start` 用 `$CLAUDE_PROJECT_DIR` 定位（不依赖钩子的工作目录）：先往 `$CLAUDE_ENV_FILE` 写一行把 `.venv/bin` 加进 PATH（环境还没建也写，setup 建好后同一会话就能用），再跑 `doctor --hook`：内容同 `--brief`，但输出成 Claude Code 钩子的格式 `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "MaaPilot 启动检查…<--brief 的信封>"}}`（直接输出的 JSON 会被当成钩子的控制字段，不进会话），退出码总是 0。
  启动检查自己出错时也是这个格式、退出码 0：`additionalContext` 写出错的原因，让 agent 自己跑一次 `doctor --brief`。
  `next` 是 `setup` / `guard` 时另带顶层的 `systemMessage`（直接显示给用户的一行）：钩子不会让 agent 先开口，要用户先说一句，这一行告诉用户"随便说一句就开始"。
  `.venv` 还没建时钩子自己按钩子的格式输出（`no_env`），agent 按 `maapilot-setup`「装运行环境」帮用户建。
- 启动钩子的输出进会话上下文；`AGENTS.md` 让 agent 看到 `next: "setup"` 时，用户一开口就进入 `maapilot-setup`。
