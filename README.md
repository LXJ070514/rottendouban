# RottenDouban - 烂番茄豆瓣聚合评分

> 聚合 Rotten Tomatoes（烂番茄）和豆瓣评分，通过 GitHub Pages 构建可交互的静态电影评分网站，解决国内用户无法直接访问烂番茄的问题。

## 功能特性

- **烂番茄评分展示** — 新鲜度（影评人评分）+ 爆米花指数（观众评分）
- **豆瓣评分展示** — 豆瓣评分 + 评分人数 + 热门短评
- **加权评分计算** — 番茄影评人 0.3 + 番茄观众 0.3 + 豆瓣 0.4
- **纯API数据获取** — 烂番茄 Algolia API + 豆瓣搜索 API，无浏览器依赖
- **GitHub Actions 自动部署** — 定时获取数据并部署到 GitHub Pages
- **暗色/亮色主题** — 自适应主题切换
- **响应式布局** — 适配桌面端、平板和手机
- **搜索与筛选** — 按名称搜索、按分类/类型/排序筛选

## 项目结构

```
├── crawler/
│   ├── config.py           # 项目配置
│   ├── database.py         # 数据库管理 (SQLite)
│   ├── rotten_tomatoes.py  # 烂番茄 API (Algolia)
│   ├── douban.py           # 豆瓣 API (搜索)
│   ├── downloader.py       # 海报下载
│   ├── site_generator.py   # 网站数据生成
│   └── main.py             # 主入口
├── site/
│   ├── index.html          # 网站主页
│   ├── css/style.css       # 样式（暗色/亮色主题）
│   ├── js/app.js           # 前端交互逻辑
│   ├── manifest.json       # PWA 配置
│   ├── images/favicon.svg  # 图标
│   └── data/
│       ├── movies.json     # 电影数据
│       └ stats.json        # 统计数据
├── .github/workflows/
│   └ crawl-deploy.yml      # GitHub Actions 定时获取+部署
├── scripts/
│   ├── update_site.py      # 更新网站数据
│   ├── check_db.py         # 检查数据库
│   ├── check_cache.py      # 检查缓存
│   └── verify_site.py      # 验证网站数据
├── requirements.txt        # Python 依赖
└── README.md
```

## 快速开始

### 方式一：直接查看静态网站

`site/data/movies.json` 已包含示例数据，直接打开 `site/index.html` 或推送到 GitHub Pages 即可。

### 方式二：运行数据获取（GitHub Actions 推荐）

1. 将项目推送到 GitHub
2. 在仓库 Settings → Pages 中选择 GitHub Actions
3. GitHub Actions 会自动定时获取数据并部署更新
4. 也可手动触发 workflow（workflow_dispatch）

### 方式三：本地运行数据获取

```bash
pip install -r requirements.txt
python -m crawler.main
```

获取的数据会输出到 `site/data/movies.json`，用任意静态服务器预览：

```bash
cd site && python -m http.server 8080
```

## 加权评分算法

```
加权分 = (番茄影评人 × 0.3 + 番茄观众 × 0.3 + 豆瓣 × 10 × 0.4) / 实际权重总和
```

当某个评分缺失时，其权重按比例重新分配给其他已有评分。

## 技术栈

- **前端**: 原生 HTML/CSS/JS，零依赖，GitHub Pages 静态托管
- **后端**: Python 3.11，纯 API 数据获取（无浏览器依赖）
- **数据源**: 烂番茄 Algolia API + 豆瓣搜索 API
- **部署**: GitHub Actions + GitHub Pages

## 许可证

MIT License
