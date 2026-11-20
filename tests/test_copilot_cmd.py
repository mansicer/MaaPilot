"""`maapilot copilot find / show / strip`：作业站查询、练度对照、去掉练度要求。

全部用假的作业站（不联网）。作业的格式照 prts.maa.plus 的真实回应缩写。
"""
from __future__ import annotations

import json
import urllib.parse
from types import SimpleNamespace

import pytest

from maapilot import cli, paths
from maapilot.tools import common, copilot_cmd

MTYPES = {"uniequip_002_yao": "X", "uniequip_003_yao": "Y"}


def plan(pid, opers, groups=(), hot=100.0, title=None, actions=3, stage="act52side_04"):
    c = {"stage_name": stage, "doc": {"title": title or f"作业{pid}", "details": "说明"},
         "opers": list(opers), "groups": list(groups), "actions": [{}] * actions, "minimum_required": "v4.0.0"}
    return {"id": pid, "views": 10, "hot_score": hot, "like": 5, "dislike": 0, "rating_level": 9,
            "not_enough_rating": False, "uploader": "某人", "upload_time": "2026-10-09T19:13:27",
            "content": json.dumps(c, ensure_ascii=False)}


def req(elite=2, level=60, skill_level=10, module=0, pot=1):
    return {"elite": elite, "level": level, "skill_level": skill_level, "module": module, "potentiality": pot}


BOX_SKLAND = {
    "遥": {"elite": 2, "level": 60, "potential": 1, "skill_level": 7,
          "skills": [{"index": 1, "mastery": 0}, {"index": 2, "mastery": 3}, {"index": 3, "mastery": 0}],
          "modules": [{"id": "uniequip_002_yao", "level": 1}]},
    "德克萨斯": {"elite": 1, "level": 70, "potential": 3, "skill_level": 4,
              "skills": [{"index": 1, "mastery": 0}, {"index": 2, "mastery": 0}], "modules": []},
}
BOX_MAA = {"遥": {"elite": 2, "level": 60, "potential": 1}, "德克萨斯": {"elite": 1, "level": 70, "potential": 3}}


LEVELS = [
    {"level_id": "obt/memory/level_memory_demkni_1", "stage_id": "mem_demkni_1", "cat_one": "悖论模拟", "cat_two": "重装",
     "cat_three": "塞雷娅", "name": "死路"},
    {"level_id": "activities/act25side/level_act25side_ex04", "stage_id": "act25side_ex04", "cat_one": "活动关卡",
     "cat_two": "孤星", "cat_three": "CW-EX-4", "name": "死路"},
    {"level_id": "activities/act25side/level_act25side_ex04", "stage_id": "act25side_ex04#f#", "cat_one": "活动关卡",
     "cat_two": "孤星", "cat_three": "CW-EX-4", "name": "死路"},
    {"level_id": "activities/act52side/level_act52side_04", "stage_id": "act52side_04", "cat_one": "活动关卡",
     "cat_two": "某活动", "cat_three": "YW-4", "name": "某关"},
]


class FakeSite:
    def __init__(self, plans, levels=None):
        self.plans = {p["id"]: p for p in plans}
        self.levels = LEVELS if levels is None else levels
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if url.endswith("/arknights/level"):
            if self.levels is False:
                raise OSError("no route")
            return {"status_code": 200, "data": self.levels}
        if "/copilot/query" in url:
            # 作业站：认得的 stageId 只给这关的；不认得的词不管它，按热度给（所以会混进别的关）
            kw = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("levelKeyword", [""])[0]
            same = [p for p in self.plans.values() if json.loads(p["content"])["stage_name"].split("#")[0] == kw]
            data = same or list(self.plans.values())
            return {"status_code": 200, "data": {"has_next": False, "page": 1, "total": len(data), "data": data}}
        pid = int(url.rsplit("/", 1)[1])
        if pid not in self.plans:
            return {"status_code": 404, "message": "作业不存在"}
        return {"status_code": 200, "data": self.plans[pid]}


