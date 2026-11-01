# tclash

mihomo（Clash Meta）内核的终端管理器：TUI 里看节点、测延迟、切节点，CLI 里跑同样的操作。

所有操作都通过 mihomo 的 external-controller API 完成，不改动内核本身。

## 依赖

- 正在运行的 mihomo（`/usr/local/bin/mihomo` + systemd 服务）
- 配置里开启 external-controller，例如：

```yaml
external-controller: '127.0.0.1:9090'
# secret: '可选'
```

## 安装

```bash
cd ~/Projects/tclash
uv tool install .          # 装成全局命令 tclash
# 或者不安装，直接 uv run tclash ...
```

## TUI

```bash
tclash
```

左侧是策略组（`策略组 → 当前选择`），右侧是该组节点。延迟按 200/500/900ms 分档上色，
`▶` 标出当前选择。

| 按键 | 作用 |
| --- | --- |
| `↑` `↓` | 移动光标 |
| `Enter` | 在当前策略组里切换到该节点 |
| `Tab` / 点击 | 在策略组列表和节点表之间切换 |
| `t` | 测速当前组（隐藏的机场伪节点不测） |
| `T` | 连同内置 / 信息节点一起测 |
| `b` | 测速后自动切到最快的节点 |
| `s` | 按延迟排序 / 恢复配置顺序 |
| `h` | 显示 / 隐藏 `DIRECT`、`剩余流量`、`套餐到期` 这类节点 |
| `/` | 过滤节点，`Esc` 回到列表 |
| `r` | 重新读取 mihomo 状态 |
| `q` | 退出 |

测速并发数、超时、测速 URL 都能在配置里改（见下）。延迟取自 mihomo 记录的历史值，
按 `t` 才会真的发起测速。

## CLI

```bash
tclash status                    # 内核版本、模式、端口、TUN、各策略组当前选择
tclash list                      # 主策略组的节点 + 最近延迟
tclash list -a                   # 所有策略组的节点
tclash switch 香港HKBN            # 在包含它的 select 组里切换
tclash switch 香港HKBN -g 自动选择 # 指定策略组
tclash test -n 12                # 测速主组前 12 个节点，按时延排序
tclash test --all --json         # 全量测速，输出 JSON
tclash best --apply              # 测速并把最快的节点切进去
tclash mode                      # 查看当前模式
tclash mode global               # rule / global / direct
```

全局选项 `--controller URL`、`--secret XXX` 可临时覆盖配置。

## 配置

`~/.config/tclash/config.toml`（权限 600）。文件不存在时首次运行会自动从
`/etc/mihomo/config.yaml` 读取 `external-controller` 和 `secret`。

```toml
controller = "http://127.0.0.1:9090"
secret = ""
timeout = 8.0            # API 请求超时（秒）
test_timeout_ms = 5000   # 单节点测速超时（毫秒），等于该值的延迟视为不可用
concurrency = 8          # 测速并发
test_url = "http://www.gstatic.com/generate_204"
config_path = "/etc/mihomo/config.yaml"
```

## 进度

- [x] 阶段 1：API 客户端、TUI 节点列表、切换、并发测速、CLI 子命令
- [ ] 阶段 2：订阅 / Profile 管理（拉取订阅 → 套本地覆盖项 → 写 config.yaml → 热重载，带备份回滚）
- [ ] 阶段 3：白名单自动测速切换、实时网速面板、规则 / 模式页

## 开发

```bash
uv run pytest
```
