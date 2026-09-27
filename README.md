<div align="center">

# 🍅 RottenDouban

**烂番茄 × 豆瓣 聚合评分** — 把欧美影评人口味与中文观众口味并排放在一起看

一个纯静态、运行时零第三方依赖、由 GitHub Actions 每半月自动更新数据的电影评分对照站。

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
- **豆瓣 Top250 全自动** — 榜单名次、评分、人数直接取自豆瓣官方接口，不再手工维护片单
- **中文详情** — 简介、导演、主演、制片国家、片长、类型
- **豆瓣热门短评** — 每部片附热门短评与星级
- **搜索与筛选** — 支持片名（中英）、导演、**演员**、类型；按分类 / 类型过滤，5 种排序
  （加权 / 番茄 / 观众 / 豆瓣 / 年份）
- **暗色 / 亮色主题**，响应式布局，桌面与手机均可用
- **自动更新** — GitHub Actions 每半月抓取并部署，无需人工干预
- **失败安全** — 抓取异常或数据量低于下限时**不覆盖线上数据**，CI 如实红灯；
  站点校验不通过时只回传豆瓣缓存，坏数据不会变成下一轮的基线

### 运行时零依赖

爬虫只用 Python 标准库（`urllib` + `sqlite3` + `json`），前端是原生 HTML/CSS/JS，无框架、无构建步骤。
`pip install` 这一步在 CI 里根本不存在。

## 它是如何工作的

```
        ┌───────────────────────────────────────────────────────────┐
        │ ① 豆瓣 j/chart/top_list                                   │
        │    Top250 榜单：官方 rank、subject id、评分、人数、海报、类型 │
        └─────────────────────────┬─────────────────────────────────┘
                                  │  subject id 是后续一切的钥匙
                                  ▼
        ┌───────────────────────────────────────────────────────────┐
        │ ② 豆瓣 Rexxar  movie/<id>  +  movie/<id>/interests         │
        │    中文简介、导演、演员、原名/别名、制片国家、片长、热门短评    │
        │    按 subject id 缓存，只在缺失时才请求                      │
        └─────────────────────────┬─────────────────────────────────┘
                                  │  英文片名（original_title → aka → TMDB 反查）
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
   ┌────────────────────────┐            ┌────────────────────────────┐
   │ ③ RT Algolia 搜索       │            │ ④ TMDB（可选，需密钥）       │
   │    新鲜度 / 爆米花指数   │            │    海报、英文简介、MPAA 评级 │
   │    精确标题 + 年份择优   │            │    兼任中文名→英文名反查     │
   └───────────┬────────────┘            └─────────────┬──────────────┘
               └───────────────────┬───────────────────┘
                                   ▼
        ┌───────────────────────────────────────────────────────────┐
        │ SQLite（movies.db，slug = douban-<subject id>）             │
        │ 评分量纲归一 · 加权计算 · 评分历史快照                        │
        └─────────────────────────┬─────────────────────────────────┘
                                  ▼
        ┌───────────────────────────────────────────────────────────┐
        │ site/data/movies.json  →  GitHub Pages · 原生 JS 渲染       │
        └───────────────────────────────────────────────────────────┘
```

以豆瓣榜单为驱动，是因为 **subject id 直接来自榜单**：豆瓣侧不再需要任何模糊匹配，
"匹配到同名剧集"这一整类 bug 从结构上消失。`crawler/movie_list.py` 仍保留，
但职责已变为"Rexxar 给不出英文名时的中英映射兜底"。

四条流水线各司其职：

| Workflow | 触发 | 职责 |
|---|---|---|
| `ci.yml` | push / PR | 跑离线测试 + 校验 workflow 语法与不变量 |
| `crawl-deploy.yml` | 每月 1、16 号 04:17 UTC / 手动 | 抓取 → 校验 → 提交数据 → 部署 |
| `deploy-site.yml` | push 改了前端时 | 只部署，不重新抓取 |
| `diagnose-sources.yml` | 手动 | 在 Runner 真实出口 IP 上探测各数据源可用性 |

