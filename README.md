# 本地腾讯文档 RAG 系统

这是一个可在本地离线运行的 RAG 基线项目。它把腾讯文档导出的文件作为语料库，使用本地 embedding 模型建立 FAISS 向量索引，并可通过本地 GGUF 大模型生成答案。

## 支持的数据来源

腾讯文档建议先在网页端导出到本地，再放入 `data/raw/`：

- 文档：`.docx`、`.txt`、`.md`、`.pdf`
- 表格：`.xlsx`、`.xls`、`.csv`

对于包含商品图片的 `.xlsx` 清单，系统会直接解析工作簿内部结构，按商品行提取文本字段，并把锚定在同一行的图片保存为语料附件。

项目不会上传文件，也不依赖在线 API。

## 目录结构

```text
customer_rag/
  app.py                  # Streamlit 本地问答和语料库管理界面
  config.yaml             # 模型、路径、检索参数
  requirements.txt
  customer_rag/
    cli.py                # 命令行入口
    corpus.py             # 可编辑语料库存储
    config.py
    loaders.py
    splitter.py
    vector_store.py
    llm.py
    pipeline.py
  data/
    raw/                  # 放腾讯文档导出文件
    index/                # 可编辑语料和自动生成的 FAISS 索引
  models/
    embeddings/           # 本地 embedding 模型目录
    llm/                  # 本地 GGUF 大模型目录
```

## 准备离线模型

推荐：

- Embedding：`BAAI/bge-small-zh-v1.5` 或 `shibing624/text2vec-base-chinese`
- LLM：任意支持 `llama.cpp` 的中文 GGUF 模型，例如 Qwen 系列量化 GGUF

把 embedding 模型完整目录放到：

```text
models/embeddings/bge-small-zh-v1.5
```

把 GGUF 文件放到：

```text
models/llm/model.gguf
```

如果暂时没有 GGUF，系统仍可检索并展示相关语料片段，只是不会生成整合答案。

默认推荐使用项目内置托管的 `llama.cpp` 原生 `llama-server.exe`。启动器会按配置启动本地
`llama-server`，RAG 程序通过 HTTP 调用它；这样打包后用户不需要额外安装 Ollama 或
`llama-cpp-python`。

把对应后端的 `llama-server.exe` 放到以下任一目录：

```text
tools/llama.cpp/cpu/llama-server.exe
tools/llama.cpp/vulkan/llama-server.exe
tools/llama.cpp/cuda/llama-server.exe
```

当 `llama_server_backend: auto` 时，启动器会优先检测 NVIDIA/CUDA，其次 Vulkan，最后 CPU。
也可以手动指定 `cpu`、`vulkan` 或 `cuda`。

当前默认配置为：

```yaml
llm:
  backend: llama_cpp_server
  ollama_url: http://localhost:11434
  ollama_model: deepseek-r1:1.5b
  llama_server_url: http://127.0.0.1:8081
  llama_server_host: 127.0.0.1
  llama_server_port: 8081
  llama_server_executable: ''
  llama_server_backend: auto
  n_gpu_layers: 0
  n_ctx: 2048
  n_threads: 4
  max_tokens: 512
  num_batch: 128
  keep_alive: 0s
```

如果你仍想使用本机 Ollama，可以把 `backend` 改成 `ollama`，并把 `ollama_model`
改成对应名称，例如 `qwen2.5:3b` 或 `llama3:8b`。这种方式不需要项目内置
`llama-server.exe`。

为了减少内存占用，默认配置会限制上下文长度和生成长度，并在每次回答后让 Ollama 尽快释放模型内存：

- `n_ctx: 2048`：降低上下文窗口，减少推理内存。
- `max_tokens: 512`：限制单次回复长度。
- `n_threads: 4`：减少 CPU 并发压力。
- `num_batch: 128`：降低推理批大小。
- `n_gpu_layers: 0`：CPU 模式不占用显存；使用 CUDA/Vulkan 时可逐步调大。
- `keep_alive: 0s`：回答结束后不长期常驻模型。

如果机器内存仍然紧张，优先把 `ollama_model` 换成更小的模型，例如 `deepseek-r1:1.5b` 或 Qwen 1.5B 量化模型。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`llama-cpp-python` 在 Windows 上可能需要本机 C++ 构建环境。如果你只想先验证入库和检索，可以先安装除它之外的依赖，或稍后再补装。

