"""Windows 窗口设备层：用 MaaFramework 的 Win32Controller 驱动《明日方舟》PC 客户端。

和 adb 路线的关系：`crew/core.Game` 只认一个 MaaFw Controller，这里给的就是这个 Controller，
所以截图 / 识字 / 点击 / 滑动这些 Game 已经实现的能力不用改；只有 Windows 特有的准备工作
（找窗口、要管理员、要前台）放在这里。

2026-11-18 真机实测出来的三条约束：
- **游戏以管理员权限运行**：脚本不是管理员时 `PostMessage` / `SetCursorPos` 一律 `error=5`（UIPI）。
- Unity 的 flip 模型窗口抓不到画面（PrintWindow / FramePool 都失败），只能用
  `ScreenDC` / `DXGI_DesktopDup_Window` —— 所以**窗口必须在前台、不被遮挡**。
- 坐标是**客户区坐标**（1280x720），MaaFramework 自己加窗口偏移；别再手动转成屏幕坐标
  （实测会变成 (2039, 674) 出屏）。

这个模块不 import MaaFramework：只有 `make_controller()` 里才延迟导入，
这样 `doctor` 之类的命令能只靠 ctypes 查窗口。
"""
from __future__ import annotations

import ctypes
import time
import ctypes.wintypes as wt

WINDOW_CLASS = "UnityWndClass"          # Unity 的窗口类，明日方舟 PC 端就是它
PROCESS_HINT = "arknights"              # 进程名（不区分大小写）
MIN_W, MIN_H = 640, 360                 # 小于这个的窗口不是游戏主窗口（最小化时 Windows 会给 160x28）

_u = ctypes.WinDLL("user32", use_last_error=True)
_k = ctypes.WinDLL("kernel32", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)

_u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
_u.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
_u.GetClassNameW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
_u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_u.IsWindowVisible.argtypes = [wt.HWND]
_u.IsIconic.argtypes = [wt.HWND]
_u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_u.SetForegroundWindow.argtypes = [wt.HWND]
_u.SwitchToThisWindow.argtypes = [wt.HWND, wt.BOOL]
_u.GetForegroundWindow.restype = wt.HWND
_u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_u.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
_u.GetWindowTextLengthW.argtypes = [wt.HWND]
_k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k.OpenProcess.restype = wt.HANDLE
_adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
_adv.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]

TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20
TOKEN_INTEGRITY_LEVEL = 25
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
SW_MAXIMIZE = 3
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
_u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_void_p]


# --- 查窗口 ----------------------------------------------------------------

def _text(hwnd: int) -> str:
    n = _u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _cls(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _u.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd: int) -> int:
    pid = wt.DWORD()
    _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _proc_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:                                  # 权限不够 / 进程没了
        return ""


def windows() -> list[dict]:
    """所有 `UnityWndClass` 且进程名像明日方舟的窗口（含被最小化的）。"""
    out: list[dict] = []

    def _cb(hwnd, _lparam):
        if _cls(hwnd) != WINDOW_CLASS:
            return True
        pid = _pid(hwnd)
        name = _proc_name(pid)
        if not name.lower().startswith(PROCESS_HINT):
            return True
        r = wt.RECT()
        _u.GetWindowRect(hwnd, ctypes.byref(r))
        c = wt.RECT()
        _u.GetClientRect(hwnd, ctypes.byref(c))
        out.append({"hwnd": int(hwnd), "pid": pid, "process": name, "title": _text(hwnd),
                    "visible": bool(_u.IsWindowVisible(hwnd)), "minimized": bool(_u.IsIconic(hwnd)),
                    "window": (r.right - r.left, r.bottom - r.top), "client": (c.right, c.bottom)})
        return True

    _u.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb), 0)
    return out


def pick_window(hint: str | None = None) -> dict | None:
    """挑主窗口。最小的窗口（160x28）和 GetClientRect 为 0 的被最小化的窗口也算候选——
    它们**还原以后**才是 1280x720，所以这里只排优先级，不硬性过滤；是否可用由 prepare() 还原后再量。
    `hint` 是标题关键词（可选）。"""
    cands = windows()
    if hint:
        by_title = [w for w in cands if hint in w["title"]]
        cands = by_title or cands
    if not cands:
        return None

    def _score(w: dict) -> tuple:
        cw, ch = w["client"]
        usable = cw >= MIN_W and ch >= MIN_H
        # 先要有画面的（还原后能用的算同一档），再按面积大
        return (0 if usable or w["minimized"] else 1, cw * ch)

    cands.sort(key=lambda w: (not (w["client"][0] >= MIN_W), -w["client"][0] * w["client"][1]))
    return cands[0]