`crawl-deploy` 刻意拆成 **fetch** 与 **deploy** 两个 job：`environment: github-pages` 会让 job
一启动就登记一个 Pages deployment，若抓取 job 中途失败，那个 deployment 会永久留在队列里，
把后续所有运行堵死。拆分后抓取失败绝不会碰到 Pages。

定时刻意避开整点：GitHub 文档明确 "High load times include the start of every hour"，
整点触发的定时任务更容易被延迟调度。

### 为什么要有一个诊断 workflow

豆瓣对不同 IP 段策略不同，而且**限流时返回 HTTP 200**，只看状态码会得出完全错误的结论。
本项目的两次关键决策都来自它，而不是来自本地测试：

- `search.douban.com` 在 Runner 上返回 200 但 `items` 为空 → 整个模块弃用它
- `rexxar/api/v2/movie/<id>` 超额返回 HTTP 400，响应体 `{"msg":"subject_ip_rate_limit"}`
  → 确诊是按 IP 的时间窗配额，与请求头无关（桌面/移动 UA、有无 cookie、Referer 粒度
  四种变体表现一致）。再用 matrix 让 0.5s/2s/4s/8s 四档节流各跑一个独立 Runner，
  得到 10/14/19/17 的成功数，**且四档的首次失败都落在第 10-11 次** —— 这说明配额是
  按时间窗滚动的固定额度，放慢只能摊平、不能提高总额。这个结论直接决定了下面的
  抓取策略，也否掉了两个诱人但错误的方案：按 IP 分片并行、以及"把节流调到 8s 就稳了"。

改抓取逻辑前先跑一次 `diagnose-sources.yml`，用 Runner 的结论而不是本地的结论做决定。

### 配额有限时怎么抓满 250 部

Top250 每部要两次 Rexxar 请求（详情 + 短评），共约 500 次，而单个 Runner 的时间窗
配额远不够。第一版的做法是固定 4s 节流 + 撞配额就退避重试，结果是 run `36262133540`
在 **75 分钟整被 CI 强杀**，而且缓存只在成功路径提交 —— 一整轮颗粒无收。

现在的做法是承认"一轮抓不完"，把它变成**单调递增的续抓**：

| 机制 | 作用 |
|---|---|
| 基础节流降到 1.5s | 配额是固定额度，慢并不换来更多配额，只换来更长的墙钟 |
| `DOUBAN_TIME_BUDGET`（默认 1200s） | 豆瓣阶段的墙钟硬上限，用尽即停止发请求 |
| 退避睡眠封顶 180s | 不封顶时撞一次配额就干等 400s，把短评阶段整段堵死 |
| 详情与短评拆成两轮 | 详情是短评的前提，也是英文名的来源，冷启动时优先吃预算 |
| 缓存在 `finally` 里保存 | 限流、超预算、抛异常都不丢已抓到的部分 |
| CI 用 `if: always()` 提交 | 抓取失败也回传，下一轮从缓存接着补 |

**退避要有总额上限：它的危害不是费时间，是堵路。** 不封顶时，一次撞配额会让
流水线连睡 400 秒，短评阶段根本轮不上。两次线上运行的对照（唯一变量是封顶）：

| | 不封顶 `36296432188` | 封顶 180s `36298235604` |
|---|---:|---:|
| 豆瓣详情 | 101 | **158** |
| 豆瓣短评 | 0（75 部待办全中止） | **158** |
| RT 命中 | 214 | 218 |
| 墙钟耗时 | 22.3 min | **15.3 min** |
| 预算 | 1182s 用尽 | 未用尽 |

机制：封顶是**整轮累计口径**（单请求最多也只等 20+40+60=120s，做不了单请求约束）——
整轮最多为退避花 180s，之后撞配额立刻放弃、把预算留给下一条。这样不再为同一条死等，
其间穿插的 RT/TMDB 调用本身就拉长了豆瓣请求的自然间隔，后续请求常常就能通过。
省下的墙钟还够把短评跑完。（`DOUBAN_RATE_LIMIT_SLEEP_BUDGET`，默认 180s。）