@pytest.fixture
def site(monkeypatch):
    plans = [
        plan(1, [{"name": "遥", "skill": 2, "requirements": req(module=1)},
                 {"name": "德克萨斯", "skill": 1, "requirements": req(level=40, skill_level=7, module=2)}], hot=500),
        plan(2, [{"name": "遥", "skill": 2, "requirements": req()}], hot=10),
        plan(3, [{"name": "遥", "skill": 3, "requirements": req(skill_level=10)},
                 {"name": "史尔特尔", "skill": 3, "requirements": req()}], hot=900),
        plan(4, [{"name": "遥", "skill": 2, "requirements": req()}],
             groups=[{"name": "高台", "opers": [{"name": "能天使", "skill": 3, "requirements": req()},
                                               {"name": "德克萨斯", "skill": 2}]}], hot=50),
    ]
    fake = FakeSite(plans)
    monkeypatch.setattr(copilot_cmd, "http_get_json", fake)
    monkeypatch.setattr(copilot_cmd, "_module_types", lambda: MTYPES)
    from maapilot.knowledge import stages
    monkeypatch.setattr(stages, "resolve_stage",
                        lambda t: SimpleNamespace(stage_id="act52side_04") if t == "YW-4" else (_ for _ in ()).throw(
                            stages.UnknownStageError(t)))
    return fake


def box(ops, source="skland"):
    common.write_json(paths.STATE / "box.json", {"at": "2026-10-11T23:51:36+08:00", "source": source, "operators": ops})


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# --- 差距 ------------------------------------------------------------------------------------

def test_gaps_skland():
    op = BOX_SKLAND["德克萨斯"]
    cur = copilot_cmd.current(op, 1, MTYPES)
    assert copilot_cmd.gaps(req(level=40, skill_level=7, module=2), 1, op, cur, MTYPES) == [
        "要精2 40级，现在精1 70级", "要技能等级 7，现在 4", "要γ模组，现在没有"]
    op = BOX_SKLAND["遥"]
    cur = copilot_cmd.current(op, 3, MTYPES)
    assert cur["mastery"] == 0 and cur["modules"] == [{"type": "χ", "level": 1, "unlocked": None}]
    assert copilot_cmd.gaps(req(skill_level=10, module=1), 3, op, cur, MTYPES) == ["要技能3 专3，现在专0"]
    assert copilot_cmd.gaps(req(level=90, pot=3), 2, op, copilot_cmd.current(op, 2, MTYPES), MTYPES) == [
        "要精2 90级，现在精2 60级", "要潜能 3，现在 1"]
    assert copilot_cmd.gaps(req(), 2, None, None, MTYPES) == ["没有这个干员"]


def test_gaps_maa_source_unknown_is_not_gap():
    op = BOX_MAA["遥"]
    cur = copilot_cmd.current(op, 3, MTYPES)
    assert cur["mastery"] is None and cur["modules"] is None and cur["skill_level"] is None
    assert copilot_cmd.gaps(req(skill_level=10, module=1), 3, op, cur, MTYPES) == []
    assert copilot_cmd.unknowns(req(skill_level=10, module=1), cur, MTYPES) == ["技能等级", "专精", "χ模组"]


def test_module_unlock_inference():
    e0 = {"elite": 0, "level": 1, "modules": [{"id": "uniequip_002_yao", "level": 1}]}
    cur = copilot_cmd.current(e0, 1, MTYPES)
    assert copilot_cmd.gaps(req(elite=0, level=0, skill_level=0, module=1), 1, e0, cur, MTYPES) == ["要χ模组，现在没有"]
    e2 = {"elite": 2, "level": 60, "modules": [{"id": "uniequip_002_yao", "level": 2}]}
    assert copilot_cmd.current(e2, 1, MTYPES)["modules"][0]["unlocked"] is True
    lk = {"elite": 2, "level": 60, "modules": [{"id": "uniequip_002_yao", "level": 1, "locked": True}]}
    cur = copilot_cmd.current(lk, 1, MTYPES)
    assert copilot_cmd.gaps(req(level=0, skill_level=0, module=1), 1, lk, cur, MTYPES) == ["要χ模组，现在没有"]