def describe() -> dict:
    """给 doctor / 报错用：找到的窗口和它们的尺寸。"""
    return {"windows": windows(), "picked": pick_window(), "admin": is_admin()}


# --- 权限 / 前台 ------------------------------------------------------------

IL_NAMES = {0x0000: "Untrusted", 0x1000: "Low", 0x2000: "Medium", 0x3000: "High", 0x4000: "System"}


def _integrity_level(tok) -> str:
    """令牌的完整性级别名（Low / Medium / High / System）。读不出来 → "?"。

    注意 `GetSidSubAuthority` 必须声明 restype=C_void_p：默认按 int 返回会把 64 位指针截断，
    再拿它去解引用就是访问冲突（进程直接崩，不是抛异常）。"""
    try:
        _adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        _adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        _adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]
        _adv.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD)
        size = wt.DWORD(0)
        _adv.GetTokenInformation(tok, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size))
        if not size.value:
            return "?"
        buf = ctypes.create_string_buffer(size.value)
        if not _adv.GetTokenInformation(tok, TOKEN_INTEGRITY_LEVEL, buf, size.value, ctypes.byref(size)):
            return "?"
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        cnt = _adv.GetSidSubAuthorityCount(ctypes.c_void_p(sid))[0]
        if not cnt:
            return "?"
        last = _adv.GetSidSubAuthority(ctypes.c_void_p(sid), cnt - 1)[0]
        return IL_NAMES.get(last, hex(last))
    except Exception:                                   # noqa: BLE001
        return "?"


def token_state() -> tuple[bool, str]:
    """(是否提权, 完整性级别)。**只看 TokenElevation 会骗人**：把 High 令牌的完整性级别降到 Low
    （沙箱就这么做）以后，TokenElevation 还是 1，但点击会被 UIPI 全拦掉。所以两个都要看。"""
    try:
        h = _k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, _k.GetCurrentProcessId())
        if not h:
            return False, "?"
        try:
            tok = wt.HANDLE()
            if not _adv.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
                return False, "?"
            try:
                val = wt.DWORD(0)
                size = wt.DWORD(0)
                elevated = bool(_adv.GetTokenInformation(tok, TOKEN_ELEVATION, ctypes.byref(val),
                                                         ctypes.sizeof(val), ctypes.byref(size)) and val.value)
                il = _integrity_level(tok)
                return elevated, il
            finally:
                _k.CloseHandle(tok)
        finally:
            _k.CloseHandle(h)
    except Exception:                                   # noqa: BLE001
        return False, "?"


def is_admin() -> bool:
    """能不能动游戏的窗口：要提权，而且完整性级别不能是 Low（Low 的令牌就算 TokenElevation=1 也点不动）。"""
    elevated, il = token_state()
    return elevated and il not in ("Low", "Untrusted", "?")


def require_admin() -> None:
    if not is_admin():
        elevated, il = token_state()
        if elevated and il == "Low":
            raise RuntimeError(
                f"进程被降到了低完整性（Low）：提权也是白提，点击会被 UIPI 全部拦掉。"
                f"用管理员身份重开终端 / 重开 worker（右键 -> 以管理员身份运行）")
        raise RuntimeError(
            "要管理员权限：游戏是提权运行的，普通权限的进程动不了它的窗口（UIPI，error=5）。"
            "用管理员身份重开终端，或走 install-admin-task.bat 注册的计划任务")


