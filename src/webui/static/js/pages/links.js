// The short-link manager (Shlink).
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

// ── Short Links (Shlink) ──────────────────────────
export async function loadShortLinks() {
    state.shlink.loading = true;
    try {
        state.shlink.status = await api('/api/shlink/status');
        if (!state.shlink.status?.configured) {
            state.shlink.items = [];
            state.shlink.pagination = {};
            return;
        }
        const params = new URLSearchParams();
        params.set('page', String(state.shlink.page || 1));
        if (state.shlink.search) params.set('search', state.shlink.search);
        const data = await api(`/api/shlink/short-urls?${params}`);
        state.shlink.items = data?.short_urls || [];
        state.shlink.pagination = data?.pagination || {};
    } catch (e) {
        state.shlink.items = [];
        state.shlink.pagination = {};
    } finally {
        state.shlink.loading = false;
    }
}

export function renderShortLinkRow(item) {
    const editing = state.shlink.editing === item.short_code;
    const code = escapeHtml(item.short_code);
    if (editing) {
        return `
                <div style="border:1px solid var(--accent); border-radius:var(--radius); padding:12px; margin-bottom:8px; display:flex; flex-direction:column; gap:8px;">
                    <div><a href="${escapeHtml(item.short_url)}" target="_blank" rel="noopener">${escapeHtml(item.short_url)}</a></div>
                    <div>
                        <label class="form-label">URL de destination</label>
                        <input type="url" class="form-input" id="shlink-edit-url" value="${escapeHtml(item.long_url)}" maxlength="4096" aria-label="URL cible">
                    </div>
                    <div>
                        <label class="form-label">Titre</label>
                        <input type="text" class="form-input" id="shlink-edit-title" value="${escapeHtml(item.title)}" placeholder="Optionnel" maxlength="512">
                    </div>
                    <div>
                        <label class="form-label">Tags (séparés par des virgules)</label>
                        <input type="text" class="form-input" id="shlink-edit-tags" value="${escapeHtml((item.tags || []).join(', '))}">
                    </div>
                    <div style="display:flex; gap:6px; justify-content:flex-end;">
                        <button class="btn btn-ghost btn-sm" data-click="cancelEditShortLink">Annuler</button>
                        <button class="btn btn-success btn-sm" data-click="saveShortLink" data-click-args="${actionArgs(item.short_code)}">💾 Enregistrer</button>
                    </div>
                </div>
            `;
    }
    const tags = (item.tags || []).map(t =>
        `<span style="background:var(--bg-tertiary); border-radius:var(--radius); padding:2px 8px; font-size:12px; color:var(--text-secondary);">${escapeHtml(t)}</span>`
    ).join(' ');
    return `
            <div style="border:1px solid var(--border); border-radius:var(--radius); padding:12px; margin-bottom:8px; display:flex; flex-direction:column; gap:6px;">
                <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
                    <div style="min-width:0;">
                        <a href="${escapeHtml(item.short_url)}" target="_blank" rel="noopener"><strong>${escapeHtml(item.short_url)}</strong></a>
                        ${item.title ? `<span style="color:var(--text-muted); margin-left:8px;">${escapeHtml(item.title)}</span>` : ''}
                    </div>
                    <div style="display:flex; gap:6px; align-items:center;">
                        <span style="color:var(--text-muted); font-size:12px;">${item.visits ?? 0} visite(s)</span>
                        <button class="btn btn-ghost btn-sm" data-click="copyShortLink" data-click-args="${actionArgs(item.short_url)}" title="Copier" aria-label="Copier">📋</button>
                        <button class="btn btn-ghost btn-sm" data-click="startEditShortLink" data-click-args="${actionArgs(item.short_code)}">✏️ Modifier</button>
                        <button class="btn btn-ghost btn-sm" style="color:var(--danger)" data-click="deleteShortLink" data-click-args="${actionArgs(item.short_code)}" aria-label="Supprimer le lien ${code}" title="Supprimer">🗑️</button>
                    </div>
                </div>
                <div style="font-size:13px; color:var(--text-secondary); word-break:break-all;">↳ ${escapeHtml(item.long_url)}</div>
                ${tags ? `<div style="display:flex; gap:4px; flex-wrap:wrap;">${tags}</div>` : ''}
            </div>
        `;
}

