# 发布运行手册

`release.yml` 只接受 `v*` 标签，并在发布前校验标签版本、`origin/master` 可达性、完整
公开门和冻结的 M3 聚合证据。工作流只构建一次，后续环境下载同名
`release-dists` artifact。

## 外部配置

在 GitHub 中创建两个受保护 Environment：

- `testpypi`：用于 TestPyPI 演练。
- `pypi`：用于公共发布，必须配置 maintainer 审批。

在 TestPyPI 与 PyPI 中为 `opendocs-sdk` 分别配置 Trusted Publisher，绑定必须精确为：

- Owner：`caichuanwang`
- Repository：`OpenDocs`
- Workflow：`release.yml`
- Environment：对应 `testpypi` 或 `pypi`

工作流不读取 PyPI token。只有两个发布 job 拥有 `id-token: write`，只有最终 GitHub
Release job 拥有 `contents: write`。

## 发布顺序

1. 完成 maintainer 批准的 M2 checklist 与 replay/live 门。
2. 冻结策略、候选 commit、manifest、evaluator、模型、replay 和环境身份。
3. 使用未污染 holdout 与资源门生成 `v0.1.0-evidence.md`。
4. 合并候选到 `master`，确认 Ubuntu/macOS 的 Python 3.11-3.13 CI 全绿。
5. 从该 commit 创建并推送 `v0.1.0` 标签。
6. 在 `pypi` Environment 审批后，等待公共安装 smoke 与 GitHub Release 完成。

任何源码、策略或身份变化都使冻结证据失效，必须重跑受影响的门。