于是覆盖率随每轮运行爬升：78 → 101 → 158 → 206 部详情，且每轮耗时在降
（22.3 → 15.3 → 11.1 分钟，缓存命中后不再重复请求）。代价是**冷启动需要几轮**，
换来的是任何一轮都必定能在 CI 超时内跑完并把成果落盘。

## 匹配策略：宁缺毋滥

**只有烂番茄一侧需要匹配**。豆瓣的 subject id 直接来自官方榜单，不存在猜的问题；
RT 侧则靠"片名 + 年份"对齐，规则是**规范化后标题必须完全相同，且上映年份必须吻合（±1）**，
对不上就放弃，而不是取搜索结果的第一个。

这不是保守，是修过的事故。旧实现无脑取 `hits[0]`，结果：

| 影片 | 旧实现匹配到 | 正确条目 |
|---|---|---|
| 泰坦尼克号 (1997) | `titanic_2018` —— 2018 年同名片 | 🍅 88% |
| 霸王别姬 (1993) | `farewell_my_concubine_2014` —— 2014 年纪录片 | 🍅 90% |
| 千与千寻 (2001) | `spirited_away_studio_ghibli_fest_2018` —— 重映活动 | 🍅 96% |

站点会显示错误电影的评分，而且看起来完全正常——这类错误比崩溃危险得多。

**英文片名有三级来源**，因为华语片的 `original_title` 是空的（原名就是中文）：

1. Rexxar 的 `original_title`
2. Rexxar 的 `aka` 里的 ASCII 别名 —— 顺序不保证，《活着》是 `['人生','Lifetimes','To Live']`，
   `Lifetimes` 排在前面却不是 RT 收录的那个，所以**逐个候选试**，由严格匹配器自校验
3. TMDB 用中文片名反查 —— 这一级让 RT 覆盖率不再依赖豆瓣详情接口是否可达。
   run `36267829360` 实测：即使只拿到 78 部详情，反查仍补出 153 个英文名，
   最终 RT 命中 211/250。检索用 `language=zh-CN` 是为了让"标题精确匹配"生效
   （用 `en-US` 搜中文片名也能命中，但落在兜底取首条那一级，全靠年份把关）

《这个杀手不太冷》还需要 `_query_variants` 兜一层：它在 RT 索引里挂在 1994 年的
*The Professional* 条目下，所以会依次尝试原名、去重音名、冒号前后两段。

**确实查不到就不显示**：RT 的索引里没有《七宗罪》《你的名字》《活着》。
这种情况下站点不显示番茄分，而不是显示错的。

### 当前覆盖率

run `36299219243`（2026-09-27，冷启动第四轮，取自线上实际部署的数据）：

| 数据源 | 覆盖 | 说明 |
|---|---:|---|
| 豆瓣榜单字段 | 250 / 250 | 名次 1–250、评分、人数、类型、制片国家全部来自榜单 |
| 豆瓣评分 | 250 / 250 | 榜单直接提供，不受 Rexxar 配额影响 |
| 加权总分 | 250 / 250 | 缺源时权重按比例分给其余源 |
| 海报 | 246 / 250 | RT / TMDB 优先，豆瓣图床兜底 |
| 番茄 / 观众评分 | 182 / 201 | 未命中的多为 RT 索引内确实不存在 |
| 豆瓣简介 · 导演 · 主演 | 206 / 250 | 逐轮递增：78 → 101 → 158 → 206 |
| 豆瓣短评 | 206 / 250 | 与详情同轮补齐（详情补完即轮到短评） |

