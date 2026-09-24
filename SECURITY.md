# 安全审计与加固说明

本文档记录对上游项目 [`lmx566/NSFW-Guard-Pro`](https://github.com/lmx566/NSFW-Guard-Pro)
的审计结果，以及本加固版逐项做了什么。上游仓库只有一个初始提交（2026-03-02），
无 LICENSE、无测试、无 CI，共 11 个文件约 50KB。

**审计方式**：全量静态阅读 11 个源码文件；对可疑点构造最小复现服务实际验证，
而不是仅凭推断下结论。


## 一、已确认并实测复现的高危漏洞

### 漏洞：`GET /api/files/{filename}` 路径穿越 → 任意文件读取

**位置**：上游 `backend/app.py` 第 262–270 行。

```python
@app.get("/api/files/{filename}")
async def get_file(filename: str):                      # ← 注意：没有任何鉴权依赖
    # Security: prevent path traversal
    if ".." in filename or "/" in filename:             # ← 只拦正斜杠和 ".."
        raise HTTPException(status_code=400, detail="Invalid filename.")
    path = os.path.join(PROCESSED_DIR, filename)
```

两个缺陷叠加：

1. **鉴权缺失**。同一文件里 `/api/process` 等接口都有 `Depends(verify_api_key)`，
   唯独这个下载接口没有。前端 `<img src>` 用不上自定义请求头，作者就把鉴权去掉了，
   却没有补上替代方案。
2. **过滤基于黑名单且忽略反斜杠**。Windows 上 `\` 同样是路径分隔符，
   而 `"\\" not in filename` 这个条件根本不存在。更关键的是
   `os.path.join("processed", "C:\\Windows\\win.ini")` 在 Windows 上
   **会直接返回 `C:\Windows\win.ini`** —— `os.path.join` 遇到绝对路径会丢弃前面的部分。
   要触发这个行为，路径里既不需要 `/` 也不需要 `..`，因此两个过滤条件全部落空。

**复现**（本机 Windows，照抄上游代码起服务后实测）：

```
$ curl "http://127.0.0.1:8099/api/files/C:%5CUsers%5CHiTE%5CAppData%5CLocal%5CTemp%5Csecretdir%5Csecret.txt"
TOP_SECRET_CONTENT_12345
HTTP 200
```

`%5C` 是反斜杠的 URL 编码；uvicorn 在交给路由前会做一次 `unquote`，
于是路由参数 `filename` 就变成了 `C:\Users\...\secret.txt`，两个过滤条件均不命中，
最终 `FileResponse` 把这个文件原样返回。**全程未携带任何 API Key。**

**影响**：在 Windows 上部署且端口可达时，任何人可读取该进程权限下的任意文件 ——
SSH 私钥、`.env`、数据库文件、源码等。Linux 上由于必须用到 `/` 或 `..`，不可利用，
但上述两个缺陷本身依然存在（鉴权缺失 + 黑名单过滤）。


## 二、其他问题

| 级别 | 问题 | 位置 |
|---|---|---|
| 高 | **硬编码默认 API Key，且在三个文件里公开**：`backend/app.py` 第 47 行、`api_docs.md` 第 19 行、`frontend/js/app.js` 第 22 行。前端还把该默认值预填进输入框，用户基本不会去改，等于部署即无鉴权 | 三处 |
| 高 | **密钥用 `==` 比较**，存在时序侧信道；且鉴权失败时把收到的密钥前 8 位打进日志 | `app.py:58-62` |
| 中 | `ProxyHeadersMiddleware(trusted_hosts="*")` 无条件信任 `X-Forwarded-For`，任何人伪造该头即可绕过 slowapi 的按 IP 限流 | `app.py:34` |
| 中 | `CORSMiddleware(allow_origins=["*"])` | `app.py:37-43` |
| 中 | 硬编码 `host="0.0.0.0"`，启动即对整个局域网暴露，且无 TLS | `app.py:277` |
| 中 | **原图与结果图以明文存盘**，`cleanup_old_files` 只在处理请求时被 `BackgroundTasks` 触发 —— 服务一旦闲置，24 小时清理逻辑永不执行，上传的原始敏感图片会永久留在磁盘上 | `app.py:82-91, 137` |
| 中 | 限流函数 `@limiter.limit` 实际未挂在任何路由上（`Limiter` 已注册，但接口没有装饰器），文档里承诺的限流并未生效 | `app.py:29-31` |
| 低 | `mode` / `intensity` / `color` 无服务端校验。`intensity` 直接赋给 `cv2.GaussianBlur` 的核大小，传入极大值会导致内存暴涨 | `app.py:130` |
| 低 | `_processor.blur_radius = intensity` —— 全局单例上的可变状态，并发请求互相覆盖强度 | `app.py:67,130` |
| 低 | Deep Scan 的瓦片临时文件固定命名为 `_tile_<pid>.png` 且写在工作目录，并发时互相覆盖、甚至一方 `os.remove` 掉另一方正在读的文件 | `engines.py:88-105` |
| 低 | 批量接口对文件数量无上限 | `app.py:168` |
| 低 | 同步推理直接写在 `async def` 里，阻塞事件循环，实际并发能力与 `MAX_CONCURRENCY=2` 的设定不符 | `app.py:123-125` |
| 低 | 异常处理把 `str(e)` 直接回显给调用方，可能泄露内部路径 | `app.py:259` |
| 低 | 全部依赖未锁版本 | `requirements*.txt` |
| 提示 | `pipeline(...)` 未传 `device`，即使装了 CUDA 版 torch，分类器仍跑在 CPU 上 —— README 宣称的 GPU 加速实际未生效 | `engines.py:147` |

### 上游没有问题的部分（值得说明）

- **没有恶意代码**：全量阅读后确认无混淆、无 `eval`/`exec`、无 `subprocess`、
  无 `socket`、无向第三方上报数据的行为。
- **没有可疑外联**：唯一的外部资源是前端引用的 `cdn.tailwindcss.com` 与
  `fonts.googleapis.com`，以及首次运行时从 HuggingFace 下载模型权重
  （`trust_remote_code` 默认 `False`，不会执行远端代码）。
- **安装脚本干净**：没有 `curl | bash`，没有从可疑地址下载二进制。
- README 宣称的"图片 100% 本地处理"在图片处理环节是成立的。


## 三、许可与合规（不是漏洞，但影响能否商用）

- **上游仓库没有 LICENSE 文件**。按著作权法默认保留所有权利，严格来说
  不可自由分发、修改或商用。
- **依赖 `nudenet` 的许可证不一致**：PyPI 元数据（3.4.2）标注为 MIT，
  但其 GitHub 仓库的 LICENSE 为 **AGPL-3.0**。AGPL 的"网络服务条款"要求
  以网络服务形式提供修改版时必须向用户提供源码，对闭源商用有实质约束。
  建议商用前向作者确认。
- 上游 `README.md` 中引用的 `walkthrough.md` 并不存在于仓库中。


## 四、本加固版的对应处理

### 4.1 路径穿越（核心修复）

三重防护，任何一层单独生效都能挡住原漏洞：

1. **白名单校验**（`security.is_safe_filename`）：文件名必须完全匹配服务端
   自身的命名格式 `processed_<uuid4>.<jpg|jpeg|png|webp>`。因为允许的字符集
   里根本不存在 `/`、`\`、`:`、`.`（连续点）等，**无法构造出任何穿越载荷** ——
   这比原版的"黑名单排除 `..` 和 `/`"可靠得多。
2. **realpath 容器校验**（`security.resolve_within`）：解析后确认父目录
   严格等于 `PROCESSED_DIR`，可挡住符号链接、Windows 8.3 短名等绕过手法。
3. **HMAC 下载令牌**：下载地址形如 `/api/files/<name>?t=<过期时间>.<签名>`，
   签名绑定文件名与有效期。无令牌一律 403（也接受合法的 `X-API-KEY` 头）。
   这样既补上了缺失的鉴权，又不破坏前端 `<img src>` 的用法。

越界与不存在的名字统一返回 404，不区分原因，避免多余的信息泄露。

### 4.2 密钥管理

- **彻底移除硬编码密钥**，三处全部清除。
- 优先读取环境变量 `NSFW_API_KEY`（短于 16 字符直接拒绝启动）；
  未设置时随机生成，并以 **0600** 权限写入 `data/api_key`（用
  `os.open(..., 0o600)` 先创建再写入，避免出现权限宽松的窗口期）。
- 比较改用 `hmac.compare_digest`（常量时间）。
- 启动日志只打印长度与 SHA-256 前缀指纹，不再打印密钥片段。
- 安装脚本不再把密钥明文追加进 `~/.bashrc`；systemd 单元改用
  `EnvironmentFile` 指向 0600 的独立文件，而不是写在默认 644 的 unit 里。
- 前端不再预填任何密钥，且只存 `sessionStorage`。

### 4.3 默认配置收敛

| 项 | 原版 | 本版 |
|---|---|---|
| 监听地址 | `0.0.0.0` | `127.0.0.1`（对外需显式 `NSFW_HOST=0.0.0.0`，且启动时打印警告） |
| 代理信任 | `trusted_hosts="*"` | 仅 `127.0.0.1`/`::1`，可用 `NSFW_TRUSTED_PROXIES` 配置 |
| CORS | `allow_origins=["*"]` | 默认不加 CORS 中间件（同源），需跨域才显式配置 |
| 限流 | 已注册但未挂载 | 实际挂到接口上：处理类 30/分钟、批量 5/分钟 |

### 4.4 输入校验与资源保护

- `mode` 白名单、`intensity` 限 3–199、`color` 必须是 `#RRGGBB`，全部服务端强制；
  非法输入返回 422 而不是崩溃或静默异常。
- 新增 `MAX_IMAGE_PIXELS`（默认 4096×4096）防止"图片炸弹"。
- 上传后先实际解码验证，伪装成 `.png` 的脚本文件会被 400 拒绝。
- 批量接口限制单次文件数（默认 20）。
- 图片扩展名归一化，不再直接把用户提供的扩展名拼进文件名。

### 4.5 并发与正确性

- **移除共享可变状态**：`intensity` 改为函数参数传递，不再写全局单例属性。
  测试用例专门验证了两个并发请求不会互相污染强度。
- **推理移出事件循环**：同步推理放到 `asyncio.to_thread`，上限由
  `asyncio.Semaphore` 控制，恢复真实的并发语义。
- **消除瓦片临时文件竞争**：Deep Scan 改为把 numpy 切片直接交给 NudeNet
  （该库本身就支持内存图像），连磁盘 I/O 一并省掉；仅在内存调用失败时才降级到
  `tempfile.mkstemp` 生成的唯一文件，并在 `finally` 中清理。
- **修正 GPU 未生效**：`pipeline(...)` 现在传入 `device`。

### 4.6 数据留存

- 原图在单张处理完成后**立即删除**，不再躺在 `uploads/` 等下次请求来触发清理。
- 清理改为后台定时任务（默认每 600 秒），并额外清理 `tmp/` 残留，服务空闲时也生效。

### 4.7 前端

前端重写为完全自包含（原生 HTML/CSS/JS，不再依赖 Tailwind CDN 与 Google Fonts），
因此可以启用严格的 CSP：

```
default-src 'none'; img-src 'self' data: blob:; style-src 'self';
script-src 'self'; connect-src 'self'; font-src 'self';
base-uri 'none'; form-action 'none'; frame-ancestors 'none'
```

无内联脚本、无内联事件处理器、不使用 `localStorage`。
所有插入 DOM 的文本走 `textContent` 而非 `innerHTML`。


## 五、验证情况

四个测试文件，共 **146 条断言，全部通过，零失败**（实测结果）。

| 测试 | 断言数 | 内容 | 结果 |
|---|---|---|---|
| `tests/test_security.py` | 32 | 文件名白名单 15 组载荷、realpath 容器校验、令牌签名 7 项、常量时间比较、密钥生成 | 全通过 |
| `tests/test_engines.py` | 31 | 三种打码模式确实修改像素、阈值逻辑、畸形输入防御、并发不串扰、NudeNet 与 Deep Scan | 全通过 |
| `tests/test_api_e2e.py` | 70 | 真实 uvicorn 全流程：鉴权、三个接口、错误码、安全响应头、**原漏洞 8 种载荷回归** | 全通过 |
| `tests/test_pipeline_stub.py` | 13 | 分类器返回整理、分类结果→打码强度联动、模型不可用时的降级 | 全通过 |

关键回归结果（`test_api_e2e.py` B 节）：

```
反斜杠绝对路径 未泄露内容 / 返回 404
正斜杠绝对路径 未泄露内容 / 返回 404
../ 正向穿越    未泄露内容 / 返回 404
..\ 反斜杠穿越  未泄露内容 / 返回 404
Windows 系统文件 未泄露内容 / 返回 404
UNC 路径        未泄露内容 / 返回 404
URL 双重编码    未泄露内容 / 返回 404
带密钥也不放行越界路径
```

### 未能验证的部分（如实说明）

**ViT 分类器（`Falconsai/nsfw_image_detection`）的真实权重未能加载**：
执行环境禁止访问 huggingface.co 与其镜像站，实测报 502 Bad Gateway 与
TLS 握手超时，约 340MB 的权重无法下载。中断的尝试还在本地缓存里留下了一个
724 字节的半成品（快照指向空文件），使后续启动报出
`config.json is not a valid JSON file` 这种与真实原因无关的错误 ——
该损坏缓存已被改名备份（未删除），并据此在代码里补上了针对性的排查提示。

对此的处理是：代码**设计为优雅降级** —— 分类器加载失败时只打印一行提示，
服务照常运行，退化为"仅 NudeNet 检测器"模式，打码功能不受影响。
该降级路径已在测试中验证，分类器链路本身则用桩对象单独验证通过
（`test_pipeline_stub.py`）。

因此**在你的机器上首次运行时**，需要能访问 HuggingFace 以下载权重；
国内网络请设置 `HF_ENDPOINT=https://hf-mirror.com`。


## 六、残留风险

1. **模型权重来自 HuggingFace**，属于供应链信任的一环。代码已用
   `trust_remote_code=False`（transformers 默认值）避免执行远端代码，
   但权重本身被替换的风险无法在代码层消除。可通过 `NSFW_CLASSIFIER_MODEL`
   指向自建或本地已审计的模型目录来规避。
2. **分类精度不保证**。本版只修安全与工程问题，未改动模型与阈值策略。
   自动审核的误判率仍然取决于 NudeNet 与 ViT 本身，不应当作唯一的合规手段。
3. **`in-process` 限流是单实例的**。多进程 / 多副本部署时需改为共享存储
   （slowapi 支持 Redis 后端）。
4. **仍建议前置反向代理**做 TLS、认证与请求体大小限制，不要直接把服务
   暴露到公网。
