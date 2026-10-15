"""森空岛：签名、数美设备号的加密、登录流程、角色选择、player/info → box.json、命令与日常里的用法。

全部用假的 HTTP（不联网）。响应按社区公开的格式与 ProbiusOfficial/Skland_API 的 dump.json 缩写而成。
加密的固定值：本项目的实现与 arknights-mower 的 SecuritySm.py 逐字节核对过一次，这里钉住结果防止改坏。
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import stat
import time

import pytest

from maapilot import cli, paths
from maapilot.crew import skland, skland_device
from maapilot.knowledge import sources
from maapilot.tools import common, skland_cmd

TOKEN = "TESTTOKEN0123456789abcdefSECRET"


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, json.loads(out.out), out


# --- 签名与加密 ---------------------------------------------------------------------------------------------------------

def test_sign_matches_documented_algorithm():
    sig, head = skland.sign("signkey", "/api/v1/game/player/info", "uid=123", "1700000000", "Babc")
    assert head == {"platform": "1", "timestamp": "1700000000", "dId": "Babc", "vName": "1.62.0"}
    s = '/api/v1/game/player/info' + 'uid=123' + '1700000000' + '{"platform":"1","timestamp":"1700000000","dId":"Babc","vName":"1.62.0"}'
    expect = hashlib.md5(hmac.new(b"signkey", s.encode(), hashlib.sha256).hexdigest().encode()).hexdigest()
    assert sig == expect == "645192aae3f9f8d6014025a63b2b64a8"


def test_device_crypto_vectors():
    assert skland_device.des_field("default", "uy7mzc4h") == "Xoz/PL65pzA="
    assert skland_device.des_field("259ffe69", "snrn887t") == "I6evoagk0jI="
    assert skland_device.aes_hex(b"hello world", b"0123456789abcdef") == "b69e15fb926d8abb317791b3243bc539"
    assert skland_device.tn({"b": "x", "a": 1, "c": {"d": 2.5}}) == "10000x25000.0"
    ob = skland_device.des_obfuscate({"appId": "default", "box": "", "protocol": 102, "smid": "s"})
    assert ob == {"xx": "Xoz/PL65pzA=", "jf": "", "protocol": 102, "smid": "s"}


def test_device_id_request_and_response():
    seen = {}

    def fake(method, url, headers, body):
        seen.update(method=method, url=url, body=body)
        return 200, {}, {"code": 1100, "detail": {"deviceId": "XYZ"}}
    assert skland_device.device_id(fake) == "BXYZ"
    assert seen["url"] == skland_device.DEVICE_URL and seen["body"]["organization"] == skland_device.ORGANIZATION
    assert set(seen["body"]) == {"appId", "compress", "data", "encode", "ep", "organization", "os"}
    with pytest.raises(skland_device.DeviceIdError):
        skland_device.device_id(lambda *a: (200, {}, {"code": 9}))


# --- 假的森空岛 ----------------------------------------------------------------------------------------------------------

INFO = {
    "status": {"uid": "12345678", "name": "博士#1", "level": 120,
               "ap": {"current": 100, "max": 135, "lastApAddTime": 1000, "completeRecoveryTime": 0}},
    "chars": [
        {"charId": "char_002_amiya", "level": 80, "evolvePhase": 2, "potentialRank": 5, "mainSkillLvl": 7,
         "skills": [{"id": "skcom_magic_rage[3]", "specializeLevel": 0}, {"id": "skchr_amiya_2", "specializeLevel": 3},
                    {"id": "skchr_amiya_3", "specializeLevel": 1}],
         "equip": [{"id": "uniequip_001_amiya", "level": 1}, {"id": "uniequip_002_amiya", "level": 3}],
         "defaultEquipId": "uniequip_002_amiya"},
        {"charId": "char_999_new", "level": 1, "evolvePhase": 0, "potentialRank": 0, "mainSkillLvl": 1,
         "skills": [], "equip": [], "defaultEquipId": ""},
    ],
    "charInfoMap": {"char_002_amiya": {"name": "阿米娅", "rarity": 4}, "char_999_new": {"name": "新干员", "rarity": 5}},
    "equipmentInfoMap": {"uniequip_002_amiya": {"name": "接口给的模组名"}},
}


class FakeSkland:
    def __init__(self, *, grant_ok=True, roles=None, info=INFO, info_code=0):
        self.calls = []
        self.grant_ok, self.info, self.info_code = grant_ok, info, info_code
        self.roles = roles if roles is not None else [
            {"uid": "12345678", "nickName": "博士#1", "channelName": "官服", "channelMasterId": "1", "isDefault": True}]

    def __call__(self, method, url, headers, body=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if url == skland_device.DEVICE_URL:
            return 200, {}, {"code": 1100, "detail": {"deviceId": "DEV"}}
        if url == skland.GRANT_URL:
            if not self.grant_ok:
                return 200, {}, {"status": 3, "msg": "登录已过期"}
            assert body == {"appCode": skland.APP_CODE, "token": TOKEN, "type": 0}
            return 200, {}, {"status": 0, "data": {"code": "GRANT", "uid": "u"}}
        if url == skland.CRED_URL:
            assert body == {"kind": 1, "code": "GRANT"} and headers["dId"] == "BDEV"
            return 200, {"Date": "Mon, 05 Oct 2026 10:00:00 GMT"}, {"code": 0, "data": {"cred": "CRED", "token": "SIGNKEY", "userId": "9"}}
        if url == skland.BINDING_URL:
            return 200, {}, {"code": 0, "data": {"list": [{"appCode": "endfield", "bindingList": [{"uid": "x"}]},
                                                          {"appCode": "arknights", "bindingList": self.roles}]}}
        if url.startswith(skland.INFO_URL + "?"):
            if self.info_code:
                return 200, {}, {"code": self.info_code, "message": "用户未登录"}
            return 200, {}, {"code": 0, "data": self.info}
        raise AssertionError(f"没想到的请求 {url}")


def test_client_flow_signs_requests():
    fake = FakeSkland()
    c = skland.Client(TOKEN, transport=fake)
    roles = c.roles()
    assert roles[0]["uid"] == "12345678" and len(roles) == 1                 # 只要明日方舟
    info = c.player_info("12345678")
    assert info["status"]["uid"] == "12345678"
    get = fake.calls[-1]
    assert get["url"] == skland.INFO_URL + "?uid=12345678"
    h = get["headers"]
    assert h["cred"] == "CRED" and h["platform"] == "1" and h["dId"] == "BDEV" and h["vName"] == skland.VNAME
    expect, _ = skland.sign("SIGNKEY", "/api/v1/game/player/info", "uid=12345678", h["timestamp"], "BDEV")
    assert h["sign"] == expect
    assert [c["url"] for c in fake.calls][:3] == [skland_device.DEVICE_URL, skland.GRANT_URL, skland.CRED_URL]


def test_token_invalid_is_redacted():
    c = skland.Client(TOKEN, did="BDEV", transport=FakeSkland(grant_ok=False))
    with pytest.raises(skland.SklandError) as e:
        c.login()
    assert e.value.kind == "token_invalid" and TOKEN not in str(e.value)


def test_choose_role():
    a = {"uid": "1", "channelMasterId": "1", "isDefault": False}
    b = {"uid": "2", "channelMasterId": "2", "isDefault": True}
    assert skland.choose_role([a], "Bilibili") == a
    assert skland.choose_role([a, b], "Bilibili") == b and skland.choose_role([a, b], "Official") == a
    with pytest.raises(skland.SklandError) as e:
        skland.choose_role([a, dict(a, uid="3")], "Official")
    assert e.value.kind == "choose_role" and len(e.value.data) == 2
    with pytest.raises(skland.SklandError):
        skland.choose_role([], "Official")


def test_to_box_and_sanity():
    names = {"chars": {"char_002_amiya": "阿米娅"}, "equips": {"uniequip_001_amiya": "证章"},
             "skills": {"skchr_amiya_2": "精神爆发"}}
    ops = skland.to_box(INFO, names)
    a = ops["阿米娅"]
    assert a["elite"] == 2 and a["level"] == 80 and a["potential"] == 6 and a["rarity"] == 5 and a["skill_level"] == 7
    assert [s["mastery"] for s in a["skills"]] == [0, 3, 1] and a["skills"][1] == {"index": 2, "id": "skchr_amiya_2", "name": "精神爆发", "mastery": 3}
    assert a["skills"][0]["name"] is None
    assert a["modules"] == [{"id": "uniequip_001_amiya", "name": "证章", "level": 1, "equipped": False, "locked": None},
                            {"id": "uniequip_002_amiya", "name": "接口给的模组名", "level": 3, "equipped": True, "locked": None}]
    assert ops["新干员"]["rarity"] == 6 and ops["新干员"]["modules"] == [] and ops["新干员"]["potential"] == 1
    assert skland.sanity_of(INFO, now_ts=1000 + 360 * 10) == {"current": 110, "max": 135}
    assert skland.sanity_of({"status": {"ap": {"current": 358, "max": 205, "lastApAddTime": 0}}}, 10 ** 9)["current"] == 358


# --- 命令 ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def fake(monkeypatch):
    f = FakeSkland()
    monkeypatch.setattr(skland, "http", f)
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    return f


def test_token_command_saves_privately(fake, capsys, monkeypatch):
    page = json.dumps({"code": 0, "data": {"content": TOKEN}, "msg": "接口调用成功"})
    monkeypatch.setattr("sys.stdin", io.StringIO(page))                     # 整页 JSON 粘进来也认
    code, env, out = run(capsys, "skland", "token")
    assert code == 0, env
    assert env["data"]["saved"] is True and env["data"]["uid"] == "12345678"
    assert TOKEN not in out.out and TOKEN not in out.err
    p = skland_cmd.store_path()
    assert json.loads(p.read_text(encoding="utf-8"))["token"] == TOKEN and json.loads(p.read_text(encoding="utf-8"))["did"] == "BDEV"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    code, env, out = run(capsys, "skland", "status")
    assert code == 0 and env["data"]["configured"] is True and env["data"]["token_expired"] is False
    assert TOKEN not in out.out


def test_token_command_rejects_bad_input(fake, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("not a token"))
    code, env, _ = run(capsys, "skland", "token")
    assert code == 2 and env["error"]["code"] == "bad_token" and not skland_cmd.store_path().exists()
    fake.grant_ok = False
    monkeypatch.setattr("sys.stdin", io.StringIO(TOKEN))
    code, env, out = run(capsys, "skland", "token")
    assert code == 3 and env["error"]["code"] == "token_invalid" and TOKEN not in out.out
    assert not skland_cmd.store_path().exists()


def _configure():
    skland_cmd.save_store({"token": TOKEN, "did": "BDEV", "uid": "12345678", "role": {}, "saved_at": common.iso()})


def test_state_skland_writes_box_and_sanity(fake, capsys):
    _configure()
    code, env, out = run(capsys, "state", "skland", "--op", "阿米娅")
    assert code == 0, env
    d = env["data"]
    assert d["source"] == "skland" and d["operators"] == 2 and d["with_modules"] == 1 and d["mastered"] == 1
    assert d["operators_detail"]["阿米娅"]["skills"][1]["mastery"] == 3
    box = json.loads((paths.STATE / "box.json").read_text(encoding="utf-8"))
    assert box["source"] == "skland" and box["uid"] == "12345678" and "阿米娅" in box["operators"]
    assert json.loads((paths.STATE / "sanity.json").read_text(encoding="utf-8"))["source"] == "skland"
    assert TOKEN not in out.out
    log = next(paths.LOGS.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "state skland" in log and TOKEN not in log and "12345678" not in log     # 不记 token、uid
    assert json.loads(skland_cmd.store_path().read_text(encoding="utf-8"))["last"]["ok"] is True


def test_state_skland_failure_keeps_box(fake, capsys):
    _configure()
    common.write_json(paths.STATE / "box.json", {"at": "old", "operators": {"x": {}}})
    fake.info_code = 10002
    code, env, out = run(capsys, "state", "skland")
    assert code == 3 and env["error"]["code"] == "token_invalid" and "skland token" in env["error"]["hint"]
    assert json.loads((paths.STATE / "box.json").read_text(encoding="utf-8"))["at"] == "old"
    code, env, _ = run(capsys, "skland", "status")
    assert env["data"]["token_expired"] is True and env["data"]["last"]["ok"] is False


def test_state_skland_not_configured(capsys):
    code, env, _ = run(capsys, "state", "skland")
    assert code == 2 and env["error"]["code"] == "not_configured"


def test_guided_browser_opens_login_then_token_page(monkeypatch, capsys):
    from maapilot.tools import skland_cmd
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append(url) or True)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    skland_cmd._guided_browser("https://example.test/token")
    assert opened == [skland_cmd.LOGIN_PAGE, "https://example.test/token"]
    opened.clear()
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: False)      # 没有图形界面：只提示手动打开
    skland_cmd._guided_browser("https://example.test/token")
    assert "手动打开" in capsys.readouterr().err


def test_annihilation_progress_from_campaign(tmp_path, monkeypatch):
    info = {"campaign": {"reward": {"current": 1215, "total": 1800},
                         "records": [{"campaignId": "camp_r_33", "maxKills": 370}, {"campaignId": "camp_01", "maxKills": 400}]},
            "campaignInfoMap": {"camp_r_33": {"name": "默祷圣祠"}}}
    ann = skland.annihilation_of(info)
    assert ann["current"] == 1215 and ann["total"] == 1800
    assert ann["max_kills"]["camp_r_33"] == {"name": "默祷圣祠", "kills": 370}
    with pytest.raises(sources.UpstreamFormat, match="campaign"):
        skland.annihilation_of({})
    from maapilot.tools import maa_cmd
    d = maa_cmd.note_annihilation_progress(1215, 1800)
    assert d["orundum"] == 1215 and d["capped"] is False and d["progress"]["source"] == "skland"
    assert maa_cmd.note_annihilation_progress(1800, 1800)["capped"] is True
    assert maa_cmd.annihilation_week()["capped"] is True


def test_state_sanity_prefers_skland(fake, capsys, monkeypatch):
    """接了森空岛：state sanity 从森空岛读，不碰模拟器、不拿锁。"""
    from maapilot.tools import state
    _configure()
    monkeypatch.setattr(state, "read_sanity", lambda: pytest.fail("不该回首页 OCR"))
    monkeypatch.setattr(common, "device", lambda *a, **k: pytest.fail("不该拿模拟器锁"))
    code, env, _ = run(capsys, "state", "sanity")
    assert code == 0, env
    assert env["data"]["source"] == "skland" and env["data"]["max"] == 135
    assert json.loads((paths.STATE / "sanity.json").read_text(encoding="utf-8"))["source"] == "skland"
    row = json.loads(next(paths.LOGS.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()[-1])
    assert row["command"] == "state sanity" and row["result"]["max"] == 135 and row["result"]["source"] == "skland"
    assert "current" in row["result"]


def test_state_sanity_falls_back_to_ocr(fake, capsys, monkeypatch):
    from maapilot.tools import state
    _configure()
    fake.info_code = 10002
    from contextlib import nullcontext
    monkeypatch.setattr(common, "device", lambda *a, **k: nullcontext())
    monkeypatch.setattr(state, "read_sanity", lambda: {"at": common.iso(), "current": 50, "max": 135, "source": "ocr"})
    code, env, _ = run(capsys, "state", "sanity")
    assert code == 0 and env["data"]["source"] == "ocr" and env["data"]["skland_error"]
    log = next(paths.LOGS.glob("*.jsonl")).read_text(encoding="utf-8")
    row = json.loads(log.splitlines()[-1])
    assert row["result"] == {"current": 50, "max": 135, "source": "ocr", "skland_error": True}
    assert TOKEN not in log


# --- 接口格式变了 -------------------------------------------------------------------------------------------------------

def _info_without(*path):
    info = json.loads(json.dumps(INFO))
    info["campaign"] = {"reward": {"current": 0, "total": 1800}, "records": []}
    cur = info
    for k in path[:-1]:
        cur = cur[k]
    del cur[path[-1]]
    return info


def test_state_skland_sanity_format_change_is_recorded(fake, capsys):
    """理智的字段改了名：练度照样写，理智不写，记进 sources.json（doctor --brief 会列出来）。"""
    _configure()
    fake.info = _info_without("status", "ap")
    code, env, _ = run(capsys, "state", "skland")
    assert code == 0 and env["data"]["sanity"] is None
    assert json.loads((paths.STATE / "box.json").read_text(encoding="utf-8"))["source"] == "skland"
    assert not (paths.STATE / "sanity.json").exists()
    (u,) = sources.unhealthy()
    assert u["source"] == "skland" and u["line"] == "森空岛的数据格式变了（缺 status.ap），理智改用首页识别"


def test_state_skland_chars_format_change_fails(fake, capsys):
    _configure()
    common.write_json(paths.STATE / "box.json", {"at": "old", "operators": {"x": {}}})
    fake.info = _info_without("chars")
    code, env, _ = run(capsys, "state", "skland")
    assert code == 1 and env["error"]["code"] == "upstream_format" and "缺 chars" in env["error"]["message"]
    assert env["data"]["source"] == "skland" and env["data"]["field"] == "chars"
    assert json.loads((paths.STATE / "box.json").read_text(encoding="utf-8"))["at"] == "old"
    assert "干员练度改用 MAA 识别" in sources.unhealthy()[0]["line"]


def test_state_sanity_format_change_falls_back_to_ocr(fake, capsys, monkeypatch):
    from contextlib import nullcontext
    from maapilot.tools import state
    _configure()
    fake.info = _info_without("status", "ap", "current")
    monkeypatch.setattr(common, "device", lambda *a, **k: nullcontext())
    monkeypatch.setattr(state, "read_sanity", lambda: {"at": common.iso(), "current": 50, "max": 135, "source": "ocr"})
    code, env, _ = run(capsys, "state", "sanity")
    assert code == 0 and env["data"]["source"] == "ocr" and "status.ap.current" in env["data"]["skland_error"]
    assert sources.unhealthy()[0]["effect"] == "理智改用首页识别"
    fake.info = _info_without("status", "level")                       # 接口修好了：又读成了
    code, env, _ = run(capsys, "state", "sanity")
    assert env["data"]["source"] == "skland" and sources.unhealthy() == []