**详情逐轮爬升，且每轮耗时在降**（22.3 → 15.3 → 11.1 分钟）—— 因为缓存命中后
不再重复请求，预算全部用在新增条目上。这就是"承认一轮抓不完、但保证每轮不白跑"
换来的东西。

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
python -m pytest                    # 133 项，全部离线，不碰网络
```

## 配置

全部通过环境变量，无配置文件：

| 变量 | 默认 | 作用 |
|---|---|---|
| `CRAWLER_MODE` | `full` | `full` 全流程 / `site_only` 仅从 `movies.db` 重出站点数据 |
| `CRAWLER_LIMIT` | 全量 | 只抓榜单前 N 部，本地调试用 |
| `DOUBAN_TOP_N` | `250` | 榜单取前 N 名 |
| `DOUBAN_REQUEST_DELAY` | `1.5` | 豆瓣基础请求间隔（秒）。配额是按时间窗的固定额度，放慢不换来更多配额 |
| `DOUBAN_TIME_BUDGET` | `1200` | 豆瓣阶段的墙钟上限（秒），详情与短评两轮共用；用尽即停，剩余留给下一轮 |
| `DOUBAN_RATE_LIMIT_RETRIES` | `3` | 撞上 IP 配额后的重试次数 |
| `DOUBAN_RATE_LIMIT_BACKOFF` | `20` | 配额重试的退避基数（秒），按次数递增 |
| `DOUBAN_RATE_LIMIT_SLEEP_BUDGET` | `180` | 退避的**整轮累计**睡眠上限（秒）。触顶后撞配额即放弃本条 |
| `DOUBAN_COMMENT_COUNT` | `3` | 每部片抓几条热门短评 |
| `DOUBAN_BLOCK_MIN_SAMPLES` | `12` | 判定豆瓣限流所需的最小实时样本数 |
| `DOUBAN_BLOCK_EMPTY_RATIO` | `0.85` | 空结果占比达到该值即熔断，剩余影片只读缓存 |
| `TMDB_API_KEY`<br>`TMDB_BEARER_TOKEN` | 空 | 补海报、英文简介、MPAA 评级，并兼任中文名→英文名反查；不配也能跑 |
| `MIN_MOVIES_TO_PUBLISH` | 目标数 × 0.8 | 发布下限。低于它就不覆盖 `site/data/`，保住线上旧数据 |
| `CRAWLER_INSECURE_SSL` | 关 | 设 `1` 跳过证书校验，**仅**用于本地代理做 HTTPS 中间人时排障 |

TMDB 密钥放在仓库 **Settings → Secrets and variables → Actions**，不要写进代码。

## 测试

133 项 pytest，全部离线（构造数据 + 打桩，不碰豆瓣 / RT / TMDB 网络），约 2 秒跑完：

| 文件 | 项数 | 覆盖 |
|---|---:|---|
| `test_douban.py` | 35 | 榜单分页与 rank 语义、翻页封顶、Rexxar 归一化、id 级缓存与旧格式识别、详情/短评两轮拆分、短评跨轮留存、时间预算与退避封顶、IP 配额识别与重试 |
| `test_workflows.py` | 20 | workflow 语法、fetch/deploy 必须拆分、禁止用 shell 短路吞掉退出码、禁止表达式直插 shell、提交必须 `if: always()`、推送必须收在**一个**步骤里并先 rebase 且可重试、校验失败要丢弃新数据、Pages 部署方共用一个并发组 |
| `test_pipeline.py` | 18 | 打桩四个数据源跑通 `main()`：落盘位置、退出码、字段贯通、TMDB 反查、限流降级、短评阶段的增量与中止、制片国家的榜单兜底 |
| `test_verify_site.py` | 17 | 发布闸门本身：量纲越界/占位链接/缺 douban_id/短评非数组必须拦住，且**不得崩成堆栈**（曾对字符串评分抛 TypeError） |
| `test_tmdb_match.py` | 14 | 检索 language 跟着查询语言走、中文译名精确命中、模糊匹配的年份把关、兜底路径保留 |
| `test_rotten_tomatoes_match.py` | 9 | 同名翻拍片择优、年份门槛、重音归一、别名变体 |
| `test_database.py` | 9 | slug 唯一性、评分归一与夹取、历史快照、导出不含历史 |
| `test_scoring.py` | 7 | 加权算法、量纲、缺源权重重分配 |
| `test_config.py` | 4 | `SITE_DIR` 指向回归锁 |

两类测试值得单独说：

- **事故回归锁** — `test_config.py` 与 `test_pipeline.py` 钉住的是曾经导致线上数据
  静默停更三个多月的两个错误（输出目录写错、失败被吞掉后 CI 照样绿灯）。
- **夹具防漂移** — `test_pipeline.py` 的榜单数据由**真实的 `fetch_top_list`** 产出
  （只桩掉网络层），而不是手写字典。之前正是夹具与真实输出的键名漂移
  （`rank` vs `douban_rank`）掩盖了一个线上会 KeyError 的 bug。

## 项目结构

```
crawler/
  config.py            路径、权重、TLS；无 import 副作用
  douban.py            豆瓣榜单 + Rexxar 详情/短评 + id 级缓存 + 配额退避 + 时间预算
  rotten_tomatoes.py   RT Algolia 检索 + 严格择优
  tmdb_api.py          TMDB 详情（可选），兼任中文名→英文名反查（检索按 zh-CN）
  movie_list.py        中英片名映射，仅在 Rexxar 给不出英文名时兜底
  database.py          SQLite：slug 唯一键、评分量纲归一
  site_generator.py    输出 site/data/{movies.json,movies.csv,stats.json}
  main.py              全流程入口，返回退出码供 CI 判断
  data/
    douban_cache.json  按 subject id 缓存详情，CI 每轮回传增量
