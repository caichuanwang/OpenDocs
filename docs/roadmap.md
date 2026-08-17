# OpenDocs 路线图

已批准的里程碑设计文档详见
[OpenDocs 基础与路线图设计](archive/m0/2026-07-27-opendocs-foundation-and-roadmap-design.md)。
本路线图仅概括交付顺序和退出标准，不重复设计文档中的内部架构算法。

## M0 - 基础

概述：

- 打包 Python 3.11+ SDK。
- 交付 `parse()` 和 `aparse()`，附带稳定的选项、错误、检测、注册和渲染。
- 支持所有输入的 TXT，对 `.md`/`.markdown` 本地路径或命名的二进制流保留 Markdown；
  未命名的 UTF-8 字节/流有意检测为 TXT。
- 补充测试、Ruff、ty、wheel 检查、语料清单、README、CONTRIBUTING 和 CI。

退出标准：

- 干净安装可解析所有输入形式的 TXT，对 `.md`/`.markdown` 本地路径或命名的二进制流
  保留 Markdown。
- 同步和异步调用对等价输入产生一致的 Markdown。
- 静态检查、测试和 wheel 构建全部通过。

## M1 - PDF 与图片

状态：已实现；提供了公开验证和可选私有重放/实时门。

实现架构：
[M1 PDF 与图片架构计划](archive/m1/2026-07-28-m1-pdf-images-architecture.md)。

概述：

- 新增 Pillow、pdfplumber、Poppler 和 LiteLLM 视觉集成。
- 支持独立的 PNG、JPEG 和 WebP 解析。
- 支持原生、混合、全视觉和空白 PDF 路由。
- 通过哈希优先、可选的重放/实时验收门激活 PDF 文件和超宽 PNG。

退出标准：

- 每个预期的 PDF 页面按顺序出现。
- 表格图片保留其多行表头、20 列和 4 个正文行。
- 原生 PDF 内容避免不必要的整页模型调用。

## M2 - Office

状态：已验收；提供了公开验证，维护者批准的本地检查清单、确定性重放和实时供应商门
均通过哈希绑定的验收资产。

实现架构：
[M2 Office 架构与实现计划](archive/m2/2026-07-29-m2-office-architecture.md)。

详细执行计划：
[M2 Office 详细实现计划](archive/m2/2026-07-29-m2-office-implementation.md)。

概述：

- 交付 DOCX 和 PPTX 原生提取器作为核心格式。
- 保留段落、表格、幻灯片和形状顺序。
- 通过现有视觉路径合并内嵌图片的视觉输出。
- 为 DOCX 和 PPTX 验收资产提供可选、哈希优先的门。

退出标准：

- 原生文本完整且保持源顺序。
- 内嵌视觉输出合并时不重排纯原生输出。
- 所有验收资产产生确定性的 Markdown。

## M3 - 质量与公开发布

状态：已完成。`opendocs-sdk==0.1.0` 于 2026-08-03 通过 PyPI 可信发布作为首个公开
Alpha 版本发布。受保护的发布流程、TestPyPI 演练、生产审批、Ubuntu/macOS 公共索引
smoke 以及 GitHub tag/Release 均针对同一不可变产物集通过。

发布架构：
[M3 Alpha 发布架构](plans/2026-07-31-m3-alpha-release-architecture.md)。

详细执行计划：
[M3 Alpha 详细实现计划](plans/2026-07-31-m3-alpha-release-implementation.md)。

概述：

- 在发布前关闭维护者批准的 M2 验收门。
- 验证一份真正独立的真实 DOCX 和 PPTX 作为精简 Alpha holdout。
- 扩展取消、超时、单次解析并发、依赖边界和资源证据。
- 在 Ubuntu 和 macOS 上验证 CPython 3.11-3.13 与 Poppler。
- 通过可信发布公开 `opendocs-sdk==0.1.0` 作为首个公开 Alpha 版本。
- 提供发布说明、安全聚合证据和独立的 PyPI 消费者示例。

已实现的仓库证据：

