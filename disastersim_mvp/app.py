"""DisasterSim - natural-language driven, fictional flood routing demo."""
import json
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from geo import DEFAULT_CENTER, Anchor
from road_engine import build_graph, fetch_roads, plan_routes
from interpreter import interpret_locally, normalize_hazard
from sim_engine import AREAS, HAZARD_TYPES, N, SHELTERS, STARTS, apply_hazards, risk_grid, simulate
from sponsors import nosana_explain, nosana_interpret, run_daytona

EXAMPLES = [
    "駅前がかなり浸水してる",
    "橋は通れなさそう",
    "西側の道は大丈夫そう",
    "西の学校のまわりが冠水して通れないらしい",
    "商店街で建物が崩れてる",
]
NOSANA_MODE = "🧠 Nosana LLM（AI解釈）"
LOCAL_MODE = "💻 ローカル簡易ルール（AIではない）"
REAL_MAP = "実在の地図に重ねる"
GRID_MAP = "マス目のみ（架空の街）"
ROAD_ROUTE = "実在の道路に沿う"
CELL_ROUTE = "マス目上（道路を無視）"

# Browser geolocation via a tiny inline component (no extra dependency).
# Location is requested only when the user clicks the button.
geo_locator = st.components.v2.component(
    "geo_locator",
    html='<button class="geo">📍 現在地を取得</button><span class="msg"></span>',
    css="""
    .geo { width: 100%; padding: .5rem .75rem; border-radius: .5rem; cursor: pointer;
           border: 1px solid var(--st-border-color, #ccc); font: inherit;
           background: var(--st-secondary-background-color, #f0f2f6);
           color: var(--st-text-color, inherit); }
    .geo:hover { border-color: var(--st-primary-color, #ff4b4b); }
    .msg { display: block; font-size: .8rem; opacity: .7; margin-top: .25rem; }
    """,
    js="""
    export default function(component) {
      const { setStateValue, parentElement } = component;
      const btn = parentElement.querySelector('.geo');
      const msg = parentElement.querySelector('.msg');
      btn.onclick = () => {
        if (!navigator.geolocation) {
          setStateValue('location', { error: 'このブラウザは位置情報に対応していません' });
          return;
        }
        msg.textContent = '取得中…（ブラウザの許可ダイアログを確認してください）';
        navigator.geolocation.getCurrentPosition(
          (p) => {
            msg.textContent = '';
            setStateValue('location', { lat: p.coords.latitude, lon: p.coords.longitude,
                                        accuracy: p.coords.accuracy, t: Date.now() });
          },
          (e) => {
            msg.textContent = '';
            setStateValue('location', { error: e.message || ('code ' + e.code), t: Date.now() });
          },
          { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
      };
    }
    """,
)

st.set_page_config(page_title="DisasterSim", page_icon="🌊", layout="wide")
st.markdown("# 🌊 DisasterSim")
st.caption("曖昧な現場の声 → AIが数値化 → 避難経路を再計算 • Daytona × Nosana")
st.warning("⚠️ 架空の街・仮想リスクによる技術デモです。実際の避難判断には使用しないでください。")

ss = st.session_state
ss.setdefault("hazards", [])
ss.setdefault("pending", None)
ss.setdefault("pending_meta", None)
ss.setdefault("editor_rev", 0)
ss.setdefault("result", None)
ss.setdefault("previous", None)
ss.setdefault("history", [])

