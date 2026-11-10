"""流量采样与连接解析。"""

from __future__ import annotations

from tclash.traffic import Connection, TrafficMeter, format_speed, parse_connections, sparkline

PAYLOAD = {
    "downloadTotal": 10_000,
    "uploadTotal": 2_000,
    "connections": [
        {
            "id": "a",
            "metadata": {"host": "example.com", "destinationPort": "443"},
            "rule": "DomainSuffix",
            "rulePayload": "example.com",
            "chains": ["主组", "香港01"],
            "upload": 100,
            "download": 4000,
            "start": "2026-11-02T00:00:00Z",
        },
        {
            "id": "b",
            "metadata": {"destinationIP": "1.1.1.1", "destinationPort": "53"},
            "rule": "MATCH",
            "rulePayload": "",
            "chains": ["DIRECT"],
            "upload": 10,
            "download": 20,
            "start": "2026-11-02T00:00:01Z",
        },
    ],
}


def test_format_speed_units() -> None:
    assert format_speed(0) == "0 B/s"
    assert format_speed(1536) == "1.5 KB/s"
    assert format_speed(1024 * 1024 * 3.25) == "3.2 MB/s"


def test_sparkline_keeps_width_and_scales_to_peak() -> None:
    line = sparkline([0, 512, 1024], 5)
    assert len(line) == 5
    assert line.endswith("█")  # 峰值是最高的方块
    assert sparkline([], 4) == "    "
    assert sparkline([1, 2], 0) == ""


def test_meter_first_sample_has_no_speed() -> None:
    meter = TrafficMeter()
    meter.sample(PAYLOAD, now=100.0)
    assert meter.down_speed == 0
    assert meter.up_speed == 0
    assert meter.connections == 2
    assert meter.down_total == 10_000


def test_meter_diffs_totals_over_elapsed_time() -> None:
    meter = TrafficMeter()
    meter.sample({"downloadTotal": 0, "uploadTotal": 0, "connections": []}, now=100.0)
    # 1 秒里下行涨了 1024 字节
    meter.sample({"downloadTotal": 1024, "uploadTotal": 2048, "connections": []}, now=101.0)
    # 平滑系数 0.55：首帧 0，所以 = 0.55 * 1024
    assert 560 < meter.down_speed < 565
    assert meter.down_total == 1024
    assert meter.up_total == 2048
    assert len(meter.history) == 1


def test_meter_ignores_too_fast_samples() -> None:
    meter = TrafficMeter()
    meter.sample({"downloadTotal": 0}, now=100.0)
    meter.sample({"downloadTotal": 999_999}, now=100.05)  # 50ms，太近
    assert meter.down_speed == 0


def test_meter_never_goes_negative_after_kernel_restart() -> None:
    meter = TrafficMeter()
    meter.sample({"downloadTotal": 5000}, now=100.0)
    meter.sample({"downloadTotal": 10}, now=101.0)  # 内核重启，累计归零
    assert meter.down_speed == 0
    meter.reset()
    assert meter.down_speed == 0
    assert meter.history == []


def test_meter_history_is_capped() -> None:
    meter = TrafficMeter(_history_limit=3)
    meter.sample({"downloadTotal": 0}, now=0.0)
    for second in range(1, 10):
        meter.sample({"downloadTotal": second * 1000}, now=float(second))
    assert len(meter.history) == 3


def test_parse_connections_sorts_by_traffic_and_falls_back_to_ip() -> None:
    items = parse_connections(PAYLOAD)
    assert [c.id for c in items] == ["a", "b"]
    assert items[0].host == "example.com"
    assert items[0].destination == "example.com:443"
    assert items[0].rule == "DomainSuffix,example.com"
    assert items[0].chains == ["主组", "香港01"]
    assert items[0].total == 4100
    assert items[1].host == "1.1.1.1"
    assert items[1].rule == "MATCH"


def test_parse_connections_tolerates_garbage() -> None:
    assert parse_connections({}) == []
    assert parse_connections({"connections": [None, "x", {"id": "c"}]}) == [
        Connection(
            id="c", host="?", destination="?", rule="", chains=[], upload=0, download=0, started=""
        )
    ]
