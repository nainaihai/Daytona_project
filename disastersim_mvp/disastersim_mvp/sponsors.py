"""Actual sponsor integrations; errors are surfaced (never fake success)."""
import base64
import json
import os
import re
from pathlib import Path


def run_daytona(rain, start_name):
    if not os.getenv("DAYTONA_API_KEY"):
        raise RuntimeError("DAYTONA_API_KEY is missing")
    from daytona import Daytona
    source = Path(__file__).with_name("sim_engine.py").read_text(encoding="utf-8")
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    script = (
        "import base64, json\n"
        f"exec(compile(base64.b64decode('{encoded}').decode('utf-8'), 'sim_engine.py', 'exec'))\n"
        f"print('SIM_JSON:'+json.dumps(simulate({int(rain)}, {start_name!r}),ensure_ascii=False))\n"
    )
    with Daytona() as daytona:
        sandbox = daytona.v2.create(wait_seconds=60)
        try:
            reply = sandbox.run_code(script)
            output = reply.logs.stdout or ""
            for line in reversed(output.splitlines()):
                if line.startswith("SIM_JSON:"):
                    return json.loads(line[len("SIM_JSON:"):])
            raise RuntimeError(f"Daytona did not return SIM_JSON. Logs: {output[:600]}")
        finally:
            daytona.v2.delete(sandbox)


def _nosana_client():
    """Prefer our own Nosana deployment (NOSANA_URL, Ollama/OpenAI-compatible).
    Falls back to Nosana hosted inference when only NOSANA_API_KEY is set."""
    from openai import OpenAI
    url = os.getenv("NOSANA_URL")
    if url:
        # Ollama on a Nosana GPU needs no key, but the OpenAI client requires a non-empty one.
        return OpenAI(api_key=os.getenv("NOSANA_API_KEY") or "ollama",
                      base_url=url.rstrip("/") + "/v1", timeout=180)
    key = os.getenv("NOSANA_API_KEY")
    if not key:
        raise RuntimeError("NOSANA_URL (or NOSANA_API_KEY) is missing")
    return OpenAI(api_key=key, base_url="https://inference.nosana.com/v1", timeout=60)


def nosana_explain(result):
    client = _nosana_client()
    models = list(client.models.list().data)
    if not models:
        raise RuntimeError("Nosana has no available models right now")
    model = os.getenv("NOSANA_MODEL") or models[0].id
    best = result["best"]
    information = {
        "rain_level": result["rain"], "origin": result["start_name"],
        "selected_shelter": best,
        "recommended_route": result["routes"].get(best) if best else None,
        "other_routes": [dict(name=name,steps=r["steps"],avg_risk=r["avg_risk"],max_risk=r["max_risk"])
                         for name, r in result["routes"].items() if name != best],
    }
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": (
                "あなたは架空の街の防災シミュレーターの解説者です。"
                "以下の計算済みJSONだけを根拠に日本語で3文以内に解説してください。"
                "1) 推奨した避難先 2) 危険度と距離のトレードオフ 3) 仮想データであり実際の避難指示ではないこと。"
                "現実の安全を保証したり、現地の状況や行政の指示を推測しないでください。"
            )},
            {"role": "user", "content": json.dumps(information, ensure_ascii=False)},
        ],
        max_tokens=1500,
    )
    text = response.choices[0].message.content or ""
    # Reasoning models (Qwen3 / GLM etc.) may include <think>...</think>; show only the answer.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).split("</think>")[-1].strip()
    return text or "Nosana returned an empty explanation"