with st.sidebar:
    st.header("🌧️ 災害シナリオ")
    rain = st.slider("大雨の強さ", min_value=1, max_value=5, value=3)
    start_name = st.selectbox("現在地（架空の街の中の位置）", list(STARTS), index=0)
    st.divider()
    st.header("🗺️ 地図")
    map_mode = st.radio("表示方法", [REAL_MAP, GRID_MAP], index=0)
    route_mode = CELL_ROUTE
    if map_mode == REAL_MAP:
        loc_state = geo_locator(key="geo", on_location_change=lambda: None)
        loc = getattr(loc_state, "location", None) if loc_state is not None else None
        if isinstance(loc, dict) and loc.get("lat") is not None:
            ss.real_location = loc
        elif isinstance(loc, dict) and loc.get("error"):
            st.error("位置情報を取得できませんでした: " + str(loc["error"])
                     + "（ブラウザ/macOSの設定で位置情報の利用を許可してください）")
        with st.expander("位置を手動で指定"):
            use_manual = st.checkbox("手動の位置を使う", value=False)
            m_lat = st.number_input("緯度", value=float(DEFAULT_CENTER[0]), format="%.5f")
            m_lon = st.number_input("経度", value=float(DEFAULT_CENTER[1]), format="%.5f")
        if use_manual:
            map_center, map_center_label = (m_lat, m_lon), "手動指定の位置"
        elif ss.get("real_location"):
            loc = ss.real_location
            map_center, map_center_label = (loc["lat"], loc["lon"]), "あなたの現在地（実在）"
            st.success(f"📍 現在地を取得済み（誤差 約{loc.get('accuracy', 0):.0f}m）")
        else:
            map_center, map_center_label = DEFAULT_CENTER, "既定の位置（東京駅付近）"
            st.caption("現在地を取得するまでは東京駅付近に表示します。")
        route_mode = st.radio("経路の計算方法", [ROAD_ROUTE, CELL_ROUTE], index=0,
                              help="実在の道路に沿う: 現在地周辺の道路網をOpenStreetMapから取得し、"
                                   "報告された災害エリアと仮想リスクを避けて経路を探します。")
        st.caption("位置情報は Daytona・Nosana には送信しません。背景地図の画像と道路データは "
                   "OpenStreetMap から読み込むため、表示範囲（約1km四方）が OpenStreetMap のサーバーに伝わります。")
    st.divider()
    st.header("スポンサー連携")
    has_daytona = bool(os.getenv("DAYTONA_API_KEY"))
    has_nosana = bool(os.getenv("NOSANA_API_KEY"))
    st.write("Daytona API Key:", "✅" if has_daytona else "❌")
    st.write("Nosana API Key:", "✅" if has_nosana else "❌")
    use_daytona = st.toggle("Daytona上で経路計算", value=has_daytona, disabled=not has_daytona)
    interp_mode = st.radio("報告の解釈方法", [NOSANA_MODE, LOCAL_MODE],
                           index=0 if has_nosana else 1,
                           disabled=not has_nosana,
                           help="NOSANA_API_KEYが無い場合はローカル簡易ルールのみ使えます。")
    st.caption("ローカル実行・ローカル簡易ルールは画面確認用です。スポンサー連携としてはカウントされません。")
    st.divider()
    if st.button("🔄 報告をすべてリセット", width="stretch"):
        ss.hazards, ss.pending, ss.pending_meta = [], None, None
        ss.history, ss.previous, ss.result = [], None, None


# --------------------------------------------------------------------------
# Simulation (re-runs whenever scenario or confirmed hazards change)

def compute(hazards):
    if use_daytona:
        try:
            with st.spinner("Daytona sandboxで経路シミュレーション中..."):
                return run_daytona(rain, start_name, hazards), "✅ Daytona sandboxで実行", None
        except Exception as e:
            return simulate(rain, start_name, hazards), "⚠️ ローカル代替（Daytona未実行）", str(e)
    return simulate(rain, start_name, hazards), "💻 ローカル実行", None


cache_key = json.dumps([rain, start_name, use_daytona, ss.hazards], ensure_ascii=False, sort_keys=True)
if ss.get("cache_key") != cache_key:
    if ss.result is not None:
        ss.previous = ss.result
    ss.cache_key = cache_key
    ss.explanation = None
    ss.result, ss.engine_mode, ss.engine_error = compute(ss.hazards)
    ss.history.append({"hazards": len(ss.hazards), "best": ss.result["best"],
                       "mode": ss.engine_mode})

result, previous = ss.result, ss.previous


# --------------------------------------------------------------------------
# 1. Natural-language report

