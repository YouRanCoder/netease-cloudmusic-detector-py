import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import time

from cloudmusic_detector import CloudMusic, AsyncCloudMusic, Track, PlayingState, PlayState
from cloudmusic_detector._constant import ELOG_RULES
from cloudmusic_detector._elog_analysis import _build_decode_table, decode_elog


def test_types():
    print("=== 测试 1: 公开类型 ===")

    # Track 不可变
    t = Track(id=123, name="晴天", artists=("周杰伦",), album="叶惠美",
              cover_url="http://x.com/1.jpg", duration=269)
    assert t.id == 123
    assert t.name == "晴天"
    assert t.artist_str == "周杰伦"
    assert t.duration == 269
    try:
        t.name = "x"  # type: ignore
        assert False, "Track 应该是 frozen"
    except AttributeError:
        print("  Track 不可变 (frozen dataclass)")

    # PlayingState
    s = PlayingState(state=PlayState.PLAYING, track=t, position=65.5)
    assert s.is_playing is True
    assert s.is_paused is False
    assert s.is_stopped is False
    assert s.has_track is True
    assert s.progress == 65.5 / 269
    assert s.position_ms == 65500
    assert abs(s.remaining - 203.5) < 0.01
    print(f"  PlayingState: {t.artist_str} - {t.name}  {s.progress:.1%}")

    # 空状态
    s2 = PlayingState()
    assert s2.is_stopped is True
    assert s2.has_track is False
    assert s2.progress == 0.0
    print("  PlayingState 默认 = STOPPED OK")
    print()


def test_match_rules():
    print("=== 测试 2: 事件匹配规则 ===")

    # EXIT
    assert ELOG_RULES["EXIT"]["rule"]('【app】,{"actionId":"exitApp"}')
    print("  EXIT OK")

    # SET_PLAYING_STATUS
    row = '【playing】,"native播放state",1,'
    assert ELOG_RULES["SET_PLAYING_STATUS"]["rule"](row)
    assert ELOG_RULES["SET_PLAYING_STATUS"]["args"](row) == "1"
    print("  STATUS OK")

    # SET_PLAYING_POSITION
    row = '【playing】,"setPlayingPosition",123.45'
    assert ELOG_RULES["SET_PLAYING_POSITION"]["rule"](row)
    assert ELOG_RULES["SET_PLAYING_POSITION"]["args"](row) == "123.45"
    print("  POSITION OK")

    # SET_PLAYING
    row = ('【playing】,"checkPlayPrivilege",'
           '{"id":"123","name":"Test","duration":240000,'
           '"album":{"name":"ALBUM","cover":"http://c.jpg"},'
           '"artists":[{"name":"A1"}]}')
    assert ELOG_RULES["SET_PLAYING"]["rule"](row)
    data = ELOG_RULES["SET_PLAYING"]["args"](row)
    assert data["name"] == "Test"
    assert data["artists"][0]["name"] == "A1"
    print(f"  SET_PLAYING OK → {data['name']}")
    print()


def test_api_surface():
    print("=== 测试 3: API 接口完整性 ===")

    cm = CloudMusic()
    # 属性存在
    assert hasattr(cm, 'state')
    assert hasattr(cm, 'track')
    assert hasattr(cm, 'is_playing')
    assert hasattr(cm, 'position')
    assert hasattr(cm, 'on_track_change')
    assert hasattr(cm, 'on_state_change')
    assert hasattr(cm, 'on_seek')
    assert hasattr(cm, 'start')
    assert hasattr(cm, 'stop')

    # 默认状态
    s = cm.state
    assert s.is_stopped
    assert cm.track.id == -1
    assert cm.is_playing is False
    print("  CloudMusic 接口完整，默认状态 = STOPPED")

    # AsyncCloudMusic 继承
    acm = AsyncCloudMusic()
    assert isinstance(acm, CloudMusic)
    assert hasattr(acm, 'start')
    assert hasattr(acm, 'stop')
    print("  AsyncCloudMusic 继承 CloudMusic OK")
    print()


def test_make_track():
    print("=== 测试 4: Track 构建（兼容三种数据结构） ===")
    cm = CloudMusic()

    # SET_PLAYING 结构
    data1 = {
        "id": "100", "name": "歌1",
        "artists": [{"name": "A"}, {"name": "B"}],
        "album": {"name": "专辑1", "cover": "http://c.jpg"},
        "duration": 200000,
    }
    t1 = cm._make_track(data1)
    assert t1.id == 100
    assert t1.name == "歌1"
    assert t1.artists == ("A", "B")
    assert t1.album == "专辑1"
    assert t1.cover_url == "http://c.jpg"
    assert t1.duration == 200.0
    print(f"  SET_PLAYING 结构 → {t1.artist_str} - {t1.name}")

    # PLAY_ONE 结构
    data2 = {
        "track": {
            "id": "200", "name": "歌2",
            "artists": [{"name": "C"}],
            "album": {"name": "专辑2", "cover": "http://d.jpg"},
            "duration": 300000,
        }
    }
    t2 = cm._make_track(data2)
    assert t2.id == 200
    assert t2.name == "歌2"
    print(f"  PLAY_ONE 结构 → {t2.artist_str} - {t2.name}")

    # webdb 结构 (songId 字段)
    data3 = {
        "songId": "300", "name": "歌3",
        "artists": [{"name": "D"}],
        "album": {"name": "专辑3", "cover": "http://e.jpg"},
        "duration": 180000,
    }
    t3 = cm._make_track(data3)
    assert t3.id == 300
    assert t3.duration == 180.0
    print(f"  webdb 结构 → {t3.artist_str} - {t3.name}")
    print()


