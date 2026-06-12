"""
豆瓣电影爬虫模块 v3.0
============================
- 参考 douban_spider.py 优化
- Selenium 访问豆瓣搜索页 + 详情页，抓取完整电影信息
- 与烂番茄共用同一个 driver，避免额外浏览器开销
- 返回: 评分、简介、类型、导演、演员、海报、评论数、中文名
- 指数退避重试
- CI 环境自动缩短延迟，支持单部电影超时
"""
import os
import re
import time
import random
import logging
import urllib.parse

from selenium.webdriver.common.by import By

from crawler.config import (
    DOUBAN_BASE_URL, DOUBAN_SEARCH_URL,
    IMAGE_RETRY_MAX, IMAGE_RETRY_BACKOFF_FACTOR,
)

logger = logging.getLogger("douban")


class DoubanMatcher:
    """豆瓣电影匹配器 v3.0 — Selenium 搜索 + 抓取完整详情"""

    def __init__(self, driver=None):
        self.driver = driver
        self._cache = {}
        # CI 环境减少延迟 (不需要模拟人类行为)
        ci_env = os.environ.get("CI_ENV", "").lower() in ("true", "1", "yes")
        if ci_env:
            self._search_delay = (0.5, 1.0)
            self._detail_delay = (1.0, 2.0)
        else:
            self._search_delay = (1.5, 3.0)
            self._detail_delay = (2.0, 4.0)

    def _build_search_url(self, title):
        query = title.strip()
        return (f'https://movie.douban.com/subject_search?'
                f'search_text={urllib.parse.quote(query)}')

    def find_movie(self, title, release_date=''):
        """
        匹配豆瓣电影并抓取详情
        :return: dict with keys: url, score, vote_count, synopsis,
                 genre, director, actors, poster, chinese_title
        """
        cache_key = f"{title}|{release_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = {}
        try:
            if self.driver:
                result = self._selenium_full_fetch(title, release_date)
        except Exception as err:
            logger.debug(f"豆瓣搜索异常 [{title[:30]}]: {err}")

        self._cache[cache_key] = result
        return result

    def _selenium_full_fetch(self, title, release_date=''):
        """Selenium 搜索 + 抓取完整详情 (参考 douban_spider.py)"""
        try:
            # 1. 搜索豆瓣
            search_url = self._build_search_url(title)
            self.driver.get(search_url)
            time.sleep(random.uniform(*self._search_delay))

            # 获取搜索结果链接
            link_elems = self.driver.find_elements(
                By.XPATH,
                '//div[@id="root"]//a[contains(@href,"/subject/")]'
            )
            url = ''
            if link_elems:
                url = link_elems[0].get_attribute('href')

            if not url:
                # 回退: 尝试其他搜索结果容器
                link_elems = self.driver.find_elements(
                    By.XPATH,
                    '//a[contains(@href,"/subject/")]'
                )
                if link_elems:
                    url = link_elems[0].get_attribute('href')

            if not url:
                logger.debug(f"豆瓣搜索未找到: {title[:30]}")
                return {}

            # 2. 访问豆瓣详情页
            self.driver.get(url)
            time.sleep(random.uniform(*self._detail_delay))

            data = {'url': url, 'title': title}

            # ---- 评分 (多策略) ----
            data['score'] = self._extract_score()
            data['vote_count'] = self._extract_vote_count()

            # ---- 中文标题 ----
            data['chinese_title'] = self._safe_text(
                '//span[@property="v:itemreviewed"]'
            )

            # ---- 类型 ----
            data['genre'] = self._extract_list(
                '//span[@property="v:genre"]'
            )

            # ---- 导演 ----
            data['director'] = self._extract_list(
                '//a[@rel="v:directedBy"]'
            )

            # ---- 演员 ----
            data['actors'] = self._extract_list(
                '//a[@rel="v:starring"]', limit=6
            )

            # ---- 简介 ----
            data['synopsis'] = self._safe_text(
                '//span[@property="v:summary"]'
            )

            # ---- 海报 ----
            data['poster'] = self._safe_attr(
                '//img[@rel="v:image"]', attr='src'
            )

            # ---- 上映日期 ----
            data['release_date'] = self._safe_text(
                '//span[@property="v:initialReleaseDate"]'
            )

            # ---- 片长 ----
            data['runtime'] = self._safe_text(
                '//span[@property="v:runtime"]'
            )

            logger.info(f"  豆瓣详情: {title[:25]} -> "
                        f"评分:{data.get('score', '-')} "
                        f"类型:{data.get('genre', '')[:30]}")
            return data

        except Exception as err:
            logger.debug(f"Selenium豆瓣搜索失败: {err}")
            return {}

    def _extract_score(self):
        """提取豆瓣评分 (三策略回退)"""
        try:
            # 方式1: 评分元素
            elems = self.driver.find_elements(
                By.XPATH,
                '//strong[contains(@class,"ll")]'
                '|//span[@property="v:average"]'
            )
            for e in elems:
                t = e.text.strip()
                if t and re.match(r'\d+\.?\d*', t):
                    return t
            # 方式2: meta
            try:
                meta = self.driver.find_element(
                    By.XPATH, '//meta[@property="video:rating"]'
                )
                v = meta.get_attribute('content') or ''
                if v:
                    return v
            except Exception:
                pass
            # 方式3: JS
            try:
                val = self.driver.execute_script(
                    'var e=document.querySelector('
                    '"strong.ll, span[property=v\\3a average]");'
                    'if(e)return e.textContent.trim();return "";'
                )
                if val and re.match(r'\d+\.?\d*', val):
                    return val
            except Exception:
                pass
        except Exception:
            pass
        return ''

    def _extract_vote_count(self):
        """提取评分人数"""
        try:
            elem = self.driver.find_element(
                By.XPATH,
                '//span[@property="v:votes"]'
                '|//span[contains(@class,"rating_people")]//span'
            )
            t = elem.text.strip().replace(',', '').replace(' ', '')
            if t.isdigit():
                return t
        except Exception:
            pass
        return ''

    def _extract_list(self, xpath, limit=None):
        """提取列表类型数据 (类型、导演、演员)"""
        try:
            elems = self.driver.find_elements(By.XPATH, xpath)
            items = [e.text.strip() for e in elems if e.text.strip()]
            if limit:
                items = items[:limit]
            return ', '.join(items)
        except Exception:
            return ''

    def _safe_text(self, xpath):
        """安全提取文本"""
        try:
            elem = self.driver.find_element(By.XPATH, xpath)
            return elem.text.strip()
        except Exception:
            return ''

    def _safe_attr(self, xpath, attr='src'):
        """安全提取属性"""
        try:
            elem = self.driver.find_element(By.XPATH, xpath)
            return elem.get_attribute(attr) or ''
        except Exception:
            return ''

    # ==================== 对外接口 (适配 main.py) ====================
    def match_and_fetch(self, title, original_title=None, timeout=60):
        """自动匹配豆瓣并抓取完整数据 (适配 main.py 的调用方式)
        timeout: 单部电影匹配最大秒数, 超时返回空结果
        """
        start = time.time()
        # 优先用英文原名搜索，再用中文标题
        search_title = original_title or title
        douban_info = self.find_movie(search_title, '')

        if not douban_info and (time.time() - start) < timeout:
            # 回退: 用标题搜索
            if original_title and original_title != title:
                douban_info = self.find_movie(title, '')

        if time.time() - start > timeout:
            logger.warning(f"豆瓣匹配超时 ({time.time()-start:.1f}s>{timeout}s): {title[:30]}")
            return {
                "douban_id": "", "douban_url": "", "douban_score": "",
                "douban_vote_count": "", "douban_title": "", "douban_genre": "",
                "douban_director": "", "douban_cast": "", "douban_synopsis": "",
                "douban_poster": "",
            }

        if douban_info:
            douban_id = ''
            id_match = re.search(r'/subject/(\d+)/', douban_info.get('url', ''))
            if id_match:
                douban_id = id_match.group(1)

            return {
                "douban_id": douban_id,
                "douban_url": douban_info.get("url", ""),
                "douban_score": douban_info.get("score", ""),
                "douban_vote_count": douban_info.get("vote_count", ""),
                "douban_title": douban_info.get("chinese_title", ""),
                "douban_genre": douban_info.get("genre", ""),
                "douban_director": douban_info.get("director", ""),
                "douban_cast": douban_info.get("actors", ""),
                "douban_synopsis": douban_info.get("synopsis", ""),
                "douban_poster": douban_info.get("poster", ""),
            }

        return {
            "douban_id": "",
            "douban_url": "",
            "douban_score": "",
            "douban_vote_count": "",
            "douban_title": "",
            "douban_genre": "",
            "douban_director": "",
            "douban_cast": "",
            "douban_synopsis": "",
            "douban_poster": "",
        }