## 使用

1. 把腾讯文档导出文件复制到 `data/raw/`
2. 构建索引：

```powershell
python -m customer_rag.cli ingest
```

3. 命令行提问：

```powershell
python -m customer_rag.cli ask "客户退款流程是什么？"
```

4. 启动 Web 图形化界面：

```powershell
streamlit run app.py
```

也可以直接运行：

```powershell
python .\app.py
```

项目会自动转为 Streamlit 启动。

## 图形化语料库管理

Web 界面包含三个页签：

- `问答`：对当前向量索引提问，并展示引用片段。
- `语料管理`：对语料库进行新增、搜索、筛选、查看、编辑、单条删除和批量删除。
- `导入文件`：上传腾讯文档导出的本地文件，自动解析后加入语料库。
- `Prompt 设置`：编辑系统提示词，让模型按你的格式和口径回答。

语料库的可编辑数据保存在：

```text
data/index/corpus.jsonl
```

导入的原始文件保存在：

```text
data/raw/
```

修改、删除或新增语料后，需要点击侧边栏的 `重建向量索引`，更新后的内容才会进入问答检索结果。上传文件导入时会自动重建索引。

语料管理支持：

- 按关键词搜索标题、来源、位置和正文。
- 按来源文件筛选，例如只看某一个 Excel 导入的商品。
- 按自定义 Tag 分类筛选，例如 `小家电`、`家装`、`618`、`床头柜`。
- 按是否包含图片筛选。
- 使用每条语料前的复选框勾选，支持全选当前显示列表。
- 给勾选语料批量追加 Tag。
- 删除勾选语料。
- 删除当前筛选结果中的全部语料。

批量删除只会删除 `data/index/corpus.jsonl` 中的语料记录，不会删除 `data/raw/` 下的原始上传文件。删除后请点击 `重建向量索引`。

新增语料和导入文件时可以填写 Tag，多个 Tag 用逗号分隔。问答页也可以选择 Tag 分类，系统会优先在选定分类内检索。

Tag 对搜索速度的影响：

- 仅给语料加 Tag 不会让 FAISS 向量计算天然变快。
- 当问答时选择 Tag，系统会缩小候选范围，减少无关资料进入回答上下文，通常会提高准确性。
- 在当前几千条语料规模下，速度提升有限；如果未来扩展到几十万条以上，可以进一步为每个 Tag 建独立索引，届时按分类检索会明显提速。

如果导入的 Excel 包含图片，图片会保存到：

```text
data/raw/_assets/<文件名>/
```

语料管理页和问答引用片段中会展示对应图片。当前 RAG 检索仍以文字字段为主，图片作为商品语料的可视化附件保留。

对于合并单元格或空白继承的 Excel，导入器会把上一行的公共字段向下补齐，例如 `品牌`、`品类`、`商品链接`、`赠品`、`迷住权益`、`特殊信息`、`其他说明` 等。这样多个款式共用同一个链接时，每个款式语料也能检索和回答出链接。

如果你修改了导入规则或发现旧语料缺少共享字段，请点击侧边栏的 `重新解析全部原始文件`。它会重新读取 `data/raw/`，重建 `corpus.jsonl`，并在依赖齐全时同步重建向量索引。

## 配置

主要参数在 `config.yaml`：

- `embedding_model_path`：本地 embedding 模型目录
- `llm_model_path`：本地 GGUF 文件路径
- `chunk_size` / `chunk_overlap`：语料分块大小
- `top_k`：每次检索的片段数量
- `temperature` / `max_tokens`：生成参数

## 实现方案

1. 文档加载：读取腾讯文档导出的本地文件，保留文件名、页码、表格行号等元数据。
2. 文本分块：按段落和长度切分，使用 overlap 保持上下文连续。
3. 向量化：本地 sentence-transformers 模型生成 embedding。
4. 索引：FAISS `IndexFlatIP` 做余弦相似度检索。
5. 生成：把检索片段和问题组装成中文提示词，交给本地 llama.cpp GGUF 模型。
6. 溯源：答案附带引用片段，便于核对原始腾讯文档。
