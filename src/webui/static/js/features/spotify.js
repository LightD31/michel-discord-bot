// Spotify: playlist and vote statistics.
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';
import { getDiscord2NameMap, resolveServerMembers } from '../forms/pickers/members.js';
import { publicApi } from '../pages/public.js';
import { applyChartTheme, cssVar, ensureChartJs } from '../ui/charts.js';
import { fmtNum, parseIsoDay } from './xp.js';

// ── Spotify: playlist & vote statistics ─────────────────────────────
// Drawn with Chart.js through the XP section's lazy loader (ensureChartJs).
// Every chart also gets a "Voir les données" table: a canvas is opaque to
// screen readers, and the tables stay usable if Chart.js can't load.
export const SPOTIFY_STATS_TTL_MS = 5 * 60 * 1000;
export const _spotifyStatsCache = {};  // serverId -> { data, nameOf, at }
export let _spotifyCharts = [];
export let _spotifyStatsSeq = 0;   // fetches: a newer load supersedes this one
export let _spotifyRenderSeq = 0;  // draws: a newer render supersedes this one
// Diverging keep / no-opinion / remove set; ink = count colour inside a segment.
export const VOTE_KEYS = [
    { key: 'conserver', label: 'Conserver', color: '--viz-keep', ink: '#111214' },
    { key: 'menfou', label: 'Menfou', color: '--viz-neutral', ink: '#ffffff' },
    { key: 'supprimer', label: 'Supprimer', color: '--viz-remove', ink: '#111214' },
];

export const fmtDec = n => Number(n || 0).toLocaleString('fr-FR', { maximumFractionDigits: 1 });
export const fmtPct = r => `${(Number(r || 0) * 100).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %`;
export const plural = (n, word) => `${fmtNum(n)} ${word}${n >= 2 ? 's' : ''}`;

export function fmtClock(seconds) {  // 215 -> "3:35"
    const s = Math.round(seconds);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function fmtLongDuration(ms) {
    const minutes = Math.round(ms / 60000);
    const d = Math.floor(minutes / 1440), h = Math.floor((minutes % 1440) / 60), m = minutes % 60;
    if (d) return `${d} j ${h} h`;
    if (h) return `${h} h ${String(m).padStart(2, '0')}`;
    return `${m} min`;
}

export function fmtMonth(key, long = false) {  // "2026-01" -> "janv. 26" / "janvier 2026"
    const [y, m] = key.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString('fr-FR',
        long ? { month: 'long', year: 'numeric' } : { month: 'short', year: '2-digit' });
}

// Long spans label each January with its year; shorter ones every 1st/3rd month.
export function monthAxisLabels(months) {
    if (months.length > 24) return months.map(m => m.endsWith('-01') ? m.slice(0, 4) : '');
    const every = months.length > 8 ? 3 : 1;
    return months.map((m, i) => i % every === 0 ? fmtMonth(m) : '');
}

export function destroySpotifyCharts() {
    _spotifyCharts.forEach(chart => { try { chart.destroy(); } catch (_) { /* already gone */ } });
    _spotifyCharts = [];
}

export function vizPanel(title, sub, body, wide = false) {
    return `<div class="viz-panel${wide ? ' viz-wide' : ''}">
            <h4>${escapeHtml(title)}</h4>
            ${sub ? `<div class="viz-sub">${escapeHtml(sub)}</div>` : ''}
            ${body}
        </div>`;
}

export function vizCanvas(id, height, label) {
    return `<div class="viz-canvas" style="height:${height}px">
            <canvas id="${id}" role="img" aria-label="${escapeHtml(label)}"></canvas>
        </div>`;
}

export function vizTable(headers, rows) {
    // headers: [{ label, num }]; rows: arrays of display strings (escaped here).
    const cls = i => headers[i].num ? ' class="num"' : '';
    return `<details class="viz-data"><summary>Voir les données</summary>
            <div class="viz-table-scroll"><table>
                <thead><tr>${headers.map((h, i) => `<th${cls(i)}>${escapeHtml(h.label)}</th>`).join('')}</tr></thead>
                <tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td${cls(i)}>${escapeHtml(c)}</td>`).join('')}</tr>`).join('')}</tbody>
            </table></div>
        </details>`;
}

