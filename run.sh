#!/usr/bin/env bash
# 启动本机 B站直播消费数据分析工具；加 --demo 可用假数据试用
cd "$(dirname "$0")"
# 直接把 src 加进路径：项目在 iCloud 同步的文件夹里时，venv 里的 .pth 可能被标成隐藏而失效
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec uv run python -m bili_local_tool "$@"
