# Daytona_project
災害時の避難判断を支援するAIシミュレーターを作ろうと思っています！

**コンセプト**
災害現場では、状況が刻々と変化するうえに、得られる情報も「この道めっちゃ浸水してる」「橋が危なそう」といった曖昧なものが多いと思います。

そこで、人間が自然言語や地図への殴り書きで伝えた情報を、AIが解釈して定量的なリスクデータに変換し、避難経路をリアルタイムに再計算するシステムを考えています。

**具体的な流れ**
1. 地図上に避難所・現在地・危険区域を表示
2. ユーザーが「駅前の道が冠水してる」などと入力
3. AIが場所・危険度・不確実性を解釈して数値化
4. シミュレーションを再実行し、避難経路を更新
5. 追加情報が入るたびに、AIが状況を再解釈して再計算

**技術構成**
- Nosana：自然言語の理解・構造化・説明
- Daytona：Pythonによる経路探索・シミュレーション実行
- Streamlit：地図表示と操作UI

**差別化ポイント**
従来のシミュレーターは、あらかじめ整理されたデータを入力する必要がありますが、今回は人間の曖昧な観察や直感をそのまま計算可能なデータに変換できることが特徴です。

まずは架空の街を使って、文章入力によって危険区域や推奨経路が変化するMVPを作ります。実際の避難判断には使用しないデモです。

ブラッシュアップ案
1. 地図を具体的にどこにするか --soichiro
2. userの情報追加場所 --toshiya
4. 3Dでモデル化
5. 評価対象の避難先がなにをもって定量化しているか不明瞭 --nanami
6. 平均リスクの内容が不明瞭
7. 4stepsとかなにをさしてるのかがわからない,距離 --yu
8. スマホ用のwebアプリにする 
9. 指定されいてる避難所を高い優先順位で出力
10. sponser --yuga

Endpoints
https://4W7pPTuxYDLv7HApt2QYY4rqMPoWVt2jnJZP2a2d7MMR.node.k8s.prd.nos.ci

以下のPythonプロジェクト（Streamlitアプリ「DisasterSim」）のNosana連携部分を修正してください。
他のロジック（Daytona連携、sim_engine、画面レイアウトなど）は変更しないでください。
ファイル構成や関数名が想定と違う場合は、同じ意図になるように合わせて修正してください。

【背景】
Nosanaのダッシュボードで、自分たちのデプロイ（Ollama、ポート11434、RTX 3090）を立ち上げた。
このデプロイはAPIキー不要で、Endpoint URLにOpenAI互換API（URL/v1）でアクセスできる。
現在のコードは inference.nosana.com と NOSANA_API_KEY 前提なので、自分たちのデプロイのURLを使えるようにしたい。

【sponsors.py の修正】
1. Nosana用のOpenAIクライアントを作る関数 _nosana_client() を追加する。
   - 環境変数 NOSANA_URL があれば、base_url = NOSANA_URL の末尾の「/」を除いて「/v1」を付けたもの、
     api_key = NOSANA_API_KEY があればそれ、なければ "ollama"（OpenAIクライアントは空のキーを受け付けないため）、
     timeout=180（初回のモデル読み込みに時間がかかるため）で作成する。
   - NOSANA_URL がなく NOSANA_API_KEY だけある場合は、従来どおり https://inference.nosana.com/v1 に timeout=60 で接続する。
   - どちらもなければ RuntimeError("NOSANA_URL (or NOSANA_API_KEY) is missing") を出す。
2. nosana_explain() はこの関数でクライアントを作るように変更する。モデル名は従来どおり
   NOSANA_MODEL があればそれ、なければ client.models.list() の最初のモデルを使う。
3. max_tokens を 1500 に増やす（推論型モデルは考える過程でトークンを消費するため）。
4. 応答テキストから <think>...</think> の部分を正規表現で取り除き、さらに「</think>」が残っていればその後ろだけを使う。
   空になった場合は "Nosana returned an empty explanation" を返す。import re を追加する。

【app.py の修正】
1. has_nosana の判定を、NOSANA_URL または NOSANA_API_KEY のどちらかが設定されていれば True にする。
2. サイドバーの表示「Nosana API Key:」を「Nosana 接続先:」に変更する。
3. 未設定時の案内文を「NOSANA_URL（NosanaデプロイのEndpoint URL）を設定すると実際のLLM応答を表示できます。」に変更する。
4. 生成中のスピナー文言を「Nosanaで生成中...（初回はモデル読み込みで1〜2分かかることがあります）」に変更する。

【.env.example の修正】
本物の値は入れず、次の内容にする。
DAYTONA_API_KEY=replace_with_real_key
# Nosana deployment Endpoint URL (Ollama, port 11434). Never commit the real URL.
NOSANA_URL=https://replace_with_your_deployment.node.k8s.prd.nos.ci
# Optional: only needed for Nosana hosted inference instead of your own deployment
# NOSANA_API_KEY=replace_with_real_key
# Optional: model id shown at NOSANA_URL/v1/models (first model is used if empty)
# NOSANA_MODEL=optional_model_id

【.gitignore を新規作成（既にあれば追記）】
.env
.venv/
__pycache__/
.pytest_cache/
*.pyc

【README.md の修正】
1. 起動手順の $env:NOSANA_API_KEY="..." の行を $env:NOSANA_URL="your_nosana_deployment_endpoint_url" に置き換える。
2. Architecture のNosanaの説明を次の趣旨に書き換える：
   「Nosana GPU（RTX 3090）上にOllamaでオープンなLLMをデプロイし、アプリはそのデプロイの
   OpenAI互換API（NOSANA_URL/v1）を呼び出して、提供中のモデルを選び、計算結果を架空データである旨とともに解説する。」
3. 既存の「Judging honesty」の方針は維持し、実際に動作していない機能を動作しているように書かないこと。

【注意】
- 実際のURL、APIキー、パスワードをコードやREADMEに直接書かないこと。
- 修正後、変更したファイルごとに変更点を簡単に説明し、修正後のファイル全文を出力してください。
