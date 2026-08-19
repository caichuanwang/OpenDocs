# OpenDocs

[![PyPI version](https://img.shields.io/pypi/v/opendocs-sdk.svg)](https://pypi.org/project/opendocs-sdk/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://img.shields.io/pypi/dm/opendocs-sdk.svg)](https://pypistats.org/packages/opendocs-sdk)

> 语言：[English](README.md) | **简体中文**

OpenDocs 是一个 Python SDK，可将本地文档转换为整洁的 Markdown —— 支持 TXT、Markdown、图片、
PDF（原生 / 混合 / 视觉）、DOCX、PPTX 和 XLSX —— 通过统一的同步/异步 API。

> **包名**：`opendocs-sdk` &nbsp;|&nbsp; **当前 Alpha 版本**：`opendocs-sdk==0.2.0` &nbsp;|&nbsp; **导入名**：`opendocs` &nbsp;|&nbsp; **Python**：3.11+

## 安装

```bash
pip install opendocs-sdk
```

如果需要在本地检出仓库中开发（或使用尚未发布的改动）：

```bash
uv add ../OpenDocs       # 或：pip install ../OpenDocs
```

## 快速开始

同步本地路径示例：

```python
from opendocs import parse

markdown = parse("notes.md")
```

异步字节示例：

```python
import asyncio

from opendocs import aparse


async def main() -> str:
    markdown = await aparse(b"plain text")
    return markdown


markdown = asyncio.run(main())
```

接受的输入类型：

- 本地文件系统路径（`str`）
- 本地文件系统路径（`os.PathLike[str]`）
- `bytes`
- 提供 `read() -> bytes` 的二进制文件对象

远程下载由调用方负责。`http://`、`https://`、`oss://` 和 `s3://` 来源必须在调用 OpenDocs
之前先下载到本地。

## 支持的格式

| 格式 | 状态 | 说明 |
| --- | --- | --- |
| TXT | ✅ | 端到端解析为确定性 Markdown |
| Markdown（`.md`、`.markdown`） | ✅ | 命名的 Markdown 路径/流原样保留；未命名的 UTF-8 字节按 TXT 检测 |
| PDF | ✅ | 逐页原生、混合、全视觉或空白路由；源顺序的页面边界与表格 |
| PNG / JPEG / WebP | ✅ | 仅静态图片；在送入配置的视觉模型前进行清洗 |
| DOCX | ✅ | 连续的原生正文流，包含结构化文本、列表、链接、表格、显式分页与内联图片 |
| PPTX | ✅ | 按幻灯片与形状树源顺序输出，含文本、表格、可访问图表、组合与内联图片 |
| XLSX（`.xlsx`） | ✅ | 按源顺序输出所有工作表类条目、已保存值、表格/区域、合并单元格、标准文本对象、原生图表事实及可选视觉解读 |

仅支持标准 `.xlsx` 工作簿。旧版 `.xls`、启用宏的 `.xlsm`、二进制 `.xlsb` 及其他电子表格
格式均不受支持。

## 视觉解析

当提供 `VisionConfig` 时，图片与视觉 PDF 处理通过供应商无关的
[LiteLLM](https://github.com/BerriAI/litellm) 适配器完成。原生与空白 PDF 页面不会调用模型。

**Poppler**（`pdftoppm`）仅在视觉/混合 PDF 解析时需要 —— 使用平台包管理器安装：

```bash
# macOS
brew install poppler

# Debian / Ubuntu
apt-get install poppler-utils
```

独立图片需要 `VisionConfig`。未配置视觉时，PDF 与 Office 文档会保留可用的原生内容，并对
视觉区域发出确定性警告；在视觉增强不可用时，XLSX 始终保留其原生工作表与图表事实。当某
格式需要视觉、而文档又没有可用原生内容时，将抛出 `VisionRequiredError`。

```python
from opendocs import ParseOptions, VisionConfig, parse

markdown = parse(
    "scan.pdf",
    options=ParseOptions(timeout=300, max_pages=100, vision_concurrency=4),
    vision=VisionConfig(
        model="openai/gpt-4o-mini",
        api_key="...",  # 生产环境建议使用由环境变量托管的密钥。
    ),
)
```

`ParseOptions` 控制文档超时、PPTX/PDF 页数、输出大小与视觉并发。模型失败 —— 认证、权限、
无效请求、临时不可用、无效响应 —— 使用不同的类型化异常，便于精确错误处理。

`ParseOptions.vision_concurrency` 限制单次解析内的视觉请求数量。跨文档并发由应用自行控制
（例如使用 `asyncio.Semaphore`）；参见
[独立消费者示例](examples/basic_consumer/README.md)。

### DOCX、PPTX 与 XLSX 细节

DOCX 提取保留原生正文段落、标题、列表、安全链接、表格、合并单元格、显式分页与内联位图
位置。DOCX 保持单一连续逻辑流；`max_pages` 不会推断 Word 的物理页数。

PPTX 提取输出每个幻灯片边界，并按源顺序遍历每张幻灯片的形状树，包括递归组合、文本、
表格、可访问图表数据与位图图片。完全相同的重复嵌入图片每次解析只分析一次，并在每个原生
位置重放。

XLSX 提取按工作簿顺序输出每个工作表与图表工作表，包括可见、隐藏、深度隐藏和空白工作表。
它保留非空区域、Excel 表格、合并单元格跨度、标准批注/文本框/链接/页眉页脚文本，以及常见
已保存显示语义，如 `$`、`€`、`£`、`¥` 货币、分组、小数、百分比、日期与时间。优先使用已
保存的公式缓存；当缓存缺失时，返回公式文本并给出警告。OpenDocs 不会重新计算公式，也不会
获取链接工作簿、数据连接或 URL —— 引用仅作为文本保留。

图表标题、标签、系列、类别与可访问值来自原生工作簿数据。配置视觉时，规范化的图表事实
卡片与嵌入图片可补充趋势、标签、关系或含义解读。这种增强是故障开放的（fail-open），永远
不会取代原生事实。XLSX 输出不承诺 Excel 像素外观、字体、颜色、边框、尺寸或其他视觉样式
保真。

### 对比

| 特性 | OpenDocs | marker | docling | unstructured | pypdf |
| --- | :---: | :---: | :---: | :---: | :---: |
| PDF → Markdown | ✅ | ✅ | ✅ | ✅ | ❌ |
| DOCX → Markdown | ✅ | ❌ | ✅ | ✅ | N/A |
| PPTX → Markdown | ✅ | ❌ | ✅ | ✅ | N/A |
| XLSX → Markdown | ✅ | ❌ | ✅ | ✅ | N/A |
| LLM 视觉集成 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 同步 + 异步 API | ✅ | ❌ | ❌ | ❌ | ❌ |
| 无需外部服务 | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 纯 Python（无系统依赖） | ✅ | ❌ | ❌ | ❌ | ✅ |
| 类型化错误与警告 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 图片 → Markdown | ✅ | ❌ | ❌ | ✅ | ❌ |
| 供应商无关视觉（LiteLLM） | ✅ | N/A | N/A | ❌ | N/A |

> **核心差异点**：OpenDocs 是唯一将原生 Office/PDF 提取与可选 LLM 视觉理解相结合、并通过
> 带类型化错误的整洁同步/异步 API 提供的库。

**平台**：Ubuntu 与 macOS，Python 3.11、3.12、3.13（视觉 PDF 需要 Poppler）。Windows 在
`0.2.0` 中尚未验证。

**隐私**：OpenDocs 从不下载 HTTP、OSS 或 S3 URL。模型调用会将经过清洗的图片发送给
`VisionConfig` 选择的供应商 —— 在启用视觉之前，请查看该供应商的隐私与费用条款。

## 警告与错误

OpenDocs 对可恢复的降级使用 Python 警告，对致命失败使用类型化异常。

```python
import warnings

from opendocs import OpenDocsError, OpenDocsWarning, ParseOptions, parse

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always", OpenDocsWarning)
    markdown = parse(
        b"first paragraph\n\nsecond paragraph\n",
        options=ParseOptions(max_output_chars=16),
    )

assert markdown == "first paragraph\n"
assert caught[0].message.code == "output_truncated"

try:
    parse("slides.pdf")
except OpenDocsError as error:
    print(error.code, error.retryable)
```

## 项目文档

- [文档](docs/README.md)
- [路线图](docs/roadmap.md)
- [贡献指南](CONTRIBUTING.md)
- [许可证](LICENSE)
