# netease-cloudmusic-detector (Python 版)

*本项目基于[Coooookies/netease-cloudmusic-detector](https://github.com/Coooookies/netease-cloudmusic-detector)这一开源项目，使用GLM5.0 Turbo进行重写为Python版本，并保留了原作者的LICENSE。在此感谢原作者的开源*

本项目已上传到Pypi：[网易云音乐检测器 ·PyPI](https://pypi.org/project/netease-cloudmusic-detector/)

**以下为AIGC内容**

使用 Python 监听网易云音乐客户端正在播放中的音乐。

支持获取 SongId、暂停状态、歌曲进度，歌曲进度跟随进度条拖拽同步。仅支持 3.0 以上客户端。

## 环境要求

- Python >= 3.10
- Windows 系统已安装网易云音乐客户端 3.0+
- 客户端至少运行过一次（以生成 elog 文件）

## 安装

```bash
# 使用 uv
uv sync

# 或使用 pip
pip install -e .
```

## 使用

```bash
uv run python main.py
```

## 依赖

无第三方依赖，仅使用 Python 标准库。

## 文件结构

```
cloudmusic_detector/
├── __init__.py        # 包入口
├── elog_analysis.py   # 二进制解码 + 日志头解析
├── elog_listener.py   # 文件轮询监听
├── constant.py        # 路径常量 + 事件匹配规则
├── webdb.py           # SQLite 只读查询
└── detector.py        # 核心调度器
main.py               # 示例入口
test_decode.py         # 测试脚本
```

## 事件


| 事件名     | 触发时机      | 参数              |
| ---------- | ------------- | ----------------- |
| `play`     | 切歌时        | `song_id: int`    |
| `status`   | 播放/暂停切换 | `playing: bool`   |
| `position` | 进度拖拽      | `position: float` |
