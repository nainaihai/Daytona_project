"""Actual sponsor integrations; errors are surfaced (never fake success)."""
import base64
import json
import os
from pathlib import Path

from interpreter import build_prompt, normalize_all, parse_llm_json

NOSANA_BASE_URL = os.getenv("NOSANA_BASE_URL", "https://inference.nosana.com/v1")


# --------------------------------------------------------------------------
# Daytona: run the same sim_engine.py inside an isolated sandbox

def build_daytona_script(rain, start_name, hazards):
    source = Path(__file__).with_name("sim_engine.py").read_text(encoding="utf-8")
    code = base64.b64encode(source.encode("utf-8")).decode("ascii")
    payload = base64.b64encode(json.dumps(
        {"rain": int(rain), "start_name": start_name, "hazards": hazards or []},
        ensure_ascii=False).encode("utf-8")).decode("ascii")
    return (
        "import base64, json\n"
        "ns = {'__name__': 'sim_engine'}\n"
        f"exec(compile(base64.b64decode('{code}').decode('utf-8'), 'sim_engine.py', 'exec'), ns)\n"
        f"args = json.loads(base64.b64decode('{payload}').decode('utf-8'))\n"
        "print('SIM_JSON:' + json.dumps(ns['simulate'](args['rain'], args['start_name'], args['hazards']), ensure_ascii=False))\n"
    )


def parse_daytona_output(output):
    for line in reversed((output or "").splitlines()):
        if line.startswith("SIM_JSON:"):
            return json.loads(line[len("SIM_JSON:"):])
    raise RuntimeError(f"Daytona did not return SIM_JSON. Logs: {(output or '')[:600]}")


def run_daytona(rain, start_name, hazards=None):
    """Run the simulation in a fresh Daytona sandbox (Python SDK, no CLI)."""
    if not os.getenv("DAYTONA_API_KEY"):
        raise RuntimeError("DAYTONA_API_KEY is missing")
    from daytona import Daytona  # reads DAYTONA_API_KEY / DAYTONA_API_URL / DAYTONA_TARGET

    daytona = Daytona()
    sandbox = daytona.create(timeout=90)
    try:
        reply = sandbox.process.code_run(build_daytona_script(rain, start_name, hazards), timeout=60)
        if reply.exit_code != 0:
            raise RuntimeError(f"Daytona code_run exit {reply.exit_code}: {(reply.result or '')[:600]}")
        result = parse_daytona_output(reply.result)
        result["executed_in"] = f"daytona:{sandbox.id}"
        return result
    finally:
        daytona.delete(sandbox)


# --------------------------------------------------------------------------
# Nosana: OpenAI-compatible inference

def _nosana_client():
    key = os.getenv("NOSANA_API_KEY")
    if not key:
        raise RuntimeError("NOSANA_API_KEY is missing")
    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=NOSANA_BASE_URL, timeout=30)
    model = os.getenv("NOSANA_MODEL")
    if not model:
        models = list(client.models.list().data)
        if not models:
            raise RuntimeError("Nosana has no available models right now")
        model = models[0].id
    return client, model


def nosana_interpret(text, current_hazards=None):
    """Ask the Nosana-hosted LLM to structure a vague report into hazards.

    Returns (hazards, meta) where meta holds the model id and raw reply so the
    UI can show exactly what the AI said.
    """
    client, model = _nosana_client()
    response = client.chat.completions.create(
        model=model,
        messages=build_prompt(text, current_hazards),
        temperature=0,
        max_tokens=700,
    )
    raw = response.choices[0].message.content or ""
    hazards = normalize_all(parse_llm_json(raw), text)
    return hazards, {"model": model, "raw": raw}


def nosana_explain(result, previous=None):
    client, model = _nosana_client()
    best = result["best"]
    information = {
        "rain_level": result["rain"], "origin": result["start_name"],
        "selected_shelter": best,
        "recommended_route": ({k: v for k, v in result["routes"][best].items() if k not in ("path", "coords")}
                              if best else None),
        "other_routes": [dict(name=name, steps=r.get("steps"), distance_m=r.get("distance_m"), avg_risk=r["avg_risk"], max_risk=r["max_risk"])
                         for name, r in result["routes"].items() if name != best],
        "reported_hazards": [{k: h[k] for k in ("area", "hazard_type", "risk_score",
                                                "block_probability", "uncertainty", "source_text")}
                             for h in result.get("hazards", [])],
    }
    if previous and previous.get("best"):
        pb = previous["routes"][previous["best"]]
        information["before_update"] = {"selected_shelter": previous["best"], "steps": pb.get("steps"),
                                        "distance_m": pb.get("distance_m"),
                                        "max_risk": pb["max_risk"]}
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": (
                "あなたは架空の街の防災シミュレーターの解説者です。"
                "以下の計算済みJSONだけを根拠に日本語で4文以内に解説してください。"
                "1) 推奨した避難先 2) 住民の報告がどう経路を変えたか（before_updateがあれば比較） "
                "3) 危険度と距離のトレードオフ、不確実性の高い報告があればその注意 "
                "4) 仮想データであり実際の避難指示ではないこと。"
                "現実の安全を保証したり、現地の状況や行政の指示を推測しないでください。"
            )},
            {"role": "user", "content": json.dumps(information, ensure_ascii=False)},
        ],
        max_tokens=380,
    )
    return response.choices[0].message.content or "Nosana returned an empty explanation"
