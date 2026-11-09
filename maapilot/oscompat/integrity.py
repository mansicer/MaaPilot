"""Windows 的完整性级别（Mandatory Integrity Control）：本进程的级别、文件夹上的完整性标签。别的系统没有这回事，都返回 None。

为什么要查：DSH（DeepSeek Harness）的 Windows 沙箱模式要往工作区写东西时，会给工作区文件夹加"低完整性"标签（Low，
NW 不许往上写，子文件夹、文件都继承）。之后哪怕切成"完全权限"，标签也还留着；Windows 让从带"低"标签的文件夹里启动的程序
都以低完整性运行，所以项目里的 .venv Python、maa.exe 写不了项目外普通权限的地方（MAA 写不了 %LOCALAPPDATA%\\loong\\maa\\cache，
os error 5），这台电脑上所有 harness 跑 MaaPilot 都会失败。低完整性的进程改不回标签（只能往低改），要在普通权限的 shell 里去掉。
"""
from __future__ import annotations

from pathlib import Path

from maapilot.oscompat import WINDOWS

# 完整性级别 SID（S-1-16-<RID>）的 RID → 名字
LEVELS = {0x0000: "untrusted", 0x1000: "low", 0x2000: "medium", 0x2100: "medium_plus", 0x3000: "high", 0x4000: "system"}


def level_name(rid: int) -> str:
    """RID → 名字；不在表里的按区间归（低于 0x2000 都算 low 一类）。"""
    if rid in LEVELS:
        return LEVELS[rid]
    if rid < 0x1000:
        return "untrusted"
    if rid < 0x2000:
        return "low"
    if rid < 0x3000:
        return "medium"
    return "high" if rid < 0x4000 else "system"


def fix_command(folder: Path | str) -> str:
    """去掉文件夹低完整性标签的命令（PowerShell 里 (OI)(CI)M 要带引号，不然括号被当成表达式）。"""
    return f'icacls "{folder}" /setintegritylevel "(OI)(CI)M"'


def _sid_rid(ctypes, advapi, sid) -> int:
    n = ctypes.cast(advapi.GetSidSubAuthorityCount(sid), ctypes.POINTER(ctypes.c_ubyte))[0]
    return ctypes.cast(advapi.GetSidSubAuthority(sid, n - 1), ctypes.POINTER(ctypes.c_uint32))[0]


def _api():
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetSidSubAuthorityCount.restype = ctypes.c_void_p
    advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi.GetSidSubAuthority.restype = ctypes.c_void_p
    advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    return ctypes, wintypes, advapi, k32


def process_level() -> str | None:
    """本进程的完整性级别（"low" / "medium" / "high" …）；不是 Windows 或读不了 → None。"""
    if not WINDOWS:
        return None
    try:
        ctypes, wintypes, advapi, k32 = _api()
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
            return None
        try:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))                   # TokenIntegrityLevel
            buf = ctypes.create_string_buffer(size.value or 64)
            if not advapi.GetTokenInformation(token, 25, buf, ctypes.sizeof(buf), ctypes.byref(size)):
                return None
            sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]                            # TOKEN_MANDATORY_LABEL.Label.Sid
            return level_name(_sid_rid(ctypes, advapi, sid))
        finally:
            k32.CloseHandle(token)
    except Exception:                                   # noqa: BLE001 - 读不了就当不知道
        return None


def folder_label(folder: Path | str) -> dict | None:
    """文件夹上的完整性标签：{"level": "low" …, "inherited": 是不是从上层继承来的}；没有标签（默认就是普通级别）→ {"level": None}；
    不是 Windows 或读不了 → None。"""
    if not WINDOWS:
        return None
    try:
        ctypes, wintypes, advapi, k32 = _api()
        sacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
        err = advapi.GetNamedSecurityInfoW(str(folder), 1, 0x10, None, None, None,     # SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION
                                           ctypes.byref(sacl), ctypes.byref(sd))
        if err:
            return None
        try:
            if not sacl.value:
                return {"level": None}
            count = ctypes.cast(sacl.value + 4, ctypes.POINTER(ctypes.c_uint16))[0]          # ACL.AceCount
            for i in range(count):
                ace = ctypes.c_void_p()
                if not advapi.GetAce(sacl, i, ctypes.byref(ace)):
                    continue
                head = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))
                if head[0] != 0x11:                                                          # SYSTEM_MANDATORY_LABEL_ACE_TYPE
                    continue
                sid = ctypes.c_void_p(ace.value + 8)                                         # 头 4 字节 + Mask 4 字节
                return {"level": level_name(_sid_rid(ctypes, advapi, sid)), "inherited": bool(head[1] & 0x10)}
            return {"level": None}
        finally:
            if sd.value:
                k32.LocalFree(sd)
    except Exception:                                   # noqa: BLE001
        return None


def low_integrity(root: Path | str) -> dict | None:
    """项目文件夹被加上了低完整性标签，或者本进程就是低完整性：{"folder": 标签级别或 None, "process": 本进程级别, "fix": 命令}；
    都正常、不是 Windows、读不了 → None。"""
    if not WINDOWS:
        return None
    label = folder_label(root)
    proc = process_level()
    folder_low = bool(label and label.get("level") in ("low", "untrusted"))
    proc_low = proc in ("low", "untrusted")
    if not folder_low and not proc_low:
        return None
    return {"folder": (label or {}).get("level"), "process": proc, "fix": fix_command(root)}