st.subheader("📝 1. 現場の状況を文章で報告")
with st.form("report_form", clear_on_submit=True):
    text = st.text_area("見たこと・聞いたことをそのまま書いてください（架空の街の地名で）",
                        placeholder="例: " + " ／ ".join(EXAMPLES[:3]), height=90)
    c1, c2 = st.columns([1, 3])
    submitted = c1.form_submit_button("🧠 AIで解釈", type="primary")
    c2.caption("地名の例: " + "、".join(AREAS))
ex_cols = st.columns(len(EXAMPLES))
for col, ex in zip(ex_cols, EXAMPLES):
    if col.button(ex, key=f"ex_{ex}", width="stretch"):
        text, submitted = ex, True

if submitted and text and text.strip():
    try:
        if interp_mode == NOSANA_MODE:
            with st.spinner("Nosana LLMで報告を解釈中..."):
                hazards, meta = nosana_interpret(text, ss.hazards)
            meta["mode"] = "✅ Nosana LLMで解釈"
        else:
            hazards = interpret_locally(text, ss.hazards)
            meta = {"mode": "💻 ローカル簡易ルールで解釈（AIではない）", "raw": None, "model": None}
        ss.pending, ss.pending_meta = hazards, meta
        ss.editor_rev += 1
    except Exception as e:
        ss.pending = None
        label = "Nosana連携エラー" if interp_mode == NOSANA_MODE else "解釈エラー"
        st.error(f"{label}: {str(e)[:400]}")
        if interp_mode == NOSANA_MODE:
            st.caption("サイドバーで『ローカル簡易ルール』に切り替えると、AIなしで画面を確認できます。")


# --------------------------------------------------------------------------
# 2. Review & edit the interpretation

if ss.pending:
    st.subheader("🔍 2. 解釈結果を確認・修正")
    meta = ss.pending_meta or {}
    st.info("解釈元: " + meta.get("mode", "")
            + (f"（model: `{meta['model']}`）" if meta.get("model") else ""))
    df = pd.DataFrame([{"反映": True, **h} for h in ss.pending])
    edited = st.data_editor(
        df, key=f"editor_{ss.editor_rev}", hide_index=True, width="stretch",
        column_config={
            "反映": st.column_config.CheckboxColumn(width="small"),
            "area": st.column_config.SelectboxColumn("対象エリア", options=list(AREAS), required=True),
            "hazard_type": st.column_config.SelectboxColumn("危険の種類", options=HAZARD_TYPES, required=True),
            "risk_score": st.column_config.NumberColumn("危険スコア", min_value=0.0, max_value=1.0, step=0.05, format="%.2f"),
            "block_probability": st.column_config.NumberColumn("通行不可の可能性", min_value=0.0, max_value=1.0, step=0.05, format="%.2f"),
            "uncertainty": st.column_config.NumberColumn("不確実性", min_value=0.0, max_value=1.0, step=0.05, format="%.2f"),
            "source_text": st.column_config.TextColumn("元の入力文", disabled=True),
            "reason": st.column_config.TextColumn("解釈の根拠"),
        },
    )
    if meta.get("raw"):
        with st.expander("LLMの生の応答"):
            st.code(meta["raw"], language="json")
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("✅ 確定して経路を再計算", type="primary"):
        try:
            rows = [r for r in edited.to_dict("records") if r.pop("反映")]
            ss.hazards = ss.hazards + [normalize_hazard(r) for r in rows]
            ss.pending, ss.pending_meta = None, None
            st.rerun()
        except Exception as e:
            st.error(f"入力値エラー: {e}")
    if b2.button("🗑️ 破棄"):
        ss.pending, ss.pending_meta = None, None
        st.rerun()


# --------------------------------------------------------------------------
# 3. Results

st.subheader("🗺️ 3. 避難経路（モデル上）")
st.info("計算元: " + ss.engine_mode)
if ss.engine_error:
    st.error("Daytona連携エラー: " + ss.engine_error[:300])

