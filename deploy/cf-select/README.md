# 优选任务的结构化结果

此目录保存已实测的线上增量。补丁适用于 2026-11-19 接入结构化输出前的 `cf_select.py` 和 `subscription_publish.py`，现有脚本、订阅文件和凭据由运维备份保管。

1. 在备份副本中运行 `patch --dry-run -p1 < structured-output.patch`，通过后用 `patch -p1` 应用。保留两个脚本原来的文件权限。
2. 将 `cf_result.py` 放在脚本目录 `/opt/wizardj-egress/`。将此仓库的 `assistant/ops/cf_status.py` 和 `status_server.py` 安装到 `/opt/home-weixin-status/cf-ops/`，并从脚本目录建立 `cf_status.py` 到该文件的符号链接。
3. 将两份 override 示例分别安装到对应单元的 `.service.d/30-structured-status.conf`。如果已使用新的 `wizardj-ops-collect@cf.service`，它已经指向新版采集器，只需为优选任务增加结果目录的写权限。
4. 验证脚本语法、重载 systemd，并在授权后跑一轮现有优选任务。检查后台接口读到与本轮启动时间匹配的结果。原有日志保留以兼容未升级的采集端。

测速阶段写入计数、淘汰原因和延迟。发布端沿用原有 SHA-256 与公开 HTTPS 字节校验，只有校验成功才更新本轮的 `published` 字段。结果由同目录临时文件原子替换，默认仅 root 可读。后台只接收采集后的字段。
