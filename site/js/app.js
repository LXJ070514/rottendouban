/**
 * RottenDouban 前端应用 - 搜索/过滤/对比/趋势/推荐/分享/导出/主题/PWA
 */
(function() {
    'use strict';

    // ===== Data Store =====
    let movies = [];
    let currentPanel = 'home';

    // ===== Pinyin Search Helper =====
    const pinyinMap = {
        '影': 'ying', '视': 'shi', '电': 'dian', '戏': 'xi', '剧': 'ju',
        '动': 'dong', '画': 'hua', '漫': 'man', '科': 'ke', '幻': 'huan',
        '恐': 'kong', '怖': 'bu', '惊': 'jing', '悚': 'song', '爱': 'ai',
        '情': 'qing', '战': 'zhan', '争': 'zheng', '犯': 'fan', '罪': 'zui',
        '喜': 'xi', '乐': 'le', '悲': 'bei', '伤': 'shang', '历': 'li',
        '史': 'shi', '纪': 'ji', '录': 'lu', '神': 'shen', '奇': 'qi',
        '武': 'wu', '侠': 'xia', '古': 'gu', '装': 'zhuang', '家': 'jia',
        '庭': 'ting', '童': 'tong', '儿': 'er', '音': 'yin', '乐': 'le',
        '悬': 'xuan', '疑': 'yi', '冒': 'mao', '险': 'xian', '西': 'xi',
        '部': 'bu', '黑': 'hei', '暗': 'an', '传': 'chuan', '奇': 'qi',
    };

    function toPinyin(str) {
        if (!str) return '';
        let result = '';
        for (const ch of str) {
            if (pinyinMap[ch]) {
                result += pinyinMap[ch];
            } else if (/[a-zA-Z0-9]/.test(ch)) {
                result += ch.toLowerCase();
            }
        }
        return result;
    }

    // ===== Theme =====
    function initTheme() {
        const saved = localStorage.getItem('rd-theme') || 'light';
        document.documentElement.setAttribute('data-theme', saved);
        updateThemeIcon(saved);
    }

    function updateThemeIcon(theme) {
        const btn = document.getElementById('theme-toggle');
        btn.textContent = theme === 'dark' ? '☀️' : '🌙';
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('rd-theme', next);
        updateThemeIcon(next);
    }

    // ===== Load Data =====
    async function loadData() {
        const grid = document.getElementById('movie-grid');
        grid.innerHTML = '<div class="loading-spinner"><div class="spinner"></div></div>';
        try {
            const resp = await fetch('data/movies.json');
            if (!resp.ok) throw new Error('数据加载失败');
            movies = await resp.json();
            loadStats();
            populateFilters();
            renderMovies(movies);
        } catch(e) {
            grid.innerHTML = '<div class="no-data">数据加载失败，请先运行爬虫生成数据</div>';
        }
    }

    // ===== Stats =====
    function loadStats() {
        const total = movies.length;
        const avg = movies.reduce((s, m) => s + (m.weighted_score > 0 ? m.weighted_score : 0), 0) /
                    movies.filter(m => m.weighted_score > 0).length || 0;
        const matched = movies.filter(m => m.douban_id && m.douban_id !== '').length;
        document.getElementById('stat-total').textContent = total;
        document.getElementById('stat-avg').textContent = avg.toFixed(1);
        document.getElementById('stat-matched').textContent = matched;
    }

    // ===== Filters =====
    function populateFilters() {
        // Genre filter
        const genres = new Set();
        movies.forEach(m => {
            if (m.genre) m.genre.split(',').forEach(g => genres.add(g.trim()));
            if (m.douban_genre) m.douban_genre.split(',').forEach(g => genres.add(g.trim()));
        });
        const genreSelect = document.getElementById('filter-genre');
        genreSelect.innerHTML = '<option value="">全部</option>';
        [...genres].sort().forEach(g => {
            if (g) genreSelect.innerHTML += `<option value="${g}">${g}</option>`;
        });

        // Compare selects
        populateCompareSelects();
        populateTrendsSelect();
    }

    // ===== Search (fuzzy + pinyin) =====
    function searchMovies(query) {
        if (!query) return movies;
        query = query.toLowerCase().trim();
        const pinyinQuery = toPinyin(query);
        return movies.filter(m => {
            const title = (m.title || '').toLowerCase();
            const orig = (m.original_title || '').toLowerCase();
            const dbTitle = (m.douban_title || '').toLowerCase();
            const titlePinyin = toPinyin(m.title || '');
            const dbPinyin = toPinyin(m.douban_title || '');
            return title.includes(query) || orig.includes(query) ||
                   dbTitle.includes(query) || titlePinyin.includes(pinyinQuery) ||
                   dbPinyin.includes(pinyinQuery);
        });
    }

    // ===== Filter & Sort =====
    function filterAndSort(list) {
        let filtered = list;
        const category = document.getElementById('filter-category').value;
        const genre = document.getElementById('filter-genre').value;
        const sort = document.getElementById('filter-sort').value;

        if (category) filtered = filtered.filter(m => m.category === category);
        if (genre) filtered = filtered.filter(m =>
            (m.genre || '').includes(genre) || (m.douban_genre || '').includes(genre)
        );

        filtered.sort((a, b) => {
            const va = a[sort] || -1;
            const vb = b[sort] || -1;
            return vb - va;
        });
        return filtered;
    }

    // ===== Render Movie Grid =====
    function renderMovies(list) {
        const grid = document.getElementById('movie-grid');
        const filtered = filterAndSort(list);
        if (!filtered.length) {
            grid.innerHTML = '<div class="no-data">没有找到匹配的电影</div>';
            return;
        }
        grid.innerHTML = filtered.map(m => renderCard(m)).join('');
        // Bind card click events
        grid.querySelectorAll('.movie-card').forEach(card => {
            card.addEventListener('click', (e) => {
                if (e.target.closest('.share-btn-card')) return;
                const id = card.dataset.id;
                const movie = movies.find(m => m.id == id);
                if (movie) showDetail(movie);
            });
        });
        // Bind share buttons
        grid.querySelectorAll('.share-btn-card').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = btn.closest('.movie-card').dataset.id;
                const movie = movies.find(m => m.id == id);
                if (movie) showShareModal(movie);
            });
        });
    }

    function renderCard(m) {
        const poster = m.poster_local || m.poster_url || m.douban_poster;
        const posterHtml = poster ?
            `<img class="card-poster" src="${poster}" alt="${esc(m.title)}" loading="lazy" onerror="this.outerHTML='<div class=card-poster-placeholder>🎬</div>'">` :
            '<div class="card-poster-placeholder">🎬</div>';

        const scores = [];
        if (m.tomatometer >= 0) scores.push(`<span class="score-badge rt"><span class="score-icon">🍅</span>${m.tomatometer}%</span>`);
        if (m.audience_score >= 0) scores.push(`<span class="score-badge audience"><span class="score-icon">🍿</span>${m.audience_score}%</span>`);
        if (m.douban_score >= 0) scores.push(`<span class="score-badge douban"><span class="score-icon">🌟</span>${m.douban_score}</span>`);
        if (m.weighted_score >= 0) scores.push(`<span class="score-badge weighted"><span class="score-icon">📊</span>${m.weighted_score}</span>`);

        const year = m.year || '';
        const runtime = m.runtime || '';
        const genre = m.genre || m.douban_genre || '';

        const links = [];
        if (m.rt_url) links.push(`<a class="card-link" href="${m.rt_url}" target="_blank">烂番茄</a>`);
        if (m.douban_url) links.push(`<a class="card-link" href="${m.douban_url}" target="_blank">豆瓣</a>`);

        return `<div class="movie-card" data-id="${m.id}">
            <button class="share-btn-card" title="分享">📤</button>
            ${posterHtml}
            <div class="card-body">
                <div class="card-title">${esc(m.title || '未知')}</div>
                <div class="card-scores">${scores.join('')}</div>
                <div class="card-meta">
                    ${year ? `<span>${year}</span>` : ''}
                    ${runtime ? `<span>${runtime}</span>` : ''}
                    ${genre ? `<span>${genre.substring(0, 30)}</span>` : ''}
                </div>
                <div class="card-links">${links.join('')}</div>
            </div>
        </div>`;
    }

    function esc(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ===== Detail Modal =====
    function showDetail(m) {
        const overlay = document.getElementById('modal-overlay');
        const body = document.getElementById('modal-body');

        const poster = m.poster_local || m.poster_url || m.douban_poster;
        const posterHtml = poster ?
            `<img class="detail-poster" src="${poster}" alt="${esc(m.title)}" onerror="this.outerHTML='<div class=detail-poster-placeholder>🎬</div>'">` :
            '<div class="detail-poster-placeholder">🎬</div>';

        // Score circles - weighted calculation visualization
        const circles = [];
        if (m.tomatometer >= 0) circles.push(`<div class="score-circle rt-circle"><div class="score-circle-value">${m.tomatometer}%</div><div class="score-circle-label">影评人</div></div>`);
        if (m.audience_score >= 0) circles.push(`<div class="score-circle audience-circle"><div class="score-circle-value">${m.audience_score}%</div><div class="score-circle-label">观众</div></div>`);
        if (m.douban_score >= 0) circles.push(`<div class="score-circle douban-circle"><div class="score-circle-value">${m.douban_score}</div><div class="score-circle-label">豆瓣</div></div>`);
        if (m.weighted_score >= 0) circles.push(`<div class="score-circle weighted-circle"><div class="score-circle-value">${m.weighted_score}</div><div class="score-circle-label">加权</div></div>`);

        // Meta rows (hide empty fields)
        const metaRows = [];
        const fields = [
            ['评级', m.rating], ['类型', m.genre || m.douban_genre],
            ['导演', m.director || m.douban_director],
            ['演员', m.cast || m.douban_cast],
            ['上映日期', m.release_date], ['片长', m.runtime], ['年份', m.year],
        ];
        fields.forEach(([label, value]) => {
            if (value) metaRows.push(`<div class="detail-meta-row"><span class="detail-meta-label">${label}</span><span class="detail-meta-value">${esc(value)}</span></div>`);
        });

        // Douban section
        let doubanHtml = '';
        if (m.douban_id) {
            const dbFields = [
                ['中文名', m.douban_title], ['豆瓣类型', m.douban_genre],
                ['豆瓣导演', m.douban_director], ['豆瓣演员', m.douban_cast],
                ['评分人数', m.douban_vote_count ? `${m.douban_vote_count} 人` : ''],
            ];
            const dbRows = dbFields.filter(([,v]) => v).map(([l,v]) =>
                `<div class="douban-meta-row"><span class="detail-meta-label">${l}</span><span>${esc(v)}</span></div>`
            ).join('');
            doubanHtml = `<div class="douban-section"><h3>豆瓣信息</h3>${dbRows}</div>`;
        }

        // Links
        const links = [];
        if (m.rt_url) links.push(`<a class="detail-link rt-link" href="${m.rt_url}" target="_blank">查看烂番茄 →</a>`);
        if (m.douban_url) links.push(`<a class="detail-link douban-link" href="${m.douban_url}" target="_blank">查看豆瓣 →</a>`);

        body.innerHTML = `
            <div class="detail-header">
                ${posterHtml}
                <div class="detail-info">
                    <div class="detail-title">${esc(m.title || '未知')}</div>
                    ${m.original_title ? `<div class="detail-original-title">${esc(m.original_title)}</div>` : ''}
                    ${m.douban_title && m.douban_title !== m.title ? `<div class="detail-original-title">${esc(m.douban_title)}</div>` : ''}
                    <div class="score-circles">${circles.join('')}</div>
                    <div class="detail-meta-section">${metaRows.join('')}</div>
                    <div class="detail-links">${links.join('')}</div>
                </div>
            </div>
            ${m.synopsis ? `<div class="detail-synopsis">${esc(m.synopsis)}</div>` : ''}
            ${m.douban_synopsis && m.douban_synopsis !== m.synopsis ? `<div class="detail-synopsis" style="margin-top:8px;color:var(--douban-yellow)">豆瓣简介: ${esc(m.douban_synopsis)}</div>` : ''}
            ${doubanHtml}
        `;

        overlay.style.display = 'block';
    }

    function closeDetail() {
        document.getElementById('modal-overlay').style.display = 'none';
    }

    // ===== Compare =====
    function populateCompareSelects() {
        const selA = document.getElementById('compare-a');
        const selB = document.getElementById('compare-b');
        const options = movies.map(m => `<option value="${m.id}">${esc(m.title || '未知')}</option>`).join('');
        selA.innerHTML = options;
        selB.innerHTML = options;
    }

    function showCompare() {
        const idA = document.getElementById('compare-a').value;
        const idB = document.getElementById('compare-b').value;
        const mA = movies.find(m => m.id == idA);
        const mB = movies.find(m => m.id == idB);
        if (!mA || !mB) return;

        const result = document.getElementById('compare-result');
        result.innerHTML = renderCompareMovie(mA) + renderCompareMovie(mB);
    }

    function renderCompareMovie(m) {
        const bars = [
            ['影评人', m.tomatometer, 100, 'var(--rt-green)'],
            ['观众', m.audience_score, 100, 'var(--audience-green)'],
            ['豆瓣', m.douban_score >= 0 ? m.douban_score * 10 : 0, 100, 'var(--douban-yellow)'],
            ['加权', m.weighted_score, 100, 'var(--weighted-blue)'],
        ];
        const barsHtml = bars.map(([label, val, max, color]) => {
            const pct = val >= 0 ? (val / max * 100) : 0;
            const display = label === '豆瓣' ? (m.douban_score >= 0 ? m.douban_score : '-') :
                           (val >= 0 ? val : '-');
            return `<div class="compare-bar">
                <span class="compare-bar-label">${label}</span>
                <div class="compare-bar-track"><div class="compare-bar-fill" style="width:${pct}%;background:${color}"></div></div>
                <span class="compare-bar-value" style="color:${color}">${display}</span>
            </div>`;
        }).join('');

        return `<div class="compare-movie">
            <h3>${esc(m.title || '未知')}</h3>
            ${barsHtml}
        </div>`;
    }

    // ===== Trends =====
    let trendsChart = null;
    function populateTrendsSelect() {
        const sel = document.getElementById('trends-movie-select');
        sel.innerHTML = movies.map(m =>
            `<option value="${m.id}">${esc(m.title || '未知')}</option>`
        ).join('');
    }

    function showTrends() {
        const id = document.getElementById('trends-movie-select').value;
        const movie = movies.find(m => m.id == id);
        if (!movie || !movie.score_history || !movie.score_history.length) {
            document.getElementById('trends-chart').getContext('2d').clearRect(0,0,0,0);
            return;
        }
        const history = movie.score_history;
        const labels = history.map(h => {
            const d = new Date(h.recorded_at);
            return d.toLocaleDateString('zh-CN');
        });
        const datasets = [
            { label: '影评人', data: history.map(h => h.tomatometer >= 0 ? h.tomatometer : null), borderColor: '#fa320a', tension: 0.3 },
            { label: '观众', data: history.map(h => h.audience_score >= 0 ? h.audience_score : null), borderColor: '#18bc3c', tension: 0.3 },
            { label: '豆瓣', data: history.map(h => h.douban_score >= 0 ? h.douban_score * 10 : null), borderColor: '#e6a23c', tension: 0.3 },
            { label: '加权', data: history.map(h => h.weighted_score >= 0 ? h.weighted_score : null), borderColor: '#3b82f6', tension: 0.3 },
        ];

        if (trendsChart) trendsChart.destroy();
        trendsChart = new Chart(document.getElementById('trends-chart'), {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                plugins: { legend: { labels: { color: getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim() } } },
                scales: {
                    y: { min: 0, max: 100, ticks: { color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() } },
                    x: { ticks: { color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() } },
                }
            }
        });
    }

    // ===== Recommendation (collaborative filtering) =====
    function showRecommend() {
        // Simple collaborative: genre + director based
        const grid = document.getElementById('recommend-grid');
        const top = movies.filter(m => m.weighted_score > 0)
                         .sort((a,b) => b.weighted_score - a.weighted_score)
                         .slice(0, 12);

        grid.innerHTML = top.map(m => {
            const reason = getRecommendReason(m);
            return `<div class="recommend-card" data-id="${m.id}">
                <div style="font-weight:600">${esc(m.title || '未知')}</div>
                <div class="card-scores" style="margin:6px 0">
                    ${m.weighted_score >= 0 ? `<span class="score-badge weighted">加权 ${m.weighted_score}</span>` : ''}
                </div>
                <div style="font-size:0.8rem;color:var(--text-muted)">${esc(m.genre || m.douban_genre || '')}</div>
                <div class="recommend-reason">${reason}</div>
            </div>`;
        }).join('');

        grid.querySelectorAll('.recommend-card').forEach(card => {
            card.addEventListener('click', () => {
                const movie = movies.find(m => m.id == card.dataset.id);
                if (movie) showDetail(movie);
            });
        });
    }

    function getRecommendReason(m) {
        if (m.weighted_score >= 80) return '高分佳作';
        if (m.weighted_score >= 70) return '口碑良好';
        const genre = m.genre || m.douban_genre || '';
        if (genre.includes('科幻')) return '科幻迷必看';
        if (genre.includes('动画')) return '动画爱好者推荐';
        if (genre.includes('恐怖')) return '恐怖片精选';
        if (genre.includes('爱情')) return '爱情片佳选';
        return '值得一看';
    }

    // ===== Share =====
    function showShareModal(m) {
        const area = document.getElementById('share-capture-area');
        const scores = [];
        if (m.tomatometer >= 0) scores.push(`<span class="score-badge rt">🍅 ${m.tomatometer}%</span>`);
        if (m.audience_score >= 0) scores.push(`<span class="score-badge audience">🍿 ${m.audience_score}%</span>`);
        if (m.douban_score >= 0) scores.push(`<span class="score-badge douban">🌟 ${m.douban_score}</span>`);
        if (m.weighted_score >= 0) scores.push(`<span class="score-badge weighted">📊 ${m.weighted_score}</span>`);

        area.innerHTML = `<div class="share-capture">
            <div class="share-title">${esc(m.title || '未知')}</div>
            <div class="share-scores">${scores.join('')}</div>
            <div style="font-size:0.8rem;color:var(--text-muted)">${esc(m.genre || m.douban_genre || '')}</div>
            <div class="share-watermark">RottenDouban | 烂番茄豆瓣聚合评分</div>
        </div>`;
        document.getElementById('share-modal').style.display = 'block';
    }

    function downloadShareImage() {
        const captureArea = document.querySelector('.share-capture');
        html2canvas(captureArea, { backgroundColor: null, scale: 2 }).then(canvas => {
            const link = document.createElement('a');
            link.download = 'rotten douban-share.png';
            link.href = canvas.toDataURL('image/png');
            link.click();
        });
    }

    function closeShareModal() {
        document.getElementById('share-modal').style.display = 'none';
    }

    // ===== Export =====
    function showExportModal() {
        document.getElementById('export-modal').style.display = 'block';
    }

    function exportJSON() {
        const data = JSON.stringify(movies, null, 2);
        downloadFile(data, 'rotten douban-movies.json', 'application/json');
    }

    function exportCSV() {
        if (!movies.length) return;
        const headers = Object.keys(movies[0]);
        const csv = [headers.join(','), ...movies.map(m =>
            headers.map(h => `"${(m[h] || '').toString().replace(/"/g, '""')}"`).join(',')
        )].join('\n');
        downloadFile(csv, 'rotten douban-movies.csv', 'text/csv');
    }

    function downloadFile(content, filename, mime) {
        const blob = new Blob([content], { type: mime });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    }

    function closeExportModal() {
        document.getElementById('export-modal').style.display = 'none';
    }

    // ===== Panel Switching =====
    function switchPanel(panel) {
        currentPanel = panel;
        // Nav buttons
        document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
        document.getElementById(`btn-${panel}`).classList.add('active');

        // Show/hide panels
        const panels = {
            home: ['movie-grid', 'stats-bar', 'filter-bar'],
            compare: ['compare-panel'],
            trends: ['trends-panel'],
            recommend: ['recommend-panel'],
        };

        // Hide all optional panels
        document.getElementById('compare-panel').style.display = 'none';
        document.getElementById('trends-panel').style.display = 'none';
        document.getElementById('recommend-panel').style.display = 'none';

        if (panel === 'home') {
            document.getElementById('movie-grid').style.display = '';
            document.getElementById('stats-bar').style.display = '';
            document.getElementById('filter-bar').style.display = '';
        } else {
            document.getElementById('movie-grid').style.display = 'none';
            document.getElementById('stats-bar').style.display = 'none';
            document.getElementById('filter-bar').style.display = 'none';
            document.getElementById(`${panel}-panel`).style.display = '';

            if (panel === 'compare') showCompare();
            if (panel === 'trends') showTrends();
            if (panel === 'recommend') showRecommend();
        }
    }

    // ===== Event Bindings =====
    function bindEvents() {
        // Theme toggle
        document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

        // Search
        document.getElementById('search-btn').addEventListener('click', () => {
            const q = document.getElementById('search-input').value;
            renderMovies(searchMovies(q));
        });
        document.getElementById('search-input').addEventListener('keyup', (e) => {
            if (e.key === 'Enter') {
                renderMovies(searchMovies(e.target.value));
            } else if (!e.target.value) {
                renderMovies(movies);
            }
        });

        // Filters
        ['filter-category', 'filter-genre', 'filter-sort'].forEach(id => {
            document.getElementById(id).addEventListener('change', () => {
                renderMovies(movies);
            });
        });

        // Modal close
        document.getElementById('modal-close').addEventListener('click', closeDetail);
        document.getElementById('modal-overlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeDetail();
        });

        // Nav buttons
        ['home', 'compare', 'trends', 'recommend'].forEach(panel => {
            document.getElementById(`btn-${panel}`).addEventListener('click', () => switchPanel(panel));
        });

        // Compare
        document.getElementById('compare-a').addEventListener('change', showCompare);
        document.getElementById('compare-b').addEventListener('change', showCompare);

        // Trends
        document.getElementById('trends-movie-select').addEventListener('change', showTrends);

        // Share
        document.getElementById('share-close').addEventListener('click', closeShareModal);
        document.getElementById('share-modal').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeShareModal();
        });
        document.getElementById('share-download-btn').addEventListener('click', downloadShareImage);

        // Export
        document.getElementById('export-btn').addEventListener('click', showExportModal);
        document.getElementById('export-close').addEventListener('click', closeExportModal);
        document.getElementById('export-modal').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) closeExportModal();
        });
        document.getElementById('export-json-btn').addEventListener('click', exportJSON);
        document.getElementById('export-csv-btn').addEventListener('click', exportCSV);
    }

    // ===== Init =====
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