COLORSCALE = [[0, "#e1f5ec"], [0.2, "#a9deb8"], [0.5, "#f6d26d"], [0.75, "#f18a55"], [1, "#b93149"]]
DELTA_SCALE = [[0, "#1b8a5a"], [0.5, "#f7f7f7"], [1, "#b93149"]]
HAZARD_COLORS = {"安全情報": "#1b8a5a", "浸水": "#1f6fd1", "通行止め": "#b0102b",
                 "土砂災害": "#8a5a1b", "建物倒壊": "#6b4c9a", "火災": "#e0590b", "その他": "#555555"}


# ---- real-road routing (OpenStreetMap roads x fictional risk) --------------

@st.cache_data(ttl=3600, show_spinner=False, max_entries=8)
def load_road_graph(bbox):
    return build_graph(fetch_roads(bbox))


@st.cache_data(show_spinner=False, max_entries=64)
def road_plan(rain_, start, hazards_json, center):
    anchor = Anchor(STARTS[start], *center)
    graph = load_road_graph(tuple(round(v, 4) for v in anchor.bbox(N)))
    base = risk_grid(rain_, open_water=False)
    grid, unc = apply_hazards(base, json.loads(hazards_json), open_water=False)
    shelters = {k: anchor.latlon(*v) for k, v in SHELTERS.items()}
    plan = plan_routes(graph, grid, anchor, center, shelters)
    return {**plan, "grid": grid, "base_grid": base, "uncertainty_grid": unc, "road": True}


def to_road_view(res):
    if res is None:
        return None
    hz = json.dumps(res.get("hazards", []), ensure_ascii=False, sort_keys=True)
    return {**res, **road_plan(res["rain"], res["start_name"], hz, tuple(map_center))}


use_roads = map_mode == REAL_MAP and route_mode == ROAD_ROUTE
view, prev_view, road_error = result, previous, None
if use_roads:
    try:
        with st.spinner("OpenStreetMapから周辺の道路データを取得中…（初回は30秒ほどかかります）"):
            view, prev_view = to_road_view(result), to_road_view(previous)
    except Exception as e:
        road_error, use_roads = str(e), False
        view, prev_view = result, previous

if use_roads:
    st.info("道路経路: 💻 ローカル計算（OpenStreetMapの実在道路 × 架空の危険度）"
            f"｜道路ノード {view['graph_size']:,} 点")
if road_error:
    st.error("道路データ取得エラー: " + road_error[:300] + " — マス目上の経路を表示しています。"
             "しばらくして再読み込みするか、サイドバーで『マス目上』を選んでください。")


def length_of(route):
    if "distance_m" in route:
        return route["distance_m"], f"{route['distance_m']:,.0f} m"
    return route["steps"], f"{route['steps']} マス"


best = view["best"]
prev_best = prev_view["best"] if prev_view else None
if best:
    r = view["routes"][best]
    pr = prev_view["routes"].get(prev_best) if prev_view and prev_best else None
    same_unit = pr is not None and (("distance_m" in pr) == ("distance_m" in r))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("推奨避難所（モデル上）", best,
              delta=("変更: " + prev_best + " →") if pr and prev_best != best else None,
              delta_color="off")
    c2.metric("経路の長さ", length_of(r)[1],
              delta=f"{length_of(r)[0] - length_of(pr)[0]:+,.0f}" if same_unit else None,
              delta_color="inverse")
    c3.metric("経路上の最大リスク（仮定）", f"{r['max_risk']:.0%}",
              delta=f"{(r['max_risk'] - pr['max_risk']) * 100:+.0f}pt" if pr else None,
              delta_color="inverse")
    c4.metric("反映済みの報告", len(view.get("hazards", [])))
    sp = view.get("shortest_to_best") if use_roads else None
    if sp and sp["max_risk"] > r["max_risk"] + 0.005:
        st.success(f"🛣️ 災害エリアを回避: 最短経路（{sp['distance_m']:,.0f} m・最大リスク {sp['max_risk']:.0%}）より "
                   f"{r['distance_m'] - sp['distance_m']:+,.0f} m 遠回りして、最大リスクを {r['max_risk']:.0%} に抑えています。")
else:
    st.error("このシナリオではモデル上の到達可能経路がありません。")
