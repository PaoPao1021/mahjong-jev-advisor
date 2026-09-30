# 雀魂 · Jev 实时决策顾问

Python / PySide6 桌面程序，通过截屏、牌面模板和 OCR 重建公开状态，生成本地合法候选，再调用在线模型选择动作。程序不代替玩家操作。

实时数据源优先使用随项目提供的只读浏览器 Hook：它在页面加载时接收雀魂 WebSocket 的 Protobuf 牌局事件，经本机 `127.0.0.1:8765` 内存解码后更新顾问。它不发送游戏操作，不保存原始网络帧。屏幕识别保留为 Hook 未连接时的备用方式。

## 推荐：网页 Hook 实时监测

1. 先启动桌面顾问；本机桥接服务只监听 `127.0.0.1:8765`。
2. 在 Edge 地址栏打开 `edge://extensions`，开启“开发人员模式”。Chrome 使用 `chrome://extensions`。
3. 点击“加载解压缩的扩展”，选择项目中的 `browser-extension` 文件夹。
4. 已经加载过扩展时，先在扩展卡片上点一次“重新加载”；再完全刷新雀魂网页（Ctrl+R）。仅刷新网页不会更新扩展脚本。
5. 顾问会依次显示“网页 Hook 已注入”“已截获雀魂 WebSocket”“已收到数据帧”“实时牌局已识别”。只有最后一项表示已取得手牌；端口连通或协议载入不会再被误判为牌局可用。
6. 如果没有出现最后一项，打开【桌】→【查看网页 Hook 诊断】。诊断只显示计数、事件名和连接主机，不保存原始帧或账号信息。

项目已内置雀魂 Unity WebGL 客户端对应的完整 `liqi.json`（支持 4.0+ 版本的 Protobuf 架构），扩展采用 Manifest V3 主执行环境（MAIN world）即时注入，适配 Unity WebGL 不再通过 HTTP 动态下载 `liqi.json` 的情况。扩展只匹配雀魂官方网页域名，帧只发往本机回环地址。Hook 尚未解出真实牌局时，程序不会停用已校准的屏幕识别备用通道。

## 启动

Windows 双击 `run_windows.cmd`；macOS 执行 `zsh run_macos.sh`。需要 Python 3.12+。已有虚拟环境时，启动脚本直接加载当前项目源码。

窗口使用系统标题栏，可拖动、缩放、最小化；较矮屏幕上的中间内容可滚动，底部按钮保持可见。首次升级默认不透明，避免文字与背景叠加；可用 Ctrl + 滚轮调整透明度。

### 使用前自检

Windows 双击 `check_windows.cmd`；macOS 执行 `zsh run_macos.sh --check`。检查 Python、依赖、OCR 模型、配置、校准、模板与屏幕采集。自检不保存截图、不显示 Key。输出 `ready: false` 时按各项提示补齐；不代表程序无法启动。

也可以在项目目录执行：

```powershell
.venv\Scripts\python.exe -m mahjong_jev_advisor.preflight --online --output readiness-report.json
```

`--online` 会向当前保存地址发送一次真实推断，可能产生少量费用；不加时只检查本机。报告只包含诊断信息。可先填写 Key，用【手动核对】输入真实牌局验证在线建议，再准备屏幕模板。

## 先验证模型连接

1. 打开底部【Jev设置】（在线模型连接设置）。
2. 选择协议，填写完整 POST 请求地址、API Key 和模型名称。
3. 点击【测试连接 · 真实请求】。该按钮通过与正式决策相同的后台请求与解析代码发送一次简短推断，可能产生少量费用。只有收到 HTTP 200 且答案可解析时显示成功。
4. 保存配置。配置保存在本机用户目录，不写入仓库。

| 协议 | 默认完整请求地址 | 模型 |
| --- | --- | --- |
| Vercel Jev 评估 | `https://ai-gateway.vercel.sh/v4/ai/evaluation-model` | `typesafe-ai/jev-latest` |
| TypeSafe System One | `https://api.typesafe.ai/v1/systemone` | `jev-latest` |
| OpenRouter Jev System One | `https://openrouter.ai/api/v1/systemone` | `jev-latest` |
| OpenAI 兼容 Chat Completions | `https://ai-gateway.vercel.sh/v1/chat/completions` | 填服务商提供的聊天模型 ID |

请求地址可以修改为自己的服务地址。不同协议的请求 JSON 与返回结构不同；Jev 使用评估协议。聊天协议要求模型返回候选动作 ID 的 JSON，返回候选以外的动作会被拒绝。未返回概率的模型不会显示编造的概率柱状图。

Key 不会出现在决策日志中。已保存的 Key 优先；仅在对应协议的官方 HTTPS 地址下，允许从 `AI_GATEWAY_API_KEY`、`TYPESAFE_API_KEY`、`OPENROUTER_API_KEY` 补充缺失的 Key。自定义地址不会自动取得这些环境变量。切换协议会清空输入框，须填写该渠道自己的 Key。

配置保存在本机用户目录的 `mahjong-jev-advisor/settings.json`，Key 为本机明文，勿分享此文件。保存使用原子替换；损坏的配置重新保存前会备份为 `settings.invalid.json`。开发和测试可用 `MAHJONG_ADVISOR_CONFIG_DIR` 指定独立配置目录。

### Jev 接入渠道

