// La Gazette: the preview of the week, and publishing it now.
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { escapeHtml } from '../core/util.js';
import { mdInline } from '../forms/schema-form.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

// ── La Gazette: a preview of the week, and publishing it now ────────
// The post's own Markdown, as Discord shows it: bold, code, and https links.
export function gazetteText(text) {
    return mdInline(text).replace(
        /\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}

export function gazetteHtml(data) {
    const fields = (data.fields || []).map(f => `
            <div class="gazette-field">
                <div class="gazette-field-name">${escapeHtml(f.name)}</div>
                <div class="gazette-field-value">${gazetteText(f.value)}</div>
            </div>`).join('');
    return `
            <div class="gazette-preview">
                <div class="gazette-title">${escapeHtml(data.title || '')}</div>
                <div class="gazette-intro">${gazetteText(data.intro)}</div>
                ${fields}
            </div>`;
}

export async function loadGazette(serverId) {
    const container = document.getElementById('gazette-preview');
    if (!container) return;
    container.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        const data = await api(`/api/servers/${encodeURIComponent(serverId)}/gazette`);
        container.innerHTML = gazetteHtml(data || {});
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(String(e.message || e))}</p></div>`;
    }
}

export async function publishGazette(serverId, btn) {
    if (!(await askConfirm("La Gazette de ces 7 derniers jours est publiée tout de suite dans son salon. L'édition de la semaine paraîtra quand même à son heure.", { title: 'Publier la Gazette maintenant ?', confirm: '📰 Publier' }))) return;
    if (btn) btn.disabled = true;
    try {
        const result = await api(`/api/servers/${encodeURIComponent(serverId)}/gazette/publish`, { method: 'POST' });
        toast(`Gazette publiée dans #${result.channel}.`, 'success');
    } catch (e) { /* handled by api() */ }
    finally { if (btn) btn.disabled = false; }
}

registerActions({
    loadGazette,
    publishGazette,
});
