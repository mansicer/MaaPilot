"""oscompat.fs / venv / cli 的编码与原子写：各系统都该成立的行为（在 macOS / Linux 上也能测的部分）。"""
import json
import os
import sys

import pytest

from maapilot import cli, paths
from maapilot.flows import goals
from maapilot.oscompat import WINDOWS, fs, venv


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# --- 原子写 ---------------------------------------------------------------------------------------------------------

def test_atomic_write_leaves_no_tmp_and_writes_lf(tmp_path):
    p = tmp_path / "sub" / "a.json"
    fs.atomic_write_text(p, "一\n二\n")
    fs.atomic_write_text(p, "三\n")
    assert p.read_bytes() == "三\n".encode("utf-8")
    assert sorted(x.name for x in p.parent.iterdir()) == ["a.json"]


def test_atomic_write_keeps_crlf_when_asked(tmp_path):
    p = tmp_path / "a.md"
    p.write_bytes(b"x\r\ny\r\n")
    fs.atomic_write_text(p, "x\nz\n", newline=fs.newline_of(p))
    assert p.read_bytes() == b"x\r\nz\r\n"


def test_atomic_write_failure_removes_tmp(tmp_path, monkeypatch):
    p = tmp_path / "a.txt"

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(fs.os, "replace", boom)
    with pytest.raises(OSError):
        fs.atomic_write_text(p, "x")
    assert list(tmp_path.iterdir()) == []


def test_retry_only_on_windows(monkeypatch):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(32, "被占用")
        return "ok"
    monkeypatch.setattr(fs, "RETRY_S", 0)
    monkeypatch.setattr(fs, "WINDOWS", True)
    assert fs._retry(flaky) == "ok" and len(calls) == 3
    calls.clear()
    monkeypatch.setattr(fs, "WINDOWS", False)
    with pytest.raises(PermissionError):
        fs._retry(flaky)
    assert len(calls) == 1


def test_safe_unlink(tmp_path):
    p = tmp_path / "x"
    fs.safe_unlink(p)                    # 不存在不算错
    p.write_text("1", encoding="utf-8")
    fs.safe_unlink(p)
    assert not p.exists()


def test_newline_of(tmp_path):
    p = tmp_path / "n"
    assert fs.newline_of(p) == "\n"
    p.write_bytes(b"abc")
    assert fs.newline_of(p, "\r\n") == "\r\n"
    p.write_bytes(b"a\r\nb\n")
    assert fs.newline_of(p) == "\r\n"
    p.write_bytes(b"\na\r\n")
    assert fs.newline_of(p) == "\n"


# --- 读 -------------------------------------------------------------------------------------------------------------

def test_read_text_utf8_strips_bom_and_crlf(tmp_path):
    p = tmp_path / "b.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{"a": "中"}\r\n'.encode("utf-8"))
    assert fs.read_text_utf8(p) == '{"a": "中"}\n'
    from maapilot.tools import common
    assert common.read_json(p) == {"a": "中"}


def test_read_text_utf8_gbk_is_bad_encoding(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_bytes("服务器: 官服\n".encode("gbk"))
    with pytest.raises(fs.BadEncoding) as e:
        fs.read_text_utf8(p)
    assert "UTF-8" in str(e.value) and isinstance(e.value, ValueError)
    from maapilot.tools import common
    assert common.read_json(p, "d") == "d"       # 读 JSON 时照旧当读不到


def test_config_gbk_is_clear_error(tmp_path):
    from maapilot import config
    from maapilot.cliutil import CliError
    p = tmp_path / "config.yaml"
    p.write_bytes("emulator:\n  address: 模拟器\n".encode("gbk"))
    with pytest.raises(CliError) as e:
        config.load(p)
    assert e.value.code == "invalid_config" and "UTF-8" in e.value.message


def test_config_with_bom(tmp_path):
    from maapilot import config
    p = tmp_path / "config.yaml"
    p.write_bytes(b"\xef\xbb\xbf" + "emulator:\n  address: 127.0.0.1:16384\n".encode("utf-8"))
    assert config.load(p)["emulator"]["address"] == "127.0.0.1:16384"


# --- 路径比较 -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,same", [
    (r"C:\x", "c:/x", True),
    (r"C:\x", r"\\?\C:\x", True),
    (r"\\?\c:\X", "C:/x/", True),
    (r"C:\Users\Me\MaaPilot", "c:/users/me/MaaPilot/", True),
    (r"C:\a\..\x", r"C:\x", True),
    (r"C:\x", r"D:\x", False),
    (r"C:\x", r"C:\x\y", False),
])
def test_same_path_windows_rules(monkeypatch, a, b, same):
    import ntpath
    monkeypatch.setattr(fs, "_path", ntpath)
    assert fs.same_path(a, b) is same


