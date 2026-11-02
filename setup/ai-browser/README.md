# AI 浏览器

已安装 agent-browser 0.41.1 和 Playwright MCP 0.0.84。供本云端代理通过终端使用；未注册为聊天宿主的内置工具。

入口：`/workspace/ai-browser/browser`

```bash
/workspace/ai-browser/browser open https://example.com
/workspace/ai-browser/browser snapshot -i
/workspace/ai-browser/browser click @e1
/workspace/ai-browser/browser screenshot /workspace/ai-browser/page.png
/workspace/ai-browser/browser close
```

MCP 客户端可配置 command 为 /workspace/ai-browser/browser，args 为 ["mcp"]。宿主是否加载需另行核验。

复装：npm_config_cache=/workspace/.npm-cache npm ci --prefix /workspace/ai-browser

保留继承的代理，使用系统Chromium和环境CA合集，未关闭TLS校验。容器不支持Chromium沙箱，因此仅关闭Chromium内部沙箱，保留环境隔离。无付费浏览器或LLM调用。正常网页的动态渲染、点击和截图可用，不保证通过网站验证或反爬拦截。

已实际验证 example.com 导航、DOM读取和截图。截图 smoke.png。各目标网站需独立验证。
