# 与 alice-tools 的结合点

System4 的容器读写委托给 [nunuhara/alice-tools](https://github.com/nunuhara/alice-tools) 的 `alice` CLI，
本项目解析其区段表及解密后的文本区段；旧 AINI/AIN2 使用内置解析器。
本文记录每个调用与上游源码的对应关系，便于升级或排错。

## 使用的命令

| 本项目位置 | alice 命令 | 上游源码依据 |
| --- | --- | --- |
| 读取全部文本节 | `alice ain dump -t --input-encoding <enc> --output-encoding UTF-8 -o out.txt in.ain` | `src/cli/ain_dump.c`，核心输出在 `src/core/ain/dump.c::ain_dump_text()` |
| 写回修改 | `alice ain edit -t changes.txt --input-encoding UTF-8 --output-encoding <enc> -o out.ain in.ain` | `src/cli/ain_edit.c`，文本补丁解析在 `src/core/ain/text.c::ain_read_text()` |
| 编码自动识别 | 多次执行上面的 dump，按成功与否选择 | `src/core/conv.c`（CP932↔UTF-8 转换） |

## 文本补丁语法（本项目的 model.py 对应实现）

- dump 中每一行是被注释的赋值：

  ```text
  ; 函数/节名
  ;s[123] = "文本"
  ;m[456] = "消息"
  ```

- 去掉行首 `;` 后即可被 `ain edit -t` 接受。
- 转义规则（见 `src/core/ain/dump.c::escape_string` 与
  `src/core/ain/text_lexer.l`）：
  `\`、`\"`、`\n`、`\r`；lexer 额外接受 `\t`、`\b`、`\f`。
- `s` 是字符串表（供代码使用），`m` 是消息表（显示给玩家）。
- 同一 ID 重复出现时，后出现的赋值覆盖先出现的（`text.c` 顺序应用 statements）。

## 编码语义

- `alice ain dump -t` 默认 input=CP932、output=UTF-8。
- `alice ain edit -t` 默认 input=UTF-8、output=CP932。
- 对中文汉化 AIN（文本字节为 CP936），必须给 `ain dump` 传
  `--input-encoding CP936`，给 `ain edit` 传 `--output-encoding CP936`；
  这正是本工具把每个文件的探测编码保存下来并在写回时复用的原因。

## 混合编码写回（v0.4.6）

alice 0.13.0 的 [ain_edit.c](https://github.com/nunuhara/alice-tools/blob/0.13.0/src/cli/ain_edit.c)
在应用文本修改前调用 `ain_init_member_functions(ain, conv_output_utf8)`，按输出编码解码函数名。
因此 CP932 函数名与 CP936 正文混合时，即使只修改消息，也可能先触发 `iconv` 错误。

`AliceTools.edit_text` 保留原流程，仅在 `AliceRunError` 包含 `iconv:` 时重试一次：

1. 严格解析本程序生成的数字 ID 赋值，反转义后按实际目标编码生成字节。
2. 将这些字节一一映射到 Latin-1 字符，重新转义并以 UTF-8 保存临时补丁。必须在字节映射后转义，避免 CP932/CP936 的反斜杠尾字节被当作语法。
3. 使用 `--input-encoding UTF-8 --output-encoding ISO-8859-1` 交给 alice，得到原本所需的目标字节。Latin-1 仅用于临时传输，不改变用户选择或文件的实际文本编码。
4. 重试前移除第一次失败产生的临时输出；成功生成非空文件后才备份并原子替换。

函数名初始化能通过 Latin-1 解码，存储内容无需转码。实现仍使用上游写入器，未新增 System4 二进制写入器。
参见上游 [text_lexer.l](https://github.com/nunuhara/alice-tools/blob/0.13.0/src/core/ain/text_lexer.l) 中的转义与 `conv_output` 处理。
重试拒绝非法补丁、NUL、不可编码文本及超出上游固定字符串缓冲区的传输内容，不吞掉非编码错误。

真实样本验证中，ランス０２同时修改 MSG0、STR0 后的完整解密数据与预期字节一致，恢复后也完全一致；这仍不等于游戏内运行验证。

## 升级上游

1. 查看 [Releases](https://github.com/nunuhara/alice-tools/releases) 最新 tag。
2. 更新 `src/aindiff/__init__.py` 中的 `ALICE_RELEASE_URL`/`ALICE_RELEASE_TAG`。
3. 更新 `vendor/alice/alice.exe` 或用户缓存。
4. 跑 `python -m unittest` 与 `aindiff check <a.ain> <b.ain>` 冒烟测试。
