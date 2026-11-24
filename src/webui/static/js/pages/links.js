// The short-link manager: the server's own Shlink, on its module page (P37).
import { html, raw } from '../core/html.js';
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

// ── Short Links (Shlink) ──────────────────────────
function shlinkApi(path = '') {
    return `/api/servers/${encodeURIComponent(state.shlink.serverId)}/shlink${path}`;
}

export async function loadShortLinks() {
    state.shlink.loading = true;
    try {
        state.shlink.status = await api(shlinkApi('/status'));
        if (!state.shlink.status?.configured) {
            state.shlink.items = [];
            state.shlink.pagination = {};
            return;
        }
        const params = new URLSearchParams();
        params.set('page', String(state.shlink.page || 1));
        if (state.shlink.search) params.set('search', state.shlink.search);
        const data = await api(shlinkApi(`/short-urls?${params}`));
        state.shlink.items = data?.short_urls || [];
        state.shlink.pagination = data?.pagination || {};
    } catch (e) {
        // api() has said why; the card leaves its spinner for the "no instance" state.
        state.shlink.status ||= { configured: false };
        state.shlink.items = [];
        state.shlink.pagination = {};
    } finally {
        state.shlink.loading = false;
    }
}

export function renderShortLinkRow(item) {
    const editing = state.shlink.editing === item.short_code;
    const code = item.short_code;
    if (editing) {
        return String(html`
                <div class="u-bd-1-solid-accent u-br-radius u-p-12 u-mb-8 u-d-flex u-fd-column u-gap-8">
                    <div><a href="${item.short_url}" target="_blank" rel="noopener">${item.short_url}</a></div>
                    <div>
                        <label class="form-label">URL de destination</label>
                        <input type="url" class="form-input" id="shlink-edit-url" value="${item.long_url}" maxlength="4096" aria-label="URL cible">
                    </div>
                    <div>
                        <label class="form-label">Titre</label>
                        <input type="text" class="form-input" id="shlink-edit-title" value="${item.title}" placeholder="Optionnel" maxlength="512">
                    </div>
                    <div>
                        <label class="form-label">Tags (séparés par des virgules)</label>
                        <input type="text" class="form-input" id="shlink-edit-tags" value="${(item.tags || []).join(', ')}">
                    </div>
                    <div class="u-d-flex u-gap-6 u-jc-flex-end">
                        <button class="btn btn-ghost btn-sm" data-click="cancelEditShortLink">Annuler</button>
                        <button class="btn btn-success btn-sm" data-click="saveShortLink" data-click-args="${actionArgs(item.short_code)}">💾 Enregistrer</button>
                    </div>
                </div>
            `);
    }
    const tags = (item.tags || []).map(t =>
        String(html`<span class="u-bg-bg-tertiary u-br-radius u-p-2-8 u-fs-12 u-c-text-secondary">${t}</span>`)
    ).join(' ');
    return String(html`
            <div class="u-bd-1-solid-border u-br-radius u-p-12 u-mb-8 u-d-flex u-fd-column u-gap-6">
                <div class="u-d-flex u-ai-center u-jc-space-between u-gap-8 u-wrap-wrap">
                    <div class="u-minw-0">
                        <a href="${item.short_url}" target="_blank" rel="noopener"><strong>${item.short_url}</strong></a>
                        ${raw(item.title ? String(html`<span class="u-c-text-muted u-ml-8">${item.title}</span>`) : '')}
                    </div>
                    <div class="u-d-flex u-gap-6 u-ai-center">
                        <span class="u-c-text-muted u-fs-12">${raw(item.visits ?? 0)} visite(s)</span>
                        <button class="btn btn-ghost btn-sm" data-click="copyShortLink" data-click-args="${actionArgs(item.short_url)}" title="Copier" aria-label="Copier">📋</button>
                        <button class="btn btn-ghost btn-sm" data-click="startEditShortLink" data-click-args="${actionArgs(item.short_code)}">✏️ Modifier</button>
                        <button class="u-c-danger btn btn-ghost btn-sm" data-click="deleteShortLink" data-click-args="${actionArgs(item.short_code)}" aria-label="Supprimer le lien ${code}" title="Supprimer">🗑️</button>
                    </div>
                </div>
                <div class="u-fs-13 u-c-text-secondary u-wb-break-all">↳ ${item.long_url}</div>
                ${raw(tags ? `<div class="u-d-flex u-gap-4 u-wrap-wrap">${tags}</div>` : '')}
            </div>
        `);
}

