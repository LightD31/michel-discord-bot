// The public XP and Spotify dashboards, and their links.
import { EVENT, VALUE, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';
import { XP_STATS_RANGES } from '../features/xp.js';
import { askConfirm } from '../ui/confirm.js';
import { showModal } from '../ui/modal.js';
import { toast } from '../ui/toast.js';

// ── Public dashboards ─────────────────────────────
// /public/{token}/{xp|spotify}: read-only, no login. Same renderers as the
// module pages, fed by the unauthenticated /api/public/... endpoints
// (src/webui/routes/public.py). Opt-in per guild via `publicDashboard`;
// `token` is the guild's random secret, not its id.
export const PUBLIC_DASHBOARDS = {
    xp: { tab: '⭐ XP', title: 'Statistiques XP' },
    spotify: { tab: '🎵 Playlist Spotify', title: 'Playlist Spotify' },
};

export function parsePublicPath(pathname = location.pathname) {
    // Any /public/... path stays on the public page; the server decides
    // whether the token is valid (an old /public/{guild id} link gets the 404).
    const m = /^\/public\/([^/]+)(?:\/(xp|spotify))?\/?$/.exec(pathname);
    return m ? { token: m[1], kind: m[2] || null } : null;
}

// Like api(), minus the session handling and toasts: errors are rendered in place.
export async function publicApi(url) {
    const resp = await fetch(url);
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
}

export async function initPublic(route) {
    state.publicView = { ...route, meta: null, error: null };
    render();
    try {
        state.publicView.meta = await publicApi(`/api/public/${route.token}`);
    } catch (e) {
        state.publicView.error = e.message || String(e);
    }
    const meta = state.publicView.meta;
    if (meta && !meta.dashboards.includes(state.publicView.kind)) {
        // /public/{token} or an unpublished kind: land on the first published one.
        state.publicView.kind = meta.dashboards[0];
        history.replaceState(null, '', `/public/${route.token}/${state.publicView.kind}`);
    }
    window.addEventListener('popstate', () => {
        const next = parsePublicPath();
        if (!next || !state.publicView?.meta?.dashboards.includes(next.kind)) {
            location.reload();
            return;
        }
        state.publicView.kind = next.kind;
        render();
    });
    render();
}

export function navigatePublic(event, kind) {
    // Let modified clicks (new tab, ...) through to the plain link.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return true;
    event.preventDefault();
    if (state.publicView.kind !== kind) {
        history.pushState(null, '', `/public/${state.publicView.token}/${kind}`);
        state.publicView.kind = kind;
        render();
    }
    return false;
}

export function renderPublicPage() {
    const { meta, kind, error } = state.publicView;
    if (error) {
        document.title = 'Michel Bot';
        return `<div class="public-page"><div class="empty-state">
                <div class="icon">🔒</div><h3>Page indisponible</h3>
                <p>${escapeHtml(error)}</p></div></div>`;
    }
    if (!meta) {
        return `<div class="public-page"><div class="loading"><div class="spinner"></div></div></div>`;
    }
    const dash = PUBLIC_DASHBOARDS[kind];
    document.title = `${meta.name} — ${dash.title}`;
    const iconUrl = meta.icon
        ? `https://cdn.discordapp.com/icons/${meta.id}/${encodeURIComponent(meta.icon)}.png?size=128`
        : null;
    const tabs = meta.dashboards.map(k => `
            <a class="public-tab ${k === kind ? 'active' : ''}" href="/public/${state.publicView.token}/${k}"
               data-click="navigatePublic" data-click-args="${actionArgs(EVENT, k)}">${PUBLIC_DASHBOARDS[k].tab}</a>`).join('');
    const body = kind === 'xp' ? `
            <div class="card">
                <div class="card-header">
                    <div>
                        <div class="card-title">📊 Activité XP</div>
                        <div class="card-subtitle">Classement et activité du serveur, heures de Paris.</div>
                    </div>
                    <div class="card-actions">
                        <select class="form-input" style="width:150px;" aria-label="Période" data-change="setXpStatsRange" data-change-args="${actionArgs(meta.id, VALUE)}">
                            ${XP_STATS_RANGES.map(d => `<option value="${d}" ${state.xpStatsDays === d ? 'selected' : ''}>${d} jours</option>`).join('')}
                        </select>
                    </div>
                </div>
                <div id="xp-stats" data-server-id="${meta.id}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>` : `
            <div class="card">
                <div class="card-header">
                    <div>
                        <div class="card-title">📊 Playlist et votes</div>
                        <div class="card-subtitle">Playlist et votes tels que le bot les a enregistrés.</div>
                    </div>
                </div>
                <div id="spotify-stats" class="viz-root" data-server-id="${meta.id}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>`;
    return `
            <div class="public-page">
                <div class="public-header">
                    <div class="server-icon">
                        ${iconUrl ? `<img src="${iconUrl}" alt="">` : escapeHtml(meta.name.charAt(0) || '?')}
                    </div>
                    <div>
                        <h1>${escapeHtml(meta.name)}</h1>
                        <p>${escapeHtml(dash.title)}</p>
                    </div>
                </div>
                ${meta.dashboards.length > 1 ? `<nav class="public-tabs">${tabs}</nav>` : ''}
                ${body}
                <div class="public-footer">Michel Bot · données actualisées toutes les 5 minutes</div>
            </div>
        `;
}

// Module page: the public link is a per-guild secret, fetched (and minted
// on first use) from the server, then shown in the modal.
export function publicPageButton(serverId, kind, modConfig) {
    if (!modConfig?.enabled || !modConfig?.publicDashboard) return '';
    return `<button class="btn btn-ghost btn-sm" data-click="showPublicLink" data-click-args="${actionArgs(serverId, kind)}"
                    title="Lien secret vers la page publique, accessible sans connexion">🌐 Page publique</button>`;
}

export async function showPublicLink(serverId, kind, rotate = false) {
    let data;
    try {
        data = await api(`/api/servers/${encodeURIComponent(serverId)}/public-dashboard${rotate ? '/rotate' : ''}`,
            { method: 'POST' });
    } catch (e) { return; /* handled by api() */ }
    if (!data) return;
    const url = `${location.origin}/public/${encodeURIComponent(data.token)}/${encodeURIComponent(kind)}`;
    const overlay = document.getElementById('modal-overlay');
    document.getElementById('modal-title').textContent = `🌐 Page publique — ${PUBLIC_DASHBOARDS[kind].title}`;
    document.getElementById('modal-save-btn').style.display = 'none';
    overlay.querySelector('.modal-body').innerHTML = `
            <p style="color:var(--text-secondary);font-size:13px;margin-bottom:12px;">
                Toute personne disposant de ce lien peut consulter la page, sans connexion.
                Régénérer le lien désactive immédiatement l'ancien (pour les deux pages publiques du serveur).
            </p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <input id="public-link-url" class="form-input" readonly value="${escapeHtml(url)}"
                       style="flex:1;min-width:220px;" data-click="select-self" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">
                <button class="btn btn-primary btn-sm" data-click="copyPublicLink">📋 Copier</button>
                <a class="btn btn-ghost btn-sm" href="${escapeHtml(url)}" target="_blank" rel="noopener">Ouvrir ↗</a>
            </div>
            <div style="margin-top:16px;">
                <button class="btn btn-ghost btn-sm" data-click="rotatePublicLink" data-click-args="${actionArgs(serverId, kind)}">🔄 Régénérer le lien</button>
            </div>
        `;
    showModal();
    if (rotate) toast('Nouveau lien généré : l\'ancien ne fonctionne plus.', 'success');
}

export async function copyPublicLink() {
    const input = document.getElementById('public-link-url');
    if (!input) return;
    try {
        await navigator.clipboard.writeText(input.value);
        toast('Lien copié !', 'success');
    } catch (e) {
        input.select();
        toast('Copie impossible : sélectionne le lien et copie-le à la main.', 'warning');
    }
}

export async function rotatePublicLink(serverId, kind) {
    if (!(await askConfirm('Les liens déjà partagés (XP et Spotify) cesseront de fonctionner.', { title: 'Régénérer le lien ?', confirm: 'Régénérer', danger: true }))) return;
    showPublicLink(serverId, kind, true);
}

registerActions({
    copyPublicLink,
    navigatePublic,
    rotatePublicLink,
    showPublicLink,
});
