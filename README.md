# rms-mcp

楽天RMS APIのMCPサーバー — 売上ダッシュボード & 受注管理

## できること

Hermes (or any MCP client) から楽天RMSのデータを取得:

| ツール | 説明 |
|--------|------|
| `rms_daily_sales` | 日別売上サマリー（件数・税・クーポン・送料） |
| `rms_product_ranking` | 商品別ランキング（数量・売上・平均単価） |
| `rms_order_detail` | 注文番号指定で全詳細JSON |
| `rms_cancel_rate` | キャンセル率・件数 |

内部では RakutenPayOrderAPI を使用しています。

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

## 動作確認

```bash
RMS_SERVICE_SECRET="..." RMS_LICENSE_KEY="..." uv run python -c "
from rms_mcp.order_api import OrderAPI
from rms_mcp.client import RMSClient
c = RMSClient('SP...', 'SL...')
api = OrderAPI(c)
r = api.search_orders('2026-05-01T00:00:00+0900','2026-05-08T23:59:59+0900')
print(len(r.get('orderNumberList',[])), '件')
c.close()
"
```

## Hermes設定

`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  rms-mcp:
    command: uv
    args: ["run", "rms-mcp"]
    cwd: /path/to/rms-mcp
    env:
      RMS_SERVICE_SECRET: "SP404839_xxx"
      RMS_LICENSE_KEY: "SL404839_xxx"
```

## クイックリファレンス

- 日別売上: `今日の楽天の売上見せて` → `rms_daily_sales`
- 商品ランキング: `今月の楽天TOP10は？` → `rms_product_ranking(top_n=10)`
- キャンセル率: `今月のキャンセル率は？` → `rms_cancel_rate`
- 注文詳細: `この注文の詳細` → `rms_order_detail(order_numbers=[...])`

## APIカバレッジ

- [x] RakutenPayOrderAPI (searchOrder, getOrder)
- [x] PurchaseItemAPI (searchOrderItem)
- [ ] InventoryAPI
- [ ] ProductAPI
- [ ] CouponAPI

## License

MIT — ORYZAE Inc.
