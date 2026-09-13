# AinDiff —— AliceSoft AIN 文本对照编辑工具

打开一个或两个 `.ain` 文件，查看、查找和编辑文本，并把修改写回原文件。
支持 System4 AIN，以及 System39 等旧 xsys35 的 AINI/AIN2 容器。

System4 的读取和写回调用 [alice-tools](https://github.com/nunuhara/alice-tools)；
旧 xsys35 容器由本项目内置解析器处理。实际支持范围取决于文件格式、编码和工具版本。

## Windows 解压即用版

1. 将 `aindiff-v<版本>-win64.zip` **完整解压**到一个目录。
2. 双击 `aindiff.exe`。发布包已包含 Python、Tkinter 和 alice，无需另外安装。
3. 点“打开左侧”选择 AIN，需要对照时再选择右侧文件，然后点“加载 / 刷新”。
4. 双击文本单元格编辑，点“保存左侧”或“保存右侧”，确认路径后写回。

不要只复制 `aindiff.exe`，它需要同目录中的 `_internal` 文件夹。
简明操作见包内 `使用说明.txt`，完整说明见 [使用说明](docs/usage.md)。

读取和解析在后台执行，主表分批显示并提示进度。加载期间暂时不能编辑或保存；读取失败保留已有表格和编辑。主表仍保存全部条目，分批显示不等于分页或降低全部内存占用。

## 主要功能

- **单文件编辑与双栏对照**：双栏按节序号、文本类型、ID 和出现次数对齐，适合同一游戏、同一版本的不同文本版本。
- **查找与筛选**：按两侧文本、节名、类型或 ID 查找；支持“仅显示匹配”和“仅显示差异行”。
- **大表响应**：首次查找和筛选分批计算，显示进度并保持窗口响应；同查询筛选复用查找结果。
- **文本区段导航**：System4 支持 MSG0/MSG1 消息表、STR0 字符串表；xsys35 支持 MSGI/VARI。其他区段只显示信息。
- **编辑与备份**：支持多行文本；默认写回前备份为 `<原文件名>.bak`，生成临时文件后原子替换原文件。
- **混合编码回退**：函数级文本无法读取时尝试原始文本表。回退视图只包含选中的表，并非整个 AIN 的全部文本。
- **命令行**：通过 `check` 查看差异统计，通过 `export` 导出 UTF-8 TSV 对照表。

## 编辑与保存

1. 双击左侧或右侧文本单元格；回车换行，`Ctrl+Enter` 或点击别处提交，`Esc` 取消。
2. 点“保存左侧 / 保存右侧”，或按 `Ctrl+S` 保存有修改的两侧；写回前会询问确认。
3. 默认备份在原文件旁，例如 `game.ain.bak`。**后续保存会覆盖同名备份**；需要保留最初版本时，请另行复制。

同一 `s[id]` 或 `m[id]` 可能在多个位置出现，但文件中对应同一个表项。
修改一处并保存后，其他出现位置会同步更新；若多处编辑互相冲突，工具会拒绝保存。
切换视图或重新加载时如有未保存编辑，会询问是否丢弃。

## 编码与兼容

| 场景 | 编码选择与行为 |
| --- | --- |
| 日文 System4 原版 | 自动识别优先尝试 CP932 |
| 中文汉化版 | 常用 CP936 / GBK，可手动选择 |
| 旧 xsys35 容器 | 自动识别优先尝试 UTF-8，再尝试 CP932、CP936 |
| 混合编码 System4 | 函数视图读取失败后尝试 MSG0、MSG1、STR0；原始表默认依次尝试 CP936、CP932、UTF-8 |

自动识别以能否解码为依据，不能保证语言判断正确。如果文字不对，请为对应一侧手动选择编码，再点“加载 / 刷新”。
GUI 回退时也会保留手动编码，不会悄悄换成其他编码。

原始文本表及旧 AINI/AIN2 使用严格解码；所选编码不能解码时会报错，避免用替换字符显示后继续编辑。
v0.4.6 起，常规写回遇到 `iconv` 编码错误时会自动尝试保留原始字节的写回方式，无需切换编码。
ランス０２样本的 MSG0、STR0 已通过副本编辑、备份及恢复验证，函数级读取仍需回退。文件级验证不等于游戏内运行验证，读取成功也不保证所有混合编码文件均可保存。

大文件可先使用查找或按需展开函数节。点击切换超过 5 万条的原始文本表时会询问确认；这不是对所有加载入口的大小限制。
不建议对大文件执行“展开全部结构”。

## 从源码运行与命令行

以下命令在**源码仓库根目录**执行，需要 Python 3.9 或更新版本，并安装 Tkinter。
除外部 alice 工具外，运行时仅使用 Python 标准库。Windows 发布版以图形界面为主；需要终端中的统计或错误输出时，使用源码安装后的命令行入口。

先安装项目，再准备 alice：

```bash
python -m pip install -e .
python -m aindiff fetch-alice
```

`fetch-alice` 下载项目指定的 Windows 发布包，默认存入用户缓存，运行时自动查找。
也可从 [alice-tools Releases](https://github.com/nunuhara/alice-tools/releases) 获取工具，
将 `alice.exe` 放到 `vendor/alice/alice.exe`，或用 `AINDIF_ALICE` 环境变量、`--alice PATH` 指定。
Linux 等平台应自行安装对应平台的 alice，并确保 Tkinter 可用。
当前应用初始化时仍会查找 alice，源码运行旧 xsys35 文件也需完成这一步。

```bash
# 空窗口、单文件、双文件
python -m aindiff
python -m aindiff "单个文件.ain"
python -m aindiff "日文原版.ain" "中文汉化.ain"

# 指定两侧编码
python -m aindiff gui "原版.ain" "汉化.ain" --left-encoding CP932 --right-encoding CP936

# 差异统计与 TSV 导出
python -m aindiff check "原版.ain" "汉化.ain" --max-diffs 20
python -m aindiff export "原版.ain" "汉化.ain" -o "compare.tsv"

# 显式指定 alice；选项放在子命令后
python -m aindiff check --alice "vendor/alice/alice.exe" "A.ain" "B.ain"

# 查看帮助
python -m aindiff --help
python -m aindiff export --help
```

不安装本项目时，可用 `python scripts/run_gui.py "A.ain" "B.ain"`；
Windows 也可使用 `scripts\run_gui.bat "A.ain" "B.ain"`。

CLI 未指定编码时可自动回退，对照时会统一使用对应的原始表，并提示实际区段。
CLI 显式指定编码后，读取失败直接报错。TSV 输出不能指向输入 AIN；已有 TSV 会在成功导出后被替换。

## 开发与文档

- [详细使用说明](docs/usage.md)：界面、快捷键、保存、导出和常见问题。
- [开发说明](docs/development.md)：模块职责与验证方式。
- [参与维护](CONTRIBUTING.md)：测试和构建命令。
- [更新记录](CHANGELOG.md)：版本改动。

核心模块在 `src/aindiff/`：`gui.py` 负责界面，`cli.py` 负责命令行，
`model.py` 负责文本解析和对齐，`alice_tools.py` 调用外部工具，
`loading.py` 为 GUI 和命令行提供共享读取逻辑，`ain_sections.py` 读取原始文本表，`xsys35.py` 处理旧容器。

```bash
python -m unittest discover -s tests -v
python -m ruff check src tests scripts
python scripts/build_release.py
```

构建 Windows 发布包另需 PyInstaller，以及 `vendor/alice/alice.exe`。
源码安装默认使用用户缓存中的 alice；构建前需将它另行放入上述 vendor 路径。
构建会同时生成 ZIP 和同名 `.zip.sha256` 校验文件。
打包配置以 `aindiff.spec` 为准，构建脚本直接使用它。

## 许可证与上游

- 本项目：MIT，见 [LICENSE](LICENSE)。
- alice-tools：见 [上游项目](https://github.com/nunuhara/alice-tools) 和随附的 `licenses/alice-tools/COPYING.txt`。
- xsys35 格式参考：[kichikuou/xsys35c](https://github.com/kichikuou/xsys35c)。
