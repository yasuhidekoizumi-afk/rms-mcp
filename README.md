# rms-mcp

MCP server for Rakuten RMS API - sales dashboard & order management.

## Setup

```bash
# Install
uv sync

# Set credentials (from RMS管理画面 → API設定)
export RMS_SERVICE_SECRET="SPxxxxxxxxxxxxx"
export RMS_LICENSE_KEY="SLxxxxxxxxxxxxx"

# Run
uv run rms-mcp
```

## Hermes Config

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  rms-mcp:
    command: uv
    args: ["run", "rms-mcp"]
    cwd: /path/to/rms-mcp
    env:
      RMS_SERVICE_SECRET: "${RMS_SERVICE_SECRET}"
      RMS_LICENSE_KEY: "${RMS_LICENSE_KEY}"
```

## Tools

| Tool | Description |
|------|-------------|
| `rms_daily_sales` | Daily sales summary |
| `rms_product_ranking` | Product ranking by revenue |
| `rms_order_detail` | Full order detail |
| `rms_cancel_rate` | Cancellation rate |

## API Coverage

- [x] RakutenPayOrderAPI
- [x] PurchaseItemAPI
- [ ] InventoryAPI
- [ ] ProductAPI
- [ ] CouponAPI
