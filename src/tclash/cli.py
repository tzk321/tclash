"""命令行入口：``tclash`` 默认进 TUI，其余为脚本化的子命令。"""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .api import MihomoAPI, MihomoError
from .models import Proxy, delay_style, format_delay
from .settings import VALID_MODES, Settings

app = typer.Typer(
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="mihomo 内核的 CLI / TUI 管理器：切换节点、测速、更新订阅、看延迟与网速。",
)
console = Console()


# --------------------------------------------------------------------- utils
def make_settings(controller: str | None, secret: str | None) -> Settings:
    settings = Settings.load()
    if controller:
        settings.controller = controller
    if secret is not None:
        settings.secret = secret
    return settings


def build_api(settings: Settings) -> MihomoAPI:
    return MihomoAPI(settings.controller, settings.secret, timeout=settings.timeout)


def fail(message: str) -> typer.Exit:
    console.print(f"[red]✗[/] {message}")
    return typer.Exit(code=1)


def is_hidden(proxy: Proxy) -> bool:
    return proxy.is_info or proxy.is_builtin


def sort_groups(proxies: list[Proxy]) -> list[Proxy]:
    """select 组排前面（通常就是主策略组）。"""
    groups = [p for p in proxies if p.is_group and p.name.upper() != "GLOBAL"]
    return sorted(groups, key=lambda g: 0 if g.type.lower() == "selector" else 1)


def find_group(proxies: list[Proxy], name: str) -> Proxy:
    for proxy in proxies:
        if proxy.name == name and proxy.is_group:
            return proxy
    raise fail(f"没有名为 {name!r} 的策略组")


def pick_group(proxies: list[Proxy], name: str | None) -> Proxy:
    if name:
        return find_group(proxies, name)
    groups = sort_groups(proxies)
    if not groups:
        raise fail("mihomo 里没有可用的策略组")
    return groups[0]


def delay_cell(delay: int | None) -> Text:
    return Text(format_delay(delay), style=delay_style(delay))


def members_to_test(group: Proxy, by_name: dict[str, Proxy], *, include_hidden: bool = False) -> list[str]:
    names: list[str] = []
    for member in group.members:
        proxy = by_name.get(member)
        if proxy is None:
            continue
        if not include_hidden and is_hidden(proxy):
            continue
        names.append(member)
    return names


# ------------------------------------------------------------------ callback
@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    controller: str | None = typer.Option(None, "--controller", "-c", help="外部控制器地址"),
    secret: str | None = typer.Option(None, "--secret", help="外部控制器 secret"),
) -> None:
    """不带子命令时启动 TUI。"""
    settings = make_settings(controller, secret)
    ctx.obj = settings
    if ctx.invoked_subcommand is None:
        launch_tui(settings)


def launch_tui(settings: Settings) -> None:
    from .tui.app import TclashApp

    TclashApp(settings).run()


@app.command()
def tui(ctx: typer.Context) -> None:
    """启动 TUI 界面。"""
    launch_tui(ctx.obj)


@app.command()
def status(ctx: typer.Context) -> None:
    """查看内核版本、模式、控制器与策略组当前选择。"""

    async def go() -> None:
        async with build_api(ctx.obj) as api:
            version = await api.version()
            configs = await api.configs()
            proxies = await api.proxies()

        info = Table(show_header=False, box=None, pad_edge=False)
        info.add_row("内核", f"mihomo {version}")
        info.add_row("控制器", ctx.obj.controller)
        info.add_row("模式", str(configs.get("mode", "?")))
        info.add_row("混合端口", str(configs.get("mixed-port", "?")))
        tun = configs.get("tun") or {}
        info.add_row("TUN", "开启" if tun.get("enable") else "关闭")
        console.print(info)
        console.print()

        groups = sort_groups(proxies)
        if not groups:
            return
        gt = Table(title="策略组", title_justify="left")
        gt.add_column("策略组", style="bold")
        gt.add_column("类型", style="grey50")
        gt.add_column("当前选择")
        gt.add_column("成员", justify="right")
        for group in groups:
            gt.add_row(group.name, group.type, group.now or "—", str(len(group.members)))
        console.print(gt)

    try:
        asyncio.run(go())
    except MihomoError as exc:
        raise fail(str(exc)) from exc


