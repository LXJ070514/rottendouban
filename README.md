# RottenDouban - 烂番茄豆瓣聚合评分

聚合 Rotten Tomatoes（烂番茄）和豆瓣评分，通过 GitHub Pages 构建可交互的静态电影评分网站，解决国内用户无法直接访问烂番茄的问题。

## 功能特性

- **烂番茄数据爬取** — 获取电影信息、影评人评分、观众评分
- **豆瓣自动匹配** — 搜索并抓取豆瓣完整数据（评分、评分人数、简介、类型、导演、演员、海报、中文名）
- **加权评分计算** — 番茄影评人 0.3 + 番茄观众 0.3 + 豆瓣 0.4，缺失评分按比例重新分配
- **四层反检测机制** — undetected-chromedriver → selenium-stealth → JS属性覆盖 → 人类行为模拟
- **三重回退评分提取** — JS执行 → XPath → 正文正则
- **数据清理** — 自动过滤 CSS/SVG 噪声，精准分离导演和演员
- **评分历史追踪** — 记录评分变化趋势，折线图可视化
- **电影推荐** — 协同过滤推荐算法（结合类型、导演、评分）
- **电影对比** — 多维度评分可视化对比
- **搜索增强** — 模糊搜索 + 中文拼音搜索
- **分享功能** — 一键生成分享图片并下载
- **数据导出** — JSON/CSV 格式导出
- **暗色/亮色主题** — 主题切换
- **移动端 PWA** — 支持离线缓存
- **定时爬取** — GitHub Actions 自动定期爬取数据并部署

## 项目结构

```
├── crawler/
│   ├── config.py          # 项目配置
│   ├── database.py        # 数据库管理（SQLite）
│   ├── rotten_tomatoes.py # 烂番茄爬虫（四层反检测）
│   ├── douban.py          # 豆瓣爬虫（自动匹配）
│   └── main.py            # 主入口
├── site/
│   ├── index.html         # 静态网站主页
│   ├── css/style.css      # 样式（含暗色主题）
│   ├── js/app.js          # 前端交互逻辑
│   ├── manifest.json      # PWA 配置
│   ├── images/favicon.svg # 图标
│   └── data/
│       ├── movies.json    # 电影数据
│       ├── movies.csv     # CSV 数据
│       └ stats.json       # 统计数据
├── .github/workflows/
│   └ crawl-deploy.yml     # GitHub Actions 定时爬取+部署
├── requirements.txt       # Python 依赖
└── README.md              # 项目文档
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 安装 Chrome 浏览器

烂番茄爬虫需要 Chrome 浏览器（undetected-chromedriver 会自动管理 ChromeDriver）。

### 3. 运行爬虫

```bash
python -m crawler.main
```

### 4. 本地预览网站

```bash
# 使用任意静态服务器预览
cd site
python -m http.server 8080
# 浏览器访问 http://localhost:8080
```

### 5. 部署到 GitHub Pages

1. 将项目推送到 GitHub
2. 在仓库 Settings → Pages 中选择 `gh-pages` 分支
3. GitHub Actions 会自动定时爬取数据并部署更新

## 加权评分算法

```
加权分 = (番茄影评人 × 0.3 + 番茄观众 × 0.3 + 豹瓣 × 10 × 0.4) / 实际权重总和
```

当某个评分缺失时，其权重按比例重新分配给其他已有评分，确保计算公平。

## 反检测机制

| 层级 | 方案 | 说明 |
|------|------|------|
| Layer 1 | undetected-chromedriver | 专门绕过 Cloudflare，自动修补 ChromeDriver |
| Layer 2 | selenium-stealth | 伪装浏览器指纹（语言、WebGL、插件等） |
| Layer 3 | JS 属性覆盖 | 覆盖 navigator.webdriver/plugins/chrome 等 |
| Layer 4 | 人类行为模拟 | 随机滚动、随机延时、Cloudflare 等待 |

## 数据清理

- `_clean_text()` — 过滤 `.icon-bg{fill:...}` 等 CSS 片段和 `<svg>` 标签
- `_clean_name_list()` — 过滤姓名列表中的 CSS/SVG 噪声、超长字符、无效项
- 导演/演员精准分离 — 从 `Director:` 和 `Cast:` 标签行专门提取，避免混合

## 许可证

MIT License