if view.get("origin_blocked"):
    st.warning("現在地が通行不可と報告されたエリア内にあります。モデルは危険区域から最短で抜け出す経路を示していますが、"
               "実際の行動は自治体・消防などの公式情報に従ってください。")


def _best_latlon(res, anchor):
    b = res.get("best")
    if not b:
        return None
    route = res["routes"][b]
    if "coords" in route:
        return [p[0] for p in route["coords"]], [p[1] for p in route["coords"]]
    return anchor.path(route["path"])


def make_real_map(res, prev=None, z=None, zrange=(0, 1), colorscale=COLORSCALE, ztitle="仮想リスク"):
    """Fictional grid drawn translucently over a real OpenStreetMap basemap."""
    anchor = Anchor(tuple(res["origin"]), *map_center)
    z = res["grid"] if z is None else z
    unc = res.get("uncertainty_grid") or [[0] * N for _ in range(N)]
    ids, zs, cd = [], [], []
    for y in range(N):
        for x in range(N):
            ids.append(f"{x},{y}")
            zs.append(z[y][x])
            cd.append([x, y, unc[y][x]])
    fig = go.Figure()
    fig.add_trace(go.Choroplethmap(
        geojson=anchor.cells_geojson(N), locations=ids, z=zs, zmin=zrange[0], zmax=zrange[1],
        colorscale=colorscale, marker_opacity=0.35, marker_line_width=0, customdata=cd,
        colorbar={"title": ztitle, "tickformat": ".0%"}, name=ztitle, showlegend=False,
        hovertemplate="マス(%{customdata[0]},%{customdata[1]})<br>仮想値=%{z:.1%}"
                      "<br>報告の不確実性=%{customdata[2]:.0%}<extra></extra>",
    ))
    for h in res.get("hazards", []):
        lats, lons = anchor.rect(*AREAS[h["area"]]["rect"])
        fig.add_trace(go.Scattermap(
            lat=lats, lon=lons, mode="lines", name=f"報告: {h['area']}（{h['hazard_type']}）",
            line={"color": HAZARD_COLORS.get(h["hazard_type"], "#555"),
                  "width": 2 if h["uncertainty"] >= .5 else 4},
            hoverinfo="name"))
    if prev:
        pll = _best_latlon(prev, anchor)
        if pll:
            fig.add_trace(go.Scattermap(lat=pll[0], lon=pll[1], mode="lines", name="更新前の経路",
                                        line={"color": "#7a7a7a", "width": 4}, hoverinfo="name"))
    sp = res.get("shortest_to_best") if res.get("road") else None
    if sp:
        fig.add_trace(go.Scattermap(lat=[p[0] for p in sp["coords"]], lon=[p[1] for p in sp["coords"]],
                                    mode="lines", name="最短距離の経路（災害を考慮しない）",
                                    line={"color": "#a23ea0", "width": 3}, hoverinfo="name"))
    b = res["best"]
    bll = _best_latlon(res, anchor)
    if bll:
        fig.add_trace(go.Scattermap(lat=bll[0], lon=bll[1], mode="lines",
                                    name="災害を回避した経路" + ("（実在の道路）" if res.get("road") else "（マス目）"),
                                    line={"color": "#113d91", "width": 6}, hoverinfo="name"))
    for label, (x, y) in res["shelters"].items():
        la, lo = anchor.latlon(x, y)
        fig.add_trace(go.Scattermap(
            lat=[la], lon=[lo], mode="markers", name=f"{'⭐ ' if label == b else ''}{label}（架空）",
            marker={"color": "#136b45" if label == b else "#414f61", "size": 16},
            hovertemplate=f"{label}（架空の避難所）<extra></extra>"))
    la, lo = anchor.latlon(*res["origin"])
    fig.add_trace(go.Scattermap(lat=[la], lon=[lo], mode="markers", showlegend=False,
                                marker={"color": "white", "size": 22}, hoverinfo="skip"))
    fig.add_trace(go.Scattermap(lat=[la], lon=[lo], mode="markers", name="📍 " + map_center_label,
                                marker={"color": "#d61f3c", "size": 15},
                                hovertemplate=f"{map_center_label}<br>シミュレーション上の現在地"
                                              f"「{res['start_name']}」<extra></extra>"))
    clat, clon = anchor.center(N)
    fig.update_layout(height=620, margin={"l": 0, "r": 0, "t": 10, "b": 0},
                      legend={"orientation": "h", "y": -0.02, "yanchor": "top"},
                      map={"style": "open-street-map", "center": {"lat": clat, "lon": clon},
                           "zoom": 14.6})
    return fig