site/                  GitHub Pages 直接上传这个目录
  index.html  css/  js/app.js  data/movies.json
scripts/
  diagnose_sources.py  在 Runner 出口 IP 上探测各数据源可用性
  diagnose_rexxar.py   Rexxar IP 配额探测（配合 matrix 分档节流）
  update_site.py       不碰上游 API，仅从 movies.db 重出站点数据
  verify_site.py       校验量纲、必填字段、占位链接、短评类型；坏数据只报错不崩溃
  check_db.py          查看本地库概况
  check_cache.py       查看豆瓣缓存概况
tests/                 133 项离线测试
.github/workflows/     ci · crawl-deploy · deploy-site · diagnose-sources
```

## 已知限制

- **豆瓣详情一轮抓不满 250 部**。`rexxar/api/v2/movie/<id>` 超额返回 HTTP 400
  （`{"msg":"subject_ip_rate_limit"}`）。CI matrix 实测：0.5s 节流 10/20 成功、
  2s → 14、4s → 19、8s → 17（8s 的失败是 SSL 握手超时而非配额），
  且各档首次失败都在第 10-11 次 —— 配额是按时间窗滚动的**固定额度**，放慢只能摊平。
  冷启动实测：78 → 101 → 158 → 206 部详情，逐轮递增且单轮耗时在降。
  因此改为时间预算 + 退避封顶 + 跨轮续抓
  （见[配额有限时怎么抓满 250 部](#配额有限时怎么抓满-250-部)）：
  **冷启动需要几轮才能补齐详情与短评**，覆盖率随每轮运行递增。
  固定 4s 节流的旧方案实测在 75 分钟被 CI 强杀且缓存全丢，已废弃。
- **`search.douban.com` 在 CI 上不可用**：返回 200 但 `items` 为空。整个模块已弃用它，
  改用榜单 + Rexxar。本地住宅 IP 上它仍然可用，所以这个差异只能在 CI 里发现。
- **短评条数有限**（默认每部 3 条）。豆瓣短评接口同样受配额约束，条数越多耗时越长。
- **海报走远程直链**（TMDB / RT / 豆瓣图床），不下载入库。好处是仓库体积小，代价是图床
  策略变化时海报会失效。前端按 `poster_url`（TMDB / RT）→ `douban_poster` → picsum
  占位图的顺序取，所以豆瓣图床挂了也不会出现碎图。
- **豆瓣图床是反向防盗链，且认 Referer 的域名族**。实测 24 张随机海报：

  | 请求带的 Referer | 200 |
  |---|---:|
  | 无 | 0 / 24 |
  | `https://lxj070514.github.io/`（本站） | 7 / 24 |
  | `https://movie.douban.com/` | **24 / 24** |

  所以前端**不能**给它加 `referrerpolicy="no-referrer"`（曾经加过，整批 418 打回），
  但也**无法**把它伪装成豆瓣域名 —— `Referer` 是浏览器禁止脚本修改的头。
  这决定了豆瓣图床只适合当兜底：真正扛覆盖率的是 RT 与 TMDB 提供的 `poster_url`。