- 严格的基准策略/清单验证、污染过渡、质量/Office 评估器、冻结/holdout 运行器、
  安全证据渲染器和资源探查。
- Alpha 元数据、兼容性测试、类型标记、wheel/sdist 检查、SHA-256 校验和，以及原生
  隔离安装 smoke 工具。
- Ubuntu/macOS Python 3.11-3.13 CI、独立消费者示例和仅 tag 触发的 OIDC 发布流程。

已冻结的 [v0.1.0 证据](releases/v0.1.0-evidence.md) 记录了接受的候选版本，仅包含
安全的聚合结果。已完成的 M2 审批和独立 Office holdout 细节按照私有验收契约保留在忽略的
本地产物中。公开发布由以下验证：[发布流程](https://github.com/caichuanwang/OpenDocs/actions/runs/30807658018)、
[PyPI 项目](https://pypi.org/project/opendocs-sdk/0.1.0/) 和
[GitHub Release](https://github.com/caichuanwang/OpenDocs/releases/tag/v0.1.0)。wheel 和
源码分发的 SHA-256 摘要跨 PyPI、TestPyPI 和 GitHub Release 资产一致。

退出标准：

- M2 审批、公开门和维护者对独立真实 DOCX/PPTX holdout 的审查通过。
- 私有 Office 证据保持源隔离，仅提交安全的聚合结果。
- wheel 和源码分发在 Ubuntu 和 macOS 上通过隔离安装检查。
- `opendocs-sdk==0.1.0` 在 PyPI 上公开安装并在两个支持的操作系统上通过。
- GitHub tag 和 Release `v0.1.0` 标识确切的发布源码和产物。

明确排除：

- 无 Windows 正式支持、无新格式、无 Node.js SDK、无 CLI、无服务、无公开 JSON schema。
- 无依赖 extras 重新设计、无全局跨文档并发控制。
- 无模型调用/token/货币限制、无公开性能 SLA。

## v0.2.0 - XLSX 与资源边界加固

状态：已实现，待完成发布切版与正式发布；继续按 Alpha 定位。

详细发布计划：
[v0.2.0 发布计划](plans/2026-08-07-v0.2.0-release-plan.md)。

XLSX 的已批准产品与技术契约：
[XLSX 解析详细计划](plans/2026-08-07-001-feat-xlsx-parsing-plan.md)。

概述：

- 已新增标准 `.xlsx` 作为一个需求驱动的新格式，不承接完整 M4 范围；旧版 `.xls`、
  `.xlsm` 与 `.xlsb` 仍不支持。
- 已固定并实现全部 sheet、保存值、常见格式、合并单元格、标准文本对象、原生图表事实、
  可选 fail-open 视觉语义补充和预加载资源限制。
- `max_pages` 继续只约束真正分页或类分页格式，不把 DOCX body 或 XLSX sheet 伪装成页面。
- 保留已完成的取消清理和对抗性 PDF 防护作为发布回归门槛。
- Windows 保持探索性非阻断。

此前讨论的 30/30 PDF/图片标注工作已明确延期，不属于 v0.2.0。结构化解析结果、
依赖 extras、HTML/email、Node.js、跨语言 schema、全局并发和 CLI 同样不在本版本。

退出标准：

- XLSX 通过 `parse()` 和 `aparse()` 从 path、bytes、binary stream 确定性解析。
- 工作表排序、合并单元格、值、非文本降级、损坏和资源限制均有明确契约和测试。
- 最终 `max_pages` 决议、DOCX 计数单位、hard limit 和执行点均已记录并测试。
- 取消清理、PDF 视觉候选边界、静态检查、测试、构建和隔离安装 smoke 均通过。

## M4 - 更多格式与 Node.js

概述：

- 基于实际需求增加更多格式，如 HTML、email 或其他表格格式。
- 决定是否稳定一个跨语言的中间 schema。
- 发布 Node.js SDK，匹配输入语义、配置名称、错误码和 Markdown 固定件。

退出标准：

- 新格式按实际需求而非猜测排定优先级。
- Node.js SDK 在支持的里程碑范围内匹配 Python SDK 的公开契约。