def make_grid_map(res, prev=None, z=None, zrange=(0, 1), colorscale=COLORSCALE, ztitle="仮想リスク"):
    z = res["grid"] if z is None else z
    unc = res.get("uncertainty_grid") or [[0] * N for _ in range(N)]
    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=z, x=list(range(N)), y=list(range(N)), zmin=zrange[0], zmax=zrange[1],
        customdata=unc, colorscale=colorscale,
        colorbar={"title": ztitle, "tickformat": ".0%"},
        hovertemplate="座標(%{x},%{y})<br>値=%{z:.1%}<br>報告の不確実性=%{customdata:.0%}<extra></extra>",
    ))
    for h in res.get("hazards", []):
        x0, y0, x1, y1 = AREAS[h["area"]]["rect"]
        color = HAZARD_COLORS.get(h["hazard_type"], "#555")
        fig.add_shape(type="rect", x0=x0 - .5, y0=y0 - .5, x1=x1 + .5, y1=y1 + .5,
                      line={"color": color, "width": 2, "dash": "dash" if h["uncertainty"] >= .5 else "solid"})
        fig.add_annotation(x=x0 - .4, y=y0 - .4, text=f"{h['area']}:{h['hazard_type']}",
                           showarrow=False, xanchor="left", yanchor="top",
                           font={"size": 10, "color": color}, bgcolor="rgba(255,255,255,.75)")
    o = res["origin"]
    fig.add_trace(go.Scatter(x=[o[0]], y=[o[1]], mode="markers+text", text=["現在地"],
                             textposition="top center", name="現在地",
                             marker={"color": "#1f57ac", "size": 15}))
    b = res["best"]
    for label, (x, y) in res["shelters"].items():
        fig.add_trace(go.Scatter(x=[x], y=[y], mode="markers+text", text=[label],
                                 textposition="top center", name=label,
                                 marker={"color": "#136b45" if label == b else "#414f61",
                                         "size": 15, "symbol": "diamond"}))
    if prev and prev.get("best") and "path" in prev["routes"][prev["best"]]:
        pp = prev["routes"][prev["best"]]["path"]
        fig.add_trace(go.Scatter(x=[v[0] for v in pp], y=[v[1] for v in pp], mode="lines",
                                 name="更新前の経路", line={"color": "#7a7a7a", "width": 4, "dash": "dot"}))
    base = res.get("baseline_to_best")
    if base:
        fig.add_trace(go.Scatter(x=[v[0] for v in base["path"]], y=[v[1] for v in base["path"]],
                                 mode="lines", name="距離だけで選んだ経路",
                                 line={"color": "#a23ea0", "width": 3, "dash": "dot"}))
    if b:
        path = res["routes"][b]["path"]
        fig.add_trace(go.Scatter(x=[v[0] for v in path], y=[v[1] for v in path], mode="lines",
                                 line={"color": "#113d91", "width": 6}, name="危険度を考慮した経路"))
    fig.update_layout(height=560, margin={"l": 5, "r": 5, "t": 20, "b": 5},
                      legend={"orientation": "h", "y": -0.10},
                      xaxis={"dtick": 1, "title": "X"},
                      yaxis={"dtick": 1, "title": "Y", "autorange": "reversed",
                             "scaleanchor": "x", "scaleratio": 1})
    return fig


def make_map(*args, **kwargs):
    return (make_real_map if map_mode == REAL_MAP else make_grid_map)(*args, **kwargs)


