// XP: activity statistics and charts.
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';
import { publicApi } from '../pages/public.js';
import { applyChartTheme, cssVar, ensureChartJs, withAlpha } from '../ui/charts.js';

export const XP_STATS_RANGES = [7, 30, 90, 365];
export const XP_WEEKDAYS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
export const XP_MEDALS = ['🥇', '🥈', '🥉'];
export let _xpCharts = [];
export let _xpStatsSeq = 0;

export function destroyXpCharts() {
    _xpCharts.forEach(chart => { try { chart.destroy(); } catch (_) { /* already gone */ } });
    _xpCharts = [];
}

export function setXpStatsRange(serverId, value) {
    const days = parseInt(value, 10);
    if (!XP_STATS_RANGES.includes(days)) return;
    state.xpStatsDays = days;
    loadXpStats(serverId);
}

export function fmtNum(n) {
    return Number(n || 0).toLocaleString('fr-FR');
}

export function parseIsoDay(iso) {
    // 'YYYY-MM-DD' as a local date — new Date(iso) would parse it as UTC midnight.
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d);
}

export function formatVoiceMinutes(minutes) {
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return h ? `${fmtNum(h)} h ${String(m).padStart(2, '0')}` : `${m} min`;
}

export async function loadXpStats(serverId) {
    const container = document.getElementById('xp-stats');
    if (!container) return;
    // A newer load (range change, re-render) supersedes this one.
    const seq = ++_xpStatsSeq;
    const stale = () => seq !== _xpStatsSeq || !container.isConnected;
    destroyXpCharts();
    container.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    let data;
    try {
        const sid = encodeURIComponent(serverId);
        data = state.publicView
            ? await publicApi(`/api/public/${state.publicView.token}/xp/stats?days=${state.xpStatsDays}`)
            : await api(`/api/servers/${sid}/xp/stats?days=${state.xpStatsDays}`);
    } catch (e) {
        if (!stale()) {
            container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(e.message || String(e))}</p></div>`;
        }
        return;
    }
    if (stale()) return;
    if (!data) {
        container.innerHTML = `<div class="empty-state"><h3>Accès refusé</h3><p>Statistiques réservées aux administrateurs de ce serveur.</p></div>`;
        return;
    }
    if (!data.summary?.members) {
        container.innerHTML = `<div class="empty-state"><h3>Aucune donnée</h3><p>Personne n'a encore gagné d'XP sur ce serveur.</p></div>`;
        return;
    }
    container.innerHTML = renderXpStats(data);
    try {
        await ensureChartJs();
    } catch (e) {
        if (stale()) return;
        container.querySelectorAll('.xp-chart-canvas').forEach(el => {
            el.innerHTML = `<div class="xp-chart-fallback">Graphique indisponible : Chart.js n'a pas pu être chargé.</div>`;
        });
        return;
    }
    if (stale()) return;
    drawXpCharts(data);
}

export function renderXpStats(data) {
    const s = data.summary;
    const period = `${data.days} j`;
    const cards = [
        ['Membres classés', fmtNum(s.members)],
        ['XP total', fmtNum(s.total_xp)],
        [`XP · ${period}`, fmtNum(s.period_xp)],
        [`Membres actifs · ${period}`, fmtNum(s.active_members)],
        [`Messages · ${period}`, fmtNum(s.period_messages)],
        [`Vocal · ${period}`, formatVoiceMinutes(s.period_voice_minutes || 0)],
    ];
    return `
            <div class="stats-grid xp-stats-cards">
                ${cards.map(([label, value]) => `
                    <div class="stat-card">
                        <div class="stat-label">${escapeHtml(label)}</div>
                        <div class="stat-value">${escapeHtml(value)}</div>
                    </div>
                `).join('')}
            </div>
            <div class="xp-stats-grid">
                <div class="xp-panel">
                    <div class="xp-panel-title">Activité quotidienne</div>
                    <div class="xp-chart-canvas"><canvas id="xp-chart-activity"></canvas></div>
                </div>
                <div class="xp-panel">
                    <div class="xp-panel-title">Répartition des niveaux</div>
                    <div class="xp-chart-canvas"><canvas id="xp-chart-levels"></canvas></div>
                </div>
            </div>
            <div class="xp-panel">
                <div class="xp-panel-title">XP par jour de la semaine et par heure · ${period}</div>
                <div class="xp-scroll">${renderXpHeatmap(data.heatmap)}</div>
            </div>
            <div class="xp-panel">
                <div class="xp-panel-title">Top ${data.top.length} membres</div>
                <div class="xp-scroll">${renderXpTopTable(data.top)}</div>
            </div>
        `;
}