def test_strip_requirements():
    c = json.loads(plan(4, [{"name": "遥", "skill": 2, "requirements": req()}],
                        groups=[{"name": "g", "opers": [{"name": "a", "requirements": req()}, {"name": "b"}]}])["content"])
    out, n = copilot_cmd.strip_requirements(c)
    assert n == 2 and "requirements" not in json.dumps(out) and "requirements" in json.dumps(c)


# --- find ------------------------------------------------------------------------------------

def test_find_sorts_and_annotates(site, capsys):
    box(BOX_SKLAND)
    code, out = run(capsys, "copilot", "find", "YW-4", "--limit", "5")
    assert code == 0, out
    d = out["data"]
    assert d["stage_id"] == "act52side_04" and d["box_source"] == "skland"
    assert "levelKeyword=act52side_04" in site.urls[0] and "order_by=hot" in site.urls[0] and "limit=5" in site.urls[0]
    # 都有的在前，差距少的在前，没核对到的少的在前（4 的可选位挑的德克萨斯作业没写要求），再按热度
    assert [p["id"] for p in d["plans"]] == [2, 4, 1, 3]
    p2, p4, p1, p3 = d["plans"]
    assert p2["all_owned"] and p2["gap_count"] == 0 and p2["uri"] == "prts://2"
    assert p1["all_owned"] and p1["gap_count"] == 3
    tex = next(o for o in p1["opers"] if o["name"] == "德克萨斯")
    assert tex["have"] is True and tex["current"]["elite"] == 1 and len(tex["gaps"]) == 3
    assert p3["all_owned"] is False and p3["missing_ops"] == ["史尔特尔"]
    g = p4["groups"][0]
    assert g["have"] is True and g["best"] == "德克萨斯" and p4["missing_ops"] == []
    assert p4["all_owned"] is True and p4["gap_count"] == 0
    assert next(o for o in g["opers"] if o["name"] == "能天使")["have"] is False
    assert p3["opers"][0]["gaps"] == ["要技能3 专3，现在专0"]
    assert p2["meets"] is True and p2["unchecked"] == [] and p2["unknown_count"] == 0
    assert p1["meets"] is False and p3["meets"] is False
    # 可选位挑的德克萨斯作业没写要求：人有、差距 0，但不能算满足
    assert p4["meets"] is None and p4["unchecked"] == ["作业没写练度要求，只核对了有没有这名干员：德克萨斯"]


def test_find_without_box(site, capsys):
    code, out = run(capsys, "copilot", "find", "1-7")            # 认不出 → 当关键词；关卡表里也没有 → 作业都去掉
    assert code == 0
    d = out["data"]
    assert d["stage_id"] is None and d["keyword"] == "1-7" and d["box_at"] is None and d["box_source"] is None
    assert d["plans"] == [] and d["dropped"] == 4
    code, out = run(capsys, "copilot", "find", "YW-4")
    d = out["data"]
    assert d["plans"] and d["warning"] is None and d["matched_stages"] is None
    assert all(o["have"] is None for p in d["plans"] for o in p["opers"])
    assert all(p["all_owned"] is None and p["gap_count"] is None for p in d["plans"])
    assert all(p["meets"] is None and p["unknown_count"] is None and p["unchecked"] == [] for p in d["plans"])


def test_find_maa_box(site, capsys):
    box(BOX_MAA, source=None)
    code, out = run(capsys, "copilot", "find", "YW-4")
    p3 = next(p for p in out["data"]["plans"] if p["id"] == 3)
    yao = next(o for o in p3["opers"] if o["name"] == "遥")
    assert yao["gaps"] == [] and yao["current"]["mastery"] is None
    # MAA 识别的练度读不到技能、专精：差距 0，但标明没对照，不算满足
    p2 = next(p for p in out["data"]["plans"] if p["id"] == 2)
    assert p2["all_owned"] and p2["gap_count"] == 0 and p2["unknown_count"] == 2
    assert p2["meets"] is None and p2["unchecked"] == ["遥：技能等级、专精读不到，没对照"]
    assert [p["id"] for p in out["data"]["plans"]][:2] == [2, 4]   # 都有、差距都是 0：没核对到的少的在前
    assert out["data"]["box_source"] == "maa"                       # state box 写的 box.json 没有 source


