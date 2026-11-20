# 云服务器部署与恢复

公开站点由 Hugo 0.170.0 构建，Caddy 从 `/srv/wizardj.cn/current` 提供本次发布。博客助手和只读运维页面使用 Python 3.10+ 标准库，分别监听本机 18766 和 18767。订阅 OAuth 辅助脚本另需 `PyJWT[crypto]`，使用 API 密钥的服务无需安装它。

## 需要恢复的状态

- 静态站点的私有文章源和发布目录。
- 博客助手的 SQLite 数据库、草稿、文章目录和可选的发布工作区。线上私有文章可以用 `WIZARDJ_ARTICLE_DIR` 指向恢复位置。
- 独立的微信机器人、优选任务和它们各自的配置。它们不随这个仓库安装。运维页面分别查询 `home-weixin-chat.service`、`wizardj-cf-select.service` 及 `wizardj-cf-select.timer`。
- 单独保管的 OpenAI 密钥、登录凭据和发布专用 SSH 配置。

## 安装

1. 从预期提交构建 Hugo，恢复文章源后发布产物，并令 `/srv/wizardj.cn/current` 指向该版本。
2. 创建 `wizardj-assistant` 服务账号与 `wizardj-status` 系统组。将该提交的 `assistant/` 安装到 `/opt/wizardj-blog-assistant/runtime/`，代码由 root 持有并可供服务读取。用 `install -d -o root -g wizardj-status -m 2750 /var/lib/home-weixin-status` 创建快照目录。助手的私有状态目录应由助手账号持有。
3. 用 `assistant.env.example` 填写服务端 `/var/lib/wizardj-blog-assistant/secrets/assistant.env`。内部令牌文件须可由助手账号读取。只有恢复了发布工作区、SSH 密钥和已验证的 `known_hosts` 后才配置 `WIZARDJ_PUBLISH_HOST`。
4. 将 `systemd/` 中的模板安装至 `/etc/systemd/system/`，执行 `systemctl daemon-reload`。既有部署迁移时先确认旧状态服务和定时器的名字，停用占用 18767 的旧服务与重复采集任务。启动 `wizardj-assistant.service`、`wizardj-ops.service`、`wizardj-ops-collect@weixin.timer`、`wizardj-ops-collect@cf.timer`。首次验收可先分别启动两个 `wizardj-ops-collect@.service` 实例立即生成快照。
5. 使用 `python3 /opt/wizardj-blog-assistant/runtime/login.py /var/lib/wizardj-blog-assistant/secrets/login.pbkdf2` 交互生成密码哈希文件。设置文件权限为 0600 并将文件所有者设为 `wizardj-assistant`，在助手服务环境中配置 `WIZARDJ_LOGIN_USER` 和 `WIZARDJ_LOGIN_HASH_FILE`，然后重启助手服务。生成命令要求密码至少 12 个字符，且仅创建新文件。迁移已有 Caddy 账号时，可将原 bcrypt 哈希单独写入该文件并保留原用户名，助手服务的 Python 需安装 `bcrypt`。会话有效期为 8 小时，助手服务重启后需要重新登录。
6. 参照 `Caddyfile.example` 添加站点路由。将域名和内部令牌作为 Caddy 的私有环境配置，令牌须与助手的令牌文件一致。验证配置后再重载 Caddy。
7. 验证公开站点返回 200，未登录访问助手和两张运维页面会进入博客助手登录页，未登录访问 API 或状态 JSON 返回 401。使用站长账号验证角色回跳、运维页面、刷新和退出。记录上线版本，例如 `git rev-parse HEAD | sudo tee /opt/wizardj-blog-assistant/REVISION`。回滚时按上一条版本记录恢复代码及对应服务配置。

## 优选结果格式

优选状态采集器优先读取 `/var/lib/home-weixin-status/cf-run-result.json` 的 v1 结果，校验本轮启动时间和字段，只接受来源计数、总数、延迟和发布布尔值。现有优选任务仍可使用日志，缺失、过期或格式错误的结果文件会回退到本轮日志。

外部生产者可从 `assistant/ops/cf_status.py` 调用 `write_result(started, summary)`。传入与 systemd `ExecMainStartTimestamp` 一致的 Unix 秒。字段定义见该模块和测试。公开订阅通过 HTTPS 字节校验后方可将 `published` 设为 true。生产者无需把订阅地址、节点 IP 或原始日志写入结果文件。发布端尚未接入此函数时，继续使用日志兼容路径。现有线上优选任务的接入补丁及安装步骤见 [优选任务结构化结果](cf-select/README.md)。