- **TypeSafe 官方直连**：[获取 Key](https://console.typesafe.ai/keys) · [官方文档](https://docs.typesafe.ai/introduction/quickstart)。选择 TypeSafe 预设。
- **OpenRouter**：[获取 Key](https://openrouter.ai/settings/keys) · [Jev 官方接入说明](https://openrouter.ai/docs/guides/community/jev)。选择 OpenRouter 预设，使用原生 System One 接口和 `jev-latest`；不要把 `typesafe/jev-router` 聊天路由模型当作此接口。
- **Vercel AI Gateway**：[控制台](https://vercel.com/ai-gateway) · [评估模型文档](https://ai-sdk.dev/docs/ai-sdk-core/evaluation)。选择 Vercel 预设；账户需满足其账单验证和额度要求。

网页试用入口：[TypeSafe Playground](https://console.typesafe.ai/playground)、[OpenRouter Jev Lab](https://openrouter.ai/labs/jev)。这两项是体验入口；桌面程序仍需相应平台 API Key。各平台是否可用、是否需要充值，以账户实际权限为准。

超时默认 15 秒，可在设置中调整；实际响应耗时由服务和网络决定，不能保证 2 秒。网络故障的本地回退默认关闭，可在设置中明确开启；认证、账单或无效答案不会被当成连接成功。

### 常见真实服务错误

- `401`：检查 Key 和所选服务是否对应。
- `403 customer_verification_required`：Vercel 要求账户先完成账单验证、绑定有效信用卡。更换客户端请求格式不能解决这个账户限制。
- `402`：检查服务余额或额度。
- `404` 或非 JSON 返回：检查完整接口路径。
- `429`：服务限流。

2026-09-28 较早的检查曾使用本机凭据发起实际 Vercel 请求，并在设置界面复现 `403 customer_verification_required`。尚未取得该账户的成功在线推断结果。后续审查测试意外覆盖了本机配置，未找到可恢复备份；已恢复空白默认配置，需重新填写 Key 和框选牌桌。测试现已增加整个配置根目录的隔离及回归检查。

## 再准备牌局识别

连接测试无需打开雀魂，但实时建议需要可用的牌局状态：

1. 在【桌】→【选择牌桌画面】框选雀魂网页版的完整牌桌。
2. 在【基础校准】框选手牌、摸牌和操作按钮区域；其他公开区域可在【扩展校准】补充。
3. 在【标注牌面样本】提供当前分辨率、牌背/牌面样式对应的样本。项目不附带可覆盖所有雀魂界面样式的预训练视觉识别器。
4. 点击【开始识别】。识别矛盾或置信度不足时暂停建议，并清除之前的推荐；可以通过【手动核对】输入真实牌局继续。

只选择牌桌、未校准区域时，程序会显示“待校准”。启动默认显示空状态，不再自动展示示范概率。手动核对后的牌局也会走正式在线决策请求。

Windows 上修复了 64 位 `SetWindowDisplayAffinity` 调用；若系统不能排除顾问窗口，程序提示把窗口移到牌桌外，不再周期性隐藏/显示窗口而打断拖动。

## 验证范围

```bash
.venv/Scripts/python.exe -m pytest -q
```

目前 91 项测试通过，包含 34 种标准牌与三种赤五的 Hook 状态重建、37 种互不相同的牌面绘制、四个座位及四家牌河/副露/分数/立直、吃碰与明暗加杠、全部操作按钮、规则与 UI 检查、四个模型协议的真实本机 HTTP 往返、配置对话框发请求、后台决策到界面更新、Hook 同步恢复与诊断、错误处理与旧建议清除，以及渠道 Key 隔离、配置损坏恢复、摸牌低置信度不污染历史、矛盾吃碰杠和无效概率拒绝。测试 HTTP 服务是本机受控服务，不代表远程服务账户已可用。

2026-09-29 已使用独立 Chromium 加载本项目扩展访问 `https://game.maj-soul.com/`，确认扩展在 Unity 页面主执行环境完成注入、替换 `window.WebSocket`，并捕获 `wss://route-5.maj-soul.com:443/gateway` 的真实二进制收发帧。该检查未登录账号、未进入牌局，因此不等同于完整实战手牌重建验收。

2026-09-30 已在 macOS 原生界面和 Chrome 雀魂网页验证真实手牌恢复、摸牌及弃牌更新。修复了 RESPONSE 外层 Wrapper、实时 ActionPrototype 的 XOR 解码、proto3 省略零座位字段及不同连接的请求 ID 冲突；重连恢复中的动作保持明文解码。新增真实格式回归检查，91 项测试通过。已保存的 OpenRouter Jev 连接测试返回 HTTP 200、ping 校验通过（2476 ms）；该结果不等同于每个实战决策的正确性或延迟验收。

本机 Python 3.12.14、依赖检查、OCR 模型初始化及 1920×1080 屏幕采集已通过。当前基础/扩展区域未校准，牌面模板为 0/38 类。`readiness-report.json` 记录本次实际结果。回放输出与帧数统计已修复；JSONL 状态回放的耗时不包含屏幕识别，不能用来证明端到端 2 秒目标。

已实测 Windows 系统标题栏拖动；尚未完成跨屏混合 DPI、雀魂完整录屏识别准确率或 P95 延迟验收。项目目前仍需校准与样本标注，不宣称达到 98%/99% 的识别指标。

规则与视觉仍有边界：固定网格模板需要与实际牌桌布局吻合；副露目前是平铺牌列表，对暗杠、加杠、吃后禁打和立直后操作等复杂状态尚未完成完整牌谱验收。因此不能声称在所有牌局中非法动作建议为零。请先用观战/回放与手动核对验证。
