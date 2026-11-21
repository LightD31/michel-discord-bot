// The home page.
import { actionArgs } from '../core/actions.js';
import { isAdmin, isDeveloper } from '../core/data.js';
import { routeHash } from '../core/router.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';

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
    const heroHtml = `
            <div class="home-hero">
                <div class="home-hero-bot">
                    <div class="bot-avatar">🤖</div>
                    <div>
                        <div class="bot-name">${escapeHtml(bi.user || 'Michel Bot')}</div>
                        ${state.user ? `<div class="bot-tag">Connecté en tant que ${escapeHtml(state.user.username)}</div>` : ''}
                    </div>
                </div>
                <div class="home-hero-right">
                    ${admin ? `
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
                    `}
                    <span class="status-badge ${bi.status === 'online' ? 'online' : 'offline'}">
                        <span class="status-dot-pulse"></span>
                        ${bi.status === 'online' ? 'En ligne' : bi.status || 'Inconnu'}
                    </span>
                </div>
            </div>
        `;

    // ── Non-admin view ─────────────────────────────
    if (!admin) {
        return heroHtml + `
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title">Bienvenue, ${escapeHtml(state.user.username)} !</div>
                            <div class="card-subtitle">Tu es connecté·e en tant que membre. Seuls les administrateurs peuvent modifier la configuration du bot.</div>
                        </div>
                    </div>
                </div>
            `;
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
                return `
                        <div class="server-card" data-click="navigate" data-click-args="${actionArgs("server", id)}">
                            <a class="visually-hidden" href="${routeHash('server', id)}" data-click="navigate" data-click-args="${actionArgs("server", id)}" data-prevent>Ouvrir ${escapeHtml(s.name || id)}</a>
                            <div class="server-card-top">
                                <div class="server-card-icon">
                                    ${iconUrl ? `<img src="${escapeHtml(iconUrl)}" alt="">` : `<span>${escapeHtml(s.name?.charAt(0) || '?')}</span>`}
                                </div>
                                <div style="min-width:0;flex:1;">
                                    <div class="server-card-name" title="${escapeHtml(s.name || id)}">${escapeHtml(s.name || id)}</div>
                                    <div class="server-card-id">${id}</div>
                                </div>
                            </div>
                            <div class="server-card-footer">
                                <div class="server-card-pills">
                                    <span class="server-card-pill modules">${modCount} module${modCount !== 1 ? 's' : ''}</span>
                                    ${activeCount > 0 ? `<span class="server-card-pill active">${activeCount} actif${activeCount !== 1 ? 's' : ''}</span>` : ''}
                                </div>
                                <span class="server-card-arrow">→</span>
                            </div>
                        </div>
                    `;
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
            <div class="card" style="margin-bottom:0;">
                <div class="card-header">
                    <div>
                        <div class="card-title">Dernières entrées de log</div>
                        <div class="card-subtitle">${state.logs.length} entrée(s) en mémoire</div>
                    </div>
                    <button class="btn btn-ghost btn-sm" data-click="navigate" data-click-args="[&quot;logs&quot;]">Voir tout →</button>
                </div>
                <div style="background:#0d1117;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;">
                    <div class="mini-log-feed">
                        ${recentLogs.map(entry => {
                        const time = new Date(entry.timestamp * 1000).toLocaleTimeString('fr-FR', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
                        const rowClass = entry.level === 'ERROR' || entry.level === 'CRITICAL' ? 'has-error'
                            : entry.level === 'WARNING' ? 'has-warning' : '';
                        return `
                                <div class="mini-log-line ${rowClass}">
                                    <span class="mini-log-time">${time}</span>
                                    <span class="mini-log-level" style="color:${levelColors[entry.level] || '#c9d1d9'}">${entry.level}</span>
                                    <span class="mini-log-msg" title="${escapeHtml(entry.message)}">${escapeHtml(entry.message)}</span>
                                </div>
                            `;
                    }).join('')}
                    </div>
                </div>
            </div>
        ` : '';

    return heroHtml + serverCardsHtml + quickActionsHtml + miniLogsHtml;
}