export function renderShortLinksCards() {
    const s = state.shlink;
    const st = s.status || {};
    if (s.status && !st.configured) {
        return `
                <div class="u-mt-16 card">
                    <div class="empty-state">
                        <div class="icon">🔗</div>
                        <h3>Pas encore d'instance Shlink</h3>
                        <p>Renseigne l'URL de l'instance et la clé API dans les réglages ci-dessus et enregistre :
                        les liens courts du serveur se gèrent ensuite ici.</p>
                    </div>
                </div>
            `;
    }
    if (!s.status) return '<div class="u-mt-16 card"><div class="loading"><div class="spinner"></div></div></div>';
    const pag = s.pagination || {};
    const currentPage = pag.currentPage || s.page || 1;
    const pagesCount = pag.pagesCount || 1;
    return String(html`
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Nouveau lien court</div>
                        <div class="card-subtitle">
                            Instance : <a href="${st.base_url}" target="_blank" rel="noopener">${st.base_url}</a>.
                            Le slug est généré automatiquement s'il est laissé vide.
                        </div>
                    </div>
                </div>
                <div class="u-d-flex u-fd-column u-gap-12">
                    <div>
                        <label class="form-label">URL de destination</label>
                        <input type="url" class="form-input" id="shlink-new-url" placeholder="https://exemple.com/une-page-tres-longue" maxlength="4096">
                    </div>
                    <div class="u-d-flex u-gap-12 u-wrap-wrap">
                        <div class="u-fx-1 u-minw-180">
                            <label class="form-label">Titre</label>
                            <input type="text" class="form-input" id="shlink-new-title" placeholder="Optionnel" maxlength="512">
                        </div>
                        <div class="u-fx-1 u-minw-180">
                            <label class="form-label">Slug personnalisé</label>
                            <input type="text" class="form-input" id="shlink-new-slug" placeholder="ma-page" maxlength="128">
                        </div>
                        <div class="u-fx-1 u-minw-180">
                            <label class="form-label">Tags (séparés par des virgules)</label>
                            <input type="text" class="form-input" id="shlink-new-tags" placeholder="${(st.default_tags || []).join(', ')}">
                        </div>
                    </div>
                    <div class="u-d-flex u-jc-flex-end">
                        <button class="btn btn-primary btn-sm" data-click="createShortLink">➕ Créer</button>
                    </div>
                </div>
            </div>

            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Liens existants</div>
                        <div class="card-subtitle">${raw(pag.totalItems ?? s.items.length)} lien(s) · page ${raw(currentPage)} / ${raw(pagesCount)}</div>
                    </div>
                    <div class="u-d-flex u-gap-8">
                        <input type="text" class="u-w-200 form-input" id="shlink-search"
                            placeholder="Rechercher…" value="${s.search || ''}"
                            data-keydown="search-on-enter" data-keydown-args="[{&quot;$&quot;:&quot;event&quot;}]">
                        <button class="btn btn-primary btn-sm" data-click="searchShortLinks">🔍</button>
                    </div>
                </div>
                ${raw(s.loading
                ? '<div class="loading"><div class="spinner"></div></div>'
                : s.items.length === 0
                    ? `<div class="empty-state"><h3>Aucun lien</h3><p>Crée-en un avec le formulaire ci-dessus.</p></div>`
                    : s.items.map(renderShortLinkRow).join(''))}
                ${raw(pagesCount > 1 ? `
                    <div class="u-d-flex u-gap-8 u-jc-center u-mt-12">
                        <button class="btn btn-ghost btn-sm" ${currentPage <= 1 ? 'disabled' : ''} data-click="gotoShortLinkPage" data-click-args="${actionArgs(currentPage - 1)}">← Précédent</button>
                        <button class="btn btn-ghost btn-sm" ${currentPage >= pagesCount ? 'disabled' : ''} data-click="gotoShortLinkPage" data-click-args="${actionArgs(currentPage + 1)}">Suivant →</button>
                    </div>
                ` : '')}
            </div>
        `);
}