SW_MINIMIZE = 6
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
REF_W, REF_H = 1280, 720        # MAA 的参考分辨率：客户区必须正好是这个尺寸
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "Windows.UI.Core.CoreWindow"}


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    _u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def hide_occluders(game_hwnd: int) -> list[dict]:
    """把压在游戏窗口**上面**的窗口最小化，返回被最小化的（事后用 restore_occluders 还原）。

    ScreenDC 截的是屏幕区域：DSH 自己的界面、子进程弹的控制台只要盖住游戏，截到的就是它们。
    EnumWindows 是按 z 序从上到下的，走到游戏窗口就可以停：它下面的窗口不会挡住它。
    """
    box = _rect(game_hwnd)
    picked: list[dict] = []
    reached_game: list[bool] = []

    def _cb(hwnd, _lparam):
        hwnd = int(hwnd)
        if hwnd == game_hwnd:                          # z 序走到游戏了：下面的都不挡
            reached_game.append(True)
            return False
        if reached_game or not _u.IsWindowVisible(hwnd) or _u.IsIconic(hwnd):
            return True
        cls = _cls(hwnd)
        if cls in SHELL_CLASSES or cls.startswith("Windows.UI"):
            return True
        if not _overlaps(box, _rect(hwnd)):
            return True
        _u.ShowWindow(hwnd, SW_MINIMIZE)
        picked.append({"hwnd": hwnd, "title": _text(hwnd), "class": cls})
        return True

    _u.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb), 0)
    return picked


def restore_occluders(picked: list[dict]) -> None:
    """把 hide_occluders 最小化的窗口还原（不抢焦点）。"""
    for w in picked or []:
        try:
            _u.ShowWindow(int(w["hwnd"]), 4)           # SW_SHOWNOACTIVATE
        except Exception:
            pass


def input_selftest(timeout_s: float = 3.0) -> tuple[bool, str]:
    """合成鼠标输入到底有没有生效：建一个自己的小窗口，用 mouse_event 点它一下，看收到没有。

    远控断开 / 会话休眠时，桌面照常渲染（截图、OCR 都正常），但投出去的点击**不会到达任何窗口**——
    游戏里表现为"点了没反应"，MAA 任务却会照常报完成。这个自测就是为了把这种情况单独认出来。"""
    try:
        import tkinter as tk
    except Exception as e:                              # noqa: BLE001 - 没有 tkinter 就判不了
        return True, f"没装 tkinter，跳过自测（{e}）"
    got: list[tuple[int, int]] = []
    try:
        root = tk.Tk()
    except Exception as e:                              # noqa: BLE001 - 没有桌面环境
        return True, f"开不了窗口，跳过自测（{e}）"
    try:
        root.geometry("240x160+160+160")
        root.attributes("-topmost", True)
        root.update()
        x, y = 160 + 120, 160 + 80
        lab = tk.Label(root, text="click", font=("Arial", 14))
        lab.pack(expand=True)
        lab.bind("<Button-1>", lambda e: got.append((e.x_root, e.y_root)))
        root.update()
        time.sleep(0.2)
        if not _u.SetCursorPos(x, y):
            return False, "系统不接受 SetCursorPos：会话可能不接收合成输入（重连远控 / 唤醒本机会话）"
        _u.mouse_event(0x0002, 0, 0, 0, 0)              # MOUSEEVENTF_LEFTDOWN
        _u.mouse_event(0x0004, 0, 0, 0, 0)              # MOUSEEVENTF_LEFTUP
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not got:
            root.update()
            time.sleep(0.05)
        if got:
            return True, "合成点击能送到窗口"
        return False, ("点击投出去没有窗口收到：会话可能断开了远控 / 休眠了（截图和识字照常，但点不动）。"
                       "重连远控或在游戏里点一下确认")
    finally:
        try:
            root.destroy()
        except Exception:                               # noqa: BLE001
            pass


def fit_reference(hwnd: int) -> None:
    """把客户区调成 1280x720（窗口边框/标题栏的高度差算进去）。已经是这个尺寸就什么也不做。"""
    if client_size(hwnd) == (REF_W, REF_H):
        return
    left, top, right, bottom = _rect(hwnd)
    cw, ch = client_size(hwnd)
    bw, bh = (right - left) - cw, (bottom - top) - ch
    _u.SetWindowPos(hwnd, 0, left, top, REF_W + bw, REF_H + bh, SWP_NOZORDER | SWP_NOACTIVATE)


def client_size(hwnd: int) -> tuple[int, int]:
    c = wt.RECT()
    _u.GetClientRect(hwnd, ctypes.byref(c))
    return (c.right, c.bottom)


