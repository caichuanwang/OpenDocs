# Changelog

本项目的发布说明遵循语义化版本，并记录用户可见的兼容性边界。

## 未发布

### 修复

- 限制单页 PDF 视觉候选数量，避免异常重叠对象触发高复杂度区域合并。
- 异步取消不再依赖仍在运行的事件循环清理 SDK 自有临时源文件。
- PPTX 在完整提取媒体和幻灯片内容前执行 `max_pages` 校验。
- 空 TXT、Markdown 和无名文本字节返回更明确的 `NoUsableContentError` 消息。

## 0.1.0 - Alpha

OpenDocs 的首个公开 Alpha 将本地文档转换为 Markdown。

### 支持范围

- 支持 TXT、Markdown、PDF、常见图片、DOCX 和 PPTX。
- 公共入口为 `opendocs.parse()` 与 `opendocs.aparse()`，返回 Markdown `str`。
- 支持 Python 3.11、3.12 和 3.13；发布阻断平台为 Ubuntu 与 macOS。
- PDF 解析依赖系统安装的 Poppler。
- 扫描页、图片和 Office 视觉内容可能调用由使用者配置的模型服务，并产生模型费用。

### 兼容性

- `0.1.x` 系列保持 `parse()`、`aparse()`、`ParseOptions`、`VisionConfig`、公开输入类型和异常类兼容。
- 同一版本、输入与配置下，原生解析和语义顺序应保持确定性；视觉模型措辞可能随提供商响应变化。
- 补丁版本可能改进 Markdown，因此不承诺跨版本逐字节一致。
- 私有解析器、benchmark schema 与内部调度不属于公共兼容性承诺。
- 公共破坏性变更必须进入 `0.2.0` 或更高版本，并提供迁移说明。

### 已知限制

- 本版本为 Alpha，适合评估与受控使用，不声明生产或企业就绪。
- Windows 尚未验证，不在 `0.1.0` 的发布阻断矩阵中。
- SDK 仅接受本地路径、字节或二进制流，不下载 HTTP、OSS 或 S3 URL。
- `vision_concurrency` 只限制单次解析内部的视觉请求；跨文档并发由调用方控制。
- 不提供全局并发、模型调用次数、token、费用上限或性能 SLA。

### 发布验证

- wheel 与 sdist 使用同一元数据契约，并生成可复核的 `SHA256SUMS`。
- Ubuntu 与 macOS 的 Python 3.11-3.13 均为发布阻断环境。
- `examples/basic_consumer` 展示公开依赖、同步解析与调用方 `asyncio.Semaphore`。