def test_group_pick_prefers_checkable():
    # 可选位两人差距都是 0：B 读不到技能等级，C 核对过、够 → 挑 C，这份算满足
    g = [{"name": "g", "opers": [{"name": "B", "skill": 1, "requirements": req(level=1, skill_level=7)},
                                 {"name": "C", "skill": 1, "requirements": req(level=1, skill_level=7)}]}]
    ops = {"B": {"elite": 2, "level": 60, "potential": 1},
           "C": {"elite": 2, "level": 60, "potential": 1, "skill_level": 7, "skills": [{"index": 1, "mastery": 0}],
                 "modules": []}}
    p = copilot_cmd.annotate(plan(9, [], groups=g), ops, MTYPES)
    assert p["groups"][0]["best"] == "C" and p["meets"] is True and p["unchecked"] == []
    # 差距一样、都核对得了：写了要求的优先（没写的只核对了人在不在）
    g = [{"name": "g", "opers": [{"name": "B", "skill": 1}, {"name": "C", "skill": 1, "requirements": req(level=1, skill_level=7)}]}]
    assert copilot_cmd.annotate(plan(9, [], groups=g), ops, MTYPES)["groups"][0]["best"] == "C"


def test_unusual_plans():
    ops = {"E": {"elite": 1, "level": 60, "potential": 1, "skill_level": 7,
                 "skills": [{"index": 1, "mastery": 0}, {"index": 2, "mastery": 0}], "modules": []}}
    # 还没有技能3：差距里说了，不再说"专精读不到"
    p = copilot_cmd.annotate(plan(9, [{"name": "E", "skill": 3, "requirements": req(level=1, skill_level=10)}]), ops, MTYPES)
    assert p["meets"] is False and p["opers"][0]["unknown"] == [] and p["unchecked"] == []
    # 要求全写 0 = 没写
    zero = {"elite": 0, "level": 0, "skill_level": 0, "module": 0, "potentiality": 0}
    p = copilot_cmd.annotate(plan(9, [{"name": "E", "skill": 1, "requirements": zero}]), ops, MTYPES)
    assert p["meets"] is None and p["unchecked"] == ["作业没写练度要求，只核对了有没有这名干员：E"]
    # 一个干员都没写：说不准，不算满足
    p = copilot_cmd.annotate(plan(9, []), ops, MTYPES)
    assert p["meets"] is None and p["unchecked"] == ["作业里没写干员，没法核对"]


@pytest.fixture
def keyword_site(monkeypatch):
    # 认不出的关：作业站按关键词给的作业混着别的关（作业站不认的词会按热度给别的关）
    plans = [plan(11, [{"name": "遥"}], stage="act25side_ex04"), plan(12, [{"name": "遥"}], stage="mem_demkni_1"),
             plan(13, [{"name": "遥"}], stage="act25side_ex04#f#"), plan(14, [{"name": "遥"}], stage="act55side_s01")]
    fake = FakeSite(plans)
    monkeypatch.setattr(copilot_cmd, "http_get_json", fake)
    monkeypatch.setattr(copilot_cmd, "_module_types", lambda: MTYPES)
    from maapilot.knowledge import stages
    def unknown(t):                                     # 本地关卡表一律认不出：不管这台电脑有没有游戏数据
        raise stages.UnknownStageError(t)
    monkeypatch.setattr(stages, "resolve_stage", unknown)
    return fake


def test_find_keyword_checks_stage(keyword_site, capsys):
    code, out = run(capsys, "copilot", "find", "死路")
    d = out["data"]
    assert code == 0 and d["stage_id"] is None and d["dropped"] == 0
    assert sorted(p["id"] for p in d["plans"]) == [11, 12, 13]               # 按两关的 stageId 查；突袭也算这关
    assert {m["stage_id"] for m in d["matched_stages"]} == {"mem_demkni_1", "act25side_ex04"}
    assert "对得上 2 关" in d["warning"]
    lab = {p["id"]: p["stage_label"] for p in d["plans"]}
    assert lab[12] == "悖论模拟·重装·塞雷娅「死路」" and lab[13] == "活动关卡·孤星·CW-EX-4「死路」"
    # 关卡号一模一样的只认那一关
    code, out = run(capsys, "copilot", "find", "CW-EX-4")
    assert sorted(p["id"] for p in out["data"]["plans"]) == [11, 13] and "对得上" not in (out["data"]["warning"] or "")


