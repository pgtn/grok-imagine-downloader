# Grok Imagine Downloader

[English](README.md) | 简体中文

轻量命令行工具，将 Grok Imagine 媒体归档到本地，可选保存提示词和生成参数。面向 macOS 和 Windows，使用 Python 标准库和系统中的 `curl`，无需第三方 Python 依赖。

这是使用 Grok 网页接口的非官方工具。接口可返回的资源、数据格式和认证要求可能变化。

## 功能

- 分页读取 Imagine 资源清单，下载生成的视频、图片及上传的参考素材。
- 并发下载、失败重试，并在接口提供大小时校验文件字节数。
- 跳过本地大小符合预期的文件，中断后可重新运行补齐。
- 生成 UTF-8 编码的 `metadata.json`，为有效的本地文件保留已保存的生成参数。
- 可选从来源对话中查询提示词和生成设置。

## 范围

下载范围是 `/rest/assets` 接口在 `workspaceKind=WORKSPACE_KIND_IMAGINE_ALL` 条件下为当前账号返回的资源。工具持续翻页，跳过标记为已删除的记录，下载清单中的生成媒体和上传的参考素材。

- 这份资源清单不等同于精确导出 Saved 或 Liked 帖子，不保证包含所有历史资源、变体、HD 版本或视频延长链中的每一步。工具无法重新获取已删除或未被接口列出的资源。
- `--prompts` 仅为符合条件的本地完整文件，从来源对话中尽力补查提示词，不保证每个文件都能查到。
- 项目专注于本地下载和元数据，不提供图库界面、生成媒体、修改或删除云端内容、自动浏览器登录功能。

## 运行要求

