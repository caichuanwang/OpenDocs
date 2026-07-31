# 文档解析发布基准

该目录只保存公开 schema、冻结策略和执行代码。真实文档、标注、模型载荷、原始
Markdown 与运行工作区必须保存在被 Git 忽略的本地目录。

## 数据分割

- `tuning` 与 `holdout` 各包含 30 个 PDF/图片页面。
- 两个 split 均覆盖策略定义的六类内容，每类至少 5 页。
- 同一页面内容或同一源文档不得跨 split。
- 每个 split 另含一个真实 DOCX 和一个真实 PPTX。
- holdout 一旦被查看、用于阈值校准或用于提示词/代码调整，就必须移入 tuning 并替换。

将 `manifest.example.json` 复制为被忽略的 `manifest.local.json`，替换全部 `replace-`
占位符，并补齐策略要求的 64 条记录。示例本身用于说明字段，不是可执行的完整
基准。

## 隐私边界

公开证据只能包含候选 commit、版本、策略与 manifest 摘要、分类计数、聚合指标、
环境身份和资源摘要。不得写入文件名、路径、提取文本、标注内容、提示词、凭据、
提供商载荷或原始 Markdown。

## 执行顺序

所有原始结果写入 `benchmarks/document_parsing/runs/` 下的独立目录：

```bash
python -m benchmarks.document_parsing.run_quality \
  --mode tuning \
  --manifest benchmarks/document_parsing/manifest.local.json \
  --workspace benchmarks/document_parsing/runs/v0.1.0-tuning \
  --observations benchmarks/document_parsing/private/tuning-observations.json \
  --candidate-commit <40-char-commit> \
  --model-identity <provider-model> \
  --environment-identity <os-python-runtime> \
  --replay-identity <replay-id>

python -m benchmarks.document_parsing.run_quality \
  --mode freeze \
  --tuning-result benchmarks/document_parsing/runs/v0.1.0-tuning/safe-quality-tuning.json \
  --freeze-record benchmarks/document_parsing/private/v0.1.0-freeze.json
```

holdout 使用相同身份参数，并额外传入 `--freeze-record`。任何摘要不匹配都会使运行
失败；holdout 不修改阈值或标注。

资源证据使用受控视觉 dispatcher，始终设置总超时：

```bash
python -m benchmarks.document_parsing.evaluate_resources \
  --environment-identity <os-python-runtime> \
  --candidate-commit <40-char-commit> \
  --policy-digest <64-char-policy-digest> \
  --manifest-digest <64-char-manifest-digest> \
  --output benchmarks/document_parsing/runs/v0.1.0-resources.json
```

只有 tuning、holdout 和资源记录全部通过后，才可生成公开证据：

```bash
python -m benchmarks.document_parsing.render_evidence \
  benchmarks/document_parsing/runs/v0.1.0-tuning/safe-quality-tuning.json \
  benchmarks/document_parsing/runs/v0.1.0-holdout/safe-quality-holdout.json \
  --resource benchmarks/document_parsing/runs/v0.1.0-resources.json \
  --output docs/releases/v0.1.0-evidence.md
```

当前仓库故意不预生成该文件。