def test_file_structure():
    print("=== 测试 5: 文件结构 ===")
    base = os.path.dirname(__file__)
    expected = [
        ("cloudmusic_detector/__init__.py", True),
        ("cloudmusic_detector/types.py", True),
        ("cloudmusic_detector/api.py", True),
        ("cloudmusic_detector/_constant.py", True),
        ("cloudmusic_detector/_elog_analysis.py", True),
        ("cloudmusic_detector/_elog_listener.py", True),
        ("cloudmusic_detector/_webdb.py", True),
        ("examples/desktop_lyrics.py", True),
        ("pyproject.toml", True),
    ]
    for path, _ in expected:
        full = os.path.join(base, path)
        exists = os.path.exists(full)
        size = os.path.getsize(full) if exists else 0
        print(f"  {'OK' if exists else 'MISS'} {path} ({size}b)")
        assert exists, f"Missing {path}"

    # 确保旧文件已清理
    old = [
        "cloudmusic_detector/constant.py",
        "cloudmusic_detector/elog_analysis.py",
        "cloudmusic_detector/elog_listener.py",
        "cloudmusic_detector/webdb.py",
        "cloudmusic_detector/detector.py",
        "main.py",
        "test_decode.py",
    ]
    for path in old:
        full = os.path.join(base, path)
        if os.path.exists(full):
            print(f"  OLD {path} (should be removed)")
    print()


def test_state_machine_position():
    print("=== 测试 6: 进度状态机（position 不应是 Unix 时间戳） ===")

    cm = CloudMusic()
    track = Track(id=12345, name="测试歌曲", duration=240.0)

    # 模拟 PLAY_ONE_TRACKIN_PLAYING_LIST：切歌后立即播放
    cm._set_new_track(track, pausing=False)
    pos = cm.state.position
    assert 0 <= pos < 60, f"播放中 position 应为秒级小值，实际 {pos} (疑似 Unix 时间戳)"
    assert cm.is_playing
    print(f"  立即播放 position={pos:.3f}s OK (is_playing={cm.is_playing})")

    # 模拟 SET_PLAYING：切歌后暂停，position 应为 0
    cm._set_new_track(track, pausing=True)
    assert cm.state.position == 0.0
    assert cm.state.is_paused
    print("  暂停 position=0 OK")
    print()


def test_decode_elog_perf():
    print("=== 测试 7: decode_elog 大文件解码性能（坏字节不应触发 O(P·n)） ===")

    table = _build_decode_table()
    # 计算逆映射（磁盘字节 -> 文本字节 是 table；文本字节 -> 磁盘字节 是逆置换）
    inverse = bytearray(256)
    for b in range(256):
        inverse[table[b]] = b
    inverse = bytes(inverse)

    line = '[123:456:2024/011010:1000:INFO:abc(1)] 2024-01-01 10:00:00 【playing】,"checkPlayPrivilege",{"id":1}'
    payload = (line.encode("utf-8") + b"\n") * 90000
    disk = payload.translate(inverse)

    # 中部 1/3 处注入一个坏字节，模拟真实 elog 的乱码
    pos = len(disk) // 3
    bad = disk[:pos] + b"\xff" + disk[pos:]
    assert len(bad) > 8 * 1024 * 1024, f"测试数据应大于 8MB，实际 {len(bad)}"

    t0 = time.time()
    out = decode_elog(bad)
    dt = time.time() - t0
    assert out.count("\n") == 90000, f"行数应完整保留，实际 {out.count(chr(10))}"
    assert dt < 1.0, f"解码耗时超过 1s（疑似 O(P·n) 剥头循环），实际 {dt:.3f}s"
    print(f"  8.8MB + 中部坏字节解码 {dt*1000:.1f}ms，行数 90000 OK")
    print()


if __name__ == "__main__":
    print("cloudmusic_detector API 测试\n")
    print("-" * 50)
    test_types()
    test_match_rules()
    test_api_surface()
    test_make_track()
    test_file_structure()
    test_state_machine_position()
    test_decode_elog_perf()
    print("-" * 50)
    print("\nAll passed!")
