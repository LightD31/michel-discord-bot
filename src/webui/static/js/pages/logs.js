// The live log stream and its page.
import { html, raw } from '../core/html.js';
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';

export let logEventSource = null;

// ── Log Streaming ─────────────────────────────────
export async function loadLogs() {
    try {
        const params = new URLSearchParams();
        params.set('count', '500');
        if (state.logLevel) params.set('level', state.logLevel);
        if (state.logSearch) params.set('search', state.logSearch);
        const data = await api(`/api/logs?${params}`);
        state.logs = data?.logs || [];
    } catch (e) {
        state.logs = [];
    }
}

export function startLogStream() {
    if (logEventSource) return;
    state.logStreaming = true;

    logEventSource = new EventSource('/api/logs/stream');

    logEventSource.addEventListener('log', (event) => {
        try {
            const entry = JSON.parse(event.data);
            state.logs.push(entry);
            if (state.logs.length > 2000) {
                state.logs = state.logs.slice(-1500);
            }
            appendLogLine(entry);
        } catch (e) { /* ignore */ }
    });

    logEventSource.addEventListener('ping', () => { /* keepalive */ });

    logEventSource.onerror = () => {
        state.logStreaming = false;
        logEventSource?.close();
        logEventSource = null;
        setTimeout(() => {
            if (state.currentPage === 'logs') {
                startLogStream();
            }
        }, 5000);
    };
}

export function stopLogStream() {
    if (logEventSource) {
        logEventSource.close();
        logEventSource = null;
    }
    state.logStreaming = false;
}

export function appendLogLine(entry) {
    const container = document.getElementById('log-lines');
    if (!container) return;

    if (state.logLevel) {
        const levels = { 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50 };
        if ((levels[entry.level] || 0) < (levels[state.logLevel] || 0)) return;
    }
    if (state.logSearch) {
        const s = state.logSearch.toLowerCase();
        if (!entry.message.toLowerCase().includes(s) && !entry.logger.toLowerCase().includes(s)) return;
    }

    const line = document.createElement('div');
    line.className = 'log-line';
    line.innerHTML = formatLogLine(entry);
    container.appendChild(line);

    const countEl = document.getElementById('log-count');
    if (countEl) countEl.textContent = container.children.length;

    if (state.logAutoScroll) {
        const logContainer = document.getElementById('log-container');
        if (logContainer) {
            logContainer.scrollTop = logContainer.scrollHeight;
        }
    }
}

