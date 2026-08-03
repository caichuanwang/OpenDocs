# 发布运行手册

`release.yml` 只接受 `v*` 标签，并在发布前校验标签版本、`origin/master` 可达性、完整
公开门和冻结的 Alpha 证据。工作流只构建一次，后续环境下载同名 `release-dists`
artifact。

发布使用两个连续提交解决证据文件无法引用自身提交 SHA 的循环：第一个提交是经过验证的
代码候选，第二个提交只能新增对应版本的 evidence 文件。标签指向 evidence 提交，工作流
要求 evidence 中的 `Candidate commit` 精确等于其唯一父提交，并拒绝夹带其他文件变化。

## 外部配置

在 GitHub 中创建两个 Environment：

- `testpypi`：用于 TestPyPI 演练。
- `pypi`：用于公共发布；仓库计划支持时配置 maintainer 审批，否则由标签推送前的人工确认
  承担不可逆发布边界。

在 TestPyPI 与 PyPI 中为 `opendocs-sdk` 分别配置 Trusted Publisher，绑定必须精确为：

- Owner：`caichuanwang`
- Repository：`OpenDocs`
- Workflow：`release.yml`
- Environment：对应 `testpypi` 或 `pypi`

工作流不读取 PyPI token。只有两个发布 job 拥有 `id-token: write`，只有最终 GitHub
Release job 拥有 `contents: write`。

## 发布顺序

1. 完成 maintainer 批准的 M2 checklist 与 replay/live 门。
2. 解析并由 maintainer 验收独立的真实 DOCX/PPTX Alpha holdout；私有文件、哈希、原始
   Markdown 和提供商载荷不得进入 Git。
3. 对代码候选运行公开套件、静态检查、构建、制品检查与适用的本地门。
4. 将代码候选推送到 `master`，确认 Ubuntu/macOS 的 Python 3.11-3.13 CI 全绿。
5. 以安全聚合形式新增 `v0.1.0-evidence.md`，其中明确记录已验证项和未运行项；该 evidence
   commit 不得包含其他文件变化。
6. 从 evidence commit 创建并推送 `v0.1.0` 标签。
7. TestPyPI 安装通过后，在适用的 `pypi` Environment 审批边界放行公共发布，等待公共
   安装 smoke 与 GitHub Release 完成。

`0.1.0` 采用精简 Alpha 验收，不把尚未执行的 30/30 PDF/图片质量基准描述为通过，也不以
此版本声明生产就绪或性能 SLA。任何源码、策略或身份变化都使冻结证据失效，必须重跑受
影响的门并重新生成 evidence commit。
