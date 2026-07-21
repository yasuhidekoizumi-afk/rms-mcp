# rms-mcp

楽天RMSのMCPサーバー — 自然言語で楽天市場運用を操作するAIエージェント向けツール群

**26ツール** | 読取12 + 書込10 + ブラウザ操作4

## ツール一覧

### 売上・分析
| ツール | 説明 |
|--------|------|
| `rms_daily_sales` | 日別売上サマリー（件数・税・クーポン・送料） |
| `rms_product_ranking` | 商品別ランキング（数量・売上・平均単価） |
| `rms_cancel_rate` | キャンセル率・件数 |

### 注文管理
| ツール | 説明 |
|--------|------|
| `rms_order_detail` | 注文番号指定で全詳細JSON |
| `rms_unconfirmed_orders` | 未確認（注文確認待ち）の注文一覧 |
| `rms_pending_shipping` | 発送待ち注文一覧 |
| `rms_get_sub_status_list` | サブステータス一覧 |
| `rms_confirm_order` | 受注確認（確認待ち→処理中） |
| `rms_update_shipping` | 配送情報更新（配送業者・追跡番号） |
| `rms_update_sub_status` | サブステータス更新 |
| `rms_update_memo` | 注文メモ更新 |
| `rms_cancel_order` | 注文キャンセル |

### 商品管理
| ツール | 説明 |
|--------|------|
| `rms_search_products` | 商品検索（管理番号・商品名・ジャンル） |
| `rms_all_products` | 全商品の管理番号・商品名・価格の一覧 |
| `rms_upsert_product` | 商品の新規登録・更新 |
| `rms_update_price` | 商品価格の変更 |

### 在庫管理
| ツール | 説明 |
|--------|------|
| `rms_get_inventory` | 指定商品の在庫情報を取得 |
| `rms_update_inventory` | 在庫数の更新（バリアント単位） |

### クーポン
| ツール | 説明 |
|--------|------|
| `rms_search_coupons` | 発行済みクーポンの一覧 |
| `rms_issue_coupon` | 新規クーポンの発行 |

### 問い合わせ
| ツール | 説明 |
|--------|------|
| `rms_inquiries` | 問い合わせ管理（件数・一覧・詳細・返信） |

### ブラウザ操作（Playwright / Cookie要）
| ツール | 説明 |
|--------|------|
| `rms_post_review_reply` | レビューに返信を投稿 |
| `rms_set_rpp_budget` | RPP広告の月予算を設定 |
| `rms_check_calendar_events` | イベントカレンダー確認 |
| `rms_generate_rmail_draft` | R-Mail（メルマガ）文面自動生成 |

## セットアップ

```bash
git clone https://github.com/yasuhidekoizumi-afk/rms-mcp.git
cd rms-mcp
uv sync
```

## 認証

RMS管理画面 → API設定 で `serviceSecret` と `licenseKey` を発行。

```bash
export RMS_SERVICE_SECRET="SP404839_xxxxxxxxxx"
export RMS_LICENSE_KEY="SL404839_xxxxxxxxxx"
```

## 接続方法

### ローカル（stdio / Claude Code / Hermes）

`.mcp.json` または `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "rms-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/rms-mcp", "rms-mcp"],
      "env": {
        "RMS_SERVICE_SECRET": "SP404839_xxx",
        "RMS_LICENSE_KEY": "SL404839_xxx"
      }
    }
  }
}
```

### リモート（Railway / HTTP）

```json
{
  "mcpServers": {
    "rms-mcp": {
      "url": "https://rms-mcp-production.up.railway.app/mcp",
      "headers": {
        "Authorization": "Bearer oryzae-rmcp-2026"
      }
    }
  }
}
```

## 動作確認

```bash
RMS_SERVICE_SECRET="SP404839_xxx" RMS_LICENSE_KEY="SL404839_xxx" uv run python -c "
from rms_mcp.order_api import OrderAPI
from rms_mcp.client import RMSClient
c = RMSClient('SP404839_xxx', 'SL404839_xxx')
api = OrderAPI(c)
nums = api.search_orders('2026-05-01T00:00:00+0900','2026-05-08T23:59:59+0900').get('orderNumberList', [])
orders = api.get_order(nums).get('OrderModelList', [])
total = sum(o.get('totalPrice', 0) or 0 for o in orders)
print(f'接続成功！ {len(nums)}件  {total:,}円')
c.close()
"
```

## 開発・テスト

```bash
uv sync --extra dev
uv run pytest
RMS_SERVICE_SECRET=SP_xxx RMS_LICENSE_KEY=SL_xxx uv run pytest tests/test_live_smoke.py
```

## License

MIT — ORYZAE Inc.