@app.command("list")
def list_proxies(
    ctx: typer.Context,
    group: str | None = typer.Option(None, "--group", "-g", help="只看某个策略组"),
    all_groups: bool = typer.Option(False, "--all", "-a", help="列出所有策略组"),
    show_hidden: bool = typer.Option(False, "--hidden", help="显示内置/信息节点"),
) -> None:
    """列出节点与最近一次测速延迟。"""

    async def go() -> None:
        async with build_api(ctx.obj) as api:
            proxies = await api.proxies()

        by_name = {p.name: p for p in proxies}
        if group:
            targets = [find_group(proxies, group)]
        elif all_groups:
            targets = sort_groups(proxies)
        else:
            targets = [pick_group(proxies, None)]

        for target in targets:
            table = Table(
                title=f"{target.name}（{target.type}）· 当前：{target.now or '—'}",
                title_justify="left",
            )
            table.add_column("", width=2)
            table.add_column("节点", style="bold")
            table.add_column("类型", style="grey50")
            table.add_column("延迟", justify="right")
            for member in target.members:
                proxy = by_name.get(member)
                if proxy is None or (is_hidden(proxy) and not show_hidden):
                    continue
                table.add_row(
                    "▶" if target.now == member else "",
                    Text(member),
                    Text(proxy.type, style="grey50"),
                    delay_cell(proxy.delay),
                )
            console.print(table)
            console.print()

    try:
        asyncio.run(go())
    except MihomoError as exc:
        raise fail(str(exc)) from exc


@app.command()
def switch(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="要切换到的节点或子组"),
    group: str | None = typer.Option(None, "--group", "-g", help="在哪个策略组里切换"),
) -> None:
    """把策略组切换到指定节点。"""

    async def go() -> str:
        async with build_api(ctx.obj) as api:
            proxies = await api.proxies()
            group_name = group
            if not group_name:
                candidates = [
                    g.name
                    for g in sort_groups(proxies)
                    if target in g.members and g.type.lower() == "selector"
                ]
                if not candidates:
                    known = {p.name for p in proxies}
                    if target not in known:
                        raise fail(f"没有名为 {target!r} 的节点或策略组")
                    raise fail(f"{target!r} 不在任何 select 组里，用 --group 指定目标组")
                group_name = candidates[0]
                if len(candidates) > 1:
                    console.print(f"[grey50]提示：也在 {', '.join(candidates[1:])} 里，使用 {group_name}[/]")
            await api.select(group_name, target)
            return group_name

    try:
        group_name = asyncio.run(go())
    except MihomoError as exc:
        raise fail(str(exc)) from exc
    console.print(f"[green]✓[/] 已在 [bold]{group_name}[/] 中切换到 [bold]{target}[/]")