export function renderXpHeatmap(grid) {
    const max = Math.max(0, ...grid.flat());
    const hours = Array.from({ length: 24 }, (_, h) =>
        `<div class="xp-heatmap-hour">${h % 3 === 0 ? `${h}h` : ''}</div>`).join('');
    const rows = grid.map((row, d) => `
            <div class="xp-heatmap-day">${XP_WEEKDAYS[d]}</div>
            ${row.map((xp, h) => {
            const title = escapeHtml(`${XP_WEEKDAYS[d]} ${h}h–${h + 1}h : ${fmtNum(xp)} XP`);
            if (!xp) return `<div class="xp-heatmap-cell zero" title="${title}"></div>`;
            const opacity = (0.15 + 0.85 * xp / max).toFixed(2);
            return `<div class="xp-heatmap-cell" style="opacity:${opacity}" title="${title}"></div>`;
        }).join('')}
        `).join('');
    return `<div class="xp-heatmap"><div></div>${hours}${rows}</div>`;
}

export function renderXpTopTable(top) {
    const rows = top.map(m => {
        const pct = m.xp_to_next ? Math.min(100, Math.round(100 * m.xp_in_level / m.xp_to_next)) : 0;
        const handle = m.username && m.username !== m.display_name ? `@${m.username}` : '';
        return `
                <tr>
                    <td class="num">${XP_MEDALS[m.rank - 1] || m.rank}</td>
                    <td>
                        <div class="xp-top-name">${escapeHtml(m.display_name)}</div>
                        ${handle ? `<div class="xp-top-sub">${escapeHtml(handle)}</div>` : ''}
                    </td>
                    <td class="num">${fmtNum(m.level)}</td>
                    <td>
                        <div class="xp-progress" title="${pct} %"><div style="width:${pct}%"></div></div>
                        <div class="xp-top-sub">${fmtNum(m.xp_in_level)} / ${fmtNum(m.xp_to_next)} XP</div>
                    </td>
                    <td class="num">${fmtNum(m.xp)}</td>
                    <td class="num">${fmtNum(m.messages)}</td>
                </tr>
            `;
    }).join('');
    return `
            <table class="xp-top-table">
                <thead>
                    <tr>
                        <th class="num">#</th><th>Membre</th><th class="num">Niveau</th>
                        <th>Progression</th><th class="num">XP</th><th class="num">Messages</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;
}

export function drawXpCharts(data) {
    destroyXpCharts();
    const Chart = window.Chart;
    const accent = cssVar('--accent', '#5865f2');
    const success = cssVar('--success', '#23a55a');
    const warning = cssVar('--warning', '#f0b232');
    applyChartTheme(Chart);
    const numTick = value => fmtNum(value);

    const days = data.daily.map(d => parseIsoDay(d.date));
    const activityCanvas = document.getElementById('xp-chart-activity');
    if (activityCanvas) {
        _xpCharts.push(new Chart(activityCanvas, {
            data: {
                labels: days.map(d => d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })),
                datasets: [
                    {
                        type: 'bar', label: 'XP messages', data: data.daily.map(d => d.message_xp),
                        backgroundColor: withAlpha(accent, 0.85), stack: 'xp', yAxisID: 'y', order: 2,
                    },
                    {
                        type: 'bar', label: 'XP vocal', data: data.daily.map(d => d.voice_xp),
                        backgroundColor: withAlpha(success, 0.85), stack: 'xp', yAxisID: 'y', order: 2,
                    },
                    {
                        type: 'line', label: 'Membres actifs', data: data.daily.map(d => d.active_users),
                        borderColor: warning, backgroundColor: warning, yAxisID: 'y1', order: 1,
                        tension: 0.3, borderWidth: 2, pointRadius: data.daily.length > 60 ? 0 : 2,
                    },
                ],
            },
            options: {
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { position: 'bottom', labels: { boxWidth: 12 } },
                    tooltip: {
                        callbacks: {
                            title: items => days[items[0].dataIndex].toLocaleDateString('fr-FR', {
                                weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
                            }),
                            label: item => `${item.dataset.label} : ${fmtNum(item.parsed.y)}`,
                        },
                    },
                },
                scales: {
                    x: { stacked: true, grid: { display: false }, ticks: { autoSkip: true, maxTicksLimit: 12, maxRotation: 0 } },
                    y: { stacked: true, beginAtZero: true, title: { display: true, text: 'XP' }, ticks: { callback: numTick } },
                    y1: {
                        position: 'right', beginAtZero: true, grid: { drawOnChartArea: false },
                        title: { display: true, text: 'Membres actifs' }, ticks: { precision: 0, callback: numTick },
                    },
                },
            },
        }));
    }

    const levelsCanvas = document.getElementById('xp-chart-levels');
    if (levelsCanvas) {
        _xpCharts.push(new Chart(levelsCanvas, {
            type: 'bar',
            data: {
                labels: data.levels.map(l => l.level),
                datasets: [{
                    label: 'Membres', data: data.levels.map(l => l.users),
                    backgroundColor: withAlpha(accent, 0.85), borderRadius: 3,
                }],
            },
            options: {
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: items => `Niveau ${items[0].label}`,
                            label: item => `${fmtNum(item.parsed.y)} membre${item.parsed.y > 1 ? 's' : ''}`,
                        },
                    },
                },
                scales: {
                    x: { grid: { display: false }, title: { display: true, text: 'Niveau' } },
                    y: { beginAtZero: true, ticks: { precision: 0 } },
                },
            },
        }));
    }
}

registerActions({
    loadXpStats,
    setXpStatsRange,
});
