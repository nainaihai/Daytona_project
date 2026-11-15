"""Turn vague natural-language field reports into structured hazard records.

Two interpreters share one output schema:
- Nosana LLM (sponsors.nosana_interpret): the real AI path.
- interpret_locally: a keyword-rule fallback for offline demos. It is NOT AI
  and the UI labels it as such.

Schema of one hazard record:
    area               one of sim_engine.AREAS
    hazard_type        one of sim_engine.HAZARD_TYPES
    risk_score         0..1  provisional danger of the area
    block_probability  0..1  chance the area is impassable
    uncertainty        0..1  how unsure the interpretation is
    source_text        the original sentence
    reason             short note on how it was interpreted
"""
import json
import re

from sim_engine import AREAS, HAZARD_TYPES

FIELDS = ["area", "hazard_type", "risk_score", "block_probability",
          "uncertainty", "source_text", "reason"]


def area_catalog():
    return [{"area": name, "description": a["desc"]} for name, a in AREAS.items()]


def build_prompt(text, current_hazards=None):
    """System + user messages asking an LLM for schema-conformant JSON."""
    system = (
        "あなたは架空の街の防災シミュレーター用の情報整理AIです。"
        "住民の曖昧な報告文を、計算に使える構造化JSONに変換します。\n"
        "必ず次の形式のJSONだけを出力してください（説明文やコードブロックは不要）:\n"
        '{"hazards":[{"area":"...","hazard_type":"...","risk_score":0.0,'
        '"block_probability":0.0,"uncertainty":0.0,"source_text":"...","reason":"..."}]}\n'
        "ルール:\n"
        f"- area は次のいずれかの名前と完全一致させる: {json.dumps(area_catalog(), ensure_ascii=False)}\n"
        f"- hazard_type は次のいずれか: {json.dumps(HAZARD_TYPES, ensure_ascii=False)}\n"
        "- risk_score: その場所の危険度 0〜1（「大丈夫そう」なら0.1前後、「めっちゃ浸水」なら0.8以上）\n"
        "- block_probability: 通行できない可能性 0〜1\n"
        "- uncertainty: 解釈の不確実性 0〜1。「〜そう」「〜かも」「らしい」等の推量、"
        "場所が曖昧（例: 単に『橋』）、伝聞の場合は高くする\n"
        "- 1文に複数の場所があれば複数要素に分ける。source_text には元の文をそのまま入れる\n"
        "- reason には解釈の根拠を日本語で短く書く\n"
        "- 一覧にない場所は最も近いareaを選び、uncertaintyを0.6以上にする\n"
        "- 架空の街のデモであり、現実の場所や安全性を推測しない"
    )
    user = {"report": text}
    if current_hazards:
        user["already_known"] = [
            {k: h.get(k) for k in ("area", "hazard_type", "risk_score", "block_probability")}
            for h in current_hazards
        ]
        user["note"] = "already_known は既に反映済みの情報。新しい報告が同じ場所の状況変化なら、その場所について新しい値を出力する。"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def parse_llm_json(raw):
    """Extract the hazards list from an LLM reply that should be JSON."""
    if not raw or not raw.strip():
        raise ValueError("LLMの応答が空でした")
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                    break
                except json.JSONDecodeError:
                    continue
    if data is None:
        raise ValueError(f"LLMの応答をJSONとして解析できませんでした: {raw[:200]}")
    if isinstance(data, dict):
        data = data.get("hazards", [data] if "area" in data else [])
    if not isinstance(data, list):
        raise ValueError("LLMの応答に hazards 配列がありません")
    return data


def _num(v, default):
    try:
        return round(min(1.0, max(0.0, float(v))), 2)
    except (TypeError, ValueError):
        return default


def normalize_hazard(item, fallback_text=""):
    """Validate one record; unknown area/type are coerced and flagged uncertain."""
    if not isinstance(item, dict):
        raise ValueError(f"解釈結果の形式が不正です: {item!r}")
    notes = []
    area = str(item.get("area", "")).strip()
    if area not in AREAS:
        guess = match_area(area) if area else None
        if guess is None:
            raise ValueError(f"地図上のエリアに対応付けできません: {area or '(空)'}")
        notes.append(f"エリア名『{area}』を『{guess}』に補正")
        area = guess
    htype = str(item.get("hazard_type", "")).strip()
    if htype not in HAZARD_TYPES:
        notes.append(f"危険種別『{htype}』を『その他』に補正")
        htype = "その他"
    uncertainty = _num(item.get("uncertainty"), 0.5)
    if notes:
        uncertainty = max(uncertainty, 0.6)
    reason = str(item.get("reason", "") or "").strip()
    if notes:
        reason = (reason + "／" if reason else "") + "、".join(notes)
    return {
        "area": area,
        "hazard_type": htype,
        "risk_score": _num(item.get("risk_score"), 0.5),
        "block_probability": _num(item.get("block_probability"), 0.0),
        "uncertainty": uncertainty,
        "source_text": str(item.get("source_text") or fallback_text),
        "reason": reason,
    }