export function formatLogLine(entry) {
    const time = new Date(entry.timestamp * 1000).toLocaleTimeString('fr-FR', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    // Highlight on the raw text, then escape each piece: matching inside
    // the escaped HTML broke on a search for "amp" or "quot".
    let msg = escapeHtml(entry.message);
    if (state.logSearch) {
        const regex = new RegExp(`(${escapeRegExp(state.logSearch)})`, 'gi');
        msg = String(entry.message).split(regex).map((piece, i) => (i % 2
            ? String(html`<span class="log-search-highlight">${piece}</span>`)
            : escapeHtml(piece))).join('');
    }
    const parts = [
        `<span class="log-time">${time}</span>`,
        `<span class="log-level ${entry.level}">${entry.level.padEnd(8)}</span>`,
    ];
    if (state.logVerbose) {
        parts.push(String(html`<span class="log-logger" title="${entry.logger}">${entry.logger}</span>`));
        const loc = entry.filename ? `${entry.filename}:${entry.lineno}` : `:${entry.lineno}`;
        parts.push(String(html`<span class="log-lineno" title="${loc}">${loc}</span>`));
    }
    parts.push(`<span class="log-msg">${msg}</span>`);
    return parts.join('');
}

export function onLogVerboseChange(checked) {
    state.logVerbose = checked;
    renderLogLines();
}

export function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function handleLogScroll() {
    const el = document.getElementById('log-container');
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    state.logAutoScroll = atBottom;
    const btn = document.getElementById('autoscroll-btn');
    if (btn) btn.classList.toggle('visible', !atBottom);
}

export function scrollToBottom() {
    const el = document.getElementById('log-container');
    if (el) {
        el.scrollTop = el.scrollHeight;
        state.logAutoScroll = true;
    }
    const btn = document.getElementById('autoscroll-btn');
    if (btn) btn.classList.remove('visible');
}

export function refreshLogs() {
    loadLogs().then(() => renderLogLines());
}

export function renderLogLines() {
    const container = document.getElementById('log-lines');
    if (!container) return;

    let filtered = state.logs;
    if (state.logLevel) {
        const levels = { 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50 };
        const minLevel = levels[state.logLevel] || 0;
        filtered = filtered.filter(e => (levels[e.level] || 0) >= minLevel);
    }
    if (state.logSearch) {
        const s = state.logSearch.toLowerCase();
        filtered = filtered.filter(e =>
            e.message.toLowerCase().includes(s) || e.logger.toLowerCase().includes(s)
        );
    }

    container.innerHTML = filtered.map(e =>
        `<div class="log-line">${formatLogLine(e)}</div>`
    ).join('');

    const countEl = document.getElementById('log-count');
    if (countEl) countEl.textContent = filtered.length;

    if (state.logAutoScroll) {
        const logC = document.getElementById('log-container');
        if (logC) logC.scrollTop = logC.scrollHeight;
    }
}

export function onLogLevelChange(val) {
    state.logLevel = val;
    renderLogLines();
}

export let logSearchTimeout = null;
export function onLogSearchInput(val) {
    clearTimeout(logSearchTimeout);
    logSearchTimeout = setTimeout(() => {
        state.logSearch = val;
        renderLogLines();
    }, 250);
}

export function clearLogs() {
    state.logs = [];
    renderLogLines();
}

// ── Logs Page ─────────────────────────────────────
export function renderLogsPage() {
    const logCount = state.logs.length;
    const errorCount = state.logs.filter(l => l.level === 'ERROR' || l.level === 'CRITICAL').length;
    const warnCount = state.logs.filter(l => l.level === 'WARNING').length;

    return String(html`
            <div class="page-header">
                <h2>📋 Logs en temps réel</h2>
                <p>Visualisation des logs du bot avec streaming en direct</p>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Total</div>
                    <div class="stat-value" id="log-count">${raw(logCount)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Erreurs</div>
                    <div class="u-c-danger stat-value">${raw(errorCount)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Avertissements</div>
                    <div class="u-c-warning stat-value">${raw(warnCount)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Streaming</div>
                    <div class="stat-value">
                        <span class="log-stat-badge ${raw(state.logStreaming ? 'live' : 'paused')}">
                            ${raw(state.logStreaming ? '● En direct' : '○ Déconnecté')}
                        </span>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Console</div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="refreshLogs">🔄 Rafraîchir</button>
                        <button class="btn btn-ghost btn-sm" data-click="clearLogs">🗑️ Effacer</button>
                        <button class="btn btn-ghost btn-sm" data-click="downloadLogs">⬇️ Exporter</button>
                    </div>
                </div>

                <div class="log-toolbar">
                    <select data-change="onLogLevelChange" data-change-args="[{&quot;$&quot;:&quot;value&quot;}]" id="log-level-select">
                        <option value="">Tous les niveaux</option>
                        <option value="DEBUG" ${raw(state.logLevel === 'DEBUG' ? 'selected' : '')}>DEBUG</option>
                        <option value="INFO" ${raw(state.logLevel === 'INFO' ? 'selected' : '')}>INFO</option>
                        <option value="WARNING" ${raw(state.logLevel === 'WARNING' ? 'selected' : '')}>WARNING</option>
                        <option value="ERROR" ${raw(state.logLevel === 'ERROR' ? 'selected' : '')}>ERROR</option>
                        <option value="CRITICAL" ${raw(state.logLevel === 'CRITICAL' ? 'selected' : '')}>CRITICAL</option>
                    </select>
                    <input type="text" placeholder="🔍 Rechercher dans les logs..."
                        value="${state.logSearch}"
                        data-input="onLogSearchInput" data-input-args="[{&quot;$&quot;:&quot;value&quot;}]">
                    <label class="verbose-toggle" title="Afficher le nom du logger et l'emplacement (fichier:ligne)">
                        <input type="checkbox" ${raw(state.logVerbose ? 'checked' : '')}
                            data-change="onLogVerboseChange" data-change-args="[{&quot;$&quot;:&quot;checked&quot;}]">
                        Détaillé
                    </label>
                </div>

                <div class="log-container" id="log-container">
                    <div class="log-lines" id="log-lines">
                        ${raw(getFilteredLogs().map(e =>
                        `<div class="log-line">${formatLogLine(e)}</div>`
                    ).join(''))}
                    </div>
                    <button class="autoscroll-btn" id="autoscroll-btn" data-click="scrollToBottom">
                        ↓ Défiler en bas
                    </button>
                </div>
            </div>
        `);
}

export function getFilteredLogs() {
    let filtered = state.logs;
    if (state.logLevel) {
        const levels = { 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50 };
        const minLevel = levels[state.logLevel] || 0;
        filtered = filtered.filter(e => (levels[e.level] || 0) >= minLevel);
    }
    if (state.logSearch) {
        const s = state.logSearch.toLowerCase();
        filtered = filtered.filter(e =>
            e.message.toLowerCase().includes(s) || e.logger.toLowerCase().includes(s)
        );
    }
    return filtered;
}

export function downloadLogs() {
    const filtered = getFilteredLogs();
    const text = filtered.map(e => {
        const time = new Date(e.timestamp * 1000).toISOString();
        return `[${time}][${e.level}][${e.logger}] ${e.message}`;
    }).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `michel-bot-logs-${new Date().toISOString().slice(0,10)}.log`;
    a.click();
    URL.revokeObjectURL(url);
}

registerActions({
    clearLogs,
    downloadLogs,
    onLogLevelChange,
    onLogSearchInput,
    onLogVerboseChange,
    refreshLogs,
    scrollToBottom,
});
