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


def test_hash_song_id():
    print("=== 测试 8: 哈希 songId（本地/无服务器 ID 歌曲）不应崩溃 ===")

    cm = CloudMusic()

    # 安全转换：哈希/None → -1，正常数字 → 原值
    assert cm._safe_song_id("FB5D760B1179E3A2E0EA339D9C8531AD0DC0873F") == -1
    assert cm._safe_song_id(None) == -1
    assert cm._safe_song_id("12345") == 12345
    assert cm._safe_song_id(-1) == -1
    print("  _safe_song_id 哈希/None/数字 OK")

    # _make_track 遇到哈希 id 不崩
    t = cm._make_track({"id": "FB5D760B1179E3A2E0EA339D9C8531AD0DC0873F", "name": "本地歌曲"})
    assert t.id == -1
    assert t.name == "本地歌曲"
    print("  _make_track 哈希 id → Track(id=-1) OK")

    # _preload 遇到 NATIVE_SONG_LOAD + 哈希 songId 不崩（跳过 webdb 继续回溯）
    header = "[123:456:2024/011010:1000:INFO:abc(1)] 2024-01-01 10:00:00"
    hash_line = f'{header} 【playing】,"native播放资源load完成，开始播放",{{"songId":"FB5D760B1179E3A2E0EA339D9C8531AD0DC0873F"}}'
    int_line = f'{header} 【playing】,"native播放资源load完成，开始播放",{{"songId":"12345"}}'
    try:
        cm._preload([hash_line, int_line])
        print("  _preload 哈希 songId 行 OK（未抛 ValueError）")
    except ValueError as e:
        raise AssertionError(f"_preload 仍抛 ValueError: {e}")
    print()


def test_song_load_rebase():
    print("=== 测试 9: NATIVE_SONG_LOAD 出声点校准（歌词提前修复） ===")

    from cloudmusic_detector._elog_analysis import _get_system_uptime_ms, parse_header

    cm = CloudMusic()

    # 行内 startup_time 必须接近当前 uptime，模拟"刚写下的日志"（offset≈0）
    startup = _get_system_uptime_ms()
    header = f"[123:456:0818/163136:{startup}:INFO:abc(1)] [2024-01-01 10:00:00]"

    def load_line(sid):
        return (f'{header} 【playing】,"native播放资源load完成，开始播放",'
                f'{{"songId":"{sid}","bitrate":320}}')

    def pos_line(sec):
        return f'{header} 【playing】,"setPlayingPosition",{sec}'

    # 流程 A：PLAY_ONE 立即起表 → 缓冲 3s → SONG_LOAD 应把进度校准回 ~0
    cm._set_new_track(Track(id=1, name="A", duration=300.0), pausing=False)
    cm._relative_time -= 3000  # 模拟 PLAY_ONE 先于出声 3 秒（库内进度已领先）
    assert cm.state.position > 2.5, f"前置条件：缓冲期进度应领先 ~3s，实际 {cm.state.position}"
    cm._handle_line(load_line(1))
    assert cm.state.position < 0.5, f"出声点应归零，实际 {cm.state.position}"
    print(f"  缓冲期领先 3s → SONG_LOAD 校准后 position={cm.state.position:.3f}s OK")

    # seek（setPlayingPosition）后的 SONG_LOAD 重载：不得归零
    cm._handle_line(pos_line(120))
    cm._relative_time -= 2000  # 拖拽后 2s，重载完成
    cm._handle_line(load_line(1))
    assert 119 <= cm.state.position <= 123, f"seek 后重载不应归零，实际 {cm.state.position}"
    print(f"  seek 到 120s 后重载 position={cm.state.position:.3f}s（未被破坏）OK")

    # 暂停中的 SONG_LOAD：不归零
    cm._handle_line(f'{header} 【playing】,"native播放state",2,')
    paused_pos = cm.state.position
    cm._handle_line(load_line(1))
    assert abs(cm.state.position - paused_pos) < 1.0, "暂停中 SONG_LOAD 不应改变进度"
    print("  暂停中 SONG_LOAD 进度不变 OK")

    # 切歌后标志复位：新歌的首个 SONG_LOAD 仍可校准
    cm._set_new_track(Track(id=2, name="B", duration=300.0), pausing=False)
    cm._relative_time -= 4000
    cm._handle_line(load_line(2))
    assert cm.state.position < 0.5, f"新歌首个 SONG_LOAD 应校准归零，实际 {cm.state.position}"
    print("  切歌后标志复位，新歌 SONG_LOAD 校准 OK")

    # 陈旧事件（elog 重读/跨会话旧行，offset 巨大）：不得重置进度
    old_startup = 1000  # 假装是很久以前（开机第 1 秒）写的日志
    old_header = f"[123:456:0818/163136:{old_startup}:INFO:abc(1)] [2024-01-01 10:00:00]"
    stale_line = (f'{old_header} 【playing】,"native播放资源load完成，开始播放",'
                  f'{{"songId":"2","bitrate":320}}')
    cm._relative_time -= 2000
    pos_before_stale = cm.state.position
    cm._handle_line(stale_line)
    assert abs(cm.state.position - pos_before_stale) < 0.5, \
        f"陈旧 SONG_LOAD 不应重置进度，{pos_before_stale} -> {cm.state.position}"
    print(f"  陈旧事件（offset 巨大）进度保持 {pos_before_stale:.2f}s 不变 OK")

    # 同曲重播：进度已走完（外推越过时长）时，即使被锚定过也应归零重新起表
    cm._handle_line(f'{header} 【playing】,"native播放state",1,')  # 恢复播放，锚定标志置位
    cm._relative_time -= int(302 * 1000)  # 外推 302s > 时长 300s（歌已播完）
    assert cm.state.position >= 299.5, f"前置条件：进度应已到末尾，实际 {cm.state.position}"
    cm._handle_line(load_line(2))
    assert cm.state.position < 0.5, f"同曲重播应归零，实际 {cm.state.position}"
    print("  同曲重播（进度已走完）SONG_LOAD 归零 OK")

    # 对照：锚定过且仍在歌曲中途的 SONG_LOAD（seek 重载）不归零
    cm._handle_line(pos_line(80))
    cm._relative_time -= 1000
    cm._handle_line(load_line(2))
    assert 79 <= cm.state.position <= 82, f"中途重载不应归零，实际 {cm.state.position}"
    print("  中途重载（锚定过且未播完）进度保持 OK")
    print()