def restore(hwnd: int) -> None:
    """最小化时 Windows 只给 160x28，必须还原才有画面。"""
    if _u.IsIconic(hwnd):
        _u.ShowWindow(hwnd, SW_RESTORE)


def foreground(hwnd: int) -> bool:
    """把窗口拉到前台并确认。ScreenDC 截的是屏幕区域，窗口被挡就截不到游戏。

    别的窗口（比如子进程弹出来的控制台）抢到前台后，`SetForegroundWindow` 会被前台锁挡掉；
    先模拟一下 Alt 键（系统把"用户刚按过键"当成允许改前台的信号），再试，最多三次。"""
    if _u.GetForegroundWindow() == hwnd:
        return True
    _u.ShowWindow(hwnd, SW_RESTORE)
    for _ in range(3):
        _u.keybd_event(VK_MENU, 0, 0, None)
        _u.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)
        _u.SwitchToThisWindow(hwnd, True)
        _u.SetForegroundWindow(hwnd)
        if _u.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.35)
    return _u.GetForegroundWindow() == hwnd


def prepare(hint: str | None = None, *, need_input: bool = True) -> dict:
    """找窗口 → 还原（最小化时只有 160x28）→ 重新量 → 拉前台。找不到或还原后仍太小 → RuntimeError。

    need_input=False 时只保证"能截到"：不抢前台、不最大化。低权限进程（Low）截得到画面但点不动，
    这种时候只读命令（screen、state 里靠 OCR 的那些）还能用，点击类的命令会另外报错。
    """
    w = pick_window(hint)
    if not w:
        raise RuntimeError("没找到《明日方舟》PC 端的窗口：游戏开着的吗？（UnityWndClass + Arknights.exe）")

    if w["minimized"] or w["client"][0] < MIN_W:
        restore(w["hwnd"])                            # 还原后 GetClientRect 才是 1280x720
        w = pick_window(hint) or w

    true = pick_window(hint)
    if true:
        w = true
    # 客户区必须是 1280x720（MAA 的参考分辨率）：窗口一旦被最大化，客户区会变成 1920x1057 这种
    # 非 16:9 尺寸，MaaCore 直接 "Unsupported resolution" 拒绝连接（实测）。遮挡交给 hide_occluders()。
    if _u.IsZoomed(w["hwnd"]):
        _u.ShowWindow(w["hwnd"], SW_RESTORE)
        time.sleep(0.3)
    fit_reference(w["hwnd"])
    w["client"] = client_size(w["hwnd"])
    cw, ch = w["client"]
    if cw < MIN_W or ch < MIN_H:
        raise RuntimeError(f"窗口只有 {cw}x{ch}，不像游戏主画面：先点开游戏窗口再试")
    if not need_input:
        w["foreground"] = _u.GetForegroundWindow() == w["hwnd"]
        return w
    w["foreground"] = foreground(w["hwnd"])
    if not w["foreground"]:
        raise RuntimeError(
            "游戏窗口没抢到前台：ScreenDC 截的是屏幕区域，被别的窗口挡住会截错、点击也会点到别处。"
            "把挡在前面的窗口（包括 DSH 自己的界面、子进程弹出来的控制台）最小化，再重试")
    return w


# --- Controller -------------------------------------------------------------

def make_controller(hwnd: int, *, readonly: bool = False):
    """造一个能用的 Win32Controller。只读时不给输入方式（截图另开一条）。"""
    from maa.controller import Win32Controller
    from maa.define import MaaWin32InputMethodEnum as I, MaaWin32ScreencapMethodEnum as M

    # 截图只能用 ScreenDC：GDI 撕裂、PrintWindow 纯黑（Unity 的 flip 模型窗口，实测过）。
    # 所以窗口必须真的看得见 —— 挡在前面的窗口用 hide_occluders() 挪开。
    # 输入只能用 Seize：PostMessage 投过去的鼠标消息 Unity 不理（实测点了没反应），
    # 所以还得先把窗口抢到前台（prepare() 里做了）。
    method = I.Null if readonly else I.Seize
    return Win32Controller(hwnd, screencap_method=M.ScreenDC, mouse_method=method, keyboard_method=method)


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """客户区坐标 → 屏幕坐标。**只用于排查**：MaaFw 的 post_click 收的是客户区坐标。"""
    pt = wt.POINT(int(x), int(y))
    _u.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y
