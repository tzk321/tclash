from __future__ import annotations

import json

import httpx
import pytest

from tclash.api import MihomoAPI, MihomoError, normalize_base
from tclash.models import parse_proxies

PROXIES = {
    "proxies": {
        "DIRECT": {"type": "Direct", "alive": True},
        "香港": {"type": "Hysteria2", "alive": True, "history": [{"delay": 88}]},
        "日本": {"type": "Hysteria2", "alive": True, "history": [{"delay": 300}]},
        "主组": {"type": "Selector", "now": "香港", "all": ["香港", "日本"], "history": []},
    }
}


def make_client(requests: list[httpx.Request], *, secret: str = "") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/version":
            return httpx.Response(200, json={"version": "v1.19.33", "meta": True})
        if path == "/configs":
            if request.method == "PATCH":
                return httpx.Response(204)
            return httpx.Response(200, json={"mode": "rule", "mixed-port": 7890})
        if path == "/proxies":
            return httpx.Response(200, json=PROXIES)
        if path.startswith("/proxies/") and path.endswith("/delay"):
            if secret and request.headers.get("Authorization") != f"Bearer {secret}":
                return httpx.Response(401)
            if "日本" in request.url.path:
                return httpx.Response(504, json={"message": "Timeout"})
            return httpx.Response(200, json={"delay": 123})
        if path.startswith("/proxies/") and request.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(404)

    return httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler), timeout=2
    )


def test_normalize_base() -> None:
    assert normalize_base("127.0.0.1:9090") == "http://127.0.0.1:9090"
    assert normalize_base("http://x:9090/") == "http://x:9090"


@pytest.mark.asyncio
async def test_reads() -> None:
    reqs: list[httpx.Request] = []
    async with MihomoAPI("127.0.0.1:9090", client=make_client(reqs)) as api:
        assert await api.version() == "v1.19.33"
        assert (await api.configs())["mode"] == "rule"
        proxies = parse_proxies(await api.proxies_raw())
        assert {p.name for p in proxies} == {"DIRECT", "香港", "日本", "主组"}


@pytest.mark.asyncio
async def test_delay_timeout_returns_none() -> None:
    async with MihomoAPI("127.0.0.1:9090", client=make_client([])) as api:
        assert await api.delay("香港") == 123
        assert await api.delay("日本") is None  # 504 Timeout 视为不可用
        # 延迟等于 timeout 也视为不可用（mihomo 超时会上报等于 timeout 的延迟）
        assert await api.delay("香港", timeout_ms=123) is None


@pytest.mark.asyncio
async def test_select_quotes_name() -> None:
    reqs: list[httpx.Request] = []
    async with MihomoAPI("127.0.0.1:9090", client=make_client(reqs)) as api:
        await api.select("主组", "香港")
    put = [r for r in reqs if r.method == "PUT"]
    assert put and put[0].url.raw_path.decode().endswith("%E4%B8%BB%E7%BB%84")
    assert json.loads(put[0].content) == {"name": "香港"}


@pytest.mark.asyncio
async def test_test_many_collects_results() -> None:
    seen: list[tuple[str, int | None]] = []
    async with MihomoAPI("127.0.0.1:9090", client=make_client([])) as api:
        results = await api.test_many(
            ["香港", "日本"], concurrency=2, on_result=lambda n, d: seen.append((n, d))
        )
    assert results == {"香港": 123, "日本": None}
    assert sorted(seen) == [("日本", None), ("香港", 123)]


@pytest.mark.asyncio
async def test_bad_secret_raises() -> None:
    async with MihomoAPI("127.0.0.1:9090", secret="nope", client=make_client([], secret="right")) as api:
        with pytest.raises(MihomoError, match="secret"):
            await api.delay("香港")


@pytest.mark.asyncio
async def test_unreachable_controller() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    async with MihomoAPI("http://test", client=client) as api:
        with pytest.raises(MihomoError, match="无法连接"):
            await api.version()
