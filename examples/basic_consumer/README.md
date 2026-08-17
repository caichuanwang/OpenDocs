# 独立 consumer 示例

该项目只依赖公开分发包 `opendocs-sdk==0.2.0`，不使用 OpenDocs 仓库路径、私有模块或
应用层集成。

发布前可先将本地构建的 wheel 安装到隔离环境，再在本目录运行：

```bash
python main.py notes.txt --output parsed
python main.py one.docx two.pptx --output parsed --async --document-concurrency 2
```

`--document-concurrency` 通过调用方拥有的 `asyncio.Semaphore` 限制同时解析的文档数；
`--vision-concurrency` 则传入每个文档自己的 `ParseOptions`，限制单次解析内部的视觉
请求。两者不是同一个并发边界。

扫描 PDF、图片或 Office 视觉区域需要 `--vision-model provider/model`，凭据应通过
提供商支持的环境变量配置。视觉解析可能产生模型费用。示例仅接受本地路径，不下载
URL。