export function renderShortLinksPage() {
    const s = state.shlink;
    const st = s.status || {};
    if (!st.configured) {
        return `
                <div class="page-header">
                    <h2>Liens courts</h2>
                    <p>Raccourcisseur Shlink — création, modification et suppression des liens publiés par le bot.</p>
                </div>
                <div class="empty-state">
                    <div class="icon">🔗</div>
                    <h3>Shlink n'est pas configuré</h3>
                    <p>Renseigne l'URL de l'instance et la clé API dans la section « Shlink » de la
                    <a href="#/global" data-click="navigate" data-click-args="[&quot;global&quot;]" data-prevent>config globale</a>.</p>
                </div>
            `;
    }
    const pag = s.pagination || {};
    const currentPage = pag.currentPage || s.page || 1;
    const pagesCount = pag.pagesCount || 1;
    return `
            <div class="page-header">
                <h2>Liens courts</h2>
                <p>
                    Instance : <a href="${escapeHtml(st.base_url)}" target="_blank" rel="noopener">${escapeHtml(st.base_url)}</a> ·
                    Raccourcissement automatique des liens du bot :
                    ${st.enabled
                    ? '<span style="color:var(--success)">activé</span>'
                    : '<span style="color:var(--text-muted)">désactivé</span>'}
                    (<a href="#/global" data-click="navigate" data-click-args="[&quot;global&quot;]" data-prevent>config globale</a>)
                </p>
            </div>

            <div class="card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Nouveau lien court</div>
                        <div class="card-subtitle">Le slug est généré automatiquement s'il est laissé vide.</div>
                    </div>
                </div>
                <div style="display:flex; flex-direction:column; gap:12px;">
                    <div>
                        <label class="form-label">URL de destination</label>
                        <input type="url" class="form-input" id="shlink-new-url" placeholder="https://exemple.com/une-page-tres-longue" maxlength="4096">
                    </div>
                    <div style="display:flex; gap:12px; flex-wrap:wrap;">
                        <div style="flex:1; min-width:180px;">
                            <label class="form-label">Titre</label>
                            <input type="text" class="form-input" id="shlink-new-title" placeholder="Optionnel" maxlength="512">
                        </div>
                        <div style="flex:1; min-width:180px;">
                            <label class="form-label">Slug personnalisé</label>
                            <input type="text" class="form-input" id="shlink-new-slug" placeholder="ma-page" maxlength="128">
                        </div>
                        <div style="flex:1; min-width:180px;">
                            <label class="form-label">Tags (séparés par des virgules)</label>
                            <input type="text" class="form-input" id="shlink-new-tags" placeholder="${escapeHtml((st.default_tags || []).join(', '))}">
                        </div>
                    </div>
                    <div style="display:flex; justify-content:flex-end;">
                        <button class="btn btn-primary btn-sm" data-click="createShortLink">➕ Créer</button>
                    </div>
                </div>
            </div>

            <div class="card" style="margin-top:16px;">
                <div class="card-header">
                    <div>
                        <div class="card-title">Liens existants</div>
                        <div class="card-subtitle">${pag.totalItems ?? s.items.length} lien(s) · page ${currentPage} / ${pagesCount}</div>
                    </div>
                    <div style="display:flex; gap:8px;">
                        <input type="text" class="form-input" id="shlink-search" style="width:200px;"
                            placeholder="Rechercher…" value="${escapeHtml(s.search || '')}"
                            data-keydown="search-on-enter" data-keydown-args="[{&quot;$&quot;:&quot;event&quot;}]">
                        <button class="btn btn-primary btn-sm" data-click="searchShortLinks">🔍</button>
                    </div>
                </div>
                ${s.loading
                ? '<div class="loading"><div class="spinner"></div></div>'
                : s.items.length === 0
                    ? `<div class="empty-state"><h3>Aucun lien</h3><p>Créez-en un avec le formulaire ci-dessus.</p></div>`
                    : s.items.map(renderShortLinkRow).join('')}
                ${pagesCount > 1 ? `
                    <div style="display:flex; gap:8px; justify-content:center; margin-top:12px;">
                        <button class="btn btn-ghost btn-sm" ${currentPage <= 1 ? 'disabled' : ''} data-click="gotoShortLinkPage" data-click-args="${actionArgs(currentPage - 1)}">← Précédent</button>
                        <button class="btn btn-ghost btn-sm" ${currentPage >= pagesCount ? 'disabled' : ''} data-click="gotoShortLinkPage" data-click-args="${actionArgs(currentPage + 1)}">Suivant →</button>
                    </div>
                ` : ''}
            </div>
        `;
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
        await api('/api/shlink/short-urls', {
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
        await api(`/api/shlink/short-urls/${encodeURIComponent(shortCode)}`, {
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
        await api(`/api/shlink/short-urls/${encodeURIComponent(shortCode)}`, { method: 'DELETE' });
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
