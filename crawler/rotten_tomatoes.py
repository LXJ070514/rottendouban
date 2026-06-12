"""
烂番茄爬虫 v3.0
====================================
- 参考 rottentomatoes_spider.py 优化
- safe_extract 统一安全抽取抽象
- 四层反检测 (uc → stealth → JS补丁 → 人类模拟)
- 三重回退评分提取 (JS → XPath → 正文正则)
- SSL 修复 + Cloudflare 增强检测
- 导演/演员精准分离 (Director:/Cast: 标签行)
- 数据清理 (_clean_text/_clean_name_list)
- 去除序号 + 隐藏空字段
"""
import os
import re
import sys
import ssl
import time
import random
import logging
import subprocess
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
)

from crawler.config import (
    RT_BASE_URL, RT_CATEGORIES, RT_MAX_MOVIES,
    HUMAN_DELAY_MIN, HUMAN_DELAY_MAX,
    HUMAN_SCROLL_PAUSE_MIN, HUMAN_SCROLL_PAUSE_MAX,
    CHROME_OPTIONS,
)

logger = logging.getLogger("rotten_tomatoes")

# 修复 SSL [ASN1: NOT_ENOUGH_DATA]
ssl._create_default_https_context = ssl._create_unverified_context

# 检测 undetected-chromedriver 是否可用
UC_AVAILABLE = False
try:
    import undetected_chromedriver as uc
    UC_AVAILABLE = True
except ImportError:
    pass

# 检测 selenium-stealth 是否可用
STEALTH_AVAILABLE = False
try:
    from selenium_stealth import stealth
    STEALTH_AVAILABLE = True
except ImportError:
    pass

# 请求头反检测
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.6099.130 Safari/537.36",
}