def normalize_all(items, fallback_text=""):
    out = [normalize_hazard(i, fallback_text) for i in items]
    if not out:
        raise ValueError("報告から危険情報を読み取れませんでした。場所を含めて入力してください。")
    return out


# --------------------------------------------------------------------------
# Local keyword interpreter (offline demo, not AI)

_ALIASES = sorted(((alias, name) for name, a in AREAS.items() for alias in a["aliases"]),
                  key=lambda p: -len(p[0]))

_TYPE_RULES = [
    ("安全情報", ["大丈夫", "問題ない", "問題なし", "平気", "安全", "通れる", "通れた", "空いて", "引いた"]),
    ("通行止め", ["通れな", "通行止め", "封鎖", "通れません", "渡れな", "危なそう", "危ない"]),
    ("土砂災害", ["土砂", "がけ崩れ", "崖崩れ", "地すべり"]),
    ("建物倒壊", ["倒壊", "倒れ", "崩れ", "瓦礫", "がれき"]),
    ("火災", ["火事", "火災", "燃え", "煙"]),
    ("浸水", ["浸水", "冠水", "水没", "水が", "水たまり", "あふれ", "溢れ", "氾濫", "水位"]),
]
_STRONG = ["めっちゃ", "かなり", "すごく", "とても", "完全", "ひどい", "深い", "腰", "胸", "激しい"]
_WEAK = ["少し", "ちょっと", "やや", "多少", "足首", "くるぶし"]
_HEDGE = ["そう", "かも", "らしい", "みたい", "気がする", "っぽい", "聞いた", "たぶん", "多分", "ような"]
_NEGATED_SAFE = ["通れな", "渡れな", "大丈夫じゃな", "安全じゃな", "安全ではな"]


def match_area(text):
    for alias, name in _ALIASES:
        if alias in text:
            return name
    return None


def _find_areas(sentence):
    found, rest = [], sentence
    for alias, name in _ALIASES:
        if alias in rest:
            if name not in (n for n, _ in found):
                found.append((name, alias))
            rest = rest.replace(alias, "　")
    return found


def interpret_locally(text, current_hazards=None):
    """Rule-based interpretation for offline demo mode (no LLM)."""
    sentences = [s.strip() for s in re.split(r"[。．！!？?\n]+", text or "") if s.strip()]
    results = []
    for s in sentences:
        areas = _find_areas(s)
        if not areas:
            continue
        htype = "その他"
        for t, words in _TYPE_RULES:
            if any(w in s for w in words):
                if t == "安全情報" and any(n in s for n in _NEGATED_SAFE):
                    continue
                htype = t
                break
        strong = any(w in s for w in _STRONG)
        weak = any(w in s for w in _WEAK)
        hedged = any(w in s for w in _HEDGE)
        if htype == "安全情報":
            score, block = 0.1, 0.0
        elif htype == "通行止め":
            score, block = 0.8, 0.85
        elif htype == "その他":
            score, block = 0.5, 0.2
        else:
            score, block = 0.65, 0.3
        if htype != "安全情報":
            if strong:
                bump = 0.35 if htype == "通行止め" else 0.2
                score, block = min(1.0, score + 0.2), min(1.0, block + bump)
            if weak:
                score, block = max(0.2, score - 0.25), max(0.0, block - 0.2)
        uncertainty = 0.3 + (0.25 if hedged else 0.0) + (0.25 if htype == "その他" else 0.0)
        for name, alias in areas:
            u = uncertainty
            why = [f"『{alias}』→{name}", f"種別={htype}"]
            if alias == "橋":
                u += 0.2
                why.append("どの橋か不明なので中央橋と仮定")
            if strong:
                why.append("強調表現あり")
            if weak:
                why.append("軽度の表現あり")
            if hedged:
                why.append("推量・伝聞表現あり")
            results.append({
                "area": name, "hazard_type": htype,
                "risk_score": round(score, 2), "block_probability": round(block, 2),
                "uncertainty": round(min(0.95, u), 2), "source_text": s,
                "reason": "ルール解析: " + "、".join(why),
            })
    return normalize_all(results, text)