col_map, col_details = st.columns([2, 1])
with col_map:
    if use_roads:
        st.caption(f"🗺️ 背景地図・道路網・📍{map_center_label} は実在です。色のマス・災害エリア・避難所は架空で、"
                   "経路は実在の道路を架空の危険度で評価したものです（1マス≈60m）。")
    elif map_mode == REAL_MAP:
        st.caption(f"🗺️ 背景地図と 📍{map_center_label} だけが実在です。色のマス・川・避難所・経路はすべて架空で、"
                   f"現実の道路や浸水状況とは関係ありません（1マス≈60m）。")
    t_after, t_before, t_delta = st.tabs(["更新後", "更新前", "リスク変化"])
    with t_after:
        st.plotly_chart(make_map(view, prev_view), width="stretch", key="map_after")
        if not prev_view:
            st.caption("報告を反映すると、更新前の経路が灰色で重ねて表示されます。")
    with t_before:
        if prev_view:
            st.plotly_chart(make_map(prev_view), width="stretch", key="map_before")
        else:
            st.caption("まだ更新前の計算はありません。")
    with t_delta:
        base = prev_view["grid"] if prev_view else view["base_grid"]
        delta = [[round(view["grid"][y][x] - base[y][x], 3) for x in range(N)] for y in range(N)]
        st.plotly_chart(make_map(view, z=delta, zrange=(-1, 1), ztitle="リスク変化", colorscale=DELTA_SCALE),
                        width="stretch", key="map_delta")
        st.caption("赤: 危険度が上がった / 緑: 下がった（" + ("前回の計算" if prev_view else "報告なしの地形リスク") + "との差）")

with col_details:
    st.subheader("📋 反映済みの報告")
    if not ss.hazards:
        st.caption("まだ報告はありません。上の入力欄から追加してください。")
    for i, h in enumerate(ss.hazards):
        with st.container(border=True):
            st.markdown(f"**{h['area']}**｜{h['hazard_type']}")
            st.caption(f"「{h['source_text']}」")
            st.write(f"危険 {h['risk_score']:.0%}｜通行不可 {h['block_probability']:.0%}｜不確実性 {h['uncertainty']:.0%}")
            if st.button("削除", key=f"del_{i}"):
                ss.hazards = ss.hazards[:i] + ss.hazards[i + 1:]
                st.rerun()
    st.divider()
    st.subheader("🏫 避難所比較")
    for name, route in sorted(view["routes"].items(), key=lambda x: x[1]["risk_cost"]):
        st.markdown(f"**{'⭐ ' if name == best else ''}{name}**")
        st.write(f"{length_of(route)[1]}｜平均リスク {route['avg_risk']:.0%}｜最大 {route['max_risk']:.0%}")
        st.progress(min(1.0, route["avg_risk"]))
    unreachable = [n for n in view["shelters"] if n not in view["routes"]]
    if unreachable:
        st.caption("モデル上到達不可: " + "、".join(unreachable))
    st.caption(("実在の道路の長さ" if use_roads else "距離")
               + "と危険度に重みをつけた経路コストを最小化。危険度88%以上の場所は通行不可として扱います。リスクは人工的な値です。")
    st.divider()
    st.subheader("🧠 Nosana AI説明")
    if st.button("選定理由をAIで説明", type="primary", disabled=not has_nosana):
        try:
            with st.spinner("Nosanaで生成中..."):
                ss.explanation = nosana_explain(view, prev_view)
        except Exception as e:
            st.error("Nosana連携エラー: " + str(e)[:300])
    if ss.get("explanation"):
        st.success(ss.explanation)
        st.caption("✅ Nosana LLM inferenceから取得した応答")
    elif not has_nosana:
        st.caption("NOSANA_API_KEYを設定すると実際のLLM応答を表示できます。")

if len(ss.history) > 1:
    with st.expander("🕒 再計算の履歴"):
        st.table(pd.DataFrame(ss.history).rename(columns={"hazards": "報告数", "best": "推奨避難所", "mode": "計算元"}))

st.divider()
st.caption("No external hazard map or live road closure data is used. This is a fictional demonstration, not an emergency service.")
