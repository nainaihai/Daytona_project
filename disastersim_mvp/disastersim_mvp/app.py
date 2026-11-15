"""DisasterSim - hackathon toy-city flood routing demo."""
import os
import streamlit as st
import plotly.graph_objects as go
from sim_engine import N, STARTS, simulate
from sponsors import run_daytona, nosana_explain

st.set_page_config(page_title="DisasterSim", page_icon="🌊", layout="wide")
st.markdown("# 🌊 DisasterSim")
st.caption("AI-powered evacuation scenario simulator • Daytona × Nosana")
st.warning("⚠️ 架空の街・仮想リスクによる技術デモです。実際の避難判断には使用しないでください。")
with st.sidebar:
    st.header("🌧️ 災害シナリオ")
    rain = st.slider("大雨の強さ", min_value=1, max_value=5, value=3)
    start_name = st.selectbox("現在地（架空）", list(STARTS), index=0)
    st.divider()
    st.header("スポンサー連携")
    has_daytona = bool(os.getenv("DAYTONA_API_KEY"))
    has_nosana = bool(os.getenv("NOSANA_URL") or os.getenv("NOSANA_API_KEY"))
    st.write("Daytona API Key:", "✅" if has_daytona else "❌")
    st.write("Nosana 接続先:", "✅" if has_nosana else "❌")
    use_daytona = st.toggle("Daytona上で経路計算", value=has_daytona, disabled=not has_daytona)
    st.caption("Daytona接続前はローカルでも画面を確認できます。スポンサー連携としてはカウントされません。")

cache_key = (rain, start_name, use_daytona)
if st.session_state.get("cache_key") != cache_key:
    st.session_state.cache_key = cache_key
    st.session_state.explanation = None
    st.session_state.engine_error = None
    if use_daytona:
        try:
            with st.spinner("Daytona sandboxで経路シミュレーション中..."):
                st.session_state.result = run_daytona(rain, start_name)
                st.session_state.engine_mode = "✅ Daytona sandboxで実行"
        except Exception as e:
            st.session_state.engine_error = str(e)
            st.session_state.result = simulate(rain, start_name)
            st.session_state.engine_mode = "⚠️ ローカル代替（Daytona未実行）"
    else:
        st.session_state.result = simulate(rain, start_name)
        st.session_state.engine_mode = "💻 ローカル実行"

result = st.session_state.result
st.info("計算元: " + st.session_state.engine_mode)
if st.session_state.engine_error:
    st.error("Daytona連携エラー: " + st.session_state.engine_error[:300])

best = result["best"]
if best:
    r = result["routes"][best]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("推奨避難所（モデル上）", best)
    c2.metric("経路の移動ステップ", r["steps"])
    c3.metric("経路上の最大リスク（仮定）", f"{r['max_risk']:.0%}")
    c4.metric("評価対象の避難先", len(result["routes"]))
else:
    st.error("このシナリオではモデル上の到達可能経路がありません。")

fig = go.Figure()
fig.add_trace(go.Heatmap(
    z=result["grid"], x=list(range(N)), y=list(range(N)), zmin=0, zmax=1,
    colorscale=[[0,"#e1f5ec"],[0.2,"#a9deb8"],[0.5,"#f6d26d"],[0.75,"#f18a55"],[1,"#b93149"]],
    colorbar={"title":"仮想リスク", "tickformat":".0%"},
    hovertemplate="座標(%{x},%{y})<br>仮想危険度=%{z:.1%}<extra></extra>",
))
origin = result["origin"]
fig.add_trace(go.Scatter(x=[origin[0]],y=[origin[1]],mode="markers+text",text=["現在地"],
                         textposition="top center",marker={"color":"#1f57ac","size":15,"symbol":"circle"},name="現在地"))
for label, (x, y) in result["shelters"].items():
    fig.add_trace(go.Scatter(x=[x], y=[y],mode="markers+text",text=[label],textposition="top center",
                             marker={"color":"#136b45" if label==best else "#414f61","size":15,
                                     "symbol":"diamond"},name=label))
if best:
    path = result["routes"][best]["path"]
    fig.add_trace(go.Scatter(x=[v[0] for v in path],y=[v[1] for v in path],mode="lines",
                             line={"color":"#113d91","width":6},name="危険度を考慮した経路"))
    baseline = result.get("baseline_to_best")
    if baseline:
        path2 = baseline["path"]
        fig.add_trace(go.Scatter(x=[v[0] for v in path2],y=[v[1] for v in path2],mode="lines",
                                 line={"color":"#6c2a68","width":3,"dash":"dot"},
                                 name="距離だけで選んだ経路"))
fig.update_layout(height=570,margin={"l":5,"r":5,"t":20,"b":5},
                  legend={"orientation":"h","y":-0.10},
                  xaxis={"dtick":1,"title":"X"},
                  yaxis={"dtick":1,"title":"Y","autorange":"reversed","scaleanchor":"x","scaleratio":1})
col_map, col_details = st.columns([2,1])
with col_map:
    st.plotly_chart(fig, use_container_width=True)
with col_details:
    st.subheader("🏫 避難所比較")
    for name, route in sorted(result["routes"].items(),key=lambda x:x[1]["risk_cost"]):
        st.markdown(f"**{'⭐ ' if name==best else ''}{name}**")
        st.write(f"{route['steps']} steps｜平均リスク {route['avg_risk']:.0%}｜最大 {route['max_risk']:.0%}")
        st.progress(min(1.0, route["avg_risk"]))
    st.caption("最短距離のみではなく、距離と危険度に重みをつけた経路コストを最小化。リスクは人工的な値です。")
    st.divider()
    st.subheader("🧠 Nosana AI説明")
    if st.button("選定理由をAIで説明", type="primary", disabled=not has_nosana):
        try:
            with st.spinner("Nosanaで生成中...（初回はモデル読み込みで1〜2分かかることがあります）"):
                st.session_state.explanation = nosana_explain(result)
        except Exception as e:
            st.error("Nosana連携エラー: " + str(e)[:300])
    if st.session_state.get("explanation"):
        st.success(st.session_state.explanation)
        st.caption("✅ Nosana LLM inferenceから取得した応答")
    elif not has_nosana:
        st.caption("NOSANA_URL（NosanaデプロイのEndpoint URL）を設定すると実際のLLM応答を表示できます。")
    else:
        st.caption("ボタンを押すとNosanaのLLMに解析結果を送信します。")

st.divider()
st.caption("No external hazard map or live road closure data is used. This is a fictional demonstration, not an emergency service.")
