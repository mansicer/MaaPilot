"""`maapilot skland token / status` 与 `maapilot state skland`：从森空岛读干员练度（不碰模拟器、不拿锁）。

token 存在 private/skland.json（权限 600）：`{token, did, uid, role, saved_at, last}`。token 只由用户自己在终端里
`maapilot skland token` 写入（agent 没有这条命令的权限，也读不了这个文件），任何输出、日志、报错里都不出现。
接口与流程见 maapilot.crew.skland；契约见 docs/cli.md「skland」。
"""
from __future__ import annotations

import getpass
import json
import os
import sys

from maapilot import config, paths
from maapilot.cliutil import BAD_REQUEST, FAILED, NEEDS_USER, CliError
from maapilot.tools import common

TOKEN_HINT = "请用户在终端里跑 maapilot skland token（按提示粘贴鹰角账号 token），agent 不要代办"


def store_path():
    return paths.PRIVATE / "skland.json"


def load_store() -> dict | None:
    d = common.read_json(store_path())
    return d if isinstance(d, dict) and d.get("token") else None


def save_store(d: dict) -> None:
    p = store_path()
    common.write_json(p, d)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def configured() -> bool:
    return load_store() is not None


def _server() -> str | None:
    try:
        return config.server(config.load_or_defaults())
    except Exception:                                   # noqa: BLE001 - 还没设置时没有 server
        return None


def parse_token(text: str) -> str:
    """接受 token 本身，或整页 JSON（{"code":0,"data":{"content":"…"}}，用户可能整段粘贴）。"""
    text = (text or "").strip()
    if text.startswith("{"):
        try:
            d = json.loads(text)
            text = str(((d.get("data") or {}).get("content")) or d.get("content") or "").strip()
        except ValueError:
            raise CliError("bad_token", "粘贴的内容不是完整的 JSON，也不是 token", exit=BAD_REQUEST) from None
    if not text or any(c.isspace() for c in text) or len(text) < 16:
        raise CliError("bad_token", "这不像鹰角账号 token（应是一长串字母数字）", exit=BAD_REQUEST,
                       hint="登录鹰角官网后打开 https://web-api.hypergryph.com/account/info/hg，复制 data.content 的值")
    return text


def _cli_error(e, token: str | None = None) -> CliError:
    from maapilot.crew.skland import redact
    msg = redact(str(e), token)
    if e.kind == "token_invalid":
        return CliError("token_invalid", msg, exit=NEEDS_USER, hint=TOKEN_HINT)
    if e.kind == "choose_role":
        return CliError("choose_role", msg, exit=BAD_REQUEST, data={"roles": e.data},
                        hint="maapilot skland token --uid <要用的 uid>（由用户在终端里跑）")
    if e.kind == "no_role":
        return CliError("no_role", msg, exit=NEEDS_USER, hint="请用户在森空岛 App 里绑定明日方舟角色")
    return CliError(f"skland_{e.kind}", msg, exit=FAILED, hint="稍后再试；一直不行就用 maapilot state box（MAA 识别）")


def _record(store: dict, ok: bool, error: CliError | None = None) -> None:
    store["last"] = {"at": common.iso(), "ok": ok, **({"error": error.message, "code": error.code} if error else {})}
    save_store(store)


# --- skland token ----------------------------------------------------------------------------------------------------

def cmd_token(args):
    from maapilot.crew import skland
    if sys.stdin.isatty():
        print("登录鹰角官网后打开 " + skland.TOKEN_PAGE + "，复制 data.content 的值粘贴到这里（不显示）：",
              file=sys.stderr, flush=True)
        raw = getpass.getpass("token: ", stream=sys.stderr)
    else:
        raw = sys.stdin.read()
    token = parse_token(raw)
    old = load_store() or {}
    client = skland.Client(token, did=old.get("did"))
    try:
        client.login()
        roles = client.roles()
        if args.uid:
            role = next((r for r in roles if str(r.get("uid")) == str(args.uid)), None)
            if role is None:
                raise skland.SklandError("choose_role", f"绑定的角色里没有 uid {args.uid}", data=roles)
        else:
            role = skland.choose_role(roles, _server())
    except skland.SklandError as e:
        raise _cli_error(e, token) from None
    store = {"token": token, "did": client.did, "uid": str(role["uid"]),
             "role": {k: role.get(k) for k in ("nickName", "channelName")}, "saved_at": common.iso()}
    _record(store, True)
    return {"saved": True, "uid": store["uid"], "role": store["role"],
            "roles": [{k: r.get(k) for k in ("uid", "nickName", "channelName")} for r in roles],
            "file": common.rel(store_path())}


