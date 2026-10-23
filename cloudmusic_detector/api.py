import time
import asyncio
import threading
from typing import Callable, Optional

from ._elog_listener import _ElogListener
from ._elog_analysis import parse_header, get_event_type
from ._constant import (
    CLOUDMUSIC_ELOG_PATH,
    ELOG_RULES,
    parse_args,
    parse_status_play_id,
)
from ._webdb import _Webdb
from .types import Track, PlayingState, PlayState


class CloudMusic:
    """
    网易云音乐播放状态监听 — 歌词软件集成接口。

    用法（同步）::

        from cloudmusic_detector import CloudMusic, Track, PlayingState

        cm = CloudMusic()
        cm.on_track_change(lambda track: print(f"切歌: {track.name}"))
        cm.on_state_change(lambda state: print(f"{'播放' if state.is_playing else '暂停'}"))
        cm.on_seek(lambda pos: print(f"跳转: {pos}s"))
        cm.start()

        # 在你的主循环中轮询
        state = cm.state   # PlayingState 快照
        track = cm.track   # 当前 Track（便捷属性）

        cm.stop()

    用法（asyncio）::

        from cloudmusic_detector import AsyncCloudMusic

        async def main():
            cm = AsyncCloudMusic()
            cm.on_track_change(lambda t: print(t.name))
            await cm.start()

            state = cm.state
            await cm.stop()
    """

    # 切歌候选缓存上限：避免长时间运行后无限堆积（远超实际排队歌曲数）
    _PENDING_LIMIT = 16

    def __init__(
        self,
        *,
        elog_path: Optional[str] = None,
        webdb_path: Optional[str] = None,
        poll_interval: float = 0.3,
    ):
        """
        Args:
            elog_path:     cloudmusic.elog 路径，默认自动检测
            webdb_path:    webdb.dat 路径，默认自动检测
            poll_interval: 文件轮询间隔（秒），默认 0.3
        """
        self._listener = _ElogListener(elog_path or CLOUDMUSIC_ELOG_PATH, poll_interval)
        self._webdb = _Webdb(webdb_path)

        # 内部状态
        self._song_id: int = -1
        self._track = Track()
        self._pausing: bool = False
        self._position: float = 0.0
        self._relative_time: int = 0
        # 自最近一次切歌以来，进度基准是否已被显式事件（进度拖拽/播放状态切换）
        # 锚定过。用于区分 NATIVE_SONG_LOAD 是"切歌后的首次出声"（需要校准）
        # 还是"seek 后的重新加载"（不能归零，否则破坏拖拽定位）
        self._pos_anchored_since_switch: bool = False
        # 待确认的切歌候选（songId -> Track）。客户端在"加入下一首播放"等场景
        # 会写 checkPlayPrivilege（播放权限预检），它携带了被插入歌曲的完整信息，
        # 但当前歌曲并没有切换。若把它当成切歌，歌词软件会提前拉取下一首的
        # 歌词并停掉当前歌词，且真正切到该曲时因为曲目 ID 已相同而被去重
        #（不再推送切歌），歌词从此不再出现。
        # 因此 SET_PLAYING / PLAY_ONE 只登记为候选，等 NATIVE_SONG_LOAD（真正出声）
        # 或 native播放state 的 playId 确认后，才正式切歌。
        # 用字典而不是单个槽位：连按几次"下一首播放"不会互相覆盖，且不论用户
        # 先播哪一首，确认时都能直接拿到完整元数据（无需再查 webdb）。
        self._pending_tracks: dict[int, Track] = {}

        # 回调
        self._on_track_change_cbs: list[Callable[[Track], None]] = []
        self._on_state_change_cbs: list[Callable[[PlayingState], None]] = []
        self._on_seek_cbs: list[Callable[[float], None]] = []

    # ────────────────────────────────
    # 公开属性：歌词软件主要用这些
    # ────────────────────────────────

    @property
    def state(self) -> PlayingState:
        """当前播放状态快照。主循环中每次调用获取最新值。"""
        if self._song_id == -1:
            return PlayingState(state=PlayState.STOPPED)
        now = time.time() * 1000
        pos = (
            self._position
            if self._pausing
            else min((now - self._relative_time) / 1000, self._track.duration)
        )
        return PlayingState(
            state=PlayState.PAUSED if self._pausing else PlayState.PLAYING,
            track=self._track,
            position=pos,
        )

    @property
    def track(self) -> Track:
        """当前歌曲信息。"""
        return self._track

    @property
    def is_playing(self) -> bool:
        """是否正在播放。"""
        return self._song_id != -1 and not self._pausing

    @property
    def position(self) -> float:
        """当前播放位置（秒）。"""
        return self.state.position

    # ────────────────────────────────
    # 事件注册
    # ────────────────────────────────

    def on_track_change(self, callback: Callable[[Track], None]) -> None:
        """注册切歌回调，参数为新的 Track 对象。"""
        self._on_track_change_cbs.append(callback)

    def on_state_change(self, callback: Callable[[PlayingState], None]) -> None:
        """注册播放/暂停切换回调，参数为 PlayingState 快照。"""
        self._on_state_change_cbs.append(callback)

    def on_seek(self, callback: Callable[[float], None]) -> None:
        """注册进度拖拽回调，参数为新位置（秒）。"""
        self._on_seek_cbs.append(callback)

    def _emit_track(self, track: Track) -> None:
        for cb in self._on_track_change_cbs:
            try:
                cb(track)
            except Exception:
                pass

    def _emit_state(self, state: PlayingState) -> None:
        for cb in self._on_state_change_cbs:
            try:
                cb(state)
            except Exception:
                pass

    def _emit_seek(self, pos: float) -> None:
        for cb in self._on_seek_cbs:
            try:
                cb(pos)
            except Exception:
                pass

    # ────────────────────────────────
    # 启动 / 停止
    # ────────────────────────────────

    def start(self) -> None:
        """
        启动监听。启动时会回溯已有日志恢复当前状态。
        阻塞约 1~2 秒（回溯开销），之后在后台线程运行。

        Raises:
            FileNotFoundError: elog 文件不存在
        """
        lines = self._listener.start()
        self._preload(lines)
        self._listener.on_line(self._handle_line)

    def stop(self) -> None:
        """停止监听，释放资源。"""
        self._listener.stop()
        self._webdb.close()

    # ────────────────────────────────
    # 内部：状态机
    # ────────────────────────────────

    def _reset(self) -> None:
        self._song_id = -1
        self._track = Track()
        self._pausing = False
        self._position = 0.0
        self._relative_time = 0
        self._pos_anchored_since_switch = False
        self._pending_tracks.clear()

    def _safe_song_id(self, value: object, default: int = -1) -> int:
        """把 songId / id 安全转为整数。

        网易云对本地导入 / 无服务器 ID 的下载曲会用文件哈希（40 位十六进制，
        如 FB5D760B...）作 ID。这类值无法 int() 转换，直接返回 default，
        避免 _preload 回溯到本地歌曲记录时崩溃。
        """
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def _make_track(self, data: dict) -> Track:
        """从 JSON 数据构建 Track（兼容 SET_PLAYING / PLAY_ONE / webdb 三种结构）。"""
        # SET_PLAYING 结构: {id, name, artists:[{name}], album:{name, cover}, duration}
        # PLAY_ONE 结构: {track:{id, name, artists, album, duration}}
        # webdb 结构: {id, name, artists, album, duration}
        track_data = data.get("track", data)
        album = track_data.get("album", {})
        return Track(
            id=self._safe_song_id(track_data.get("id", track_data.get("songId", -1))),
            name=track_data.get("name", ""),
            artists=tuple(a.get("name", "") for a in track_data.get("artists", [])),
            album=album.get("name", ""),
            cover_url=album.get("cover", ""),
            duration=track_data.get("duration", 0) / 1000,
        )

    def _set_new_track(self, track: Track, pausing: bool = True) -> None:
        self._song_id = track.id
        self._track = track
        self._pausing = pausing
        self._position = 0.0
        # pausing=False（PLAY_ONE 立即播放）时若 _relative_time 为 0，
        # state.position 会变成 (now-0)/1000 = Unix 秒级时间戳（历史 bug），
        # 这里把基准时间设为当前时刻，使进度从 0 正常递增。
        self._relative_time = 0 if pausing else int(time.time() * 1000)
        # 新歌起表：清空"进度已被显式锚定"标记，
        # 使本歌随后的首个 NATIVE_SONG_LOAD（真正出声点）能校准进度
        self._pos_anchored_since_switch = False
        self._emit_track(track)

    def _mark_pending(self, track: Track, pausing: bool) -> None:
        """登记切歌候选（尚未确认真正出声）。

        ``pausing`` 保留参数语义以便未来扩展，当前实现统一在确认时按实际
        播放状态（SONG_LOAD / 播放态事件）决定，不再沿用预检时的猜测。

        候选只在"确认出声"时被消费，不会被当前曲目的播放/暂停事件清掉——
        清掉只能让后续确认退回 webdb 查询，反而更慢更脆。
        候选数量有上限，防止长时间运行下无限堆积。
        """
        if track.id == self._song_id:
            # 已是当前曲目：该事件与切歌无关，忽略
            return
        if track.id == -1:
            # 哈希型 songId（本地导入/无服务器 ID）：无法与后续事件比对，
            # 保持旧行为直接切歌，避免这类歌曲永远不显示
            self._set_new_track(track, pausing=pausing)
            return
        self._pending_tracks.pop(track.id, None)
        self._pending_tracks[track.id] = track
        # 只保留最近的若干条（dict 保持插入顺序，超限丢最旧）
        while len(self._pending_tracks) > self._PENDING_LIMIT:
            self._pending_tracks.pop(next(iter(self._pending_tracks)))

    def _take_pending(self, song_id: int) -> Optional[Track]:
        """取出并移除已确认出声的候选；不存在返回 None。"""
        return self._pending_tracks.pop(song_id, None)

    def _collect_played_song_id(self, line: str) -> Optional[int]:
        """若该行是"真正出声"证据，返回其歌曲 ID，否则返回 None。

        证据包括：
          - ``native播放资源load完成，开始播放`` 的 songId；
          - ``native播放state`` 的 playId 前缀（形如 ``"<songId>_xxx"``）。
        """
        if ELOG_RULES["NATIVE_SONG_LOAD"]["rule"](line):
            data = parse_args("NATIVE_SONG_LOAD", line)
            if isinstance(data, dict):
                sid = self._safe_song_id(data.get("songId", -1))
                return sid if sid != -1 else None
            return None
        if ELOG_RULES["SET_PLAYING_STATUS"]["rule"](line):
            play_id = parse_status_play_id(line)
            if play_id:
                sid = self._safe_song_id(play_id.split("_")[0])
                return sid if sid != -1 else None
        return None

    def _preload(self, lines: list[str]) -> None:
        """启动回溯：倒序找到最近切歌事件，正序回放进度和状态。"""
        now = time.time() * 1000
        records: list[str] = []

        song_id = -1
        song_play_time = 0
        song_position = 0.0
        song_pausing = False
        song_track: Optional[Track] = None
        # 回溯锚点是否为 NATIVE_SONG_LOAD（出声点），以及回放途中进度是否被
        # 显式事件（拖拽/状态切换）锚定过——决定启动后同歌 SONG_LOAD 是否可归零
        anchor_is_song_load = False
        replay_anchored = False
        self._pos_anchored_since_switch = False
        # 倒序扫描过程中已经见过的"真正出声"歌曲 ID 集合（比当前行更新）
        played_ids: set[int] = set()
        # 已确认出声但 webdb 里查不到元数据的歌曲：继续向更早的日志找它的
        # 切歌/预检记录以补齐歌名歌手（webdb 只存最近一次播放，通常已过期）
        anchor_sid = -1
        anchor_timestamp = 0
        anchor_records_len = 0

        for line in reversed(lines):
            header = parse_header(line)
            if not header:
                continue
            records.append(line)

            etype = get_event_type(line, ELOG_RULES)
            if etype is None:
                continue

            if etype == "EXIT":
                self._reset()
                return

            if song_id != -1:
                continue

            # 记录出声证据（无论是否已定锚点，都要收集给更早的候选判断用）
            played_sid = self._collect_played_song_id(line)
            if played_sid is not None:
                played_ids.add(played_sid)

            if (anchor_sid != -1 and etype in ("SET_PLAYING", "PLAY_ONE_TRACKIN_PLAYING_LIST")):
                # 已确定出声的歌曲是 anchor_sid，但 webdb 里查不到它的元数据：
                # 继续向更早的日志找同一首的切歌/预检记录，那里带着完整歌名歌手。
                data = parse_args(etype, line)
                if data and isinstance(data, dict):
                    candidate = self._make_track(data)
                    if candidate.id == anchor_sid:
                        song_track = candidate
                        song_id = candidate.id
                        song_play_time = anchor_timestamp
                        # 出声点已作为回溯锚点消费，启动后同歌 SONG_LOAD 不再归零
                        anchor_is_song_load = True
                        break
                continue

            if anchor_sid != -1:
                # 元数据已由候选补齐，或还在继续找：不再处理其他切歌记录
                continue

            if etype in ("SET_PLAYING", "PLAY_ONE_TRACKIN_PLAYING_LIST"):
                data = parse_args(etype, line)
                if data and isinstance(data, dict):
                    candidate = self._make_track(data)
                    # 回溯锚点若是 checkPlayPrivilege（权限预检），必须确认该曲确实
                    # 出声过：否则"下一首播放"的预检会被误判成当前歌曲，启动即错曲。
                    # 用"更近的日志里是否出现过该曲的出声证据"来判断。
                    if (etype == "SET_PLAYING"
                            and candidate.id != -1
                            and candidate.id not in played_ids):
                        continue
                    song_track = candidate
                    song_id = song_track.id
                    song_play_time = header.timestamp
                break

            if etype == "NATIVE_SONG_LOAD":
                data = parse_args(etype, line)
                if data and isinstance(data, dict):
                    sid = self._safe_song_id(data.get("songId", -1))
                    # 哈希型 songId（本地导入/无服务器 ID 的下载曲）无法查 webdb，
                    # 跳过该记录继续回溯更早的切歌事件
                    if sid != -1:
                        detail = self._webdb.wait_for_song_detail(sid, timeout=2)
                        if detail:
                            song_track = self._make_track(detail)
                            song_id = song_track.id
                            song_play_time = header.timestamp
                            # 出声点已作为回溯锚点消费，启动后同歌 SONG_LOAD 不再归零
                            anchor_is_song_load = True
                            break
                        # webdb 只保留最近一次播放记录，多数情况查不到当前歌：
                        # 记下出声的 songId，继续向更早的日志找同一首的完整元数据
                        anchor_sid = sid
                        anchor_timestamp = header.timestamp
                        # 记录边界：回放进度只需"出声点之后"的日志，更早的行
                        # 属于上一首，不能参与回放
                        anchor_records_len = len(records)
                continue

        # 正序回放进度和状态（若锚点是从出声点回溯补齐元数据的，只回放出声点之后的日志）
        records.reverse()
        start_index = (len(records) - anchor_records_len) if anchor_records_len else 0
        last_action = song_play_time
        for line in records[start_index:]:
            header = parse_header(line)
            if not header:
                continue
            etype = get_event_type(line, ELOG_RULES)
            if etype is None:
                continue
            if etype == "SET_PLAYING_POSITION":
                pos = parse_args("SET_PLAYING_POSITION", line)
                if pos is not None:
                    song_position = float(pos)
                    last_action = header.timestamp
                    replay_anchored = True
            elif etype == "SET_PLAYING_STATUS":
                st = parse_args("SET_PLAYING_STATUS", line)
                if st is not None:
                    offset = header.timestamp - last_action
                    last_action = header.timestamp
                    song_pausing = (int(st) == 2)
                    if song_pausing:
                        song_position += offset / 1000
                    replay_anchored = True

        if not song_pausing:
            song_position += (now - last_action) / 1000

        self._song_id = song_id
        self._pausing = song_pausing
        self._position = song_position if song_pausing else 0
        self._relative_time = 0 if song_pausing else int(now - song_position * 1000)
        # 出声点已被回溯消费，或回放途中进度被拖拽/状态切换锚定过：
        # 启动后同歌 NATIVE_SONG_LOAD 视为重载，不归零（保守起见等同旧行为）
        self._pos_anchored_since_switch = anchor_is_song_load or replay_anchored
        if song_track:
            self._track = song_track

    def _handle_line(self, line: str) -> None:
        """实时处理新日志行。"""
        header = parse_header(line)
        if not header:
            return
        now = time.time() * 1000
        offset = (now - header.timestamp) / 1000
        etype = get_event_type(line, ELOG_RULES)
        if etype is None:
            return

        if etype == "EXIT":
            self._reset()
            self._emit_track(Track())
            return

        if etype == "SET_PLAYING":
            data = parse_args(etype, line)
            if not data or not isinstance(data, dict):
                return
            track = self._make_track(data)
            if track.id == self._song_id:
                return
            # checkPlayPrivilege 只是"播放权限预检"，并不代表此刻真的切歌：
            # "加入下一首播放"、把歌曲插进播放队列等操作同样会写这条日志。
            # 因此这里只登记候选，等真正出声（NATIVE_SONG_LOAD / 播放状态 playId）
            # 再确认，避免提前切歌导致歌词软件拉错歌词。
            self._mark_pending(track, pausing=True)
            return

        if etype == "PLAY_ONE_TRACKIN_PLAYING_LIST":
            data = parse_args(etype, line)
            if not data or not isinstance(data, dict):
                return
            track = self._make_track(data)
            if track.id == self._song_id:
                return
            self._mark_pending(track, pausing=False)
            return

        if etype == "NATIVE_SONG_LOAD":
            data = parse_args(etype, line)
            if not data or not isinstance(data, dict):
                return
            new_id = self._safe_song_id(data.get("songId", -1))
            if new_id == self._song_id:
                # 同一首歌的"资源加载完成，开始播放"：这是真正的出声点。
                # 播放中且进度可校准时，把进度基准重置到该事件时刻——
                # 修正 PLAY_ONE 先于出声起表造成的缓冲期进度领先（流媒体
                # 可达数秒，歌词软件表现为歌词提前），并覆盖同曲重播。
                # 以下情况不重置，避免破坏已有定位：
                #   - 暂停中（恢复时由状态事件自行锚定）
                #   - 事件明显陈旧（offset 大：elog 重读/跨会话旧行）
                #   - 进度已被拖拽/状态切换锚定，且当前仍在本歌中途
                #     （seek 后的重新加载）；若进度已走完（外推越过时长，
                #     同曲重播/单曲循环）则仍应归零重新起表
                pos_now = (now - self._relative_time) / 1000
                ended = (self._track.duration > 0
                         and pos_now >= self._track.duration - 0.25)
                if (not self._pausing
                        and abs(offset) < 5.0
                        and (not self._pos_anchored_since_switch or ended)):
                    self._relative_time = int(now - offset * 1000)
                return
            # 哈希型 songId（本地导入/无服务器 ID 的下载曲）无法查 webdb，忽略该事件
            if new_id == -1:
                return
            # 真正出声 = 切歌确认。若与候选一致，优先用候选里的完整元数据
            # （checkPlayPrivilege 自带歌名/歌手，无需再查 webdb，既快又稳）。
            pending = self._take_pending(new_id)
            if pending is not None:
                self._song_id = pending.id
                self._track = pending
                self._pausing = False
                self._position = 0.0
                self._relative_time = int(now - offset * 1000)
                self._pos_anchored_since_switch = False
                self._emit_track(pending)
                return
            detail = self._webdb.wait_for_song_detail(new_id, timeout=2)
            if not detail:
                return
            track = self._make_track(detail)
            new_rel = now - self._position * 1000
            new_pos = (now - self._relative_time) / 1000
            self._song_id = track.id
            self._track = track
            self._position = new_pos - offset if self._pausing else 0
            self._relative_time = 0 if self._pausing else int(new_rel - offset * 1000)
            self._pos_anchored_since_switch = False
            self._emit_track(track)
            return

        if etype == "SET_PLAYING_POSITION":
            pos = parse_args(etype, line)
            if pos is None:
                return
            pos = float(pos)
            self._position = pos if self._pausing else 0
            self._relative_time = 0 if self._pausing else int(now - pos * 1000 - offset * 1000)
            # 进度被显式锚定：本歌随后的 NATIVE_SONG_LOAD 属于 seek 后重新加载，不归零
            self._pos_anchored_since_switch = True
            self._emit_seek(self.state.position)
            return

        if etype == "SET_PLAYING_STATUS":
            st = parse_args(etype, line)
            if st is None:
                return
            paused = (int(st) == 2)
            # 播放态事件的 playId 形如 "<songId>_<随机串>"，代表此刻真正出声的
            # 播放实例。若它与某个候选一致，说明该候选已真正出声（极少数情况下
            # SONG_LOAD 缺失），直接确认切歌；否则说明候选只是"下一首播放"之类的
            # 权限预检，此时保留候选（等它真被播到时仍可用），不做任何状态变更。
            play_id = parse_status_play_id(line)
            play_song_id = (
                self._safe_song_id(play_id.split("_")[0]) if play_id else -1
            )
            if play_song_id != -1:
                pending = self._take_pending(play_song_id)
                if pending is not None:
                    self._set_new_track(pending, pausing=paused)
                    self._emit_state(self.state)
                    return
            new_rel = now - self._position * 1000
            new_pos = (now - self._relative_time) / 1000
            self._pausing = paused
            self._position = new_pos - offset if self._pausing else 0
            self._relative_time = 0 if self._pausing else int(new_rel - offset * 1000)
            # 进度被显式锚定：暂停/恢复后的 NATIVE_SONG_LOAD 重载不归零
            self._pos_anchored_since_switch = True
            self._emit_state(self.state)


