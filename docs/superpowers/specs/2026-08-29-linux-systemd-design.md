# A4 补充：Linux systemd 启动设计（kb.service + 安装/卸载脚本）

- 日期：2026-08-29
- 状态：设计书（先文档后代码）
- 依据：ROADMAP.md（A 线收口后补充）；`docs/superpowers/specs/2026-08-28-a4-cli-design.md`（A4 CLI 优先，Web UI 砍掉）；Dockerfile（N30，容器部署已有）
- 范围决策（2026-08-29 人工确认）：**Linux 侧只做 systemd service**——不写 bash 一键脚本、不写 docker-compose（Dockerfile 已够用），避免形式冗余与重复维护

## 1. 需求

### 1.1 问题（人工提出的两个现实顾虑）

1. **Linux 需要一键启动吗**：原生部署（非 Docker）时，Linux 世界的"一键"首选 **systemd service**（`systemctl start/stop kb`），比 .sh 更正规：开机自启、崩溃自动拉起、日志进 journald、权限可用 `User=` 限制。
2. **权限问题**：脚本/服务必须以**普通用户运行**（禁止 root 常驻）；数据目录（kb_data/ logs/）需自动创建并 chown 到运行用户，否则首次写库报权限错。

### 1.2 目标

- `kb.service`（systemd unit）：`systemctl start/stop/restart/status kb` 全可用，journald 收日志
- `install_kb_service.sh`：一键探测 venv → 生成 unit（system 级或 user 级）→ 建数据目录并 chown → daemon-reload → 可选 enable/start
- `uninstall_kb_service.sh`：停止 + 禁用 + 移除 unit，清理数据目录可选（默认保留）
- 全程**非 root 运行**：system 级用 `sudo` 安装但服务以 `User=<调用者>` 跑；user 级（`--user`）连 sudo 都不要

### 1.3 非目标

- 不做 macOS launchd（scripts/linux 只覆盖 Linux；macOS 用 nohup 或待后续）
- 不做 docker-compose（Dockerfile 注释已含两种运行模式，容器编排属用户侧自由发挥，不引入项目维护负担）
- 不做 Windows/WSL 差异处理（WSL 走 Linux 路径即可）

## 2. 设计

### 2.1 交付物（scripts/linux/）

```
scripts/linux/
├── kb.service                  # systemd unit 模板（占位符 __PYTHON__/__REPO__/__USER__）
├── install_kb_service.sh       # 安装（探测/生成/建目录/chown/enable/start）
├── uninstall_kb_service.sh     # 卸载（stop/disable/remove unit；数据目录默认保留）
└── README.md                   # 双语：用法 + 权限说明
```

### 2.2 kb.service 要点

```ini
[Unit]
Description=kb — local-first Agent memory & knowledge service
After=network.target

[Service]
Type=simple
User=__USER__                   # install 填当前调用者；user 级 unit 不写 User
WorkingDirectory=__REPO__
Environment=HF_ENDPOINT=https://hf-mirror.com
Environment=KB_DEVICE=auto
Environment=KB_LLM_MODE=auto
ExecStart=__PYTHON__ -m kb serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target      # system 级
# user 级（--user 安装时）: WantedBy=default.target
```

- `ExecStart` 用**绝对路径 venv python**（安装时探测 `.venv/Scripts/python*` 不存在则 `venv/`，再兜底系统 `python3`）
- kb 服务自身用 `KB_DATA_DIR`/`KB_LOG_DIR` 默认相对项目根；systemd `WorkingDirectory` 固定后行为与窗口启动一致
- journald 自动收 stdout/stderr + kb 的 structured log（logging 层写 `logs/` 文件照旧，双轨）
- `Restart=on-failure` + `RestartSec=5`：崩溃自动拉起（常驻期望）

### 2.3 权限模型

