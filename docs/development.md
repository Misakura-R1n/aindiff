# 开发与测试

## 环境

- Python >= 3.9（仅标准库；GUI 使用 Tkinter）
- 可选：alice-tools `alice` 可执行文件（跑集成测试或实际使用）

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

## 代码结构

| 模块 | 职责 |
| --- | --- |
| `aindiff.model` | 解析/生成 `ain dump -t` 文本格式；转义；双栏对齐 |
| `aindiff.alice_tools` | 查找/下载/调用 alice；编码探测；解密镜像；原子写回 |
| `aindiff.ain_sections` | 解析 `ain dump --map`，读取 MSG0/MSG1/STR0 文本区段 |
| `aindiff.gui` | Tkinter 窗口、Treeview 渲染、查找定位、结构导航、就地编辑、保存流程 |
| `aindiff.cli` | 子命令 `gui` / `export` / `check` / `fetch-alice` |

分层原则：

- `model` 不接触子进程和文件系统之外的 .ain 细节，便于纯单元测试。
- `gui` 只通过 `alice_tools.AliceTools` 读写 .ain。
- 所有 alice 参数拼接集中在 `alice_tools.py`，升级 alice-tools 时只改一处。

## 测试约定

- `tests/test_model.py`：无需 alice 二进制，覆盖 dump 解析、转义往返、
  编辑补丁生成、冲突检测、对齐与单边行。
- `tests/test_alice_tools.py`：使用 fake `alice` 脚本验证命令行参数、备份、
  原子替换和编码探测顺序。

### 可选集成测试

在有两个同结构 AIN 的环境下手工执行：

```bash
cp A.ain /tmp/A.ain
python - <<'PY'
from pathlib import Path
from aindiff.alice_tools import AliceTools
from aindiff.model import parse_dump, build_edit_text

tools = AliceTools("vendor/alice/alice.exe")
dump = parse_dump(tools.dump_text("/tmp/A.ain", "CP932"))
entry = dump.entries[0]
entry.text = entry.text + "测试"
tools.edit_text("/tmp/A.ain", build_edit_text([entry]), dump.encoding)
after = parse_dump(tools.dump_text("/tmp/A.ain", dump.encoding))
assert after.entries[0].text == entry.text
PY
```

## 新增命令

1. 在 `cli.build_parser()` 注册子命令。
2. 实现 `_run_xxx(args) -> int`。
3. 在 `main()` 的调度分支中调用。
4. 更新本文件与 `docs/usage.md`。

## 发布

1. 只改版本号：`src/aindiff/__init__.py` 中的 `__version__`。
2. 更新 `CHANGELOG.md`。
3. 一键构建：

   ```bash
   python scripts/build_release.py
   ```

4. 检查：
   - `aindiff --version`
   - `git check-ignore -v vendor/alice/alice.exe`
   - 解压 `release/aindiff-v<版本>-win64.zip`，运行 `aindiff.exe --version`
