/**
 * RottenDouban v2.1 - 聚焦评分展示的前端应用
 */
(function() {
    'use strict';

    // 转义必须覆盖引号：这些值来自豆瓣 / RT，且大量被插进 value="..." 这类属性里，
    // 用 div.innerHTML 的老写法只转义 &<>，一个双引号就能逃出属性
    const ESC_MAP = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};

    function esc(str) {
        return String(str ?? '').replace(/[&<>"']/g, c => ESC_MAP[c]);
    }

    function sanitizeUrl(url) {
        if (!url) return '';
        try {
            const p = new URL(url, window.location.origin);
            if (['http:', 'https:'].includes(p.protocol)) return p.href;
        } catch(e) {}
        return '';
    }

    function debounce(fn, delay) {
        let t;
        return function(...args) {
            clearTimeout(t);
            t = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    let movies = [];

    // ===== Theme =====
    function initTheme() {
        const saved = localStorage.getItem('rd-theme') || 'dark';
        document.documentElement.setAttribute('data-theme', saved);
    }

    function toggleTheme() {
        const cur = document.documentElement.getAttribute('data-theme');
        const next = cur === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('rd-theme', next);
    }

    // ===== Load Data =====
    async function loadData() {
        const grid = document.getElementById('movie-grid');
        grid.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div><div class="loading-text">加载电影数据...</div></div>';
        try {
            const resp = await fetch('data/movies.json');
            if (!resp.ok) throw new Error('数据加载失败');
            movies = await resp.json();
            populateFilters();
            renderMovies();
        } catch(e) {
            grid.innerHTML = '<div class="no-data">数据加载失败，请稍后重试</div>';
        }
    }

    // ===== Filters =====
    function fillSelect(id, values, allLabel) {
        const sel = document.getElementById(id);
        sel.innerHTML = `<option value="">${allLabel}</option>` +
            values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    }

    function populateFilters() {
        const genres = new Set();
        movies.forEach(m => {
            (m.douban_genre || m.genre || '').split(/[,\/]/).forEach(g => {
                const t = g.trim();
                if (t) genres.add(t);
            });
        });
        fillSelect('filter-genre', [...genres].sort(), '全部类型');

        // 分类必须来自数据本身：此前是硬编码的，数据换源后选项与真实 category 对不上，
        // 选唯一的那一项会筛出空列表
        const categories = [...new Set(movies.map(m => m.category).filter(Boolean))].sort();
        fillSelect('filter-category', categories, '全部分类');
    }

    // ===== Search =====
    function searchMovies(query) {
        if (!query) return movies;
        const q = query.toLowerCase().trim();
        const fields = ['title', 'original_title', 'douban_title',
                        'douban_genre', 'genre', 'director', 'cast'];
        return movies.filter(m => fields.some(f =>
            (m[f] || '').toLowerCase().includes(q)));
    }

    // ===== Filter & Sort =====
    function filterAndSort(list) {
        const category = document.getElementById('filter-category').value;
        const genre = document.getElementById('filter-genre').value;
        const sort = document.getElementById('filter-sort').value;

        // 复制后再排序：直接 sort 会打乱全局 movies 的顺序
        let filtered = list.slice();
        if (category) filtered = filtered.filter(m => m.category === category);
        if (genre) filtered = filtered.filter(m =>
            (m.douban_genre || '').includes(genre) || (m.genre || '').includes(genre)
        );

        filtered.sort((a, b) => (b[sort] > 0 ? b[sort] : -1) - (a[sort] > 0 ? a[sort] : -1));
        return filtered;
    }

    // ===== Render =====
    function renderMovies() {
        const grid = document.getElementById('movie-grid');
        const q = document.getElementById('search-input').value;
        const filtered = filterAndSort(q ? searchMovies(q) : movies);

        if (!filtered.length) {
            grid.innerHTML = '<div class="no-data">没有找到匹配的电影</div>';
            return;
        }

        grid.innerHTML = filtered.map(m => renderCard(m)).join('');
        grid.querySelectorAll('.movie-card').forEach(card => {
            card.addEventListener('click', () => {
                const movie = movies.find(m => m.id == card.dataset.id);
                if (movie) showDetail(movie);
            });
        });
    }

    // Get poster URL: try real URLs first, fallback to picsum unique placeholder
    function getPoster(m) {
        const rtUrl = sanitizeUrl(m.poster_url);
        const dbUrl = sanitizeUrl(m.douban_poster);
        // If we have a real RT image, use it; if douban, use with no-referrer
        if (rtUrl && !rtUrl.includes('example')) return { url: rtUrl, referrer: false };
        if (dbUrl) return { url: dbUrl, referrer: true };
        // Fallback: unique placeholder per movie via picsum
        return { url: `https://picsum.photos/seed/movie${m.id}/400/600`, referrer: false };
    }

    function renderCard(m) {
        const poster = getPoster(m);
        const posterHtml = `<img class="card-poster" src="${poster.url}" alt="${esc(m.title)}" loading="lazy" ${poster.referrer ? 'referrerpolicy="no-referrer"' : ''} onerror="this.src='https://picsum.photos/seed/fallback${m.id}/400/600'">`;

        const pills = [];
        if (m.tomatometer >= 0) {
            const cls = m.tomatometer >= 60 ? 'rt-fresh' : 'rt-rotten';
            const icon = m.tomatometer >= 60 ? '🍅' : '🟢';
            pills.push(`<span class="score-pill ${cls}"><span class="score-pill-icon">${icon}</span>${m.tomatometer}%</span>`);
        }
        if (m.audience_score >= 0) {
            const cls = m.audience_score >= 60 ? 'audience-pop' : 'audience-splat';
            const icon = m.audience_score >= 60 ? '🍿' : '🎬';
            pills.push(`<span class="score-pill ${cls}"><span class="score-pill-icon">${icon}</span>${m.audience_score}%</span>`);
        }
        if (m.douban_score > 0) {
            pills.push(`<span class="score-pill douban"><span class="score-pill-icon">⭐</span>${m.douban_score}</span>`);
        }

        const genreStr = m.douban_genre || m.genre || '';
        const genres = genreStr.split(/[,\/]/).map(g => g.trim()).filter(Boolean).slice(0, 4);
        const genreHtml = genres.map(g => `<span class="genre-tag">${esc(g)}</span>`).join('');

        const year = m.year || '';
        const runtime = m.runtime || '';
        // runtime 是外部 API 给的字符串，必须转义后再进 innerHTML
        const metaParts = [];
        if (year) metaParts.push(esc(year));
        if (runtime) metaParts.push(esc(runtime));
        const metaHtml = metaParts.join('<span class="card-meta-sep">·</span>');

        const catHtml = m.category ? `<span class="card-category">${esc(m.category)}</span>` : '';
        // 加权分是 0–100，与豆瓣的 0–10 并排展示时容易被误读，用 title 标明量纲
        const wsHtml = m.weighted_score > 0
            ? `<span class="card-weighted" title="综合加权分（满分 100）">${m.weighted_score.toFixed(1)}</span>`
            : '';

        return `<div class="movie-card" data-id="${m.id}">
            ${catHtml}${wsHtml}
            <div class="card-poster-wrap">
                ${posterHtml}
                <div class="card-score-overlay">${pills.join('')}</div>
            </div>
            <div class="card-body">
                <div class="card-title">${esc(m.douban_title || m.title)}</div>
                ${m.douban_title && m.douban_title !== m.title ? `<div class="card-subtitle">${esc(m.title)}</div>` : ''}
                <div class="card-meta">${metaHtml}</div>
                <div class="card-genres">${genreHtml}</div>
            </div>
        </div>`;
    }

    // ===== Detail Modal (完整展示所有信息) =====
    function showDetail(m) {
        const overlay = document.getElementById('modal-overlay');
        const body = document.getElementById('modal-body');

        const poster = getPoster(m);
        const posterHtml = `<img class="detail-poster" src="${poster.url}" alt="${esc(m.title)}" ${poster.referrer ? 'referrerpolicy="no-referrer"' : ''} onerror="this.src='https://picsum.photos/seed/fallback${m.id}/300/420'">`;

        // Score circles
        const circles = [];
        if (m.tomatometer >= 0) {
            circles.push(`<div class="score-circle rt-c"><div class="score-circle-val">${m.tomatometer}%</div><div class="score-circle-lbl">影评人</div></div>`);
        }
        if (m.audience_score >= 0) {
            circles.push(`<div class="score-circle aud-c"><div class="score-circle-val">${m.audience_score}%</div><div class="score-circle-lbl">观众</div></div>`);
        }
        if (m.douban_score > 0) {
            circles.push(`<div class="score-circle db-c"><div class="score-circle-val">${m.douban_score}</div><div class="score-circle-lbl">豆瓣</div></div>`);
        }
        if (m.weighted_score > 0) {
            circles.push(`<div class="score-circle ws-c"><div class="score-circle-val">${m.weighted_score.toFixed(1)}</div><div class="score-circle-lbl">加权 /100</div></div>`);
        }

        // ====== 烂番茄信息区 ======
        const rtMeta = [];
        if (m.rating) rtMeta.push(['评级', m.rating]);
        if (m.genre) rtMeta.push(['类型', m.genre]);
        if (m.director) rtMeta.push(['导演', m.director]);
        if (m.writers) rtMeta.push(['编剧', m.writers]);
        if (m.cast) rtMeta.push(['演员', m.cast]);
        if (m.runtime) rtMeta.push(['片长', m.runtime]);
        if (m.year) rtMeta.push(['年份', m.year]);
        if (m.release_date) rtMeta.push(['上映日期', m.release_date]);

        const rtMetaHtml = rtMeta.map(([l, v]) =>
            `<div class="meta-row"><span class="meta-label">${l}</span><span class="meta-value">${esc(v)}</span></div>`
        ).join('');

        // RT Synopsis (英文)
        let rtSynopsisHtml = '';
        if (m.synopsis) {
            rtSynopsisHtml = `<div class="detail-synopsis rt-synopsis"><div class="detail-synopsis-label">🍅 Rotten Tomatoes 简介</div>${esc(m.synopsis)}</div>`;
        }

        // ====== 豆瓣信息区 ======
        // 豆瓣只用搜索接口，拿不到导演/编剧/演员/简介/短评，这里只渲染确实存在的字段
        const dbMeta = [];
        if (m.douban_title) dbMeta.push(['中文名', m.douban_title]);
        if (m.douban_genre) dbMeta.push(['豆瓣类型', m.douban_genre]);
        if (m.douban_score > 0) dbMeta.push(['豆瓣评分', `${m.douban_score} / 10`]);
        if (m.douban_vote_count > 0) dbMeta.push(['评分人数', `${Number(m.douban_vote_count).toLocaleString()} 人`]);

        const dbMetaHtml = dbMeta.map(([l, v]) =>
            `<div class="meta-row"><span class="meta-label">${l}</span><span class="meta-value">${esc(v)}</span></div>`
        ).join('');

        // Links
        const links = [];
        if (m.rt_url) links.push(`<a class="detail-link rt-link" href="${sanitizeUrl(m.rt_url)}" target="_blank" rel="noopener noreferrer">🍅 烂番茄</a>`);
        if (m.douban_url) links.push(`<a class="detail-link db-link" href="${sanitizeUrl(m.douban_url)}" target="_blank" rel="noopener noreferrer">⭐ 豆瓣</a>`);

        // Assemble - separate RT and Douban sections clearly
        let rtSection = '';
        if (rtMetaHtml || rtSynopsisHtml) {
            rtSection = `<div class="detail-section rt-section">
                <div class="section-header"><h3>🍅 Rotten Tomatoes</h3></div>
                ${rtMetaHtml ? `<div class="detail-meta">${rtMetaHtml}</div>` : ''}
                ${rtSynopsisHtml}
            </div>`;
        }

        let dbSection = '';
        if (dbMetaHtml) {
            dbSection = `<div class="detail-section db-section">
                <div class="section-header"><h3>⭐ 豆瓣</h3></div>
                <div class="detail-meta">${dbMetaHtml}</div>
            </div>`;
        }

        body.innerHTML = `
            <div class="detail-hero">
                <div class="detail-poster-wrap">${posterHtml}</div>
                <div class="detail-info">
                    <div class="detail-title">${esc(m.douban_title || m.title)}</div>
                    ${m.douban_title && m.douban_title !== m.title ? `<div class="detail-subtitle">${esc(m.title)}</div>` : ''}
                    <div class="score-circles">${circles.join('')}</div>
                    <div class="detail-links">${links.join('')}</div>
                </div>
            </div>
            ${rtSection}
            ${dbSection}
        `;

        overlay.style.display = 'block';
        document.body.style.overflow = 'hidden';
    }

    function closeDetail() {
        document.getElementById('modal-overlay').style.display = 'none';
        document.body.style.overflow = '';
    }

    // ===== Event Bindings =====
    function bindEvents() {
        document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

        const debouncedSearch = debounce(() => renderMovies(), 300);
        document.getElementById('search-input').addEventListener('input', debouncedSearch);
        document.getElementById('search-input').addEventListener('keyup', (e) => {
            if (e.key === 'Enter') renderMovies();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
                e.preventDefault();
                document.getElementById('search-input').focus();
            }
            if (e.key === 'Escape') {
                closeDetail();
                document.getElementById('search-input').blur();
            }
        });

        ['filter-sort', 'filter-category', 'filter-genre'].forEach(id => {
            document.getElementById(id).addEventListener('change', renderMovies);
        });

        document.getElementById('modal-close').addEventListener('click', closeDetail);
        document.getElementById('modal-overlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeDetail();
        });

        document.getElementById('logo-btn').addEventListener('click', () => {
            document.getElementById('search-input').value = '';
            document.getElementById('filter-category').value = '';
            document.getElementById('filter-genre').value = '';
            document.getElementById('filter-sort').value = 'weighted_score';
            renderMovies();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }

    function init() {
        initTheme();
        bindEvents();
        loadData();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
