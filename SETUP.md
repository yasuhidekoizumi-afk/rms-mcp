# RMS売上ダッシュボード 導入マニュアル

Claude Code から楽天RMSの売上データを取得する導入手順。  
所要時間：約5分（APIキー発行含めると10分）

---

## 📋 目次

1. [事前準備](#事前準備)
2. [RMS APIキーの発行](#rms-apiキーの発行)
3. [rms-mcpのインストール](#rms-mcpのインストール)
4. [Claude Codeへの組み込み](#claude-codeへの組み込み)
5. [使い方](#使い方)
6. [トラブルシューティング](#トラブルシューティング)
7. [Claude.ai（Web版）から使う場合](#claudeaiweb版から使う場合)

---

## 事前準備

必要なもの：
- Mac（社用PC）
- Claude Code がインストール済みであること（`claude` コマンドが使える状態）
- uv（Pythonパッケージマネージャー）がインストール済みであること

uvが入っていない場合：
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## RMS APIキーの発行

### Step 1: RMS管理画面にログイン

https://mainmenu.rms.rakuten.co.jp/ にアクセスし、楽天RMSのID/パスワードでログインします。

### Step 2: API設定を開く

左メニューから「設定」→「API設定」を開きます。

### Step 3: キーを確認

「R-たすく」アプリの以下の2つのキーを探します：

- **serviceSecret**: `SP404839_...` で始まる文字列
- **licenseKey**: `SL404839_...` で始まる文字列

画面に表示されている最新のキーをメモしてください。

> ⚠️ 注意：キーは他人に共有しないでください。

---

## rms-mcpのインストール

ターミナルを開いて以下を実行：

```bash
# 1. 作業ディレクトリに移動
cd ~/oryzae  # または任意のディレクトリ

# 2. リポジトリをクローン
git clone https://github.com/yasuhidekoizumi-afk/rms-mcp.git
cd rms-mcp

# 3. 依存パッケージをインストール
uv sync
```

### 動作確認

以下のコマンドでAPIが繋がるか確認します（`SP...` / `SL...` は自分のキーに置き換えてください）：

```bash
RMS_SERVICE_SECRET="SP404839_xxxxxxxxxx" \
RMS_LICENSE_KEY="SL404839_xxxxxxxxxx" \
uv run python -c "
from rms_mcp.order_api import OrderAPI
from rms_mcp.client import RMSClient
c = RMSClient('SP404839_xxxxxxxxxx', 'SL404839_xxxxxxxxxx')
api = OrderAPI(c)
r = api.search_orders('2026-05-01T00:00:00+0900','2026-05-08T23:59:59+0900')
print('✅ 接続成功！', len(r.get('orderNumberList',[])), '件の注文があります')
c.close()
"
```

`✅ 接続成功！` と表示されればOK。エラーが出たら [トラブルシューティング](#トラブルシューティング) へ。

---

## Claude Codeへの組み込み

Claude Code では `.mcp.json` ファイルでMCPサーバーを設定します。

### Step 1: 設定ファイルを作成・編集

```bash
# グローバル設定の場合
nano ~/.claude/mcp.json

# またはプロジェクトごとの場合
cd ~/oryzae
nano .mcp.json
```

### Step 2: 以下を追加

```json
{
  "mcpServers": {
    "rms-mcp": {
      "command": "uv",
      "args": ["run", "rms-mcp"],
      "cwd": "/Users/あなたのユーザー名/oryzae/rms-mcp",
      "env": {
        "RMS_SERVICE_SECRET": "SP404839_xxxxxxxxxx",
        "RMS_LICENSE_KEY": "SL404839_xxxxxxxxxx"
      }
    }
  }
}
```

> ⚠️ `cwd` のパスと `RMS_SERVICE_SECRET` / `RMS_LICENSE_KEY` は自分の環境に合わせてください。

### Step 3: Claude Codeを再起動

現在開いている Claude Code のターミナルを閉じて、再度開きます：

```bash
cd ~/oryzae
claude
```

起動後、`/mcp` コマンドで `rms-mcp` が表示されていれば完了です。

---

## 使い方

Claude Codeのチャット画面で以下のように話しかけるだけです：

| 聞き方 | 使われるツール | 出てくる情報 |
|--------|--------------|------------|
| 「今日の楽天の売上見せて」 | rms_daily_sales | 日別の注文件数・売上・税・クーポン額・送料 |
| 「今月の楽天の売上ランキング」 | rms_product_ranking | 商品別の販売数・売上・平均単価 TOP20 |
| 「楽天の注文 123456-20260508-1234567890 の詳細」 | rms_order_detail | 注文の全情報（JSON） |
| 「今月の楽天のキャンセル率は？」 | rms_cancel_rate | 総注文数・キャンセル数・キャンセル率 |

### 応用例

```
「先月の楽天の日別売上と商品TOP10を見せて」
→ rms_daily_sales(start_date="2026-04-01", end_date="2026-04-30")
→ rms_product_ranking(start_date="2026-04-01", end_date="2026-04-30", top_n=10)
```

```
「楽天とShopifyの今月の売上を比較して」
→ rms_daily_sales + Shopifyのsales_summary を両方呼び出して比較
```

---

## トラブルシューティング

### ❌ `401 Unauthorized` エラー

**原因**: APIキーが間違っている、または無効化されている

**対処**:
1. RMS管理画面でキーが最新か確認
2. licenseKeyの `I`（アイ大文字）と `l`（エル小文字）を見間違えていないか確認
3. API利用契約が完了しているか確認

### ❌ `ModuleNotFoundError: No module named 'rms_mcp'`

**対処**: `cd ~/oryzae/rms-mcp && uv sync` を再実行

### ❌ `command not found: uv`

**対処**: `curl -LsSf https://astral.sh/uv/install.sh | sh` を実行後、ターミナルを開き直す

### ❌ Claude Codeを再起動してもツールが表示されない

**対処**:
1. `.mcp.json` が正しい場所にあるか確認（プロジェクト直下 または `~/.claude/`）
2. `cwd` のパスが正しいか確認（`pwd` で現在地を確認）
3. JSONのフォーマットが正しいか確認（カンマの付け忘れなど）
4. `/mcp` コマンドでサーバー一覧を表示して確認

---

## Claude.ai（Web版）から使う場合

ブラウザ版 Claude.ai からも使えるよう、Railway にリモートサーバーを公開しています。
**Claude.ai Pro / Team / Enterprise プランが必要**です。

認証は OAuth 2.0 + PKCE。Claude.ai 側でログイン画面（共有パスコード入力）を経由します。

### Step 1: 管理者から共有パスコードを受け取る

Slack #tech で「rms-mcp のClaude.ai接続情報ください」と依頼してください。受け取るもの:

- サーバーURL（例: `https://rms-mcp-production.up.railway.app/mcp/`）
- **共有パスコード**（OAuth認可画面で入力するワンタイム的な合言葉）

### Step 2: Claude.ai に Custom Connector として登録

1. https://claude.ai/ にログイン
2. 右上プロフィール → **Settings** → **Connectors** → **Add custom connector**
3. 以下を入力:
   - Name: `Rakuten RMS`
   - Remote MCP server URL: 受け取ったURL
   - 詳細設定の OAuth Client ID / Secret は **空のまま** でOK
4. **追加** をクリック

### Step 3: 認可画面でパスコード入力

「追加」直後、Claude.ai が自動でOAuthフローを開始し、別タブで認可画面が開きます。

1. 共有パスコードを入力
2. **許可する** をクリック
3. 自動でClaude.aiに戻り、コネクタが「接続済み」になる

### Step 4: チャットで利用

新規チャットの画面下部ツールアイコンから `Rakuten RMS` を有効化。
あとは Claude Code と同じ自然言語で使えます。

> アクセストークンは30日間有効。期限切れ時は再度パスコード入力を求められます。

---

### サーバー管理者向け（Railway デプロイ手順）

社内で初めて立ち上げる場合の手順:

```bash
# 1. Railway CLI インストール
brew install railway

# 2. ログイン & プロジェクト作成
railway login
cd ~/oryzae/rms-mcp
railway init

# 3. サービスをリンク
railway service     # 作成されたサービスを選択

# 4. 環境変数設定
railway variables --set "RMS_MCP_TRANSPORT=http"
railway variables --set "RMS_SERVICE_SECRET=SP404839_xxx"
railway variables --set "RMS_LICENSE_KEY=SL404839_xxx"
railway variables --set "RMS_MCP_OAUTH_PASSCODE=$(openssl rand -hex 12)"

# 5. デプロイ
railway up

# 6. パブリックドメイン発行
railway domain

# 7. パスコードを確認して社内メンバーに配布
railway variables --kv | grep RMS_MCP_OAUTH_PASSCODE
```

**トークンローテーション**: アクセストークンは30日で自動失効するので通常不要。
パスコードを変えたい場合は `RMS_MCP_OAUTH_PASSCODE` を再設定 → `railway up` で再デプロイ。
既存のアクセストークンも全て無効化されます（サーバー再起動でメモリ消去）。

---

## 📞 問い合わせ

導入で詰まったら Slack #tech チャンネルで質問してください。

---

最終更新: 2026-05-19
