"""
桌面歌词软件集成示例

演示如何在歌词软件中使用 cloudmusic_detector 的公开 API。

三种集成模式:
1. 轮询模式 — 最简单，适合大多数 GUI 框架
2. 事件模式 — 适合需要即时响应的场景
3. asyncio 模式 — 适合 Tauri / async GUI
"""

import time
import sys

from cloudmusic_detector import (
    CloudMusic,
    AsyncCloudMusic,
    Track,
    PlayingState,
    PlayState,
)


def demo_polling():
    """
    模式一：轮询

    最简单的集成方式。在你的 GUI 定时器中调用 cm.state。
    """
    print("=== 轮询模式 ===")
    cm = CloudMusic()
    cm.start()

    try:
        while True:
            s = cm.state
            if s.has_track:
                print(
                print(f"  [{s.state.value:7s}] {s.track.artist_str} - {s.track.name}  "
                      f"{s.position:.1f}s / {s.track.duration:.0f}s  "
                      f"{s.progress:.1%}"
                )
            else:
                print("\r  等待播放...", end="", flush=True)
            time.sleep(0.1)  # 100ms 轮询，与 GUI 刷新率一致
    except KeyboardInterrupt:
        cm.stop()


def demo_events():
    """
    模式二：事件驱动

    通过回调响应切歌、暂停、进度跳转，减少无效轮询。
    歌词软件通常: 事件回调更新缓存 + 定时器轮询进度。
    """
    print("=== 事件模式 ===")
    cm = CloudMusic()

    @cm.on_track_change
    def handle_track_change(track: Track):
        if track.id == -1:
            print("\n  [停止] 不再播放")
        else:
            print(f"\n  [切歌] {track.artist_str} - {track.name}  ({track.duration:.0f}s)")
            # 在这里: 用 track.id 请求歌词 API

    @cm.on_state_change
    def handle_state_change(state: PlayingState):
        action = "▶ 播放" if state.is_playing else "⏸ 暂停"
        print(f"  [{action}] {state.track.name}")

    @cm.on_seek
    def handle_seek(position: float):
        print(f"  [跳转] {position:.1f}s")

    cm.start()

    try:
        while True:
            time.sleep(1)  # 事件模式下不需要高频轮询
    except KeyboardInterrupt:
        cm.stop()


async def demo_async():
    """
    模式三：asyncio

    适合 Tauri 后端 / 纯 async 应用。
    """
    print("=== Async 模式 ===")
    cm = AsyncCloudMusic()

    @cm.on_track_change
    def on_track(track: Track):
        print(f"  [async 切歌] {track.artist_str} - {track.name}")

    await cm.start()

    try:
        while True:
            await asyncio.sleep(0.5)
            s = cm.state
            if s.has_track and s.is_playing:
                print(f"\r  {s.position:.1f}s / {s.track.duration:.0f}s  {s.track.name}", end="", flush=True)
    except asyncio.CancelledError:
        await cm.stop()


if __name__ == "__main__":
    import asyncio
    mode = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if mode == "event":
        demo_events()
    elif mode == "async":
        asyncio.run(demo_async())
    else:
        demo_polling()
