// The home page.
import { html, raw } from '../core/html.js';
import { actionArgs } from '../core/actions.js';
import { isAdmin, isDeveloper } from '../core/data.js';
import { routeHash } from '../core/router.js';
import { state } from '../core/state.js';

// ── Home Page ─────────────────────────────────────
export function renderHomePage() {
    const bi = state.botInfo || {};
    const admin = isAdmin();

    // Latency color coding
    const lat = bi.latency;
    const latColor = lat == null ? 'var(--text-primary)'
        : lat < 100 ? 'var(--success)'
        : lat < 200 ? 'var(--warning)'
        : 'var(--danger)';

    // ── Hero ──────────────────────────────────────
    const heroHtml = String(html`
            <div class="home-hero">
                <div class="home-hero-bot">
                    <div class="bot-avatar">🤖</div>
                    <div>
                        <div class="bot-name">${bi.user || 'Michel Bot'}</div>
                        ${raw(state.user ? String(html`<div class="bot-tag">Connecté en tant que ${state.user.username}</div>`) : '')}
                    </div>
                </div>
                <div class="home-hero-right">
                    ${raw(admin ? `
                    <div class="hero-stat">
                        <div class="hero-stat-value">${bi.guilds ?? '—'}</div>
                        <div class="hero-stat-label">Serveurs</div>
                    </div>
                    <div class="hero-stat">
                        <div class="hero-stat-value" style="color:${latColor}">${lat != null ? lat + ' ms' : '—'}</div>
                        <div class="hero-stat-label">Latence</div>
                    </div>
                    <div class="hero-stat">
                        <div class="hero-stat-value">${bi.extensions?.length ?? '—'}</div>
                        <div class="hero-stat-label">Extensions</div>
                    </div>
                    ` : `
                    <div class="hero-stat">
                        <div class="hero-stat-value" style="color:${latColor}">${lat != null ? lat + ' ms' : '—'}</div>
                        <div class="hero-stat-label">Latence</div>
                    </div>
                    `)}
                    <span class="status-badge ${raw(bi.status === 'online' ? 'online' : 'offline')}">
                        <span class="status-dot-pulse"></span>
                        ${raw(bi.status === 'online' ? 'En ligne' : bi.status || 'Inconnu')}
                    </span>
                </div>
            </div>
        `);

    // ── Non-admin view ─────────────────────────────
    if (!admin) {
        return heroHtml + String(html`
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title">Bienvenue, ${state.user.username} !</div>
                            <div class="card-subtitle">Tu es connecté·e en tant que membre. Seuls les administrateurs peuvent modifier la configuration du bot.</div>
                        </div>
                    </div>
                </div>
            `);
    }

    // ── Server Cards ───────────────────────────────
    const servers = state.servers ? Object.entries(state.servers) : [];
    const serverCardsHtml = servers.length > 0 ? `
            <div class="section-label">Serveurs</div>
            <div class="server-cards-grid">
                ${servers.map(([id, s]) => {
                const iconUrl = s.icon
                    ? `https://cdn.discordapp.com/icons/${id}/${s.icon}.png?size=128`
                    : null;
                const cfg = s.config || {};
                const modCount = Object.keys(cfg).length;
                const activeCount = Object.values(cfg).filter(m => m && m.enabled === true).length;
                return String(html`
                        <div class="server-card" data-click="navigate" data-click-args="${actionArgs("server", id)}">
                            <a class="visually-hidden" href="${raw(routeHash('server', id))}" data-click="navigate" data-click-args="${actionArgs("server", id)}" data-prevent>Ouvrir ${s.name || id}</a>
                            <div class="server-card-top">
                                <div class="server-card-icon">
                                    ${raw(iconUrl ? String(html`<img src="${iconUrl}" alt="">`) : String(html`<span>${s.name?.charAt(0) || '?'}</span>`))}
                                </div>
                                <div class="u-minw-0 u-fx-1">
                                    <div class="server-card-name" title="${s.name || id}">${s.name || id}</div>
                                    <div class="server-card-id">${raw(id)}</div>
                                </div>
                            </div>
                            <div class="server-card-footer">
                                <div class="server-card-pills">
                                    <span class="server-card-pill modules">${raw(modCount)} module${raw(modCount !== 1 ? 's' : '')}</span>
                                    ${raw(activeCount > 0 ? `<span class="server-card-pill active">${activeCount} actif${activeCount !== 1 ? 's' : ''}</span>` : '')}
                                </div>
                                <span class="server-card-arrow">→</span>
                            </div>
                        </div>
                    `);
            }).join('')}
            </div>
        ` : '';

    // ── Quick Actions ──────────────────────────────
    const loadedExts = (state.extensions || []).filter(e => e.loaded).length;
    const quickActionsHtml = !isDeveloper() ? '' : `
            <div class="section-label">Actions rapides</div>
            <div class="quick-actions-grid">
                <button class="quick-action-btn" data-click="navigate" data-click-args="[&quot;global&quot;]">
                    <span class="quick-action-icon">⚙️</span>
                    <span class="quick-action-label">Config globale</span>
                    <span class="quick-action-sub">Tokens, APIs…</span>
                </button>
                <button class="quick-action-btn" data-click="navigate" data-click-args="[&quot;extensions&quot;]">
                    <span class="quick-action-icon">🧩</span>
                    <span class="quick-action-label">Extensions</span>
                    <span class="quick-action-sub">${loadedExts} chargée${loadedExts !== 1 ? 's' : ''}</span>
                </button>
                <button class="quick-action-btn" id="reload-all-btn" data-click="reloadAllExtensions">
                    <span class="quick-action-icon">🔄</span>
                    <span class="quick-action-label">Recharger tout</span>
                    <span class="quick-action-sub">Extensions actives</span>
                </button>
                <button class="quick-action-btn" data-click="navigate" data-click-args="[&quot;logs&quot;]">
                    <span class="quick-action-icon">📋</span>
                    <span class="quick-action-label">Logs</span>
                    <span class="quick-action-sub">Temps réel</span>
                </button>
                <button class="quick-action-btn" data-click="downloadConfig">
                    <span class="quick-action-icon">⬇️</span>
                    <span class="quick-action-label">Exporter</span>
                    <span class="quick-action-sub">config.json</span>
                </button>
                <button class="quick-action-btn" data-click="cleanupConfig">
                    <span class="quick-action-icon">🧹</span>
                    <span class="quick-action-label">Nettoyer</span>
                    <span class="quick-action-sub">Clés obsolètes</span>
                </button>
            </div>
        `;

    // ── Mini Log Feed ──────────────────────────────
    const levelColors = { DEBUG: '#8b949e', INFO: '#58a6ff', WARNING: '#d29922', ERROR: '#f85149', CRITICAL: '#ff7b72' };
    const recentLogs = isDeveloper() && state.logs.length > 0 ? [...state.logs].reverse().slice(0, 10) : [];
    const miniLogsHtml = recentLogs.length > 0 ? `
            <div class="section-label">Activité récente</div>
            <div class="u-mb-0 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Dernières entrées de log</div>
                        <div class="card-subtitle">${state.logs.length} entrée(s) en mémoire</div>
                    </div>
                    <button class="btn btn-ghost btn-sm" data-click="navigate" data-click-args="[&quot;logs&quot;]">Voir tout →</button>
                </div>
                <div class="u-bg-0d1117 u-bd-1-solid-border u-br-radius u-ov-hidden">
                    <div class="mini-log-feed">
                        ${recentLogs.map(entry => {
                        const time = new Date(entry.timestamp * 1000).toLocaleTimeString('fr-FR', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
                        const rowClass = entry.level === 'ERROR' || entry.level === 'CRITICAL' ? 'has-error'
                            : entry.level === 'WARNING' ? 'has-warning' : '';
                        return String(html`
                                <div class="mini-log-line ${raw(rowClass)}">
                                    <span class="mini-log-time">${raw(time)}</span>
                                    <span class="mini-log-level" style="color:${raw(levelColors[entry.level] || '#c9d1d9')}">${raw(entry.level)}</span>
                                    <span class="mini-log-msg" title="${entry.message}">${entry.message}</span>
                                </div>
                            `);
                    }).join('')}
                    </div>
                </div>
            </div>
        ` : '';

    return heroHtml + serverCardsHtml + quickActionsHtml + miniLogsHtml;
}