export function _shlinkTags(value) {
    return (value || '').split(',').map(t => t.trim()).filter(Boolean);
}

export async function createShortLink() {
    const url = document.getElementById('shlink-new-url')?.value.trim();
    if (!url) {
        toast('URL de destination requise', 'error');
        return;
    }
    try {
        await api(shlinkApi('/short-urls'), {
            method: 'POST',
            body: JSON.stringify({
                long_url: url,
                title: document.getElementById('shlink-new-title')?.value.trim() || null,
                custom_slug: document.getElementById('shlink-new-slug')?.value.trim() || null,
                tags: _shlinkTags(document.getElementById('shlink-new-tags')?.value),
            }),
        });
        toast('Lien court créé', 'success');
        state.shlink.page = 1;
        await loadShortLinks();
        render();
    } catch (e) { /* handled by api() */ }
}

export function startEditShortLink(shortCode) {
    state.shlink.editing = shortCode;
    render();
}

export function cancelEditShortLink() {
    state.shlink.editing = null;
    render();
}

export async function saveShortLink(shortCode) {
    const url = document.getElementById('shlink-edit-url')?.value.trim();
    if (!url) {
        toast('URL de destination requise', 'error');
        return;
    }
    try {
        await api(shlinkApi(`/short-urls/${encodeURIComponent(shortCode)}`), {
            method: 'PATCH',
            body: JSON.stringify({
                long_url: url,
                title: document.getElementById('shlink-edit-title')?.value.trim() || '',
                tags: _shlinkTags(document.getElementById('shlink-edit-tags')?.value),
            }),
        });
        toast('Lien court mis à jour', 'success');
        state.shlink.editing = null;
        await loadShortLinks();
        render();
    } catch (e) { /* handled by api() */ }
}

export async function deleteShortLink(shortCode) {
    if (!(await askConfirm(`Supprimer le lien court « ${shortCode} » ? Les visiteurs recevront une erreur 404.`, { title: 'Supprimer le lien', confirm: 'Supprimer', danger: true }))) return;
    try {
        await api(shlinkApi(`/short-urls/${encodeURIComponent(shortCode)}`), { method: 'DELETE' });
        toast('Lien court supprimé', 'success');
        await loadShortLinks();
        render();
    } catch (e) { /* handled by api() */ }
}

export async function searchShortLinks() {
    state.shlink.search = document.getElementById('shlink-search')?.value.trim() || '';
    state.shlink.page = 1;
    await loadShortLinks();
    render();
}

export async function gotoShortLinkPage(page) {
    state.shlink.page = Math.max(1, page);
    await loadShortLinks();
    render();
}

export async function copyShortLink(shortUrl) {
    try {
        await navigator.clipboard.writeText(shortUrl);
        toast('Lien copié', 'success');
    } catch (e) {
        toast('Copie impossible', 'error');
    }
}

registerActions({
    cancelEditShortLink,
    copyShortLink,
    createShortLink,
    deleteShortLink,
    gotoShortLinkPage,
    saveShortLink,
    searchShortLinks,
    startEditShortLink,
    'search-on-enter': (event) => { if (event.key === 'Enter') searchShortLinks(); },
});