- **GitHub 会在仓库 60 天无活动后停掉定时任务**（官方文档原文："scheduled workflows are
  automatically disabled when no repository activity has occurred in 60 days"，
  且未定义何种行为算 activity）。本仓库每半月有一次 bot 数据提交，但**不能保证**这算活动，
  长期闲置后请到 Actions 页面确认 `Fetch Data and Deploy` 仍是 `active`，
  必要时手动 dispatch 一次或用 `PUT /actions/workflows/{id}/enable` 重新启用。

## 贡献

1. Fork 本仓库并新建分支
2. `pip install pytest pyyaml && python -m pytest` 确认 133 项全绿
3. **改动抓取逻辑前，先手动跑一次 `diagnose-sources.yml`** —— 豆瓣对不同 IP 段策略不同，
   本地能通不代表 CI 能通，反之亦然
4. 涉及匹配逻辑的改动请补测试——本项目最贵的 bug 都出在"匹配到了错误的电影"
5. 提 PR；`ci.yml` 会自动跑测试

## 许可与数据来源

代码采用 [MIT License](LICENSE)。

本项目**不托管任何影视内容**，仅聚合公开的评分元数据：

- 评分与影片元数据来自 [Rotten Tomatoes](https://www.rottentomatoes.com/) 的公开搜索接口
- 中文标题、评分、简介与短评来自 [豆瓣电影](https://movie.douban.com/) 的公开榜单与移动端接口
- 海报与简介来自 [TMDB](https://www.themoviedb.org/) API

各来源的数据与商标归其各自所有者。本项目为个人学习用途，请遵守各数据源的服务条款；
如需大规模抓取，请自行评估频率与合规性。

### 为什么是这三个源

选型时逐个评估过常见替代方案，结论是**当前三源已覆盖全部三个口径**，
再加源只会引入新的配额与匹配风险：

| 候选 | 评估结论 |
|---|---|
| **OMDb** | 官方页面未文档化番茄评分字段，且需要申请 key。它主要回 IMDb 分，而 IMDb 分不在本项目的三个口径里 —— 加了要新增第四列，还得再解决一遍"同名翻拍片"的匹配问题 |
| **Trakt / Letterboxd** | 面向"看过/想看"的社交数据，没有影评人 vs 观众的双口径评分 |
| **豆瓣 API v2** | 早已下线/收紧（社区多篇"豆瓣 API 不可用"的记录），且它不提供 Top250 榜单接口。本项目走的是榜单页与移动端 Rexxar 接口 |
| **RT 官方 API** | 面向合作方的商业授权，个人项目拿不到。本项目用的是其网页前端自己调用的 Algolia 索引（搜索键值就写在前端 JS 里、浏览器每次搜片都在用，不是破解出来的）；键值可用环境变量覆盖 |

真正需要"再找一个源"的场景只有一个：某部片在 RT 索引里确实不存在（如《七宗罪》
《你的名字》），这时站点**留空**而不是找别的分来凑 —— 混合口径会让"番茄分"这个
标签失去意义。

---

<div align="center">

**如果这个项目对你有帮助，欢迎点个 Star ⭐**

[在线演示](https://lxj070514.github.io/rottendouban/) · [报告问题](https://github.com/LXJ070514/rottendouban/issues)

</div>
