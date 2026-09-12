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

1. 从 `master` 拉一个新分支。
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

## 目录约定

- `src/aindiff/`：所有包代码
- `scripts/`：开发/构建脚本
- `tests/`：纯单元测试（CI 会跑）
- `docs/`：用户与开发文档
- `vendor/`：本地二进制，不提交
- `release/`、`dist/`、`build/`：构建产物，不提交
