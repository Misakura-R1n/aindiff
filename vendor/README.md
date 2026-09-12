# vendor/alice

本目录用于放置 alice-tools 的 `alice` 可执行文件（本地开发便利），
**不进入 git 版本库**（见 `.gitignore`）。

放置方式：

```bash
# 方式 1：自动下载到用户缓存
python -m aindiff fetch-alice

# 方式 2：复制官方 release 中的 alice.exe 到本目录
#   https://github.com/nunuhara/alice-tools/releases
cp alice.exe vendor/alice/alice.exe
```

运行时查找顺序：`AINDIF_ALICE` 环境变量 → `vendor/alice/alice(.exe)` → `PATH` → 用户缓存。

上游许可证：`../licenses/alice-tools/COPYING.txt`。