def test_same_path_long_prefix_on_this_os():
    assert fs.same_path("\\\\?\\" + str(paths.ROOT), paths.ROOT)


def test_same_path_posix():
    assert fs.same_path("/a/b/../c", "/a/c")
    assert fs.same_path("/a/c/", "/a/c")
    if not WINDOWS:
        assert not fs.same_path("/A/c", "/a/c")      # macOS 的盘可能不分大小写，但比较按字面（和 os.path 一致）


# --- 目录链接、读写图 -------------------------------------------------------------------------------------------------

@pytest.mark.skipif(WINDOWS, reason="Windows 上是目录联接，在真机上测")
def test_make_dir_link_symlink(tmp_path):
    target = tmp_path / ".agents" / "skills"
    (target / "s1").mkdir(parents=True)
    (target / "s1" / "SKILL.md").write_text("x", encoding="utf-8")
    link = tmp_path / ".claude" / "skills"
    link.parent.mkdir()
    link.write_text("旧的文件", encoding="utf-8")    # 已有的文件先删
    assert fs.make_dir_link(link, target) == "symlink"
    assert link.is_symlink() and os.readlink(link) == os.path.join("..", ".agents", "skills")
    assert (link / "s1" / "SKILL.md").read_text(encoding="utf-8") == "x"
    assert fs.make_dir_link(link, target) == "symlink"  # 再做一次：替换旧链接


def test_imread_imwrite_non_ascii_path(tmp_path):
    np = pytest.importorskip("numpy")
    img = np.zeros((4, 6, 3), dtype=np.uint8)
    img[1, 2] = (10, 20, 30)
    p = tmp_path / "截图 目录" / "模板.png"
    p.parent.mkdir()
    assert fs.imwrite(p, img)
    back = fs.imread(p)
    assert back.shape == (4, 6, 3) and tuple(back[1, 2]) == (10, 20, 30)
    assert fs.imread(tmp_path / "没有.png") is None
    (tmp_path / "空.png").write_bytes(b"")
    assert fs.imread(tmp_path / "空.png") is None


def test_clears_templates_load():
    from maapilot.crew import clears
    clears._template.cache_clear()
    names = [p.stem for p in clears.TEMPLATES.glob("*.png")]
    if not names:
        pytest.skip("没有模板")
    assert clears._template(names[0]).ndim == 3


# --- goals.md 保留换行 ----------------------------------------------------------------------------------------------

SAMPLE = ("# 目标清单\r\n\r\n## 遥 精2\r\n- 状态：进行中\r\n- 对象：干员 遥 → 精2 1级\r\n- 原话：把遥练到精2\r\n"
          "- 同意：2026-09-30 21:40，已报：龙门币约 59 万\r\n- 花合成玉/源石：否\r\n\r\n笔记\r\n")


def test_goals_keeps_crlf(capsys):
    paths.GOALS.parent.mkdir(parents=True, exist_ok=True)
    paths.GOALS.write_bytes(b"\xef\xbb\xbf" + SAMPLE.encode("utf-8"))
    goals.save(goals.load())
    assert paths.GOALS.read_bytes() == SAMPLE.encode("utf-8")          # 没改的字节不变（BOM 去掉）
    code, env = run(capsys, "goals", "update", "遥 精2", "--note", "加一行")
    assert code == 0, env
    raw = paths.GOALS.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert "加一行" in raw.decode("utf-8")


