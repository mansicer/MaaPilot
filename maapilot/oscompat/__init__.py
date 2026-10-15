"""操作系统相关的都在这里（macOS / Linux / Windows），别处只调用这里的函数，不自己判断系统。

- procs：起进程、结束整棵进程树、查父进程链 / 子孙进程、按名字找进程、UTF-8 地跑子进程
- venv：项目 .venv 里的程序（bin/ 或 Scripts\\）、系统 PATH
- fs：UTF-8 读文本、原子写（Windows 上文件被占着时重试）、路径比较、目录链接、读图
- sched：定时日常的调度后端（cron / 假后端；Windows 任务计划程序后面加）
- hints：各系统的安装提示、模拟器常见端口
"""
import sys

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