| 场景 | 安装命令 | 运行身份 | 数据目录 |
|---|---|---|---|
| system 级（推荐，开机自启） | `sudo ./install_kb_service.sh [--enable]` | unit `User=<调用者>` | 项目根 `kb_data/ logs/`，脚本自动 `mkdir -p` + `chown -R <调用者>` |
| user 级（免 sudo） | `./install_kb_service.sh --user [--enable]` | 当前用户 | 同上 |

- **禁止 root 运行服务**：unit 总是显式 `User=`（user 级 unit 在 user manager 下天然是调用者）；若检测到 root 直接运行 install，报错并提示改用 `--user` 或先 `su` 到普通用户
- 目录权限：`mkdir -p` 后 `chown` 到运行用户，杜绝"root 建的目录非 root 写不了"
- 端口 8000 为高位端口，无需 CAP_NET_BIND_SERVICE

### 2.4 install 脚本参数

```
Usage: install_kb_service.sh [--user] [--enable] [--no-start] [--dry-run]
  --user        安装到 ~/.config/systemd/user（免 sudo）；默认 system 级（需 sudo）
  --enable      enable 开机自启（system: multi-user.target / user: default.target）
  --no-start    只装不启动
  --dry-run     只打印将执行的动作，不落盘不改系统（验证用）
```

关键步骤（dry-run 也打印）：
1. 定位 repo 根（脚本所在目录上一级 `..`）
2. 探测 python：`.venv/bin/python` → `venv/bin/python` → `python3`（which）
3. 按模式选择 unit 目标：`/etc/systemd/system/kb.service` 或 `~/.config/systemd/user/kb.service`
4. 替换占位符生成 unit（sed 三处）
5. `mkdir -p kb_data logs` + `chown -R <user>`（system 级）
6. `systemctl daemon-reload`（user 级：`systemctl --user daemon-reload`）；`--enable` 则 `enable`；默认 `start`

### 2.5 uninstall 脚本

```
Usage: uninstall_kb_service.sh [--user] [--delete-data] [--dry-run]
  --user        卸载 user 级；默认 system 级
  --delete-data 同时删除 kb_data/ logs/（默认保留）
```

步骤：`systemctl stop kb`（容忍已停）→ `disable` → `rm unit 文件` → `daemon-reload` → 可选删数据目录。

## 3. 里程碑

| 节点 | 内容 | 门禁 |
|---|---|---|
| **N31** | kb.service + install/uninstall 脚本 + 双语 README + git bash 静态验证（bash -n / --dry-run / 路径探测） | 标准（静态验证 + 人工在真 Linux 上验收 systemctl 行为） |

## 4. 测试策略

本项目无 Linux 跑测环境（Windows），验证分两层：

- **自动化（git bash 可跑）**：
  - `bash -n` 三个脚本语法检查
  - `--dry-run` 执行：确认打印的 unit 内容/路径/命令正确，不产生副作用
  - 路径探测函数单测（mock 不存在 venv 时回退）
- **待人工（真 Linux）**：systemd 真机 `install → start → curl healthz → restart → stop → uninstall` 全链路

## 5. 风险与避坑

| 风险 | 规避 |
|---|---|
| sed 占位符替换遇 `/` 冲突 | 分隔符用 `|`；python 路径含空格少见，先检测到空格即拒绝并提示 |
| user 级 unit 忘记 `systemctl --user daemon-reload` | 脚本统一封装 reload 命令（按模式二选一） |
| root 运行服务后数据目录属主混乱 | **禁 root 常驻**：install 检测 `EUID==0` 且非 `--user` 时给出明确指引 |
| systemd 没有 XDG_RUNTIME_DIR（user 模式 SSH 会话） | 文档提示：user 级需 login shell 或 `export XDG_RUNTIME_DIR=/run/user/$UID` |
| .env 不在项目根时配置缺失 | 文档说明：配置依旧走 `.env`（WorkingDirectory 指向 repo 根，读取行为与窗口启动一致） |