def _clean_text(text):
    """清理文本中的 SVG/CSS 噪声和多余空白"""
    if not text:
        return ''
    # 去掉 CSS 属性块 (如 .icon-bg{fill:var(--iconFill);})
    text = re.sub(r'\.[\w-]+\s*\{[^}]*\}', '', text)
    # 去掉 SVG/HTML 标签残留
    text = re.sub(r'<svg[^>]*>.*?</svg>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    # 去掉 CSS 变量 (如 var(--iconFill))
    text = re.sub(r'var\(--[\w-]+\)', '', text)
    # 去掉多余空白和分隔符
    text = re.sub(r'\s*,\s*,\s*', ', ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = text.strip().strip(',').strip()
    return text


def _clean_name_list(text):
    """清理人名列表，过滤掉 CSS/SVG 噪声项"""
    if not text:
        return ''
    text = _clean_text(text)
    items = [x.strip() for x in text.split(',') if x.strip()]
    cleaned = []
    for item in items:
        # 跳过包含 CSS/SVG 特征的项
        if any(k in item for k in [
            '{', '}', 'var(', 'fill:', 'transform:', '.icon',
            '.wrap', '--icon', 'stroke:', 'opacity:',
        ]):
            continue
        # 跳过过长的噪声项 (正常人名不会超过60字符)
        if len(item) > 60:
            continue
        cleaned.append(item)
    return ', '.join(cleaned)


class RottenTomatoesCrawler:
    """烂番茄爬虫 v3.0 — 整合参考代码优化"""

    def __init__(self):
        self.driver = None
        self.No = 0
        self._init_browser()

    # ==================== 统一安全抽取 ====================
    def safe_extract(self, xpath=None, attr=None, default='', multiple=False,
                     script=None, fallback_xpath=None, max_len=None):
        """
        统一安全提取元素内容 (参考 rottentomatoes_spider.py)
        :param xpath: XPath 表达式 (script存在时可省略)
        :param attr: 提取属性名(None则取text)
        :param default: 默认值
        :param multiple: 是否返回多个元素的连接字符串
        :param script: JS执行脚本(优先级最高)
        :param fallback_xpath: 备选XPath
        :param max_len: 截断长度
        """
        try:
            if script:
                result = self.driver.execute_script(script)
                if result:
                    return str(result).strip()[:max_len or 99999]

            if xpath is None:
                return default

            if multiple:
                elems = self.driver.find_elements(By.XPATH, xpath)
                val = ', '.join(
                    e.text.strip() if attr is None else (e.get_attribute(attr) or '')
                    for e in elems if (attr is None and e.text.strip()) or
                                      (attr is not None and e.get_attribute(attr))
                )
            else:
                elem = self.driver.find_element(By.XPATH, xpath)
                val = elem.text.strip() if attr is None else (elem.get_attribute(attr) or '')

            if not val and fallback_xpath:
                elem = self.driver.find_element(By.XPATH, fallback_xpath)
                val = elem.text.strip() if attr is None else (elem.get_attribute(attr) or '')

            return val[:max_len] if max_len and val else (val or default)

        except Exception:
            return default

    # ==================== 浏览器初始化 (反爬增强) ====================
    def _init_browser(self):
        """初始化浏览器驱动，优先使用 undetected-chromedriver 绕过 Cloudflare"""

        # 方案1: undetected-chromedriver (最强反检测)
        if UC_AVAILABLE:
            try:
                logger.info("使用 undetected-chromedriver 初始化...")
                options = uc.ChromeOptions()
                if CHROME_OPTIONS.get("headless"):
                    options.add_argument('--headless=new')
                options.add_argument('--disable-gpu')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                options.add_argument('--window-size=1920,1080')
                options.add_argument('--disable-blink-features=AutomationControlled')
                options.add_argument(f'--user-agent={HEADERS["User-Agent"]}')
                # 额外反检测 + SSL 参数
                options.add_argument('--disable-features=IsolateOrigins,site-per-process')
                options.add_argument('--disable-site-isolation-trials')
                options.add_argument('--disable-web-security')
                options.add_argument('--allow-running-insecure-content')
                options.add_argument('--ignore-certificate-errors')
                options.add_argument('--ignore-ssl-errors=yes')
                options.add_argument('--lang=en-US,en')

                # 方法1: 自动版本
                try:
                    self.driver = uc.Chrome(options=options)
                    self._apply_stealth_patches()
                    logger.info("undetected-chromedriver 初始化完成 (自动版本)")
                    return
                except Exception:
                    logger.debug("uc 自动版本失败，尝试手动指定版本")

                # 方法2: 读取本地 Chrome 版本号 (Windows + Linux)
                try:
                    chrome_main_version = None
                    if sys.platform == 'win32':
                        result = subprocess.run(
                            ['reg', 'query',
                             'HKEY_CURRENT_USER\\Software\\Google\\Chrome\\BLBeacon',
                             '/v', 'version'],
                            capture_output=True, text=True, timeout=5
                        )
                        version_match = re.search(r'(\d+)\.', result.stdout)
                        if version_match:
                            chrome_main_version = int(version_match.group(1))
                    else:
                        # Linux: google-chrome --version
                        result = subprocess.run(
                            ['google-chrome', '--version'],
                            capture_output=True, text=True, timeout=5
                        )
                        version_match = re.search(r'(\d+)\.', result.stdout)
                        if version_match:
                            chrome_main_version = int(version_match.group(1))
                    if chrome_main_version:
                        logger.info(f"检测到 Chrome 主版本: {chrome_main_version}")
                        self.driver = uc.Chrome(options=options,
                                                 version_main=chrome_main_version)
                        self._apply_stealth_patches()
                        logger.info("undetected-chromedriver 初始化完成 (手动版本)")
                        return
                except Exception as e2:
                    logger.debug(f"手动版本指定也失败: {e2}")

            except Exception as err:
                logger.warning(f"uc 初始化失败，回退到标准方案: {err}")

        # 方案2: 增强版 selenium-stealth
        if STEALTH_AVAILABLE:
            try:
                logger.info("使用 selenium-stealth 方案初始化...")
                chrome_options = Options()
                if CHROME_OPTIONS.get("headless"):
                    chrome_options.add_argument('--headless=new')
                chrome_options.add_argument('--disable-gpu')
                chrome_options.add_argument('--no-sandbox')
                chrome_options.add_argument('--window-size=1920,1080')
                chrome_options.add_argument('--disable-blink-features=AutomationControlled')
                chrome_options.add_argument(f'user-agent={HEADERS["User-Agent"]}')
                chrome_options.add_argument('--disable-dev-shm-usage')
                chrome_options.add_argument('--disable-features=IsolateOrigins,site-per-process')
                chrome_options.add_argument('--disable-site-isolation-trials')
                chrome_options.add_argument('--disable-web-security')
                chrome_options.add_argument('--allow-running-insecure-content')
                chrome_options.add_argument('--ignore-certificate-errors')
                chrome_options.add_argument('--ignore-ssl-errors=yes')
                chrome_options.add_argument('--lang=en-US,en')
                chrome_options.add_experimental_option('excludeSwitches',
                                                      ['enable-automation'])
                chrome_options.add_experimental_option('useAutomationExtension', False)
                # 模拟真实用户偏好
                prefs = {
                    'profile.default_content_setting_values': {
                        'images': 1,
                        'javascript': 1,
                    },
                }
                chrome_options.add_experimental_option('prefs', prefs)

                # 根据操作系统设置 stealth 参数
                _platform = 'Win32' if sys.platform == 'win32' else 'Linux x86_64'
                self.driver = webdriver.Chrome(options=chrome_options)
                stealth(self.driver,
                        languages=['en-US', 'en'],
                        vendor='Google Inc.',
                        platform=_platform,
                        webgl_vendor='Intel Inc.',
                        renderer='Intel Iris OpenGL Engine',
                        fix_hairline=True)
                self._apply_stealth_patches()
                logger.info("selenium-stealth 初始化完成")
                return
            except Exception as err:
                logger.warning(f"selenium-stealth 初始化失败: {err}")

        # 方案3: 普通 Chrome + JS 补丁
        try:
            logger.info("使用普通 Chrome + JS 反检测补丁初始化...")
            chrome_options = Options()
            if CHROME_OPTIONS.get("headless"):
                chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--ignore-certificate-errors')
            chrome_options.add_argument('--ignore-ssl-errors=yes')
            chrome_options.add_argument('--lang=en-US,en')

            self.driver = webdriver.Chrome(options=chrome_options)
            self._apply_stealth_patches()
            logger.info("普通 Chrome + JS 反检测补丁初始化完成")
        except Exception as err:
            logger.error(f"所有浏览器初始化方案均失败: {err}")
            raise

    def _apply_stealth_patches(self):
        """应用额外的反检测补丁 (参考 rottentomatoes_spider.py)"""
        # 覆盖 webdriver 属性
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined})")
        # 覆盖 plugins
        self.driver.execute_script("""
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
        """)
        # 覆盖 languages
        self.driver.execute_script("""
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)
        # 覆盖 chrome 对象
        self.driver.execute_script("""
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
        """)
        # 覆盖 permission
        self.driver.execute_script("""
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({state: Notification.permission}) :
                    originalQuery(parameters)
            );
        """)
        # 随机化 viewport 和 devicePixelRatio
        width = random.randint(1200, 1920)
        height = random.randint(800, 1080)
        self.driver.set_window_size(width, height)
        self.driver.execute_script(
            f"window.devicePixelRatio = {random.uniform(1.0, 2.0):.1f};")

    # ==================== Cloudflare 检测 (增强) ====================
    def _wait_for_cloudflare(self, timeout=30):
        """检测并等待 Cloudflare 挑战完成 (参考 rottentomatoes_spider.py)"""
        logger.info("等待 Cloudflare 验证...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                page_source = self.driver.page_source.lower()
                # 检测 Cloudflare 挑战页面特征
                if any(k in page_source for k in [
                    'checking your browser', 'cloudflare',
                    'cf-browser-verification', 'please wait',
                    'verifying you are human', 'ddos-guard'
                ]):
                    logger.debug("检测到 Cloudflare 挑战，等待验证...")
                    time.sleep(3)
                    continue
                # 检测是否被拦截
                if any(k in page_source for k in [
                    'access denied', 'blocked', 'captcha',
                    'recaptcha', 'please enable javascript', 'forbidden'
                ]):
                    logger.error("页面被拦截/封禁")
                    return False
                # 检测正常页面内容
                body_text = self.driver.find_element(
                    By.TAG_NAME, 'body').text.lower()
                if len(body_text) > 200 and 'rotten tomatoes' in page_source:
                    logger.info("Cloudflare 验证已通过")
                    return True
                time.sleep(2)
            except Exception:
                time.sleep(2)
        logger.warning("Cloudflare 等待超时，继续尝试")
        return True  # 继续尝试而非放弃

    # ==================== 人类行为模拟 ====================
    def _human_like_behavior(self):
        """模拟人类行为：随机滚动和鼠标移动 (参考 rottentomatoes_spider.py)"""
        try:
            scroll_y = random.randint(100, 500)
            self.driver.execute_script(f"window.scrollBy(0, {scroll_y});")
            time.sleep(random.uniform(0.5, 1.5))
            # 偶尔回滚
            if random.random() > 0.7:
                self.driver.execute_script(
                    f"window.scrollBy(0, -{scroll_y // 2});")
                time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass

    def _human_delay(self, min_s=None, max_s=None):
        """随机延时"""
        min_s = min_s if min_s is not None else HUMAN_DELAY_MIN
        max_s = max_s if max_s is not None else HUMAN_DELAY_MAX
        delay = random.uniform(min_s, max_s)
        time.sleep(delay)

    # ==================== 安全访问 ====================
    def _safe_get(self, url, retries=3):
        """安全访问页面，带 Cloudflare 检测和重试"""
        for attempt in range(retries):
            try:
                self.driver.get(url)
                # 检测 Cloudflare
                if not self._wait_for_cloudflare(timeout=20):
                    logger.warning(f"Cloudflare 验证失败, 重试 "
                                   f"{attempt+1}/{retries}")
                    self._human_delay(5, 10)
                    continue
                # 模拟人类行为
                self._human_like_behavior()
                self._human_delay(2, 5)
                self.scroll_to_load()
                return True
            except Exception as err:
                logger.error(f"页面访问失败, 重试 "
                             f"{attempt+1}/{retries}: {err}")
                self._human_delay(3, 6)
        # 保存错误页面供调试
        try:
            with open('error_page.html', 'w', encoding='utf-8') as f:
                f.write(self.driver.page_source)
        except Exception:
            pass
        return False

    # ==================== 滚动 & Load More ====================
    def scroll_to_load(self):
        """模拟滚动触发懒加载"""
        last_height = self.driver.execute_script(
            "return document.body.scrollHeight")
        for _ in range(5):
            self.driver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(HUMAN_SCROLL_PAUSE_MIN,
                                      HUMAN_SCROLL_PAUSE_MAX))
            new_height = self.driver.execute_script(
                "return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    def click_load_more(self):
        """点击 Load More 按钮"""
        clicked = 0
        for _ in range(5):
            try:
                buttons = self.driver.find_elements(
                    By.XPATH, '//*[contains(text(),"Load more")]')
                if not buttons or not buttons[0].is_enabled():
                    break
                self.driver.execute_script(
                    "arguments[0].scrollIntoView();", buttons[0])
                time.sleep(random.uniform(0.3, 0.8))
                buttons[0].click()
                clicked += 1
                logger.info(f"点击 Load More 第 {clicked} 次")
                self._human_delay(2, 4)
            except Exception:
                break
        return clicked

    # ==================== 获取电影链接 ====================
    def collect_movie_links(self, category):
        """访问分类列表页，收集所有电影详情链接"""
        url = category["url"]
        cat_name = category["name"]
        logger.info(f"访问分类: {cat_name} | {url}")

        if not self._safe_get(url):
            logger.error(f"分类 [{cat_name}] 页面访问失败")
            return []

        # 等待电影链接
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//a[contains(@href,"/m/")]'))
            )
        except Exception:
            logger.warning(f"分类 [{cat_name}] 等待电影链接超时")

        # 点击 Load More
        clicked = self.click_load_more()
        logger.info(f"分类 [{cat_name}] Load More 点击 {clicked} 次")

        # 再次滚动
        self.scroll_to_load()

        # 收集链接
        links = []
        seen = set()
        elements = self.driver.find_elements(
            By.XPATH, '//a[contains(@href,"/m/")]')
        for link in elements:
            href = link.get_attribute('href')
            if href and '/m/' in href and href not in seen:
                seen.add(href)
                links.append({
                    'url': href,
                    'name': link.text.strip()[:100],
                    'category': cat_name,
                })

        # 限制数量
        links = links[:RT_MAX_MOVIES]
        logger.info(f"分类 [{cat_name}] 发现 {len(links)} 部电影")
        return links

    # ==================== 评分提取 (三重回退) ====================
    @staticmethod
    def _extract_percent(text):
        """从文本中提取百分比数字，如 '78% Tomatometer' -> '78%'"""
        if not text:
            return ''
        m = re.search(r'(\d+)%?', str(text))
        return f"{m.group(1)}%" if m else ''

    def _search_body_text(self, pattern):
        """在页面 body 文本中用正则搜索"""
        try:
            text = self.driver.find_element(By.TAG_NAME, 'body').text
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(0) if m else ''
        except Exception:
            return ''

    def _extract_score(self, score_type):
        """三重回退评分提取: JS → XPath → 正文正则"""
        # 番茄影评人评分
        if score_type == "tomatometer":
            # 策略1: JS (score-board / data-qa / rt-text)
            tomato_js = self.safe_extract(script="""
                var sb=document.querySelector("score-board");
                if(sb)return sb.getAttribute("tomatometerscore")||sb.textContent;
                var t=document.querySelector('[data-qa="tomatometer"]');
                if(t)return t.textContent;
                var rt=document.querySelector('rt-text[slot="criticsScore"]');
                if(rt)return rt.textContent;return "";
            """)
            val = self._extract_percent(tomato_js)
            if val:
                return val

            # 策略2: XPath
            val = self._extract_percent(
                self.safe_extract('//*[@data-qa="tomatometer"]')
            )
            if val:
                return val

            # 策略3: 正文正则
            val = self._extract_percent(
                self._search_body_text(
                    r'(\d+)\s*%\s*(?:tomatometer|rotten|fresh|certified)')
            )
            if val:
                return val

            # 最终回退: 找任何百分比数字
            val = self._extract_percent(self._search_body_text(r'(\d+)%'))
            return val  # 可能为空

        # 观众评分 (Popcornmeter)
        if score_type == "audience":
            # 策略1: JS
            audience_js = self.safe_extract(script="""
                var sb=document.querySelector("score-board");
                if(sb)return sb.getAttribute("audiencescore")||"";
                var p=document.querySelector('[data-qa="popcornmeter"]');
                if(p)return p.textContent;
                var rt=document.querySelector('rt-text[slot="audienceScore"]');
                if(rt)return rt.textContent;return "";
            """)
            val = self._extract_percent(audience_js)
            if val:
                return val

            # 策略2: XPath
            val = self._extract_percent(
                self.safe_extract(
                    '//*[@data-qa="popcornmeter"] | '
                    '//*[contains(@class,"audience-score")]')
            )
            if val:
                return val

            # 策略3: 正文正则
            val = self._extract_percent(
                self._search_body_text(
                    r'(\d+)\s*%\s*(?:popcorn|audience|verified)')
            )
            return val

        return ''

    # ==================== 解析详情页 ====================
    def parse_detail_page(self, movie_info):
        """解析电影详情页，提取完整信息 (参考 rottentomatoes_spider.py)"""
        url = movie_info['url']
        category_name = movie_info['category']

        # 访问详情页
        if not self._safe_get(url):
            logger.error(f"详情页访问失败: {url}")
            return None

        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, 'h1'))
            )
        except Exception:
            logger.warning(f"详情页加载超时: {url}")

        self.No += 1

        # ---- 1. 标题 ----
        mTitle = self.safe_extract('//h1') or movie_info.get('name', 'Unknown')
        mTitle = _clean_text(re.sub(r'^#\d+\s*', '', mTitle))  # 去除序号

        # ---- 2. 原标题 ----
        mOriginalTitle = self.safe_extract(script="""
            var el=document.querySelector('[data-qa="movie-original-title"]');
            if(el)return el.textContent.trim();
            var h1=document.querySelector('h1');
            if(h1){
                var spans=h1.querySelectorAll('span');
                for(var i=0;i<spans.length;i++){
                    var t=spans[i].textContent.trim();
                    if(t&&t!==h1.firstChild.textContent.trim())return t;
                }
            }
            return '';
        """)
        if not mOriginalTitle:
            mOriginalTitle = mTitle

        # ---- 3. 评级 (MPAA) ----
        mRating = self.safe_extract('//span[contains(@class,"rating")]')
        if not mRating:
            mRating = self.safe_extract(
                '//meta[@itemprop="contentRating"]', attr='content')
        if not mRating:
            # 从页面文本中提取常见评级
            page_text = self.driver.find_element(By.TAG_NAME, 'body').text
            for r in ['G', 'PG-13', 'PG', 'R', 'NC-17', 'NR', 'Unrated']:
                if re.search(rf'\b{r}\b', page_text):
                    mRating = r
                    break
        valid_ratings = {'G', 'PG', 'PG-13', 'R', 'NC-17', 'NR',
                         'Unrated', 'Not Rated'}
        if mRating and mRating not in valid_ratings:
            mRating = ''

        # ---- 4. 评分 (三重回退) ----
        mTomatoScore = self._extract_score("tomatometer")
        mAudienceScore = self._extract_score("audience")

        # ---- 5. 影评人共识 ----
        mCriticsConsensus = self.safe_extract(
            '//*[@data-qa="critics-consensus"]//p | '
            '//div[contains(@class,"critics-consensus")]//p | '
            '//p[contains(@class,"critics-consensus")]'
        )
        if not mCriticsConsensus:
            mCriticsConsensus = self.safe_extract(script="""
                var cc=document.querySelector('[data-qa="critics-consensus"]');
                if(cc)return cc.textContent.trim();
                var p=document.querySelector("p.critics-consensus, .consensus");
                if(p)return p.textContent.trim();return "";
            """)
        mCriticsConsensus = _clean_text(mCriticsConsensus) if mCriticsConsensus else ''

        # ---- 6. 类型 ----
        mGenre = _clean_text(self.safe_extract(script="""
            var items=[];
            document.querySelectorAll('a[href*="genres"]').forEach(function(a){
                var t=a.textContent.trim();
                if(t&&!t.includes('{'))items.push(t)
            });
            if(items.length)return items.join(', ');
            var gs=document.querySelectorAll('span[itemprop="genre"]');
            gs.forEach(function(g){var t=g.textContent.trim();if(t)items.push(t)});
            return items.join(', ');
        """))
        if not mGenre:
            mGenre = _clean_text(self.safe_extract(
                '//span[@itemprop="genre"] | '
                '//a[contains(@href,"/browse/movies_at_home/genres:")] | '
                '//a[contains(@href,"/browse/movies_in_theaters/genres:")]',
                multiple=True
            ))

        # ---- 7. 导演 (精准从 Director: 行提取) ----
        mDirector = _clean_name_list(self.safe_extract(script="""
            function getDir(){
                var rows=document.querySelectorAll(
                    'div[data-qa="movie-info-section"], .info li, '
                    '#movie-info li, dl.info dt, dl.info dd, '
                    '[data-qa="movie-info"]');
                for(var i=0;i<rows.length;i++){
                    var txt=rows[i].textContent||'';
                    if(txt.match(/^\\s*Director/i)){
                        var links=rows[i].querySelectorAll('a');
                        var names=[];
                        links.forEach(function(a){
                            var t=a.textContent.trim();
                            if(t&&!t.includes('{')&&t.length<60)names.push(t)
                        });
                        if(names.length)return names.join(', ');
                    }
                }
                var dq=document.querySelector('[data-qa="movie-info-director"]');
                if(dq){
                    var links=dq.querySelectorAll('a');
                    var names=[];
                    links.forEach(function(a){
                        var t=a.textContent.trim();
                        if(t&&!t.includes('{')&&t.length<60)names.push(t)
                    });
                    if(names.length)return names.join(', ')
                }
                return '';
            }
            return getDir();
        """))
        if not mDirector:
            mDirector = _clean_name_list(self.safe_extract(
                '//a[@data-qa="movie-info-director"] | '
                '//span[@itemprop="director"]//span[@itemprop="name"]',
                multiple=True
            ))

        # ---- 8. 演员 (精准从 Cast: 行提取) ----
        mActors = _clean_name_list(self.safe_extract(script="""
            function getCast(){
                var rows=document.querySelectorAll(
                    'div[data-qa="movie-info-section"], .info li, '
                    '#movie-info li, dl.info dt, dl.info dd, '
                    '[data-qa="movie-info"]');
                for(var i=0;i<rows.length;i++){
                    var txt=rows[i].textContent||'';
                    if(txt.match(/^\\s*(Cast|Starring|Actor)/i)){
                        var links=rows[i].querySelectorAll('a');
                        var names=[];
                        links.forEach(function(a){
                            var t=a.textContent.trim();
                            if(t&&!t.includes('{')&&t.length<60)names.push(t)
                        });
                        if(names.length)return names.slice(0,8).join(', ');
                    }
                }
                var dq=document.querySelector('[data-qa="movie-info-cast"]');
                if(dq){
                    var links=dq.querySelectorAll('a');
                    var names=[];
                    links.forEach(function(a){
                        var t=a.textContent.trim();
                        if(t&&!t.includes('{')&&t.length<60)names.push(t)
                    });
                    if(names.length)return names.slice(0,8).join(', ')
                }
                var cast=document.querySelectorAll(
                    '.cast-and-crew a[data-qa], .cast-item a');
                var names=[];
                cast.forEach(function(a){
                    var t=a.textContent.trim();
                    if(t&&!t.includes('{')&&t.length<60)names.push(t)
                });
                return names.slice(0,8).join(', ');
            }
            return getCast();
        """))
        if not mActors:
            mActors = _clean_name_list(self.safe_extract(
                '//a[@data-qa="movie-info-cast"] | '
                '//span[@itemprop="actor"]//span[@itemprop="name"]',
                multiple=True
            ))

        # ---- 9. 剧情简介 ----
        mSynopsis = _clean_text(self.safe_extract(script="""
            var sb=document.querySelector("score-board");
            if(sb){var d=sb.getAttribute("description");if(d)return d.trim();}
            var c=document.querySelector("media-scorecard");
            if(c){var s=c.querySelector('[slot="description"]');
                  if(s)return s.textContent.trim();}
            var p=document.querySelector(
                '#movieSynopsis p, .synopsis p, [data-qa="synopsis"] p');
            if(p)return p.textContent.trim();return "";
        """))
        if not mSynopsis:
            mSynopsis = _clean_text(self.safe_extract(
                '//div[@id="movieSynopsis"]//p | '
                '//*[contains(text(),"Synopsis")]/following-sibling::p | '
                '//div[contains(@class,"synopsis")]//p',
                max_len=500
            ))

        # ---- 10. 日期/片长 ----
        mReleaseDate = self.safe_extract(
            '//time[@itemprop="datePublished"] | '
            '//span[@data-qa="movie-info-release-date"] | '
            '//div[contains(text(),"Release Date")]/following-sibling::*'
        )
        if not mReleaseDate:
            mReleaseDate = self.safe_extract(script="""
                var d=document.querySelector(
                    "time[itemprop='datePublished'], "
                    "[data-qa='movie-info-release-date']");
                if(d)return d.textContent.trim()||d.getAttribute("datetime");
                var t=document.body.innerText.match(
                    /Release Date[s]?:\\s*([A-Za-z]+\\s+\\d{1,2},?\\s*\\d{4})/i);
                return t?t[1].trim():"";
            """)

        mRuntime = self.safe_extract(
            '//time[@itemprop="duration"] | '
            '//span[@data-qa="movie-info-runtime"] | '
            '//span[contains(text(),"Runtime")]/following-sibling::*'
        )
        if not mRuntime:
            mRuntime = self.safe_extract(script="""
                var r=document.querySelector(
                    "time[itemprop='duration'], "
                    "[data-qa='movie-info-runtime']");
                if(r)return r.textContent.trim()||r.getAttribute("datetime");
                var t=document.body.innerText.match(
                    /(\\d+h\\s*\\d+m|\\d+\\s*hr[s]?\\s*\\d+\\s*min[s]?)/i);
                return t?t[1].trim():"";
            """)

        # ---- 11. 海报 ----
        poster_url = self.safe_extract('//img[@itemprop="image"]', attr='src')
        if not poster_url:
            poster_url = self.safe_extract(
                '//meta[@property="og:image"]', attr='content')
        if not poster_url:
            poster_url = self.safe_extract(script="""
                var img=document.querySelector(
                    "img[src*='resizing'], img[src*='rtactor'], "
                    "poster-img, img[itemprop='image']");
                if(img)return img.src;
                var meta=document.querySelector("meta[property='og:image']");
                if(meta)return meta.content;return "";
            """)

        # ---- 12. 年份 ----
        year = None
        year_match = re.search(r'\b(19|20)\d{2}\b', url)
        if year_match:
            year = int(year_match.group())

        # ---- 13. 日志输出 ----
        logger.info(f"[{self.No}] {mTitle[:40]} | "
                     f"🍅{mTomatoScore or '-'} "
                     f"👥{mAudienceScore or '-'} "
                     f"| {mGenre[:15]}")

        # 调试: 首部电影保存页面源码
        if self.No == 1:
            try:
                with open('debug_first_page.html', 'w',
                          encoding='utf-8') as f:
                    f.write(self.driver.page_source)
                logger.debug("已保存首部电影调试文件")
            except Exception:
                pass

        # ---- 14. 组装数据 ----
        movie_data = {
            "rt_url": url,
            "title": mTitle,
            "original_title": mOriginalTitle,
            "year": year,
            "rating": mRating,
            "tomatometer": mTomatoScore,
            "audience_score": mAudienceScore,
            "genre": mGenre,
            "director": mDirector,
            "cast": mActors,
            "critics_consensus": mCriticsConsensus,
            "synopsis": mSynopsis,
            "release_date": mReleaseDate,
            "runtime": mRuntime,
            "poster_url": poster_url,
            "poster_local": "",
            "category": category_name,
        }
        return movie_data

    # ==================== 爬取所有分类 ====================
    def crawl_all(self):
        """遍历分类，爬取电影"""
        all_movies = []
        for category in RT_CATEGORIES:
            movie_links = self.collect_movie_links(category)
            if not movie_links:
                logger.info(f"分类 [{category['name']}] 无电影，跳过")
                continue
            for movie_info in movie_links:
                try:
                    movie_data = self.parse_detail_page(movie_info)
                    if movie_data:
                        all_movies.append(movie_data)
                        self._human_delay(2, 5)
                except Exception as err:
                    logger.error(f"解析详情页出错 "
                                 f"[{movie_info.get('name','?')[:30]}]: {err}")
            logger.info(f"分类 [{category['name']}] 完成")
            self._human_delay(3, 8)
        logger.info(f"爬取结束！共爬取 {len(all_movies)} 部电影")
        return all_movies

    def close(self):
        """关闭浏览器"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            logger.info("浏览器驱动已关闭")