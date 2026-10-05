"""森空岛（Skland）：用用户的鹰角账号 token 直接读明日方舟的账号数据（干员练度、技能、专精、模组、理智）。

**非官方接口**（社区从森空岛 App / 网页版扒出来的），随时可能变。流程（2026-10 核对，见 docs/cli.md「skland」）：
1. 用户自己登录鹰角官网后打开 https://web-api.hypergryph.com/account/info/hg（或森空岛网页版的
   https://web-api.skland.com/account/info/hg），JSON 里 `data.content` 就是 token；存进 private/skland.json。
2. token → OAuth 授权码：POST https://as.hypergryph.com/user/oauth2/v2/grant `{appCode: 4ca99fa6b56cc2ba, token, type: 0}`
   → `data.code`（`status` 不是 0 = token 失效）。
3. 授权码 → cred：POST https://zonai.skland.com/web/v1/user/auth/generate_cred_by_code `{kind: 1, code}`
   （请求头要带数美设备号 dId，见 skland_device）→ `data.cred`、`data.token`（签名密钥）、`data.userId`。
4. 绑定的角色：GET https://zonai.skland.com/api/v1/game/player/binding → `data.list[appCode=arknights].bindingList[]`
   （`uid`、`nickName`、`channelName`、`channelMasterId`：1 官服、2 B 服，`isDefault`）。
5. 角色数据：GET https://zonai.skland.com/api/v1/game/player/info?uid=… → `data.status`、`data.chars`、`data.charInfoMap`、
   `data.equipmentInfoMap`…
4、5 要签名：sign = md5( hmac_sha256(签名密钥, 路径 + query 或 body + 时间戳 + {"platform","timestamp","dId","vName"} 的紧凑 JSON) 的十六进制 )，
请求头带 cred、sign、platform、timestamp、dId、vName。时间戳用服务器时间（按响应的 Date 头校准）减 2 秒。

token 只在 private/skland.json 里，永不进输出、日志、报错（`redact()`）。网络请求都经 `http()`，测试里换掉。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlparse

APP_CODE = "4ca99fa6b56cc2ba"
GRANT_URL = "https://as.hypergryph.com/user/oauth2/v2/grant"
CRED_URL = "https://zonai.skland.com/web/v1/user/auth/generate_cred_by_code"
BINDING_URL = "https://zonai.skland.com/api/v1/game/player/binding"
INFO_URL = "https://zonai.skland.com/api/v1/game/player/info"
TOKEN_PAGE = "https://web-api.hypergryph.com/account/info/hg"
PLATFORM = "1"
VNAME = "1.62.0"
USER_AGENT = "Skland/1.62.0 (com.hypergryph.skland; build:106200003; Android 33; ) Okhttp/4.11.0"
CHANNELS = {"1": "Official", "2": "Bilibili"}         # channelMasterId → config.yaml 的 server
TIMEOUT_S = 20


class SklandError(RuntimeError):
    """kind：token_invalid（token 失效，要用户重新取）、no_role（没绑明日方舟）、choose_role（绑了几个、对不上服务器）、
    device（数美设备号拿不到）、api（接口报错）、network（连不上）。"""

    def __init__(self, kind: str, message: str, data=None):
        super().__init__(message)
        self.kind = kind
        self.data = data


# --- HTTP（测试里换掉） --------------------------------------------------------------------------------------------

def http(method: str, url: str, headers: dict, body: dict | None = None) -> tuple[int, dict, dict]:
    """→ (状态码, 响应头, JSON)。连不上 → SklandError(network)；非 2xx 也照样返回 JSON（接口把错误写在 JSON 里）。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                                          **({"Content-Type": "application/json"} if data is not None else {}),
                                          **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            status, hdrs, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, dict(e.headers or {}), e.read() or b"{}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise SklandError("network", f"连不上 {urlparse(url).netloc}：{e}") from None
    try:
        return status, hdrs, json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        raise SklandError("api", f"{urlparse(url).netloc} 返回的不是 JSON（HTTP {status}）") from None


# --- 签名 ------------------------------------------------------------------------------------------------------------

def sign(key: str, path: str, query_or_body: str, timestamp: str, did: str,
         platform: str = PLATFORM, vname: str = VNAME) -> tuple[str, dict]:
    """→ (sign, 要放进请求头的 {platform, timestamp, dId, vName})。"""
    head = {"platform": platform, "timestamp": timestamp, "dId": did, "vName": vname}
    s = path + query_or_body + timestamp + json.dumps(head, separators=(",", ":"))
    digest = hmac.new(key.encode("utf-8"), s.encode("utf-8"), hashlib.sha256).hexdigest()
    return hashlib.md5(digest.encode("utf-8")).hexdigest(), head


def redact(text: str, *secrets: str | None) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "***")
    return text


