# RottenDouban - 烂番茄豆瓣聚合评分

> 聚合 Rotten Tomatoes（烂番茄）与豆瓣评分，用 GitHub Pages 提供可交互的静态电影评分站，解决国内无法直接访问烂番茄的问题。
>
> 线上地址：https://lxj070514.github.io/rottendouban/

## 功能

- **烂番茄评分** — 新鲜度（影评人）+ 爆米花指数（观众）
- **豆瓣评分** — 评分 + 评分人数
- **加权总分** — 影评人 0.3 + 观众 0.3 + 豆瓣 0.4，缺失的源按比例重新分配权重
- **搜索与筛选** — 片名/导演/类型搜索，按分类、类型、排序过滤
- **暗色/亮色主题**、响应式布局
- **定时抓取 + 自动部署** — GitHub Actions，本地无需运行任何东西

数据源全部走公开接口，**运行时零第三方依赖**（只用 Python 标准库 urllib + sqlite3）。

## 匹配策略：宁缺毋滥

三部数据源靠"片名 + 年份"交叉对齐，规则是**标题规范化后必须完全相同，且年份必须吻合（±1）**，
对不上就放弃该源，而不是取搜索结果的第一个。

这不是保守，是修过的事故：旧实现无脑取 `hits[0]`，结果

| 影片 | 旧实现匹配到 | 正确条目 |
|---|---|---|
| 泰坦尼克号 (1997) | `titanic_2018` —— 2018 年同名片 | 88% 新鲜度 |
| 霸王别姬 (1993) | `farewell_my_concubine_2014` —— 2014 年纪录片 | 90% 新鲜度 |
| 千与千寻 (2001) | `spirited_away_studio_ghibli_fest_2018` —— 重映活动 | 96% 新鲜度 |

RT 的 `content` 索引里确实**没有**《七宗罪》《你的名字》《活着》，
豆瓣检索也可能返回同名剧集——这些情况下站点不显示该源分数，而不是显示错的。

别名检索是另一回事：《这个杀手不太冷》在 RT 索引里挂在 1994 年的 《The Professional》 条目下，
所以 `_query_variants` 会依次尝试原名、去重音名、冒号前后两段，最多 4 次检索。

## 项目结构

```
crawler/
  config.py            # 路径、权重、TLS；无 import 副作用
  movie_list.py        # 基础片单（title_en / title_cn / year），当前 119 部
  rotten_tomatoes.py   # RT Algolia 检索 + 择优
  douban.py            # 豆瓣搜索页 window.__DATA__ 解析 + 缓存
  tmdb_api.py          # TMDB 详情（可选，配了密钥才启用）
  database.py          # SQLite：slug 唯一键、评分量纲归一
  site_generator.py    # 输出 site/data/{movies.json,movies.csv,stats.json}
  main.py              # 全流程入口，返回退出码供 CI 判断
  data/
    douban_cache.json  # 已入库，CI 会回传增量
site/                  # GitHub Pages 直接上传这个目录
  index.html css/ js/ data/movies.json
scripts/
  update_site.py       # 不碰上游 API，仅从 movies.db 重出站点数据
  verify_site.py       # 校验 movies.json 的量纲、必填字段、占位链接
.github/workflows/
  crawl-deploy.yml     # 定时：抓取 → 提交数据 → 部署
  deploy-site.yml      # push 改了前端时：只部署，不重新抓取
tests/                 # pytest，全部离线
```

## 本地开发

```bash
python -m pytest            # 离线单元测试，不需要网络也不需要装依赖
python -m crawler.main      # 跑一次完整抓取（写 site/data/，走豆瓣/RT 网络请求）
python scripts/verify_site.py
cd site && python -m http.server 8080
```

常用环境变量：

| 变量 | 作用 |
|---|---|
| `CRAWLER_MODE` | `full`（默认）/ `douban_only` / `site_only` |
| `TMDB_API_KEY` / `TMDB_BEARER_TOKEN` | 配置后补海报、简介、编剧；不配也能跑 |
| `MIN_MOVIES_TO_PUBLISH` | 发布下限，默认 50。抓到的电影少于它就不覆盖 `site/data/`，保住线上旧数据 |
| `CRAWLER_INSECURE_SSL` | 设 `1` 跳过证书校验，仅用于本地代理做 HTTPS 中间人时排障 |

## 部署链路

`crawl-deploy.yml` 拆成 `fetch` 与 `deploy` 两个 job，这不是排版偏好而是修 bug：
`environment: github-pages` 会让 job 一启动就登记一个 Pages deployment，
爬虫中途失败会把那个 deployment 永久留在 queued，后续所有 run 排在它后面直到被 GitHub 取消。
历史上 4 个 deployment 各占位约 30 天，站点数据因此停更了三个多月。

现在的顺序是：抓取 → `verify_site.py` 校验 → 提交刷新后的数据（`[skip ci]`）→ 才进入 deploy job。
任一步失败都如实红灯，且不会动线上数据。

TMDB 密钥放在仓库 Settings → Secrets and variables → Actions → `TMDB_API_KEY`（可选）。

## 已知限制

- 片单是 `crawler/movie_list.py` 里手工维护的 119 部，不是完整的豆瓣 Top 250
- 豆瓣只用搜索接口，拿不到中文简介/导演/演员/短评，这些字段在站点上通常为空
- 海报直链 TMDB / RT / 豆瓣图床，不下载入库（豆瓣需要 `no-referrer`，已被前端处理）
- GitHub 在某仓库 60 天内没有人工活动时会停掉其定时任务，长期闲置后需手动触发一次

## 技术栈

- **前端**：原生 HTML/CSS/JS，零框架、零构建
- **后端**：Python 3.9+ 标准库（urllib + sqlite3）
- **部署**：GitHub Actions + GitHub Pages

## 许可证

MIT