- [uv](https://docs.astral.sh/uv/) **或** Python 3.8+。
- `PATH` 中可用的 `curl`。
- 已登录的 Grok 账号，以及 Netscape 格式的导出 cookie 文件。

使用 uv 时，`.python-version` 指定 Python 3.13。项目没有第三方 Python 依赖。

## 准备工作

1. 在已登录的浏览器中打开 <https://grok.com/imagine/saved>。
2. 使用你信任的 cookie 导出工具，以 Netscape 格式导出 `grok.com` 的 cookie。
3. 将文件保存为 `main.py` 同目录下的 `grok.com_cookies.txt`，或通过 `--cookies` 指定其他路径。

cookie 文件包含登录凭据。请妥善保管，不再需要时删除导出的副本。

脚本会尝试根据 macOS 或 Windows 上的 Chrome 版本构造 User-Agent，并在启动时打印。如果检测失败、使用其他浏览器，或认证失败，请用 `--ua` 传入导出 cookie 的浏览器的完整 User-Agent。可在该浏览器的开发者控制台运行 `navigator.userAgent` 获取。

## 使用方法

在项目目录中运行：

~~~bash
uv run main.py
uv run main.py --prompts
uv run main.py --help
~~~

| 命令选项 | 行为 |
| --- | --- |
| 不加选项 | 获取资源清单、下载媒体并写入元数据。 |
| `--prompts` | 额外为符合条件的已下载文件查询生成参数。 |

每次运行都会下载缺失或不完整的媒体，跳过已有完整文件，并更新 `metadata.json`。

不使用 uv 时，macOS 可运行 `python3 main.py`，Windows 可运行 `python main.py`，参数相同。部分帮助和状态消息目前使用中文。

### 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--cookies PATH` | 项目目录下的 `grok.com_cookies.txt` | Netscape 格式的 cookie 文件。 |
| `--dest PATH` | 项目目录下的 `output/` | 下载目标目录。 |
| `--ua TEXT` | 根据本机 Chrome 版本生成 | 导出 cookie 的浏览器的 User-Agent。 |
| `--workers N` | `6` | 媒体下载并发数，必须为正整数。 |
| `--prompts` | 关闭 | 资源清单和本地缓存均无生成参数时，从来源对话补查。 |

默认路径以脚本所在目录为基准，而非当前工作目录。自定义路径支持 `~`。

~~~bash
uv run main.py --cookies "/path/to/grok.com_cookies.txt" --dest "/path/to/archive" --workers 3
uv run main.py --ua "YOUR_BROWSER_USER_AGENT"
~~~

## 输出结构

~~~text
output/
├── video/          生成的视频
├── image/          生成的图片
├── uploads/        上传的参考素材
└── metadata.json   本地完整文件的索引
~~~

文件名格式为 `YYYYMMDD-HHMMSS_<资源ID前缀>.<扩展名>`。ID 前缀初始取八位，当前清单中出现文件名冲突时会加长。

每条元数据记录包含：

| 字段 | 含义 |
| --- | --- |
| `file` | 相对路径，在所有平台上统一使用 `/`。 |
| `id`、`mime`、`created` | 资源 ID、MIME 类型和创建时间。 |
| `size`、`w`、`h` | 接口提供的文件字节数、宽度和高度，缺失时可能为空或零。 |
| `genType`、`prompt`、`model`、`aspectRatio`、`duration`、`resolution` | 可获得的生成参数。 |

之前已记录、但 Grok 清单中不再出现的资源，只要本地文件仍通过原记录的大小检查，其元数据就会保留。只要文件仍符合索引条件，后续运行会复用已缓存的生成参数；本地文件缺失或不完整的旧记录不会保留。提示词以明文保存在 `metadata.json` 中，分享归档时请留意其中内容。

如果旧版本使用 `~/Downloads/grok-imagine`，可以将该目录移动为脚本旁的 `output/`，或继续通过 `--dest` 指向原目录。

## 提示词查询

资源清单可能不包含生成参数。加上 `--prompts` 后，脚本通过来源对话的 `response-node` 和 `load-responses` 接口，查找触发生成的用户消息。

- 仅查询本地文件完整、具有来源对话和回复 ID，且资源清单和本地缓存均未提供生成参数的生成资源。已有任何生成参数时，即使缺少提示词也不会补查。上传的参考素材不参与查询。
- 按对话合并请求，每段对话最多两个读取请求，同时最多处理三段对话。
- 遇到 HTTP 401、403 或 429 后停止后续查询，不重试这些请求；已在途的请求可能继续完成。已下载的文件和已查到的生成参数会保留。
- 对话已删除、无权访问，或 Grok 返回格式发生变化，都可能导致无法补全生成参数。

## 可靠性与限制

- 下载先写入 `.part` 文件，传输成功且大小检查通过后再改名。失败会重试；重新运行会跳过完整文件，并从头下载未完成的文件。未实现 HTTP Range 断点续传。
- 校验依据是字节数，不是内容哈希。接口未提供大小或返回零时，仅检查文件非空，无法确认内容是否完整。
- 同一清单中的重复资源 ID 会跳过；不同 ID 对应的相同内容不会按内容去重。
- 获取资源清单后，下载和提示词查询阶段会在正常结束，以及异常或 Ctrl+C 后的清理流程中写入元数据。强制结束进程可能使清理无法执行。
- 媒体下载有失败时，命令以非零状态退出。提示词查询失败可能使媒体已成功归档，但生成参数不完整。
- 脚本持续翻页至没有下一页 token，并在发现重复 token 时报告错误，避免无限循环。归档范围取决于接口为当前账号返回的资源。

## 常见问题

| 现象 | 排查方法 |
| --- | --- |
| HTTP 401 / 403 | 重新导出 cookie，检查 `--ua`。Chrome 最近升级过时，先重启再导出。访问控制也可能拒绝请求。 |
| HTTP 429 | 等待后再运行；如果媒体下载被限流，可降低 `--workers`。提示词查询另行限制为最多三段对话并发。 |
| HTTP 000 或 curl 错误 | 检查网络、curl 是否可用和代理设置。连接需要代理时，显式设置 `HTTPS_PROXY`。 |
| 无法读取 metadata.json | 先恢复或备份原文件再重建。元数据不可读或格式无效时，脚本会中止，不覆盖原文件。 |
| 缺少提示词 | 使用 `--prompts`；文件必须在本地完整，且来源对话仍可访问。 |
| Windows 重定向输出中的文字变成 `?` | 运行前设置 `PYTHONUTF8=1`。元数据始终使用 UTF-8 写入。 |

代理示例，请将端口改为本地代理的实际端口：

~~~bash
# macOS
export HTTPS_PROXY="http://127.0.0.1:7890"
uv run main.py
~~~

~~~powershell
# Windows PowerShell
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
$env:PYTHONUTF8 = "1"
uv run main.py
~~~

## 开发

`main.py` 是入口，具体实现在 `download.py`。

当前项目版本为 **0.1.0**。离线测试使用模拟响应验证下载逻辑，不代表已验证 Grok 当前接口兼容性或账号资源的完整覆盖。

2026-10-31 已在 Windows 上使用真实账号完成单文件检查：成功获取一页资源清单，下载一段生成的 MP4 视频，文件字节数与接口提供的大小一致，并保存了元数据。随后通过两个来源对话读取请求，成功补查该视频的提示词及生成参数，并保存到 `metadata.json`。真实账号的完整分页、全量下载，以及两个目标平台的命令行端到端流程仍待验证。

运行离线回归测试，无需 Grok 登录凭据：

~~~bash
uv run python -B -m unittest discover -s tests -v
~~~

GitHub Actions 会在 Windows 和 macOS 上使用 Python 3.13 运行这些离线检查，
并另外检查 Python 3.8 兼容性。测试不访问 Grok，也不需要账号 cookie。

发布前请检查暂存文件。仓库已忽略默认的 `output/` 目录、旧版 `downloads/` 目录与清单缓存、`*.part` 和 `*cookie*.txt`；自定义下载目录及 cookie 文件名可能需要额外的忽略规则。不要提交登录 cookie 或私人媒体。

## 许可证

[MIT](LICENSE)。
