# 丁丁盯钉钉

基于 Hugo 的静态博客源码。文章内容来自站点公开页面，样式和本地图片位于 `static/`。

## 本地预览

需要 Hugo 0.170.0。在本目录运行：

```sh
hugo server --renderToMemory --bind 127.0.0.1 --port 1313
```

浏览器打开 <http://127.0.0.1:1313/>。运行 `hugo --cleanDestinationDir` 会生成待发布的 `public/`。

## 内容与导入

`content/pages/` 保存普通页面；`content/posts/` 预留给本地文章。个人文章、草稿、密钥和线上部署配置保存在仓库外。

Halo 导入脚本可从你自己的备份重新生成文章。需要 Python 3.9 及以上版本和 Beautiful Soup：

```sh
python3 -m venv .venv
.venv/bin/pip install beautifulsoup4
.venv/bin/python scripts/import_halo.py /path/to/halo-article-backup
```

重新导入会覆盖相同路径下的导入文件。导入器跳过可执行脚本和交互式嵌入；原备份不可用的外部资源仍会保留其来源地址。

## 博客助手与微信机器人状态

`assistant/` 包含博客助手、文章和草稿工具，以及微信机器人只读状态页的源码。博客助手通过受保护的反向代理提供 `/assistant/`，启动前需配置 `WIZARDJ_INTERNAL_TOKEN_FILE`。使用 OpenAI API 时需在服务端配置 `WIZARDJ_OPENAI_API_KEY_FILE`，可通过 `WIZARDJ_OPENAI_PROJECT` 和 `WIZARDJ_MODEL` 指定项目和模型。远程发布另需配置 `WIZARDJ_PUBLISH_HOST` 及发布专用 SSH 凭据。

微信机器人状态页由 `weixin_status.py` 提供。Linux 部署可定期使用 `--collect` 读取机器人服务和日志，生成只包含状态、时间和错误类别的快照。状态页面和 JSON 接口都应位于后台登录验证之后。
