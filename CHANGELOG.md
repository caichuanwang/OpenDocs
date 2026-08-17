# Changelog

本项目的发布说明遵循语义化版本，并记录用户可见的兼容性边界。

## 未发布

后续变更将在此记录。

## 0.2.0 - Alpha - 2026-08-17

### 新增

- 新增标准 `.xlsx` 工作簿解析：按源顺序保留全部 worksheet/chartsheet（含 hidden、
  very hidden 与空 sheet）、常见保存值和货币/日期格式、Excel 表格、相离区域、合并跨度、
  公式缓存缺失回退、标准文本对象以及原生图表事实。
- XLSX 内嵌图片与图表支持可选视觉语义补充；视觉只解释趋势、标注、关系与含义，任一模型
  不可用、超时或失败均保留原生结果并产生可定位 warning。

### 兼容性与限制

- XLSX 不使用 `max_pages` 限制工作表数量，而由私有结构预算在昂贵加载前约束资源；公共
  `ParseOptions` 与 Markdown 返回契约保持不变。
- 仅支持 `.xlsx`，不支持 `.xls`、`.xlsm` 或 `.xlsb`；不重算公式，不访问外部 URL、链接
  工作簿或数据连接，也不承诺字体、颜色、边框、尺寸或像素级 Excel 外观保真。

### 修复

- 限制单页 PDF 视觉候选数量，避免异常重叠对象触发高复杂度区域合并。
- 异步取消不再依赖仍在运行的事件循环清理 SDK 自有临时源文件。
- PPTX 在完整提取媒体和幻灯片内容前执行 `max_pages` 校验。
- 空 TXT、Markdown 和无名文本字节返回更明确的 `NoUsableContentError` 消息。

## 0.1.0 - Alpha

OpenDocs 的首个公开 Alpha `opendocs-sdk==0.1.0` 将本地文档转换为 Markdown。

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
