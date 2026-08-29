# scripts/linux — Linux systemd 服务（Service）

kb 以 **systemd service** 方式在 Linux 上常驻（比 shell 脚本更正规：开机自启、崩溃自拉起、journald 日志、`User=` 限权）。
kb runs on Linux as a **systemd service** (cleaner than shell scripts: autostart, auto-restart, journald logs, `User=` privilege control).

## 交付物（Files）

| 文件 | 说明 |
|---|---|
| `kb.service` | systemd unit 模板（占位符由安装脚本填充） |
| `install_kb_service.sh` | 一键安装（探测 venv → 生成 unit → 建数据目录并 chown → reload → 可选 enable/start） |
| `uninstall_kb_service.sh` | 卸载（stop/disable/移除 unit；数据目录默认保留） |

Docker 部署不在此范围——已有根目录 `Dockerfile`（stdio 默认可入 MCP Registry/Glama，`serve` 模式可选）。
Docker deployment is out of scope here — see root `Dockerfile` instead.

## 使用（Usage）

```bash
# system 级（推荐，开机自启可选）：服务以"你的普通用户"运行，非 root
# system level (recommended): service runs as YOUR normal user, never root
cd scripts/linux
sudo ./install_kb_service.sh --enable     # 安装 + 开机自启（install + autostart）
systemctl status kb                        # 状态
curl http://127.0.0.1:8000/api/v1/healthz # 验证（verify）
journalctl -u kb -f                        # 日志（journald；kb 自身 logs/ 也有文件）

# user 级（免 sudo；需登录会话）
# user level (no sudo; requires login session)
./install_kb_service.sh --user --enable

# 卸载（保留数据）/ 卸载并删数据
# uninstall (keep data) / uninstall and delete data
sudo ./uninstall_kb_service.sh
sudo ./uninstall_kb_service.sh --delete-data
```

## 权限说明（Privileges）

- **禁止以 root 常驻**：unit 固定 `User=<调用者>`（`--user` 模式天然是当前用户）；root 直跑 install 会被拦截并提示改用 sudo/普通用户。Never run the service as root.
- **数据目录**：脚本自动 `mkdir -p kb_data logs` 并 `chown` 给运行用户，避免"root 建的目录普通用户写不进"。Script auto-creates and chowns data dirs.
- 端口 8000 高位端口，无需特权。Port 8000 needs no privilege.
- user 级服务需登录会话（SSH 用户：`export XDG_RUNTIME_DIR=/run/user/$UID`）。User-level units need a login session.

> Windows 用户请用 `../windows/` 下的 `.bat`；macOS 暂不支持（可先 `nohup` 运行）。
> Windows: use `../windows/` .bat files. macOS: not yet (use nohup for now).