// Inline Chart.js plugin: the value after each bar's end ('end'), or the
// count centred in a stacked segment when it fits ('inside').
export const spotifyValueLabels = {
    id: 'spotifyValueLabels',
    afterDatasetsDraw(chart, _args, opts) {
        const { ctx } = chart;
        ctx.save();
        ctx.font = `600 11px ${window.Chart.defaults.font.family}`;
        ctx.textBaseline = 'middle';
        chart.data.datasets.forEach((ds, di) => {
            if (!chart.isDatasetVisible(di)) return;
            chart.getDatasetMeta(di).data.forEach((bar, i) => {
                const count = ds.counts ? ds.counts[i] : ds.data[i];
                if (!count) return;
                const text = fmtNum(count);
                if (opts.mode === 'inside') {
                    if (ctx.measureText(text).width + 8 > Math.abs(bar.x - bar.base)) return;
                    ctx.fillStyle = ds.valueInk;
                    ctx.textAlign = 'center';
                    ctx.fillText(text, (bar.x + bar.base) / 2, bar.y);
                } else {
                    ctx.fillStyle = opts.color;
                    ctx.textAlign = 'left';
                    ctx.fillText(text, bar.x + 6, bar.y);
                }
            });
        });
        ctx.restore();
    },
};

export async function spotifyNameResolver(serverId, d) {
    const ids = [
        d.current_poll?.added_by,
        ...(d.top_contributors || []).map(c => c.key),
        ...(d.voters || []).map(u => u.user_id),
    ].filter(Boolean).map(String);
    let byId = new Map();
    try {
        byId = await resolveServerMembers(serverId, ids);
    } catch (e) {
        console.warn('Could not resolve Spotify member names', e);
    }
    const prenoms = getDiscord2NameMap();
    return id => prenoms[id] || byId.get(String(id))?.display_name || byId.get(String(id))?.username || id;
}

export async function loadSpotifyStats(serverId, force = false) {
    const container = document.getElementById('spotify-stats');
    if (!container) return;
    const seq = ++_spotifyStatsSeq;
    const stale = () => seq !== _spotifyStatsSeq || !container.isConnected;
    const cacheKey = state.publicView ? `public:${state.publicView.token}` : serverId;
    const cached = _spotifyStatsCache[cacheKey];
    if (cached) {
        showSpotifyStats(container, cached);
        if (!force && Date.now() - cached.at < SPOTIFY_STATS_TTL_MS) return;
        container.classList.add('viz-refreshing');  // keep the frame while refetching
    }
    let data, nameOf;
    try {
        // The public payload already carries names in place of user ids.
        if (state.publicView) {
            data = await publicApi(`/api/public/${state.publicView.token}/spotify/stats`);
            nameOf = id => id;
        } else {
            data = await api(`/api/servers/${encodeURIComponent(serverId)}/spotify/stats`);
            nameOf = data ? await spotifyNameResolver(serverId, data) : (id => id);
        }
    } catch (e) {
        if (stale()) return;
        container.classList.remove('viz-refreshing');
        if (!cached) {
            container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(e?.message || String(e))}</p></div>`;
        }
        return;
    }
    if (stale()) return;
    if (!data) {
        container.innerHTML = `<div class="empty-state"><h3>Accès refusé</h3><p>Statistiques réservées aux administrateurs de ce serveur.</p></div>`;
        return;
    }
    const entry = { data, nameOf, at: Date.now() };
    _spotifyStatsCache[cacheKey] = entry;
    showSpotifyStats(container, entry);
}

export async function showSpotifyStats(container, { data, nameOf }) {
    const token = ++_spotifyRenderSeq;
    destroySpotifyCharts();
    container.classList.remove('viz-refreshing');
    container.innerHTML = renderSpotifyStats(data, nameOf);
    if (!container.querySelector('canvas')) return;
    const stale = () => token !== _spotifyRenderSeq || !container.isConnected;
    try {
        await ensureChartJs();
    } catch (e) {
        if (stale()) return;
        container.querySelectorAll('.viz-canvas').forEach(el => {
            el.innerHTML = `<div class="viz-fallback">Graphique indisponible : Chart.js n'a pas pu être chargé. Les valeurs restent dans « Voir les données ».</div>`;
        });
        return;
    }
    if (stale()) return;
    drawSpotifyCharts(data, nameOf);
}