def test_find_keyword_queries_matched_stage_ids(keyword_site, capsys):
    # 关卡号对上了关卡表：按 stageId 去查，作业站就只给这关的（不会按热度混进别的关）
    code, out = run(capsys, "copilot", "find", "CW-EX-4")
    assert any("levelKeyword=act25side_ex04" in u for u in keyword_site.urls)
    assert out["data"]["dropped"] == 0 and sorted(p["id"] for p in out["data"]["plans"]) == [11, 13]


def test_find_without_game_data(site, capsys, monkeypatch):
    # 没有游戏数据：本地认不出 YW-4，但作业站的关卡表认得 → 照样只给这关的，warning 提醒同步数据
    from maapilot.knowledge import stages
    def no_data(t):
        raise FileNotFoundError("stage_table.json")
    monkeypatch.setattr(stages, "resolve_stage", no_data)
    site.plans[99] = plan(99, [{"name": "遥"}], stage="act55side_s01")
    code, out = run(capsys, "copilot", "find", "YW-4")
    d = out["data"]
    assert code == 0 and d["stage_id"] is None and 99 not in [p["id"] for p in d["plans"]] and len(d["plans"]) == 4
    assert d["warning"].startswith("没有游戏数据") and d["matched_stages"][0]["stage_id"] == "act52side_04"


def test_find_keyword_unknown_drops_all(keyword_site, capsys):
    code, out = run(capsys, "copilot", "find", "不存在的关")
    d = out["data"]
    assert code == 0 and d["plans"] == [] and d["dropped"] == 4 and d["matched_stages"] == []
    assert "没有「不存在的关」这一关" in d["warning"]


def test_find_keyword_no_level_table(keyword_site, capsys):
    keyword_site.levels = False                                           # 关卡表连不上：不去掉，但要说
    code, out = run(capsys, "copilot", "find", "死路")
    d = out["data"]
    assert code == 0 and len(d["plans"]) == 4 and d["matched_stages"] is None and "不一定是这关的" in d["warning"]


def test_empty_plan_sorts_after():
    ops = {"遥": BOX_SKLAND["遥"]}
    a = copilot_cmd.annotate(plan(1, [], hot=999), ops, MTYPES)
    b = copilot_cmd.annotate(plan(2, [{"name": "遥", "skill": 2, "requirements": req(level=1, skill_level=7)}], hot=1),
                             ops, MTYPES)
    assert [p["id"] for p in sorted([a, b], key=copilot_cmd._sort_key)] == [2, 1]


def test_network_error(monkeypatch, capsys):
    def boom(url):
        raise OSError("no route")
    monkeypatch.setattr(copilot_cmd, "http_get_json", boom)
    code, out = run(capsys, "copilot", "find", "something")
    assert code == 1 and out["error"]["code"] == "network"


# --- show / strip ----------------------------------------------------------------------------

def test_show(site, capsys):
    box(BOX_SKLAND)
    code, out = run(capsys, "copilot", "show", "prts://1")
    assert code == 0, out
    d = out["data"]
    assert d["id"] == 1 and d["details"] == "说明" and d["actions"] == 3 and d["title"] == "作业1"
    assert len(d["opers"]) == 2 and d["gap_count"] == 3
    code, out = run(capsys, "copilot", "show", "99")
    assert code == 2 and out["error"]["code"] == "not_found"
    code, out = run(capsys, "copilot", "show", "abc")
    assert code == 2 and out["error"]["code"] == "bad_arguments"


def test_strip_writes_file(site, capsys):
    code, out = run(capsys, "copilot", "strip", "4")
    assert code == 0, out
    p = paths.PRIVATE / "cache" / "copilot" / "4_noreq.json"
    assert out["data"] == {"id": 4, "path": str(p.resolve()), "removed": 2}
    c = json.loads(p.read_text(encoding="utf-8"))
    assert "requirements" not in json.dumps(c) and len(c["actions"]) == 3 and c["opers"][0]["name"] == "遥"
