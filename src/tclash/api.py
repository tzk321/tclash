"""mihomo 外部控制器（RESTful API）客户端。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from urllib.parse import quote

import httpx

from .models import Proxy, parse_proxies
from .settings import DEFAULT_TEST_URL


class MihomoError(RuntimeError):
    """mihomo API 调用失败。"""


def normalize_base(controller: str) -> str:
    base = controller.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = f"http://{base}"
    return base


class MihomoAPI:
    """薄封装 mihomo API，只暴露 tclash 用得到的接口。"""

    def __init__(
        self,
        controller: str,
        secret: str = "",
        timeout: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base = normalize_base(controller)
        self.secret = secret
        self.timeout = timeout
        self._client = client

    # ------------------------------------------------------------- plumbing
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.secret}"} if self.secret else {}
            self._client = httpx.AsyncClient(
                base_url=self.base, headers=headers, timeout=self.timeout
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def __aenter__(self) -> "MihomoAPI":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _call(self, method: str, path: str, **kwargs) -> object | None:
        try:
            resp = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise MihomoError(f"无法连接 mihomo API（{self.base}）：{exc}") from exc

        if resp.status_code == 401:
            raise MihomoError("mihomo 拒绝访问：external-controller 的 secret 不正确")
        if resp.status_code >= 400:
            detail = resp.text.strip()[:200]
            raise MihomoError(f"{method} {path} 失败：HTTP {resp.status_code} {detail}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    # ----------------------------------------------------------- read paths
    async def version(self) -> str:
        data = await self._call("GET", "/version")
        return str((data or {}).get("version", "?")) if isinstance(data, dict) else "?"

    async def configs(self) -> dict:
        data = await self._call("GET", "/configs")
        return data if isinstance(data, dict) else {}

    async def proxies_raw(self) -> dict:
        data = await self._call("GET", "/proxies")
        return data if isinstance(data, dict) else {}

    async def proxies(self) -> list[Proxy]:
        return parse_proxies(await self.proxies_raw())

    async def rules(self) -> list[dict]:
        data = await self._call("GET", "/rules")
        rows = (data or {}).get("rules", []) if isinstance(data, dict) else []
        return [r for r in rows if isinstance(r, dict)]

    # ---------------------------------------------------------- write paths
    async def select(self, group: str, name: str) -> None:
        await self._call("PUT", f"/proxies/{quote(group, safe='')}", json={"name": name})

    async def set_mode(self, mode: str) -> None:
        await self._call("PATCH", "/configs", json={"mode": mode})

    # --------------------------------------------------------------- delays
    async def delay(
        self,
        name: str,
        url: str | None = None,
        timeout_ms: int = 5000,
    ) -> int | None:
        """测单个节点/组的延迟；超时或失败返回 None。"""
        params = {"url": url or DEFAULT_TEST_URL, "timeout": str(timeout_ms)}
        try:
            data = await self._call(
                "GET", f"/proxies/{quote(name, safe='')}/delay", params=params
            )
        except MihomoError as exc:
            if "HTTP 504" in str(exc) or "Timeout" in str(exc):
                return None
            raise
        delay = (data or {}).get("delay") if isinstance(data, dict) else None
        if not isinstance(delay, int) or delay <= 0:
            return None
        # mihomo 在节点超时时会直接回报等于 timeout 的延迟，视为不可用
        if delay >= timeout_ms:
            return None
        return delay

    async def test_many(
        self,
        names: Iterable[str],
        url: str | None = None,
        timeout_ms: int = 5000,
        concurrency: int = 8,
        on_result: Callable[[str, int | None], None] | None = None,
    ) -> dict[str, int | None]:
        """并发测速，结果按完成顺序回调（便于 TUI 增量刷新）。"""
        names = list(names)
        semaphore = asyncio.Semaphore(max(1, concurrency))
        results: dict[str, int | None] = {}

        async def probe(name: str) -> None:
            async with semaphore:
                try:
                    delay = await self.delay(name, url=url, timeout_ms=timeout_ms)
                except MihomoError:
                    delay = None
            results[name] = delay
            if on_result is not None:
                on_result(name, delay)

        if names:
            await asyncio.gather(*(probe(n) for n in names))
        return results
