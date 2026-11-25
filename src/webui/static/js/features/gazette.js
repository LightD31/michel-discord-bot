// La Gazette: the preview of the week, and publishing it now.
import { html, raw } from '../core/html.js';
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { mdInline } from '../forms/schema-form.js';
import { SPINNER, loadCard } from '../ui/card.js';
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
    const fields = (data.fields || []).map(f => String(html`
            <div class="gazette-field">
                <div class="gazette-field-name">${f.name}</div>
                <div class="gazette-field-value">${raw(gazetteText(f.value))}</div>
            </div>`)).join('');
    return String(html`
            <div class="gazette-preview">
                <div class="gazette-title">${data.title || ''}</div>
                <div class="gazette-intro">${raw(gazetteText(data.intro))}</div>
                ${raw(fields)}
            </div>`);
}

export async function loadGazette(serverId) {
    const el = document.getElementById('gazette-preview');
    if (el) el.innerHTML = SPINNER;  // "↻ Actualiser" shows it is reading again
    await loadCard('gazette-preview', `/api/servers/${encodeURIComponent(serverId)}/gazette`, data => gazetteHtml(data || {}));
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
