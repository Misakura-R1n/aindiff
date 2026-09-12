# 与 alice-tools 的结合点

本项目不解析 AIN 二进制格式，所有底层操作委托给
[nunuhara/alice-tools](https://github.com/nunuhara/alice-tools) 的 `alice` CLI。
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

## 升级上游

1. 查看 [Releases](https://github.com/nunuhara/alice-tools/releases) 最新 tag。
2. 更新 `src/aindiff/__init__.py` 中的 `ALICE_RELEASE_URL`/`ALICE_RELEASE_TAG`。
3. 更新 `vendor/alice/alice.exe` 或用户缓存。
4. 跑 `python -m unittest` 与 `aindiff check <a.ain> <b.ain>` 冒烟测试。
