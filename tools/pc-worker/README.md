# PC 端提权通道（pc-worker）

给 **agent / harness** 用的一层薄封装：让没有管理员权限的 agent 能操控**以管理员权限运行**的
《明日方舟》PC 客户端。

## 为什么需要

游戏由鹰角启动器以管理员权限拉起，进程是 **High 完整性**。普通权限的进程去点击会被 UIPI 拦掉
（`error=5`，表现是"截图正常、点了没反应"，MAA 任务还会照样报 `Completed`）。
agent 自己拿不到提权，又不能每次点击都弹一次 UAC。

做法：**用户以管理员身份启动一次常驻 worker**，agent 往任务目录里丢命令、读结果，
真正的输入由提权进程投递。MaaPilot 的命令本身不用改——worker 只是替它换了个"父进程身份"。

## 安装（用户做一次）

1. 确认 MaaPilot 的运行环境已装好（仓库里有 `.venv\Scripts\python.exe`）。
2. 右键 `run-worker.bat` → **以管理员身份运行**，窗口留着别关。
3. 看 `private/worker/status.json`：`"admin": true` 且 `"il": "High"` 才算可用。

> 计划任务（`schtasks /create /rl highest`）实测**可能**起出 `il: "Low"` 的进程，建完要核实 `il`。

## agent 怎么用

```sh
python tools/pc-worker/submit.py screen shot
python tools/pc-worker/submit.py maa copilot prts://73344
python tools/pc-worker/submit.py --timeout 600 state box
```

- 退出码：`0` 命令成功；`1` 命令跑完但失败；`2` 等不到结果（会自动删掉任务文件，免得重启后被重跑）。
- worker 不是 High 时 `submit.py` 直接拒绝，并告诉用户怎么重启。
- 长命令（`daily run`、`maa copilot`）用 `--timeout` 放宽，别用默认 180 秒。
- 先跑 `submit.py doctor --brief` 确认 `input` 一项是 OK，再操作游戏。

## 协议

```
<repo>/private/worker/
  jobs/<id>.json        {"argv": ["screen","shot"], "timeout_s": 120, "cwd": "可选"}
                        argv 交给 <.venv>/Scripts/python.exe -m maapilot.cli；
                        argv[0] 以 .py 结尾时直接跑那个脚本。
  jobs/<id>.done.json   {"id","ok","code","stdout","stderr","argv","started","ended"}
  status.json           {"pid","admin","il","heartbeat","done"}
  worker.log
```

要点：

- **用临时文件接 stdout/stderr**，不用管道。管道会被孙进程（MaaCore、adb）占住，
  子进程被杀之后 `communicate()` 可能永远不返回——这是最坑的一种"worker 卡死"。
- 超时用 `taskkill /F /T` **杀整棵进程树**，杀完写 `.done.json`（`code: -9`）。
- 心跳线程每 5 秒写一次 `status.json`，这样"忙"和"死"能分开判断。
- 任务文件名唯一即可；`.done.json` 存在就跳过，所以**卡住时要把挂起的 `<id>.json` 删掉再重启**，
  否则重启后会被重跑一遍。

## 低完整性（`il: "Low"`）——提权了也点不动

`IsUserAnAdmin()` / `TokenElevation` 对**降过完整性**的令牌仍然返回 true，但 UIPI 照样拦输入。
常见来源：DSH / Claude Code 的沙箱会给工作区文件夹加 Low 标签（`icacls` 里能看到
`Mandatory Label\Low Mandatory Level`），从那里启动的程序都变 Low。

修（需要管理员）：

```powershell
icacls "<项目文件夹>" /setintegritylevel "(OI)(CI)M"
icacls "<项目文件夹>\.venv\Scripts\python.exe" /setintegritylevel M
```

改完重启 worker，`status.json` 的 `il` 应该是 `High`。

## 已知限制

- 同一时间只该有一个 worker（多个会抢同一个 `jobs/` 目录，结果互相覆盖）。
- worker 窗口关掉就没了；系统休眠唤醒后进程可能不响应，需要重启。
- 这里只是"换个身份跑 maapilot"，MaaPilot 自己的 PC 端限制（ScreenDC 截图、Seize 输入、
  `maa copilot --formation` 的问题）都还在，见 `docs/pc-client.md`。
