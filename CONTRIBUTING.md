# 参与维护

## 快速开始

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m ruff check src tests scripts
```

Windows 上还需保证 `vendor/alice/alice.exe` 存在（本地开发用，
git 不跟踪）：

```bash
python -m aindiff fetch-alice
```

## 修改流程

1. 检查工作区现有改动并保留；需要新分支时使用 `codex/` 前缀。
2. 修改代码，并补单元测试：
   - `tests/test_model.py`：文本 dump 模型与对齐
   - `tests/test_alice_tools.py`：alice 命令封装
   - `tests/test_ain_sections.py`：System4 原始区段解析
   - `tests/test_xsys35.py`：AINI 容器解析/回写
   - `tests/test_gui_helpers.py`：纯 GUI 逻辑
3. 本地跑：

   ```bash
   python -m ruff check src tests scripts
   python -m unittest discover -s tests
   ```

4. 提交信息使用简短动词开头，例如 `Fix ...`、`Add ...`。

## 发布

版本号只维护一处：`src/aindiff/__init__.py` 的 `__version__`。
`pyproject.toml` 从它动态读取。

一键构建解压即用包：

```bash
python scripts/build_release.py
```

产物在 `release/aindiff-v<版本>-win64.zip`。
打包配置只维护 `aindiff.spec`；构建脚本直接使用此文件，不会重新生成它。
同目录自动生成 `.zip.sha256` 校验文件。构建前需将 alice 缓存中的可执行文件复制到 `vendor/alice/alice.exe`；`fetch-alice` 不会直接填充此构建路径。

## 验证和上传范围

- GUI 验证使用少量合成文本；禁止全量 GUI 压测。真实 AIN 写回测试必须使用临时副本，不修改游戏原文件。
- 当前维护环境通过 `build/github-upload` 独立仓库和 `build/github-upload-manifest.json` 白名单上传，不直接推送原工作区历史。
- 同步白名单内文件后审查差异再上传；不上传游戏文件、凭据、本机交接记录、测试导出文本及构建目录。

## 目录约定

- `src/aindiff/`：所有包代码
- `scripts/`：开发/构建脚本
- `tests/`：纯单元测试（CI 会跑）
- `docs/`：用户与开发文档
- `vendor/`：本地二进制，不提交
- `release/`、`dist/`、`build/`：构建产物，不提交
