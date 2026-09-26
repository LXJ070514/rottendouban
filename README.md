<div align="center">

# 🍅 RottenDouban

**烂番茄 × 豆瓣 聚合评分** — 把欧美影评人口味与中文观众口味并排放在一起看

一个纯静态、运行时零第三方依赖、由 GitHub Actions 每周自动更新数据的电影评分对照站。

[![CI](https://img.shields.io/github/actions/workflow/status/LXJ070514/rottendouban/ci.yml?label=CI&logo=githubactions&logoColor=white)](https://github.com/LXJ070514/rottendouban/actions/workflows/ci.yml)
[![Fetch & Deploy](https://img.shields.io/github/actions/workflow/status/LXJ070514/rottendouban/crawl-deploy.yml?label=Fetch%20%26%20Deploy&logo=githubactions&logoColor=white)](https://github.com/LXJ070514/rottendouban/actions/workflows/crawl-deploy.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#运行时零依赖)
[![License](https://img.shields.io/github/license/LXJ070514/rottendouban)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/LXJ070514/rottendouban)](https://github.com/LXJ070514/rottendouban/commits/main)

### 👉 [在线演示](https://lxj070514.github.io/rottendouban/)

</div>

---

## 为什么做这个

烂番茄在国内无法直接访问，而豆瓣评分与欧美影评人的判断经常严重背离。单看任何一边都会误判一部片：

| 影片 | 🍅 影评人 | 🍿 观众 | ⭐ 豆瓣 | 加权 |
|---|---:|---:|---:|---:|
| 十二怒汉 · *12 Angry Men* (1957) | 100% | 97% | 9.4 | **96.7** |
| 辛德勒的名单 · *Schindler's List* (1993) | 98% | 97% | 9.5 | **96.5** |
| 教父 · *The Godfather* (1972) | 97% | 98% | 9.3 | **95.7** |
| 千与千寻 · *Spirited Away* (2001) | 96% | 96% | 9.4 | **95.2** |
| 海上钢琴师 · *The Legend of 1900* (1998) | 58% | 91% | 9.3 | **81.9** |
| 白日梦想家 · *The Secret Life of Walter Mitty* (2013) | 52% | 71% | 8.6 | **71.3** |

后两行就是这个项目存在的理由：**影评人打了不及格，中文观众却给出 9 分以上**。
RottenDouban 把三个口径并排展示，再给一个加权总分，让你自己判断该信谁。

> 表格为线上真实数据，随每次自动抓取更新。

## 功能

- **三口径并排** — 烂番茄新鲜度（影评人）、爆米花指数（观众）、豆瓣评分 + 评分人数
- **加权总分** — 影评人 0.3 + 观众 0.3 + 豆瓣 0.4；某源缺失时权重按比例重新分配给其余源，不虚假拉高
- **搜索与筛选** — 支持片名（中英）、导演、**演员**、类型；按分类 / 类型过滤，5 种排序
- **暗色 / 亮色主题**，响应式布局，桌面与手机均可用
- **自动更新** — GitHub Actions 每周两次抓取并部署，无需人工干预
- **失败安全** — 抓取异常或数据量低于下限时**不覆盖线上数据**，CI 如实红灯

### 运行时零依赖

爬虫只用 Python 标准库（`urllib` + `sqlite3` + `json`），前端是原生 HTML/CSS/JS，无框架、无构建步骤。
`pip install` 这一步在 CI 里根本不存在。

## 它是如何工作的

```
                    ┌──────────────────────────────────────────┐
                    │  crawler/movie_list.py                   │
                    │  119 部片单（英文名 + 中文名 + 年份）      │
                    └───────────────┬──────────────────────────┘
                                    │  片单是唯一事实来源
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
   ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐
   │ TMDB API（可选）    │ │ RT Algolia 搜索     │ │ 豆瓣搜索页          │
   │ 海报/简介/演职员     │ │ 新鲜度/爆米花指数    │ │ 中文标题/评分/人数   │
   │ 需配 TMDB_API_KEY   │ │ 精确标题 + 年份择优  │ │ 精确标题 + 年份择优  │
   └─────────┬──────────┘ └─────────┬──────────┘ └─────────┬──────────┘
             └─────────────────────┼─────────────────────┘
                                   ▼
                    ┌──────────────────────────────────────────┐
                    │  SQLite（movies.db，slug = 片名+年份）     │
                    │  评分量纲归一 · 加权计算 · 评分历史快照     │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │  site/data/movies.json（197 KB）          │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │  GitHub Pages · 原生 JS 渲染              │
                    └──────────────────────────────────────────┘
```

三条流水线各司其职：

| Workflow | 触发 | 职责 |
|---|---|---|
| `ci.yml` | push / PR | 跑 64 项离线测试 + 校验 workflow 语法与不变量 |
| `crawl-deploy.yml` | 每周一、四 04:00 UTC / 手动 | 抓取 → 校验 → 提交数据 → 部署 |
| `deploy-site.yml` | push 改了前端时 | 只部署，不重新抓取 |

`crawl-deploy` 刻意拆成 **fetch** 与 **deploy** 两个 job：`environment: github-pages` 会让 job
一启动就登记一个 Pages deployment，若抓取 job 中途失败，那个 deployment 会永久留在队列里，
把后续所有运行堵死。拆分后抓取失败绝不会碰到 Pages。

## 匹配策略：宁缺毋滥

三个数据源靠"片名 + 年份"交叉对齐。规则是**规范化后标题必须完全相同，且上映年份必须吻合（±1）**，
对不上就放弃该源，而不是取搜索结果的第一个。

这不是保守，是修过的事故。旧实现无脑取 `hits[0]`，结果：

| 影片 | 旧实现匹配到 | 正确条目 |
|---|---|---|
| 泰坦尼克号 (1997) | `titanic_2018` —— 2018 年同名片 | 🍅 88% |
| 霸王别姬 (1993) | `farewell_my_concubine_2014` —— 2014 年纪录片 | 🍅 90% |
| 千与千寻 (2001) | `spirited_away_studio_ghibli_fest_2018` —— 重映活动 | 🍅 96% |

站点会显示错误电影的评分，而且看起来完全正常——这类错误比崩溃危险得多。

**别名检索是另一回事**：《这个杀手不太冷》在 RT 索引里挂在 1994 年的 *The Professional* 条目下，
所以 `_query_variants` 会依次尝试原名、去重音名、冒号前后两段，最多 4 次检索。

**确实查不到就不显示**：RT 的索引里没有《七宗罪》《你的名字》《活着》，
豆瓣检索也可能返回同名剧集。这些情况下站点不显示该源分数，而不是显示错的。

### 当前覆盖率

| 数据源 | 覆盖 | 说明 |
|---|---:|---|
| 烂番茄评分 | 112 / 119 | 其余 7 部 RT 索引内确实不存在 |
| 海报 | 119 / 119 | TMDB 提供 |
| 豆瓣评分 | 57 / 119 | 见下方[已知限制](#已知限制) |

## 快速开始

### 只看网站

直接访问 **[在线演示](https://lxj070514.github.io/rottendouban/)**，或克隆后打开 `site/index.html`
（`site/data/movies.json` 已随仓库提供，无需抓取即可浏览）。

### 本地跑一次抓取

```bash
git clone https://github.com/LXJ070514/rottendouban.git
cd rottendouban

python -m crawler.main              # 完整抓取，输出到 site/data/
cd site && python -m http.server 8080
```

不需要 `pip install` 任何东西。

### 跑测试

```bash
pip install pytest pyyaml           # 仅测试需要
python -m pytest                    # 64 项，全部离线，不碰网络
```

## 配置

全部通过环境变量，无配置文件：

| 变量 | 默认 | 作用 |
|---|---|---|
| `CRAWLER_MODE` | `full` | `full` 全流程 / `douban_only` 仅豆瓣匹配 / `site_only` 仅重出站点数据 |
| `CRAWLER_LIMIT` | 全量 | 只抓片单前 N 部，本地调试用 |
| `TMDB_API_KEY`<br>`TMDB_BEARER_TOKEN` | 空 | 配置后补海报、简介、编剧、MPAA 评级；不配也能跑 |
| `MIN_MOVIES_TO_PUBLISH` | `50` | 发布下限。抓到的电影少于它就不覆盖 `site/data/`，保住线上旧数据 |
| `DOUBAN_BLOCK_MIN_SAMPLES` | `12` | 判定豆瓣限流所需的最小实时样本数 |
| `DOUBAN_BLOCK_EMPTY_RATIO` | `0.85` | 空结果占比达到该值即熔断 |
| `CRAWLER_INSECURE_SSL` | 关 | 设 `1` 跳过证书校验，**仅**用于本地代理做 HTTPS 中间人时排障 |

TMDB 密钥放在仓库 **Settings → Secrets and variables → Actions**，不要写进代码。

## 测试

64 项 pytest，全部离线（构造数据 + 打桩，不碰豆瓣 / RT 网络）：

| 文件 | 覆盖 |
|---|---|
| `test_rotten_tomatoes_match.py` | 同名翻拍片择优、年份门槛、重音归一、别名变体 |
| `test_douban_match.py` | 标题+年份双重校验、旧缓存键回退、限流比例判定 |
| `test_scoring.py` | 加权算法、量纲、缺源权重重分配 |
| `test_database.py` | slug 唯一性、评分归一与夹取、历史快照 |
| `test_pipeline.py` | 打桩三个数据源跑通 `main()`，验证落盘位置与退出码 |
| `test_config.py` | `SITE_DIR` 指向回归锁 |
| `test_workflows.py` | workflow 语法、fetch/deploy 必须拆分、禁止用 shell 短路吞掉退出码、禁止表达式直插 shell |

其中 `test_config.py` 与 `test_pipeline.py` 是**事故回归锁**：它们钉住的正是曾经导致
线上数据静默停更三个多月的两个错误（输出目录写错、失败被吞掉后 CI 照样绿灯）。

## 项目结构

```
crawler/
  config.py            路径、权重、TLS；无 import 副作用
  movie_list.py        片单（title_en / title_cn / year），119 部
  rotten_tomatoes.py   RT Algolia 检索 + 择优
  douban.py            豆瓣 window.__DATA__ 解析 + 缓存 + 限流熔断
  tmdb_api.py          TMDB 详情（可选）
  database.py          SQLite：slug 唯一键、评分量纲归一
  site_generator.py    输出 site/data/{movies.json,movies.csv,stats.json}
  main.py              全流程入口，返回退出码供 CI 判断
  data/
    douban_cache.json  已入库，CI 每轮回传增量
site/                  GitHub Pages 直接上传这个目录
  index.html  css/  js/app.js  data/movies.json
scripts/
  update_site.py       不碰上游 API，仅从 movies.db 重出站点数据
  verify_site.py       校验量纲、必填字段、占位链接
  check_db.py          查看本地库概况
  check_cache.py       查看豆瓣缓存概况
tests/                 64 项离线测试
.github/workflows/     ci.yml · crawl-deploy.yml · deploy-site.yml
```

## 已知限制

- **豆瓣覆盖率偏低且增长缓慢**。豆瓣会限流数据中心 IP：返回 HTTP 200，但页面里没有
  `window.__DATA__`，或有 `__DATA__` 而 `items` 为空（CI 实测后者是主流形态）。
  爬虫按空结果比例熔断后回落到缓存，缓存随每轮运行逐步补齐——实测已从 6 条自愈到 57 条。
  要一次填满，需在住宅 IP 下本地跑一遍把 `douban_cache.json` 灌满后提交。
- **片单是手工维护的 119 部**（`crawler/movie_list.py`），不是完整的豆瓣 Top 250。
- **豆瓣只用搜索接口**，拿不到中文简介、导演、演员、短评，站点上这些字段为空。
- **海报走远程直链**（TMDB / RT / 豆瓣图床），不下载入库；豆瓣图床需要 `no-referrer`，前端已处理。
- **GitHub 会在仓库 60 天无人工活动后停掉定时任务**。本仓库有 bot 定期提交数据可缓解，
  长期闲置后仍建议手动 dispatch 一次。

## 贡献

1. Fork 本仓库并新建分支
2. `python -m pytest` 确认 64 项全绿
3. 涉及匹配逻辑的改动请补测试——本项目最贵的 bug 都出在"匹配到了错误的电影"
4. 提 PR；`ci.yml` 会自动跑测试

新增片单条目只需在 `crawler/movie_list.py` 追加一行 `{"title_en", "title_cn", "year"}`，
`year` 务必填写：它是区分同名翻拍片的唯一依据。

## 许可与数据来源

代码采用 [MIT License](LICENSE)。

本项目**不托管任何影视内容**，仅聚合公开的评分元数据：

- 评分与影片元数据来自 [Rotten Tomatoes](https://www.rottentomatoes.com/) 的公开搜索接口
- 中文标题与评分来自 [豆瓣电影](https://movie.douban.com/) 的公开搜索页
- 海报与简介来自 [TMDB](https://www.themoviedb.org/) API

各来源的数据与商标归其各自所有者。本项目为个人学习用途，请遵守各数据源的服务条款；
如需大规模抓取，请自行评估频率与合规性。

---

<div align="center">

**如果这个项目对你有帮助，欢迎点个 Star ⭐**

[在线演示](https://lxj070514.github.io/rottendouban/) · [报告问题](https://github.com/LXJ070514/rottendouban/issues)

</div>
