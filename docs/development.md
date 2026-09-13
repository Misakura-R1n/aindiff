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
| `aindiff.loading` | GUI/CLI 共用的文件读取、编码回退和对应文本表选择 |

分层原则：

- `model` 不接触子进程和文件系统之外的 .ain 细节，便于纯单元测试。
- `gui` 只通过 `alice_tools.AliceTools` 读写 .ain。
- 所有 alice 参数拼接集中在 `alice_tools.py`，升级 alice-tools 时只改一处。

## 测试约定

- `tests/test_model.py`：无需 alice 二进制，覆盖 dump 解析、转义往返、
  编辑补丁生成、冲突检测、对齐与单边行。
- `tests/test_alice_tools.py`：使用 fake `alice` 脚本验证命令行参数、备份、
  原子替换和编码探测顺序。
- `tests/test_gui_helpers.py`：不创建 Tk 窗口，覆盖筛选、查找、编辑、保存及视图切换状态。
- `tests/test_ain_sections.py` / `tests/test_xsys35.py`：使用合成容器检查区段解析、严格解码及旧容器写回。
- `tests/test_cli.py`：验证命令行、回退对照及导出失败时保留原结果。

GUI 自测限少量合成文本，禁止全量 GUI 压测；真实文件写回只操作临时副本。查找可用按钮或 Enter／Shift+Enter，避免与其他软件的 F3 冲突。

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

混合编码写回的原理与上游代码依据见 [alice-tools 结合点](alice-tools-integration.md)。

GUI 后台任务只读取快照参数并生成模型，不调用 Tk；主线程轮询取回结果，失败保留原模型。主表按 300 行分批插入，忙碌期间禁用相关操作；关闭窗口会忽略未完成结果。
编辑时按已应用的筛选条件更新单行及所属节的可见计数；重新输入查询并应用筛选时才全表重建。主表仍保留全部行，未实现虚拟列表。
超过一万行的显式搜索/筛选由 `_scan_rows` 在 Tk 主线程分批处理，每批最多 5000 行、约 8 ms 后让出事件循环。扫描过程中保持旧表格和筛选索引，完成时统一替换；关闭窗口取消回调。同查询筛选可复用完整搜索命中集合。
编辑提交中必要的重筛、保存后的刷新仍同步执行，避免内部扫描使“全部保存”的第二侧被忙碌状态拦截；这些路径不承诺分批响应。
`model.parse_edit_text` 是 AINI 与混合编码写回共用的严格补丁解析器；读取 dump 的注释语义不同，保留独立的 `parse_dump`。

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

   PyInstaller 配置以 `aindiff.spec` 为唯一来源，脚本只负责调用及整理发布文件。

4. 检查：
   - `aindiff --version`
   - `git check-ignore -v vendor/alice/alice.exe`
   - 解压 `release/aindiff-v<版本>-win64.zip`，运行 `aindiff.exe --version`
   - ZIP 的 SHA-256 与同目录自动生成的 `.zip.sha256` 文件一致。

当前维护环境的 GitHub 上传使用独立仓库 `build/github-upload` 和白名单 `build/github-upload-manifest.json`，不直接推送工作区历史。具体范围见 [参与维护](../CONTRIBUTING.md)。