# --- skland status ---------------------------------------------------------------------------------------------------

def status() -> dict:
    s = load_store()
    if s is None:
        return {"configured": False, "hint": TOKEN_HINT}
    last = s.get("last") or {}
    return {"configured": True, "uid": s.get("uid"), "role": s.get("role"), "saved_at": s.get("saved_at"),
            "last": last or None, "token_expired": (last.get("code") == "token_invalid") if last else None}


def cmd_status(args):
    return status()


# --- state skland ----------------------------------------------------------------------------------------------------

def _local_names() -> dict:
    out: dict = {"chars": {}, "rarity": {}, "equips": {}}
    try:
        from maapilot.knowledge import gamedata
        for cid, c in gamedata._character_table().items():
            out["chars"][cid] = c.get("name")
            out["rarity"][cid] = gamedata._rarity_index(c) + 1
        ue = gamedata._uniequip() or {}
        out["equips"] = {k: v.get("uniEquipName") for k, v in (ue.get("equipDict") or {}).items()}
    except Exception:                                   # noqa: BLE001 - 没有数据表就用接口自带的名字 / id
        pass
    return out


def read_skland() -> dict:
    """读一次森空岛，写 box.json（source: skland）和 sanity.json。失败抛 CliError，不改 box.json。"""
    from maapilot.crew import skland
    store = load_store()
    if store is None:
        raise CliError("not_configured", "还没接森空岛（没有 private/skland.json）", exit=BAD_REQUEST, hint=TOKEN_HINT)
    client = skland.Client(store["token"], did=store.get("did"))
    try:
        uid = store.get("uid")
        if not uid:
            uid = str(skland.choose_role(client.roles(), _server())["uid"])
        info = client.player_info(uid)
    except skland.SklandError as e:
        err = _cli_error(e, store["token"])
        _record(store, False, err)
        raise err from None
    if client.did and client.did != store.get("did"):
        store["did"] = client.did
    ops = skland.to_box(info, _local_names())
    if not ops:
        err = CliError("skland_empty", "森空岛返回的角色数据里没有干员", exit=FAILED)
        _record(store, False, err)
        raise err
    at = common.iso()
    common.write_json(paths.STATE / "box.json", {"at": at, "source": "skland", "uid": uid, "operators": ops})
    san = skland.sanity_of(info)
    if san:
        common.write_json(paths.STATE / "sanity.json", {"at": at, **san, "source": "skland"})
    _record(store, True)
    return {"at": at, "source": "skland", "uid": uid, "operators": len(ops),
            "with_modules": sum(1 for o in ops.values() if o["modules"]),
            "mastered": sum(1 for o in ops.values() if any(s["mastery"] for s in o["skills"])),
            "sanity": san}


@common.logged
def cmd_state(args):
    out = read_skland()
    if args.op:
        snap = common.read_json(paths.STATE / "box.json") or {}
        out["operators_detail"] = {n: snap["operators"][n] for n in args.op if n in snap.get("operators", {})}
        out["missing"] = [n for n in args.op if n not in snap.get("operators", {})]
    return out


def register(sub) -> None:
    g = sub.add_parser("skland", help="森空岛：保存 token（用户自己在终端里跑）、看状态")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("token", help="保存鹰角账号 token（从标准输入读，终端里不显示；会先试一次）")
    p.add_argument("--uid", help="绑了好几个明日方舟角色时指定用哪个")
    p.set_defaults(func=cmd_token, command="skland token")
    p = gs.add_parser("status", help="接了没有、上次读成没成、token 过期没有（不显示 token）")
    p.set_defaults(func=cmd_status, command="skland status")


def register_state(gs) -> None:
    p = gs.add_parser("skland", help="从森空岛读干员练度、技能专精、模组、理智（不碰模拟器）")
    p.add_argument("--op", nargs="+", metavar="干员", help="另外输出这些干员的详细数据")
    p.set_defaults(func=cmd_state, command="state skland")
