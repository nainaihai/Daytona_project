import json

import pytest

from interpreter import (build_prompt, interpret_locally, normalize_all,
                         normalize_hazard, parse_llm_json)
from sim_engine import AREAS, HAZARD_TYPES
from sponsors import build_daytona_script, parse_daytona_output


def test_local_station_flood():
    [h] = interpret_locally("駅前がかなり浸水してる")
    assert h["area"] == "駅前"
    assert h["hazard_type"] == "浸水"
    assert h["risk_score"] >= 0.8
    assert h["source_text"] == "駅前がかなり浸水してる"


def test_local_bridge_blocked_is_uncertain():
    [h] = interpret_locally("橋は通れなさそう")
    assert h["area"] == "中央橋"
    assert h["hazard_type"] == "通行止め"
    assert h["block_probability"] >= 0.8
    assert h["uncertainty"] >= 0.6  # hedged and ambiguous bridge


def test_local_safe_report():
    [h] = interpret_locally("西側の道は大丈夫そう")
    assert h["area"] == "西側の道"
    assert h["hazard_type"] == "安全情報"
    assert h["risk_score"] <= 0.2 and h["block_probability"] == 0


def test_local_specific_bridge_and_multiple_sentences():
    hs = interpret_locally("東の橋が冠水してる。商店街は少し水が出てる")
    assert [h["area"] for h in hs] == ["東の橋", "商店街"]
    assert hs[1]["risk_score"] < hs[0]["risk_score"]


def test_local_requires_location():
    with pytest.raises(ValueError):
        interpret_locally("なんか危ない")


def test_parse_llm_json_variants():
    item = {"area": "駅前", "hazard_type": "浸水", "risk_score": 0.8,
            "block_probability": 0.2, "uncertainty": 0.3, "source_text": "x"}
    plain = json.dumps({"hazards": [item]}, ensure_ascii=False)
    assert parse_llm_json(plain) == [item]
    assert parse_llm_json("```json\n" + plain + "\n```") == [item]
    assert parse_llm_json("結果です: " + plain + " 以上") == [item]
    assert parse_llm_json(json.dumps([item])) == [item]
    with pytest.raises(ValueError):
        parse_llm_json("JSONではありません")


def test_normalize_clamps_and_corrects():
    h = normalize_hazard({"area": "駅の前", "hazard_type": "洪水", "risk_score": 3,
                          "block_probability": -1, "uncertainty": 0.1}, "元文")
    assert h["area"] == "駅前"
    assert h["hazard_type"] == "その他"
    assert h["risk_score"] == 1.0 and h["block_probability"] == 0.0
    assert h["uncertainty"] >= 0.6  # corrections raise uncertainty
    assert h["source_text"] == "元文"
    with pytest.raises(ValueError):
        normalize_hazard({"area": "火星", "hazard_type": "浸水"})
    with pytest.raises(ValueError):
        normalize_all([])


def test_prompt_lists_schema_and_context():
    msgs = build_prompt("橋が危ない", [{"area": "駅前", "hazard_type": "浸水",
                                        "risk_score": .8, "block_probability": .2}])
    assert all(a in msgs[0]["content"] for a in AREAS)
    assert all(t in msgs[0]["content"] for t in HAZARD_TYPES)
    assert "already_known" in msgs[1]["content"]


def test_daytona_script_runs_locally():
    """The exact script sent to Daytona must work in a plain Python process."""
    import contextlib
    import io
    hazards = interpret_locally("駅前がかなり浸水してる")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(build_daytona_script(3, "駅前 (南中央)", hazards), {})
    result = parse_daytona_output(buf.getvalue())
    assert result["hazards"] == hazards
    assert result["start_name"] == "駅前 (南中央)"
    with pytest.raises(RuntimeError):
        parse_daytona_output("no marker")