@app.command()
def test(
    ctx: typer.Context,
    group: list[str] = typer.Option(None, "--group", "-g", help="测速某个策略组（可重复）"),
    every: bool = typer.Option(False, "--all", "-a", help="测所有节点（去重）"),
    limit: int = typer.Option(0, "--limit", "-n", help="最多测前 N 个节点（0 = 不限制）"),
    hidden: bool = typer.Option(False, "--hidden", help="连同内置/信息节点一起测"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """并发测速并按时延排序。"""

    async def go() -> dict[str, int | None]:
        async with build_api(ctx.obj) as api:
            proxies = await api.proxies()
            by_name = {p.name: p for p in proxies}
            names: list[str] = []
            if every:
                names = [
                    p.name for p in proxies if not p.is_group and (hidden or not is_hidden(p))
                ]
            else:
                targets = [find_group(proxies, g) for g in group] if group else [pick_group(proxies, None)]
                for target in targets:
                    names.extend(members_to_test(target, by_name, include_hidden=hidden))
            names = list(dict.fromkeys(names))
            if limit > 0:
                names = names[:limit]
            if not names:
                raise fail("没有匹配到要测速的节点")
            return await api.test_many(
                names,
                url=ctx.obj.test_url,
                timeout_ms=ctx.obj.test_timeout_ms,
                concurrency=ctx.obj.concurrency,
            )

    try:
        results = asyncio.run(go())
    except MihomoError as exc:
        raise fail(str(exc)) from exc

    if as_json:
        console.print_json(json.dumps(results, ensure_ascii=False))
        return

    ordered = sorted(results.items(), key=lambda kv: (kv[1] is None, kv[1] or 0))
    table = Table(title=f"测速结果（{len(ordered)} 个节点）", title_justify="left")
    table.add_column("#", justify="right", style="grey50")
    table.add_column("节点", style="bold")
    table.add_column("延迟", justify="right")
    for index, (name, delay) in enumerate(ordered, start=1):
        table.add_row(str(index), Text(name), delay_cell(delay))
    console.print(table)


@app.command()
def best(
    ctx: typer.Context,
    group: str | None = typer.Option(None, "--group", "-g", help="在哪个策略组里选最快"),
    apply: bool = typer.Option(False, "--apply", help="直接切换过去"),
) -> None:
    """测速并选出最快的节点（--apply 立即切换）。"""

    async def go() -> tuple[str, str, int]:
        async with build_api(ctx.obj) as api:
            proxies = await api.proxies()
            target = pick_group(proxies, group)
            by_name = {p.name: p for p in proxies}
            names = members_to_test(target, by_name)
            if not names:
                raise fail("这个策略组没有可测速的节点")
            console.print(f"正在测速 [bold]{target.name}[/] 的 {len(names)} 个节点…")
            results = await api.test_many(
                names,
                url=ctx.obj.test_url,
                timeout_ms=ctx.obj.test_timeout_ms,
                concurrency=ctx.obj.concurrency,
            )
            reachable = {n: d for n, d in results.items() if d is not None}
            if not reachable:
                raise fail("全部节点测速失败")
            fastest = min(reachable, key=lambda n: reachable[n] or 10**9)
            if apply:
                await api.select(target.name, fastest)
            return target.name, fastest, reachable[fastest]

    try:
        group_name, fastest, delay = asyncio.run(go())
    except MihomoError as exc:
        raise fail(str(exc)) from exc

    console.print(f"最快：[bold green]{fastest}[/] {delay}ms")
    if apply:
        console.print(f"[green]✓[/] 已在 [bold]{group_name}[/] 中切换过去")
    else:
        console.print("[grey50]加 --apply 直接切换[/]")


@app.command()
def mode(
    ctx: typer.Context,
    value: str | None = typer.Argument(None, help="rule / global / direct，留空则显示当前模式"),
) -> None:
    """查看或切换代理模式。"""

    async def go() -> str:
        async with build_api(ctx.obj) as api:
            if value is None:
                configs = await api.configs()
                return f"当前模式：[bold cyan]{configs.get('mode', '?')}[/]"
            if value not in VALID_MODES:
                raise fail(f"模式只能是 {' / '.join(VALID_MODES)}")
            await api.set_mode(value)
            return f"[green]✓[/] 模式已切换为 [bold]{value}[/]"

    try:
        console.print(asyncio.run(go()))
    except MihomoError as exc:
        raise fail(str(exc)) from exc


@app.command()
def sub(ctx: typer.Context) -> None:
    """订阅管理（阶段 2 实现）。"""
    console.print("[yellow]订阅管理还没实现[/]，阶段 2 会加入：")
    console.print(f"  · profile 存储：[cyan]{ctx.obj.path().parent / 'profiles.toml'}[/]")
    console.print("  · 拉取订阅 → 套本地覆盖项 → sudo 写 /etc/mihomo/config.yaml → 热重载")
    # TODO(阶段 2)：本地覆盖项放 ~/.config/tclash/overrides.yaml，写盘前合并；
    # 至少必须保住 dns.fake-ip-range（默认 198.18.0.1/16 会被 SSRF 防护当内网拦）。
    console.print(f"  · 本地覆盖项：[cyan]{ctx.obj.path().parent / 'overrides.yaml'}[/]（含 dns.fake-ip-range）")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