def test_goals_keeps_lf(capsys):
    paths.GOALS.parent.mkdir(parents=True, exist_ok=True)
    paths.GOALS.write_bytes(SAMPLE.replace("\r\n", "\n").encode("utf-8"))
    code, env = run(capsys, "goals", "update", "遥 精2", "--note", "加一行")
    assert code == 0, env
    assert b"\r" not in paths.GOALS.read_bytes()


# --- venv 与 cli 的编码 ---------------------------------------------------------------------------------------------

def test_venv_paths():
    assert venv.venv_exe("maapilot").name == ("maapilot.exe" if WINDOWS else "maapilot")
    assert venv.venv_exe("python.exe").name == "python.exe"
    assert venv.maapilot_cmd() == (r".venv\Scripts\maapilot.exe" if WINDOWS else ".venv/bin/maapilot")
    assert venv.join_path(["/a", "", "/b", "/a"]) == os.pathsep.join(["/a", "/b"])
    assert all(os.path.isabs(d) for d in venv.sys_path_dirs())


def test_skland_guide_uses_platform_command():
    from maapilot.tools.skland_cmd import token_guide
    assert f"{venv.maapilot_cmd()} skland token" in token_guide()


def test_cli_sets_utf8_for_children(capsys, monkeypatch):
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    run(capsys, "goals", "list")
    assert os.environ.get("PYTHONUTF8") == "1"


def test_swap_std_handle_noop_off_windows():
    if not WINDOWS:
        assert cli._swap_std_handle() is None


def test_cli_stdout_is_utf8_even_if_locale_is_not(tmp_path):
    """真的子进程、stdout 是管道、Python 以为终端编码是 latin-1（像 Windows 的 GBK 代码页）：信封照样是 UTF-8。"""
    import subprocess
    priv = tmp_path / "private"
    priv.mkdir()
    (priv / "goals.md").write_text(SAMPLE.replace("\r\n", "\n"), encoding="utf-8")
    env = {**os.environ, "MAAPILOT_PRIVATE": str(priv), "PYTHONIOENCODING": "latin-1"}
    env.pop("PYTHONUTF8", None)
    r = subprocess.run([sys.executable, "-c", "import sys; from maapilot import cli; sys.exit(cli.main(['goals', 'list']))"],
                       env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert json.loads(r.stdout.decode("utf-8"))["data"]["goals"][0]["title"] == "遥 精2"


def test_divert_stdout_sends_fd1_to_stderr(tmp_path):
    import subprocess
    env = {**os.environ, "MAAPILOT_PRIVATE": str(tmp_path / "private")}
    code = ("import os, sys\nfrom maapilot import cli\n"
            "orig = cli._main\n"
            "def fake(argv, out_fd):\n    os.write(1, '原生日志\\n'.encode())\n    return orig(argv, out_fd)\n"
            "cli._main = fake\nsys.exit(cli.main(['goals', 'list']))\n")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert json.loads(r.stdout.decode("utf-8"))["command"] == "goals list"
    assert "原生日志" in r.stderr.decode("utf-8")


def test_no_cv2_import():
    """cv2 和 MaaFramework 各带一份 OpenCV，macOS 上一起加载 objc 会报类重复：代码里不再 import cv2。"""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "maapilot"
    hits = [str(p) for p in root.rglob("*.py") if re.search(r"^\s*(import cv2|from cv2)", p.read_text(encoding="utf-8"), re.M)]
    assert hits == []


def test_ccoeff_normed_finds_template():
    np = pytest.importorskip("numpy")
    from maapilot.crew import clears
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, (60, 80, 3), dtype=np.uint8)
    tpl = img[20:35, 30:50].copy()
    res = clears.ccoeff_normed(img, tpl)
    dy, dx = np.unravel_index(int(np.argmax(res)), res.shape)
    assert (dy, dx) == (20, 30) and abs(res[dy, dx] - 1) < 1e-6
    flat = np.full((20, 20, 3), 7, dtype=np.uint8)
    assert (clears.ccoeff_normed(flat, flat[:5, :5]) == 0).all()     # 方差为 0 的窗口记 0
