# 丁丁盯钉钉

基于 Hugo 的静态博客源码。文章内容来自站点公开页面，样式和本地图片位于 `static/`。

## 本地预览

需要 Hugo 0.170.0。在本目录运行：

```sh
hugo server --renderToMemory --bind 127.0.0.1 --port 1313
```

浏览器打开 <http://127.0.0.1:1313/>。运行 `hugo --cleanDestinationDir` 会生成待发布的 `public/`。

## 内容与导入

`content/pages/` 保存普通页面；`content/posts/` 预留给本地文章。个人文章、草稿、部署配置和助手代码不包含在此公开仓库中。

Halo 导入脚本可从你自己的备份重新生成文章。需要 Python 3.9 及以上版本和 Beautiful Soup：

```sh
python3 -m venv .venv
.venv/bin/pip install beautifulsoup4
.venv/bin/python scripts/import_halo.py /path/to/halo-article-backup
```

重新导入会覆盖相同路径下的导入文件。导入器跳过可执行脚本和交互式嵌入；原备份不可用的外部资源仍会保留其来源地址。
