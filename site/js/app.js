/**
 * RottenDouban v2.1 - 聚焦评分展示的前端应用
 */
(function() {
    'use strict';

    function esc(str) {
        const d = document.createElement('div');
        d.textContent = str || '';
        return d.innerHTML;
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
            populateGenreFilter();
            renderMovies();
        } catch(e) {
            grid.innerHTML = '<div class="no-data">数据加载失败，请稍后重试</div>';
        }
    }

    // ===== Genre Filter =====
    function populateGenreFilter() {
        const genres = new Set();
        movies.forEach(m => {
            (m.douban_genre || m.genre || '').split(/[,\/]/).forEach(g => {
                const t = g.trim();
                if (t) genres.add(t);
            });
        });
        const sel = document.getElementById('filter-genre');
        sel.innerHTML = '<option value="">全部类型</option>';
        [...genres].sort().forEach(g => {
            sel.innerHTML += `<option value="${esc(g)}">${esc(g)}</option>`;
        });
    }

    // ===== Search =====
    function searchMovies(query) {
        if (!query) return movies;
        const q = query.toLowerCase().trim();
        return movies.filter(m =>
            (m.title || '').toLowerCase().includes(q) ||
            (m.original_title || '').toLowerCase().includes(q) ||
            (m.douban_title || '').toLowerCase().includes(q) ||
            (m.douban_genre || '').toLowerCase().includes(q) ||
            (m.genre || '').toLowerCase().includes(q) ||
            (m.director || '').toLowerCase().includes(q) ||
            (m.douban_director || '').toLowerCase().includes(q)
        );
    }

    // ===== Filter & Sort =====
    function filterAndSort(list) {
        let filtered = list;
        const category = document.getElementById('filter-category').value;
        const genre = document.getElementById('filter-genre').value;
        const sort = document.getElementById('filter-sort').value;

        if (category) filtered = filtered.filter(m => m.category === category);
        if (genre) filtered = filtered.filter(m =>
            (m.douban_genre || '').includes(genre) || (m.genre || '').includes(genre)
        );

        filtered.sort((a, b) => {
            let va = a[sort], vb = b[sort];
            if (sort === 'douban_score') {
                va = va > 0 ? va : -1;
                vb = vb > 0 ? vb : -1;
            }
            return (vb || -1) - (va || -1);
        });
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
        const metaParts = [];
        if (year) metaParts.push(year);
        if (runtime) metaParts.push(runtime);
        const metaHtml = metaParts.join('<span class="card-meta-sep">·</span>');

        const catHtml = m.category ? `<span class="card-category">${esc(m.category)}</span>` : '';
        const wsHtml = m.weighted_score > 0 ? `<span class="card-weighted">${m.weighted_score.toFixed(1)}</span>` : '';

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
            circles.push(`<div class="score-circle ws-c"><div class="score-circle-val">${m.weighted_score.toFixed(1)}</div><div class="score-circle-lbl">加权</div></div>`);
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

        // Critics Consensus
        let consensusHtml = '';
        if (m.critics_consensus) {
            consensusHtml = `<div class="detail-synopsis rt-synopsis"><div class="detail-synopsis-label">🍅 影评人共识</div>${esc(m.critics_consensus)}</div>`;
        }

        // RT Synopsis (英文)
        let rtSynopsisHtml = '';
        if (m.synopsis) {
            rtSynopsisHtml = `<div class="detail-synopsis rt-synopsis"><div class="detail-synopsis-label">🍅 Rotten Tomatoes 简介</div>${esc(m.synopsis)}</div>`;
        }

        // ====== 豆瓣信息区 ======
        const dbMeta = [];
        if (m.douban_title) dbMeta.push(['中文名', m.douban_title]);
        if (m.douban_genre) dbMeta.push(['豆瓣类型', m.douban_genre]);
        if (m.douban_director) dbMeta.push(['豆瓣导演', m.douban_director]);
        if (m.douban_writers) dbMeta.push(['豆瓣编剧', m.douban_writers]);
        if (m.douban_cast) dbMeta.push(['豆瓣演员', m.douban_cast]);
        if (m.douban_score > 0) dbMeta.push(['豆瓣评分', `${m.douban_score} / 10`]);
        if (m.douban_vote_count > 0) dbMeta.push(['评分人数', `${Number(m.douban_vote_count).toLocaleString()} 人`]);

        const dbMetaHtml = dbMeta.map(([l, v]) =>
            `<div class="meta-row"><span class="meta-label">${l}</span><span class="meta-value">${esc(v)}</span></div>`
        ).join('');

        // Douban Synopsis (中文)
        let dbSynopsisHtml = '';
        if (m.douban_synopsis) {
            dbSynopsisHtml = `<div class="detail-synopsis db-synopsis"><div class="detail-synopsis-label">⭐ 豆瓣简介</div>${esc(m.douban_synopsis)}</div>`;
        }

        // Links
        const links = [];
        if (m.rt_url) links.push(`<a class="detail-link rt-link" href="${sanitizeUrl(m.rt_url)}" target="_blank" rel="noopener noreferrer">🍅 烂番茄</a>`);
        if (m.douban_url) links.push(`<a class="detail-link db-link" href="${sanitizeUrl(m.douban_url)}" target="_blank" rel="noopener noreferrer">⭐ 豆瓣</a>`);

        // Douban Reviews
        let reviewsHtml = '';
        if (m.douban_short_reviews && m.douban_short_reviews.length) {
            const reviews = m.douban_short_reviews.map(r => {
                const stars = Array.from({length: 5}, (_, i) =>
                    `<span class="review-star${i < Math.round(r.rating / 2) ? '' : ' empty'}">★</span>`
                ).join('');
                return `<div class="review-card">
                    <div class="review-top">
                        <span class="review-user">${esc(r.user)}</span>
                        <div class="review-rating">${stars}</div>
                    </div>
                    <div class="review-text">${esc(r.content)}</div>
                </div>`;
            }).join('');
            reviewsHtml = `<div class="douban-reviews">
                <div class="reviews-header"><h3>⭐ 豆瓣热评</h3><span class="reviews-count">${m.douban_short_reviews.length}条</span></div>
                ${reviews}
            </div>`;
        }

        // Assemble - separate RT and Douban sections clearly
        let rtSection = '';
        if (rtMetaHtml || rtSynopsisHtml || consensusHtml) {
            rtSection = `<div class="detail-section rt-section">
                <div class="section-header"><h3>🍅 Rotten Tomatoes</h3></div>
                ${rtMetaHtml ? `<div class="detail-meta">${rtMetaHtml}</div>` : ''}
                ${consensusHtml}
                ${rtSynopsisHtml}
            </div>`;
        }

        let dbSection = '';
        if (dbMetaHtml || dbSynopsisHtml) {
            dbSection = `<div class="detail-section db-section">
                <div class="section-header"><h3>⭐ 豆瓣</h3></div>
                ${dbMetaHtml ? `<div class="detail-meta">${dbMetaHtml}</div>` : ''}
                ${dbSynopsisHtml}
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
            ${reviewsHtml}
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