export function renderSpotifyStats(d, nameOf) {
    const s = d.summary, v = d.votes;
    if (!s.track_count && !v.polls_closed && !d.current_poll) {
        return `<div class="empty-state"><h3>Pas encore de données</h3>
                <p>Le bot n'a pas encore synchronisé la playlist de ce serveur.</p></div>`;
    }

    const playlistTiles = [
        ['Titres', fmtNum(s.track_count)],
        ['Durée totale', fmtLongDuration(s.total_duration_ms), `${fmtClock(s.avg_duration_ms / 1000)} par titre en moyenne`],
        ['Artistes', fmtNum(s.artist_count)],
        ['Contributeurs', fmtNum(s.contributor_count)],
    ];
    const voteTiles = v.polls_closed ? [
        ['Votes clos', fmtNum(v.polls_closed), `${plural(v.kept, 'conservée')} · ${plural(v.removed, 'supprimée')}`],
        ['Taux de suppression', fmtPct(v.removal_rate)],
        ['Participation', fmtDec(v.avg_participation), `votants par vote · max ${fmtNum(v.max_participation)}`],
    ] : [];
    const tileRow = tiles => tiles.length ? `<div class="stats-grid">${tiles.map(([label, value, sub]) => `
                <div class="stat-card">
                    <div class="stat-label">${escapeHtml(label)}</div>
                    <div class="stat-value">${escapeHtml(value)}</div>
                    ${sub ? `<div class="stat-sub">${escapeHtml(sub)}</div>` : ''}
                </div>`).join('')}
            </div>` : '';
    const voteHeaders = [...VOTE_KEYS.map(k => ({ label: k.label, num: true })), { label: 'Total', num: true }];
    const voteCells = r => [...VOTE_KEYS.map(k => fmtNum(r[k.key])),
        fmtNum(VOTE_KEYS.reduce((sum, k) => sum + r[k.key], 0))];

    const panels = [];
    const p = d.current_poll;
    if (p && p.results_hidden) {
        const meta = [
            p.date ? `Ouvert le ${parseIsoDay(p.date).toLocaleDateString('fr-FR')}` : '',
            p.added_by ? `ajoutée par ${nameOf(p.added_by)}` : '',
        ].filter(Boolean).join(' · ');
        panels.push(vizPanel('Vote en cours', '',
            `<div class="viz-poll-name">${escapeHtml(p.name)}</div>
                 ${meta ? `<div class="viz-sub">${escapeHtml(meta)}</div>` : ''}
                 <div class="viz-sub" style="margin:0;">${escapeHtml(p.voter_count
                 ? `${plural(p.voter_count, 'votant')} pour l'instant`
                 : 'Pas encore de votes')} · résultats masqués jusqu'à la clôture du vote.</div>`, true));
    } else if (p) {
        const total = p.conserver + p.menfou + p.supprimer;
        const meta = [
            p.date ? `Ouvert le ${parseIsoDay(p.date).toLocaleDateString('fr-FR')}` : '',
            p.added_by ? `ajoutée par ${nameOf(p.added_by)}` : '',
        ].filter(Boolean).join(' · ');
        const body = total
            ? vizCanvas('spotify-chart-poll', 76, 'Répartition des votes en cours')
              + `<div class="viz-sub" style="margin:8px 0 0;">${escapeHtml(`${plural(total, 'votant')} : ${p.voters.map(nameOf).join(', ')}`)}</div>`
              + vizTable(voteHeaders, [voteCells(p)])
            : `<div class="viz-sub" style="margin:0;">Pas encore de votes.</div>`;
        panels.push(vizPanel('Vote en cours', '',
            `<div class="viz-poll-name">${escapeHtml(p.name)}</div>
                 ${meta ? `<div class="viz-sub">${escapeHtml(meta)}</div>` : ''}${body}`, true));
    }

    if (d.additions_by_month.length) {
        panels.push(vizPanel('Ajouts par mois', 'Titres encore présents dans la playlist, par mois d\'ajout',
            vizCanvas('spotify-chart-additions', 220, 'Titres ajoutés par mois')
            + vizTable([{ label: 'Mois' }, { label: 'Titres', num: true }],
                d.additions_by_month.map(m => [fmtMonth(m.month, true), fmtNum(m.count)])),
            true));
    }

    const rankTable = (rows, labelOf, head) => vizTable(
        [{ label: head }, { label: 'Titres', num: true }, { label: 'Part', num: true }],
        rows.map(r => [labelOf(r), fmtNum(r.count), fmtPct(r.count / s.track_count)]));
    if (d.top_contributors.length) {
        panels.push(vizPanel('Top contributeurs', 'Titres présents dans la playlist, par personne',
            vizCanvas('spotify-chart-contributors', d.top_contributors.length * 26 + 24, 'Top contributeurs')
            + rankTable(d.top_contributors, c => nameOf(c.key), 'Personne')));
    }
    if (d.top_artists.length) {
        panels.push(vizPanel('Top artistes', 'Nombre de titres par artiste',
            vizCanvas('spotify-chart-artists', d.top_artists.length * 26 + 24, 'Top artistes')
            + rankTable(d.top_artists, a => a.key, 'Artiste')));
    }

    if (d.duration_buckets.length) {
        panels.push(vizPanel('Durée des titres', 'Nombre de titres par tranche de 30 secondes',
            vizCanvas('spotify-chart-durations', 220, 'Répartition des durées des titres')
            + vizTable([{ label: 'Durée' }, { label: 'Titres', num: true }],
                d.duration_buckets.map(b => [durationRange(b), fmtNum(b.count)]))));
    }

    if (d.participation_by_month.length) {
        panels.push(vizPanel('Participation aux votes', 'Votants par vote, en moyenne sur le mois',
            vizCanvas('spotify-chart-participation', 220, 'Participation moyenne aux votes par mois')
            + vizTable([{ label: 'Mois' }, { label: 'Votes', num: true }, { label: 'Votants (moy.)', num: true }],
                d.participation_by_month.map(m => [fmtMonth(m.month, true), fmtNum(m.polls),
                    m.average == null ? '—' : fmtDec(m.average)]))));
    }

    if (d.voters.length) {
        panels.push(vizPanel('Votes par personne', 'Répartition des choix sur les votes clos, par nombre de votes',
            vizCanvas('spotify-chart-voters', d.voters.length * 30 + 56, 'Répartition des votes par personne')
            + vizTable([{ label: 'Personne' }, ...voteHeaders],
                d.voters.map(u => [nameOf(u.user_id), ...voteCells(u)])),
            true));
    }

    return `${tileRow(playlistTiles)}${tileRow(voteTiles)}
            <div class="viz-grid">${panels.join('')}</div>`;
}

