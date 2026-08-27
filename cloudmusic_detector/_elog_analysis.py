"""内部模块：elog 日志解码与解析。"""

import os
import re
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class _ElogHeader:
    pid: str
    tid: str
    timestamp: int
    log_type: str
    src: str
    lr: str
    datetime: str


_DECODE_TABLE: Optional[bytes] = None

# 开机时刻（wall-clock 毫秒）缓存：开机时刻在系统运行期间是常量，缓存它即可让
# parse_header 在大日志（几十万行）回溯时避免每行触发 GetTickCount64 / time.time
# 系统调用。相比缓存"系统运行时长"，时间戳不再带缓存陈旧偏移——旧实现 1 秒内复用
# uptime 值会让 timestamp 随机超前 0~1s，进度基准（relative_time）每次重置都会
# 抖动，歌词软件表现为切歌/拖拽后进度随机慢 0~1s。定期（60s）刷新以吸收 NTP
# 对系统时钟的大幅校准。
_boot_time_cache_ms: int = -1
_boot_time_cached_at: float = 0.0
_BOOT_TIME_CACHE_TTL: float = 60.0


def _get_boot_time_ms_cached() -> int:
    global _boot_time_cache_ms, _boot_time_cached_at
    now = time.time()
    if _boot_time_cache_ms < 0 or (now - _boot_time_cached_at) >= _BOOT_TIME_CACHE_TTL:
        _boot_time_cached_at = now
        _boot_time_cache_ms = int(now * 1000) - _get_system_uptime_ms()
    return _boot_time_cache_ms


def _build_decode_table() -> bytes:
    """预计算逐字节解码映射表（纯函数，只依赖输入字节值）。"""
    table = bytearray(256)
    for byte in range(256):
        high = byte >> 4
        low = byte & 0x0F
        hex_digit = (high ^ ((low + 8) % 16)) % 16
        restored = hex_digit * 16 + (byte >> 6) * 4 + (~high & 3)
        table[byte] = restored & 0xFF
    return bytes(table)


def decode_elog(data: bytes) -> str:
    """解码 cloudmusic.elog 的自定义二进制编码。

    解码是逐字节的纯函数，用查表 + ``bytes.translate``（C 实现）加速，
    避免在大文件（可达 10MB+）上做逐字节 Python 循环而长时间占用 GIL。

    使用 ``errors="ignore"`` 单遍解码：真实 elog 可能在中部夹杂无效 UTF-8 字节，
    旧实现逐字节剥头重试是 O(P·n)（P=坏字节位置），在 8.8MB 大文件上可达分钟级；
    单遍忽略坏字节为 O(n)，实测 8.8MB + 中部坏字节约 5ms。
    """
    global _DECODE_TABLE
    if _DECODE_TABLE is None:
        _DECODE_TABLE = _build_decode_table()
    return data.translate(_DECODE_TABLE).decode("utf-8", errors="ignore")


_HEADER_RE = re.compile(
    r"^\[(\d+):(\d+):(\d{4}/\d{6}:\d+):([A-Z]+):([a-zA-Z0-9._-]+)\((\d+)\)\]\s+"
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
)


def _get_system_uptime_ms() -> int:
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        try:
            fn = kernel32.GetTickCount64
            fn.restype = ctypes.c_uint64
            return int(fn())
        except AttributeError:
            return int(kernel32.GetTickCount())
    else:
        try:
            with open("/proc/uptime") as f:
                return int(float(f.read().split()[0]) * 1000)
        except (OSError, IOError):
            return 0


def parse_header(row: str) -> Optional[_ElogHeader]:
    m = _HEADER_RE.match(row)
    if not m:
        return None
    pid, tid, ts_str, log_type, src, lr, dt = m.groups()
    startup_time = int(ts_str.split(":")[-1])
    return _ElogHeader(
        pid=pid, tid=tid,
        timestamp=startup_time + _get_boot_time_ms_cached(),
        log_type=log_type, src=src, lr=lr, datetime=dt,
    )


def get_event_type(row: str, rules: dict) -> Optional[str]:
    for name, cfg in rules.items():
        if cfg["rule"](row):
            return name
    return None