def test_header_timestamp_no_jitter():
    print("=== 测试 10: parse_header 时间戳无缓存抖动 ===")

    from cloudmusic_detector._elog_analysis import _get_system_uptime_ms, parse_header

    row = f"[123:456:0818/163136:{_get_system_uptime_ms()}:INFO:abc(1)] [2024-01-01 10:00:00] 【playing】,\"setPlayingPosition\",1"
    t1 = parse_header(row).timestamp
    time.sleep(0.05)
    t2 = parse_header(row).timestamp
    assert t1 == t2, f"同一行的时间戳应恒定（旧实现带 0~1s 缓存抖动）：{t1} != {t2}"
    print(f"  同一行两次解析 timestamp 一致（{t1}）OK")

    # 时间戳应接近当前时刻（= 事件真实 wall 时间）
    now_ms = time.time() * 1000
    assert abs(t1 - now_ms) < 100, f"timestamp 应≈当前时刻，偏差 {t1 - now_ms}ms"
    print(f"  timestamp 与当前时刻偏差 {t1 - now_ms:.0f}ms OK")
    print()


def test_pending_track():
    print("=== 测试 11: 切歌候选（权限预检）不提前切歌 ===")

    from cloudmusic_detector._elog_analysis import _get_system_uptime_ms
    from cloudmusic_detector._constant import parse_args, parse_status_play_id

    startup = _get_system_uptime_ms()
    header = f"[123:456:0818/163136:{startup}:INFO:abc(1)] [2024-01-01 10:00:00]"

    def priv_line(sid, name):
        return (f'{header} 【playing】,"checkPlayPrivilege",'
                f'{{"id":"{sid}","name":"{name}","artists":[{{"name":"歌手"}}],'
                f'"album":{{"name":"专辑","cover":""}},"duration":180000}}')

    def play_one_line(sid, name):
        return (f'{header} 【playing】,"playOneTrackInPlayingList",'
                f'{{"track":{{"id":"{sid}","name":"{name}","artists":[],'
                f'"album":{{}},"duration":180000}}}}')

    def load_line(sid):
        return (f'{header} 【playing】,"native播放资源load完成，开始播放",'
                f'{{"songId":"{sid}","bitrate":320}}')

    def state_line(st, play_id):
        return f'{header} 【playing】,"native播放state",{st},"{play_id}"'

    # 带 playId 的状态行仍能正确解析出播放态与 playId
    assert parse_args("SET_PLAYING_STATUS", state_line(1, "3_X1Y2Z3")) == "1"
    assert parse_status_play_id(state_line(1, "3_X1Y2Z3")) == "3_X1Y2Z3"
    assert parse_status_play_id(f'{header} 【playing】,"native播放state",1,') is None
    print("  playId 解析（有/无该字段）OK")

    cm = CloudMusic()
    changes = []
    cm.on_track_change(lambda t: changes.append(t.id))
    cm._set_new_track(Track(id=1, name="A", duration=180.0), pausing=False)
    changes.clear()

    # 场景 1：“下一首播放”B 只写 checkPlayPrivilege，没有任何出声事件
    cm._relative_time -= 5000  # 让当前曲目已播 ~5s
    pos_before = cm.state.position
    cm._handle_line(priv_line(2, "B"))
    assert changes == [], f"权限预检不应推送切歌，实际推送 {changes}"
    assert cm.track.id == 1, f"当前曲目应仍是 A，实际 {cm.track.id}"
    assert cm.is_playing, "当前曲目应继续播放"
    assert abs(cm.state.position - pos_before) < 0.5, \
        f"权限预检不应重置进度，{pos_before:.2f}s -> {cm.state.position:.2f}s"
    print("  预检 B → 无切歌回调，仍在播放 A（进度未重置）OK")

    # 场景 2：随后 A 的播放态事件（playId 前缀为 A）不得改变状态，也不切歌
    cm._handle_line(state_line(1, "1_AAAAAA"))
    assert cm.track.id == 1 and changes == [], "A 的播放态事件不应触发切歌"
    assert cm._pending_tracks.get(2) is not None, \
        "候选应保留，等它真正被播到时仍可直接确认"
    print("  A 的 playId 事件 → 状态不变、候选保留 OK")

    # 场景 3：真正切到 B（playOne + SONG_LOAD）只推送一次
    cm._handle_line(play_one_line(2, "B"))
    assert changes == [], "playOneTrackInPlayingList 单独出现不应切歌"
    cm._handle_line(load_line(2))
    assert changes == [2], f"SOUND_LOAD 确认后应恰好推送一次，实际 {changes}"
    assert cm.track.id == 2
    print("  出声点确认 → 恰好一次切歌回调 OK")

    # 场景 4：SOUND_LOAD 缺失时由 playId 确认
    changes.clear()
    cm._handle_line(priv_line(3, "C"))
    assert changes == [] and cm.track.id == 2
    cm._handle_line(state_line(1, "3_CCCCCC"))
    assert changes == [3], f"playId 确认后应推送切歌，实际 {changes}"
    assert cm.track.id == 3
    print("  playId 确认（无 SONG_LOAD）→ 切歌 OK")

    # 场景 5：预检曲目与当前曲目相同时不登记候选
    cm._handle_line(priv_line(3, "C"))
    assert cm._pending_tracks == {} and changes == [3]
    print("  预检与当前曲目相同 → 不登记候选 OK")

    # 场景 6：连续“下一首播放”多首，不应互相覆盖；播放其中任意一首都能确认
    cm4 = CloudMusic()
    cm4_changes = []
    cm4.on_track_change(lambda t: cm4_changes.append(t.name))
    cm4._set_new_track(Track(id=10, name="A", duration=180.0), pausing=False)
    cm4_changes.clear()
    for sid, name in ((11, "B"), (12, "C"), (13, "D")):
        cm4._handle_line(priv_line(sid, name))
    assert cm4_changes == [], "连续预检不应推送切歌"
    assert sorted(cm4._pending_tracks) == [11, 12, 13], \
        f"候选应互不覆盖，实际 {sorted(cm4._pending_tracks)}"
    # 用户先播第三首 D：只应推送 D 一次，且直接复用候选元数据
    cm4._handle_line(load_line(13))
    assert cm4_changes == ["D"], f"应恰好推送 D，实际 {cm4_changes}"
    assert sorted(cm4._pending_tracks) == [11, 12], "其余候选应保留"
    print("  连续预检多首 → 互不覆盖，播放任一首均正确确认 OK")

    # 场景 7：_preload 应过滤掉“未出声”的预检锚点
    cm2 = CloudMusic()
    cm2._preload([
        priv_line(4, "D"),
        state_line(1, "4_DDDDDD"),
        priv_line(5, "E"),  # 仅“下一首播放”E，未真正出声
    ])
    assert cm2.track.id == 4, f"回溯锚点应是已出声的 D，实际 {cm2.track.id}"
    print("  _preload 跳过未出声的预检锚点 OK")

    # 场景 8：全程只有预检、无任何出声证据 → 回溯不出曲目
    cm3 = CloudMusic()
    cm3._preload([priv_line(6, "F")])
    assert cm3.track.id == -1, f"无出声证据时不应判定当前曲目，实际 {cm3.track.id}"
    print("  _preload 仅有预检 → 无当前曲目 OK")

    # 场景 9：出声点查不到 webdb（只存最近一次播放，通常早已过期）时，
    # 应继续向更早的日志取同一首的预检记录补齐歌名歌手
    cm5 = CloudMusic()
    cm5._webdb.wait_for_song_detail = lambda sid, timeout=2: None
    cm5._preload([
        priv_line(7, "G"),          # 更早：预检 G，带完整元数据
        state_line(1, "1_AAAAAA"),
        load_line(7),               # 出声点：G 开始播放，但 webdb 查不到
    ])
    assert cm5.track.id == 7, f"应回溯到出声的 G，实际 {cm5.track.id}"
    assert cm5.track.name == "G", f"应补齐歌名，实际 {cm5.track.name!r}"
    print("  _preload 出声点查不到 webdb → 用更早的预检补齐元数据 OK")
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
    test_hash_song_id()
    test_song_load_rebase()
    test_header_timestamp_no_jitter()
    test_pending_track()
    print("-" * 50)
    print("\nAll passed!")
