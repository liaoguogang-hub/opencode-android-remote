# 模型配置说明

opencode 自带一组**免费模型**（provider id = `opencode`），不需要任何 API Key 或登录。

## 可用模型

```bash
opencode models | grep '^opencode/'
```

实测可用（2026-11-26）：`deepseek-v4.1-flash-free`、`mimo-v2.6-flash-free`、
`nemotron-3.5-lightning-free`、`big-pickle`。工具调用正常（能跑 bash / read / edit）。

## 最小配置

`~/.config/opencode/opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode/deepseek-v4.1-flash-free"
}
```

就这一行，不需要 `provider` 段。改完重启 `opencode serve` 生效。

## 注意

- `opencode providers list` 显示 `0 credentials` 是正常的 —— 免费模型走 opencode
  自己的网关，不占本机凭据。
- **不要**写成 `providers`（复数）+ `package/settings`，那是新版文档的格式；
  opencode 1.18.x 认的是 `provider`（单数）+ `npm/options`。写错时配置会被
  **静默忽略**（`opencode debug config` 里看不到这个键），模型还是旧的，
  表现为「改了配置没反应」。
- 若要用需要 Key 的 provider（如本机 OFM 网关 `127.0.0.1:18900`），完整写法：

```json
{
  "model": "ofm/minimaxai/minimax-m3.1-flash",
  "provider": {
    "ofm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OFM 网关",
      "options": { "baseURL": "http://127.0.0.1:18900/v1", "apiKey": "<key>" },
      "models": { "minimaxai/minimax-m3.1-flash": { "name": "MiniMax M3.1 Flash" } }
    }
  }
}
```

- 免费额度有限（撞到上限时会回 `Free usage limit reached`），撞上就换另一个
  `*-free` 模型，或切到 OFM 网关。