export function durationRange(b) {
    return b.end_s == null ? `${fmtClock(b.start_s)} et plus` : `${fmtClock(b.start_s)} – ${fmtClock(b.end_s)}`;
}

export function drawSpotifyCharts(d, nameOf) {
    destroySpotifyCharts();
    const Chart = window.Chart;
    applyChartTheme(Chart);
    const series = cssVar('--viz-series', '#1aa34a');
    const grid = cssVar('--viz-grid', '#35373c');
    const surface = cssVar('--bg-card', '#2b2d31');
    const ink = cssVar('--text-primary', '#f2f3f5');
    const s = d.summary;
    const add = (id, config) => {
        const canvas = document.getElementById(id);
        if (canvas) _spotifyCharts.push(new Chart(canvas, config));
    };
    const countAxis = { beginAtZero: true, grid: { color: grid }, ticks: { precision: 0, maxTicksLimit: 6, callback: v => fmtNum(v) } };
    const labelAxis = labels => ({
        grid: { display: false },
        ticks: { autoSkip: false, maxRotation: 0, callback: (_v, i) => labels[i] || '' },
    });
    const truncate = (text, max) => text.length > max ? `${text.slice(0, max - 1)}…` : text;

    // One bar series: months or duration buckets.
    const columns = (id, labels, axisLabels, values, title, label) => add(id, {
        type: 'bar',
        data: {
            labels,
            datasets: [{ data: values, backgroundColor: series, borderRadius: 4, maxBarThickness: 24 }],
        },
        options: {
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { title: items => title(items[0].dataIndex), label: item => label(item.parsed.y) } },
            },
            scales: { x: labelAxis(axisLabels), y: countAxis },
        },
    });

    // Ranked horizontal bars with the value at the tip.
    const ranking = (id, rows, labelOf) => add(id, {
        type: 'bar',
        data: {
            labels: rows.map(labelOf),
            datasets: [{ data: rows.map(r => r.count), backgroundColor: series, borderRadius: 4, maxBarThickness: 16 }],
        },
        options: {
            indexAxis: 'y',
            maintainAspectRatio: false,
            layout: { padding: { right: 36 } },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    label: item => `${plural(item.parsed.x, 'titre')} · ${fmtPct(item.parsed.x / s.track_count)} de la playlist`,
                } },
                spotifyValueLabels: { mode: 'end', color: ink },
            },
            scales: {
                x: { display: false, beginAtZero: true },
                y: { grid: { display: false }, ticks: { callback(v) { return truncate(this.getLabelForValue(v), 22); } } },
            },
        },
        plugins: [spotifyValueLabels],
    });

    // keep | menfou | remove, horizontal and stacked. `percent` draws each
    // row at 100 % (voters); otherwise raw counts on hidden axes (open poll).
    const voteStack = (id, labels, rows, percent) => {
        const totals = rows.map(r => VOTE_KEYS.reduce((sum, k) => sum + (r[k.key] || 0), 0));
        add(id, {
            type: 'bar',
            data: {
                labels,
                datasets: VOTE_KEYS.map(k => ({
                    label: k.label,
                    data: rows.map((r, i) => percent ? (totals[i] ? r[k.key] / totals[i] * 100 : 0) : r[k.key]),
                    counts: rows.map(r => r[k.key]),
                    valueInk: k.ink,
                    backgroundColor: cssVar(k.color, '#808080'),
                    // A surface-coloured edge keeps a 2 px gap between segments.
                    borderColor: surface,
                    borderWidth: { right: 2 },
                    borderSkipped: 'start',
                    borderRadius: 4,
                    maxBarThickness: 20,
                })),
            },
            options: {
                indexAxis: 'y',
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'top', align: 'start', labels: { boxWidth: 10, boxHeight: 10 } },
                    tooltip: { callbacks: {
                        label: item => {
                            const n = item.dataset.counts[item.dataIndex];
                            const total = totals[item.dataIndex];
                            return `${item.dataset.label} : ${plural(n, 'vote')} (${fmtPct(total ? n / total : 0)})`;
                        },
                    } },
                    spotifyValueLabels: { mode: 'inside' },
                },
                scales: {
                    x: {
                        stacked: true, display: percent, max: percent ? 100 : totals[0],
                        grid: { color: grid }, ticks: { callback: v => `${v} %` },
                    },
                    y: { stacked: true, display: percent, grid: { display: false } },
                },
            },
            plugins: [spotifyValueLabels],
        });
    };

    if (d.current_poll && !d.current_poll.results_hidden) voteStack('spotify-chart-poll', [''], [d.current_poll], false);

    const months = d.additions_by_month;
    columns('spotify-chart-additions', months.map(m => fmtMonth(m.month)),
        monthAxisLabels(months.map(m => m.month)), months.map(m => m.count),
        i => fmtMonth(months[i].month, true), n => plural(n, 'titre'));

    ranking('spotify-chart-contributors', d.top_contributors, c => nameOf(c.key));
    ranking('spotify-chart-artists', d.top_artists, a => a.key);

    const buckets = d.duration_buckets;
    const labelStep = buckets.length > 16 ? 120 : 60;
    columns('spotify-chart-durations', buckets.map(durationRange),
        buckets.map(b => b.start_s % labelStep === 0 ? `${fmtClock(b.start_s)}${b.end_s == null ? '+' : ''}` : ''),
        buckets.map(b => b.count), i => durationRange(buckets[i]), n => plural(n, 'titre'));

    // Monthly turnout; months without a poll are null, which breaks the line.
    const part = d.participation_by_month;
    const has = i => part[i] && part[i].average != null;
    const lastIdx = part.map((_, i) => has(i)).lastIndexOf(true);
    const dotted = i => i === lastIdx || (has(i) && !has(i - 1) && !has(i + 1));
    add('spotify-chart-participation', {
        type: 'line',
        data: {
            labels: part.map(m => fmtMonth(m.month)),
            datasets: [{
                data: part.map(m => m.average),
                borderColor: series, backgroundColor: series, borderWidth: 2, tension: 0, spanGaps: false,
                pointRadius: ctx => dotted(ctx.dataIndex) ? 4 : 0,
                pointHoverRadius: 5, pointBorderColor: surface, pointBorderWidth: 2,
            }],
        },
        options: {
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    title: items => fmtMonth(part[items[0].dataIndex].month, true),
                    label: item => `${fmtDec(item.parsed.y)} votant${item.parsed.y >= 2 ? 's' : ''} en moyenne · ${plural(part[item.dataIndex].polls, 'vote')}`,
                } },
            },
            scales: {
                x: labelAxis(monthAxisLabels(part.map(m => m.month))),
                y: { beginAtZero: true, grid: { color: grid } },
            },
        },
    });

    voteStack('spotify-chart-voters',
        d.voters.map(u => `${truncate(nameOf(u.user_id), 18)} (${fmtNum(u.conserver + u.menfou + u.supprimer)})`),
        d.voters, true);
}

registerActions({
    loadSpotifyStats,
});