class Client:
    """一次会话：token → cred → 签名请求。did 可以复用（存在 skland.json）。"""

    def __init__(self, token: str, did: str | None = None, transport=None):
        self.token = token.strip()
        self.did = did
        self.http = transport or http
        self.cred = self.key = self.user_id = None
        self.offset = 0                                       # 服务器时间 - 本机时间（秒）

    def _err(self, kind: str, msg: str, data=None) -> SklandError:
        return SklandError(kind, redact(msg, self.token, self.cred, self.key), data)

    def _sync_time(self, headers: dict, resp: dict) -> None:
        try:
            date = headers.get("Date") or headers.get("date")
            if date:
                self.offset = int(parsedate_to_datetime(date).timestamp() - time.time())
            elif isinstance(resp, dict) and resp.get("timestamp"):
                self.offset = int(int(resp["timestamp"]) - time.time())
        except (TypeError, ValueError):
            pass

    def ensure_did(self) -> str:
        if not self.did:
            from maapilot.crew import skland_device
            try:
                self.did = skland_device.device_id(self.http)
            except skland_device.DeviceIdError as e:
                raise self._err("device", str(e)) from None
        return self.did

    def login(self) -> None:
        did = self.ensure_did()
        status, hdrs, r = self.http("POST", GRANT_URL, {"dId": did}, {"appCode": APP_CODE, "token": self.token, "type": 0})
        if not isinstance(r, dict) or r.get("status") != 0 or not (r.get("data") or {}).get("code"):
            msg = (r or {}).get("msg") or (r or {}).get("message") or f"HTTP {status}"
            raise self._err("token_invalid", f"鹰角账号 token 用不了（{msg}）：过期了或填错了，要重新取")
        code = r["data"]["code"]
        status, hdrs, r = self.http("POST", CRED_URL, {"dId": did}, {"kind": 1, "code": code})
        self._sync_time(hdrs, r)
        d = (r or {}).get("data") or {}
        if (r or {}).get("code") != 0 or not d.get("cred") or not d.get("token"):
            raise self._err("api", f"森空岛登录失败（{(r or {}).get('message') or f'HTTP {status}'}）")
        self.cred, self.key, self.user_id = d["cred"], d["token"], d.get("userId")

    def get(self, url: str, params: dict | None = None) -> dict:
        if self.cred is None:
            self.login()
        query = urlencode(params or {})
        full = f"{url}?{query}" if query else url
        ts = str(int(time.time()) + self.offset - 2)
        sig, head = sign(self.key, urlparse(url).path, query, ts, self.did or "")
        status, hdrs, r = self.http("GET", full, {"cred": self.cred, "sign": sig, **head})
        if not isinstance(r, dict) or r.get("code") != 0:
            msg = (r or {}).get("message") or f"HTTP {status}"
            if (r or {}).get("code") == 10002 or "未登录" in str(msg):
                raise self._err("token_invalid", f"森空岛说没登录（{msg}）：token 可能过期了")
            raise self._err("api", f"森空岛接口报错：{msg}")
        return r.get("data") or {}

    def roles(self) -> list[dict]:
        data = self.get(BINDING_URL)
        out = []
        for app in data.get("list") or []:
            if app.get("appCode") == "arknights":
                out += [{k: b.get(k) for k in ("uid", "nickName", "channelName", "channelMasterId", "isDefault")}
                        for b in app.get("bindingList") or []]
        return out

    def player_info(self, uid: str) -> dict:
        return self.get(INFO_URL, {"uid": uid})


def choose_role(roles: list[dict], server: str | None) -> dict:
    """绑了一个就用它；几个就挑和 config 的 server 对得上的（官服 / B 服）；还分不出 → choose_role。"""
    if not roles:
        raise SklandError("no_role", "这个鹰角账号在森空岛上没有绑定明日方舟角色")
    if len(roles) == 1:
        return roles[0]
    match = [r for r in roles if CHANNELS.get(str(r.get("channelMasterId"))) == server]
    if len(match) == 1:
        return match[0]
    default = [r for r in match if r.get("isDefault")]
    if len(default) == 1:
        return default[0]
    raise SklandError("choose_role", "绑了好几个明日方舟角色，分不出用哪个",
                      data=[{k: r.get(k) for k in ("uid", "nickName", "channelName")} for r in roles])


# --- 角色数据 → box.json --------------------------------------------------------------------------------------------

def to_box(info: dict, names: dict | None = None) -> dict:
    """player/info 的 data → {名: {...}}：精英化、等级、潜能、稀有度（1–6）、技能等级、各技能专精、模组。
    names：游戏数据表给的 {char_id: 名}、{equip_id: 模组名}（缺的用接口自带的 charInfoMap / equipmentInfoMap，再缺就留 id）。"""
    names = names or {}
    char_names, equip_names = names.get("chars") or {}, names.get("equips") or {}
    cinfo, einfo = info.get("charInfoMap") or {}, info.get("equipmentInfoMap") or {}
    ops: dict[str, dict] = {}
    for c in info.get("chars") or []:
        cid = c.get("charId")
        meta = cinfo.get(cid) or {}
        name = char_names.get(cid) or meta.get("name") or cid
        rarity = meta.get("rarity")
        equipped = c.get("defaultEquipId") or ""
        ops[name] = {
            "id": cid, "rarity": (int(rarity) + 1) if isinstance(rarity, int) else names.get("rarity", {}).get(cid),
            "elite": c.get("evolvePhase"), "level": c.get("level"), "potential": c.get("potentialRank"),
            "skill_level": c.get("mainSkillLvl"),
            "skills": [{"index": i + 1, "id": s.get("id"), "mastery": s.get("specializeLevel", 0)}
                       for i, s in enumerate(c.get("skills") or [])],
            "modules": [{"id": e.get("id"), "name": equip_names.get(e.get("id")) or (einfo.get(e.get("id")) or {}).get("name")
                         or e.get("id"), "level": e.get("level"), "equipped": e.get("id") == equipped}
                        for e in c.get("equip") or []],
        }
    return ops


def sanity_of(info: dict, now_ts: float | None = None) -> dict | None:
    """data.status.ap → {current, max}：没到上限的按 6 分钟 1 点从 lastApAddTime 推到现在。"""
    ap = (info.get("status") or {}).get("ap") or {}
    if "current" not in ap or "max" not in ap:
        return None
    cur, cap = int(ap["current"]), int(ap["max"])
    last = ap.get("lastApAddTime")
    if cur < cap and last:
        cur = min(cap, cur + max(0, int(((now_ts or time.time()) - int(last)) // 360)))
    return {"current": cur, "max": cap}