class AsyncCloudMusic(CloudMusic):
    """
    asyncio 版本。回调在事件循环中调度，适合 Tauri / Qt asyncio / 纯 async 框架。

    与同步版的关键差异：
    - ``start()`` / ``stop()`` 为真正的异步，文件读取、日志回溯等阻塞操作
      通过 ``asyncio.to_thread`` 在后台线程执行，**不会阻塞事件循环**；
    - 实时日志处理（含 webdb 查询，单次最多 2 秒）在库内部轮询线程完成，
      只把轻量的回调调度回事件循环线程；
    - 回调签名为 ``cb(track)`` / ``cb(state)`` / ``cb(position)``，均在事件循环线程触发。

    用法::

        from cloudmusic_detector import AsyncCloudMusic

        async def main():
            cm = AsyncCloudMusic()
            cm.on_track_change(lambda t: print(t.name))
            await cm.start()
            state = cm.state  # 随时读取
            await cm.stop()
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self) -> None:
        """异步启动，不阻塞事件循环（文件读取 + 日志回溯在后台线程执行）。"""
        self._loop = asyncio.get_running_loop()
        lines = await asyncio.to_thread(self._listener.start)
        await asyncio.to_thread(self._preload, lines)
        # 实时行处理注册在预载之后，且运行在库内部轮询线程，
        # 阻塞的 webdb 查询不会占用事件循环
        self._listener.on_line(self._handle_line)

    async def stop(self) -> None:
        """异步停止，不阻塞事件循环。"""
        await asyncio.to_thread(self._listener.stop)
        # check_same_thread=False 后，webdb 连接可跨线程安全关闭
        self._webdb.close()

    def _emit_track(self, track: Track) -> None:
        """切歌回调：调度到事件循环线程执行。"""
        self._marshal_callbacks(self._on_track_change_cbs, (track,))

    def _emit_state(self, state: PlayingState) -> None:
        """播放/暂停回调：调度到事件循环线程执行。"""
        self._marshal_callbacks(self._on_state_change_cbs, (state,))

    def _emit_seek(self, pos: float) -> None:
        """进度回调：调度到事件循环线程执行。"""
        self._marshal_callbacks(self._on_seek_cbs, (pos,))

    def _marshal_callbacks(self, callbacks: list, args: tuple) -> None:
        """把回调调度到事件循环线程；循环未运行则直接执行（兜底）。"""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._dispatch_callbacks, callbacks, args)
        else:
            self._dispatch_callbacks(callbacks, args)

    @staticmethod
    def _dispatch_callbacks(callbacks: list, args: tuple) -> None:
        for cb in callbacks:
            try:
                cb(*args)
            except Exception:
                pass
