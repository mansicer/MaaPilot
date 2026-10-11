"""`maapilot copilot find / show / strip`：作业站查询、练度对照、去掉练度要求。

全部用假的作业站（不联网）。作业的格式照 prts.maa.plus 的真实回应缩写。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from maapilot import cli, paths
from maapilot.tools import common, copilot_cmd

MTYPES = {"uniequip_002_yao": "X", "uniequip_003_yao": "Y"}


def plan(pid, opers, groups=(), hot=100.0, title=None, actions=3):
    c = {"stage_name": "act52side_04", "doc": {"title": title or f"作业{pid}", "details": "说明"},
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


class FakeSite:
    def __init__(self, plans):
        self.plans = {p["id"]: p for p in plans}
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "/copilot/query" in url:
            return {"status_code": 200, "data": {"has_next": False, "page": 1, "total": len(self.plans),
                                                 "data": list(self.plans.values())}}
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
    assert [p["id"] for p in d["plans"]] == [4, 2, 1, 3]          # 都有的在前，差距少的在前，再按热度
    p4, p2, p1, p3 = d["plans"]
    assert p2["all_owned"] and p2["gap_count"] == 0 and p2["uri"] == "maa://2"
    assert p1["all_owned"] and p1["gap_count"] == 3
    tex = next(o for o in p1["opers"] if o["name"] == "德克萨斯")
    assert tex["have"] is True and tex["current"]["elite"] == 1 and len(tex["gaps"]) == 3
    assert p3["all_owned"] is False and p3["missing_ops"] == ["史尔特尔"]
    g = p4["groups"][0]
    assert g["have"] is True and g["best"] == "德克萨斯" and p4["missing_ops"] == []
    assert p4["all_owned"] is True and p4["gap_count"] == 0
    assert next(o for o in g["opers"] if o["name"] == "能天使")["have"] is False
    assert p3["opers"][0]["gaps"] == ["要技能3 专3，现在专0"]


def test_find_without_box(site, capsys):
    code, out = run(capsys, "copilot", "find", "1-7")            # 认不出 → 当关键词
    assert code == 0
    d = out["data"]
    assert d["stage_id"] is None and d["keyword"] == "1-7" and d["box_at"] is None
    assert all(o["have"] is None for p in d["plans"] for o in p["opers"])
    assert all(p["all_owned"] is None and p["gap_count"] is None for p in d["plans"])


def test_find_maa_box(site, capsys):
    box(BOX_MAA, source=None)
    code, out = run(capsys, "copilot", "find", "YW-4")
    p3 = next(p for p in out["data"]["plans"] if p["id"] == 3)
    yao = next(o for o in p3["opers"] if o["name"] == "遥")
    assert yao["gaps"] == [] and yao["current"]["mastery"] is None


def test_network_error(monkeypatch, capsys):
    def boom(url):
        raise OSError("no route")
    monkeypatch.setattr(copilot_cmd, "http_get_json", boom)
    code, out = run(capsys, "copilot", "find", "something")
    assert code == 1 and out["error"]["code"] == "network"


# --- show / strip ----------------------------------------------------------------------------

def test_show(site, capsys):
    box(BOX_SKLAND)
    code, out = run(capsys, "copilot", "show", "maa://1")
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
