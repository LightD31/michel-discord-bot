// The bot's persistent messages card.
import { html, raw } from '../core/html.js';
import { EL, actionArgs, dataArg, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { fetchServerChannels } from '../forms/pickers/channels.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

export async function loadBotMessages(serverId) {
    const container = document.getElementById('bot-messages-list');
    if (!container) return;
    let items = [];
    try {
        const data = await api(`/api/servers/${encodeURIComponent(serverId)}/bot-messages`);
        items = data?.messages || [];
    } catch (e) {
        container.innerHTML = `<p class="u-c-text-muted">Liste indisponible.</p>`;
        return;
    }
    if (items.length === 0) {
        container.innerHTML = `<p class="u-c-text-muted">Aucun message pour l'instant : chaque module poste le sien à son premier rafraîchissement.</p>`;
        return;
    }
    const channels = new Map(((await fetchServerChannels(serverId))?.channels || []).map(c => [String(c.id), c.name]));
    const where = (id) => channels.has(String(id))
        ? String(html`#${channels.get(String(id))}`)
        : String(html`<span title="${id}">salon introuvable</span>`);
    container.innerHTML = items.map(m => String(html`
            <div class="u-mb-8 message-status configured">
                <span class="status-icon">📌</span>
                <span class="status-text"><strong>${m.label}</strong> · ${raw(where(m.channelId))}</span>
                <a class="status-link" href="${m.url}" target="_blank" rel="noreferrer">↗ voir</a>
                <button type="button" class="btn-status-reset" data-slot="${m.slot}"
                        data-click="republishBotMessage" data-click-args="${actionArgs(serverId, dataArg('slot'), EL)}"
                        title="Supprimer le message pour que le bot le reposte">🔄 Republier</button>
            </div>`)).join('');
}

export async function republishBotMessage(serverId, slot, btn) {
    if (!(await askConfirm('Supprimer ce message ? Le bot le repostera à son prochain rafraîchissement.', { title: 'Republier le message', confirm: 'Supprimer et republier', danger: true }))) return;
    if (btn) btn.disabled = true;
    try {
        await api(`/api/servers/${encodeURIComponent(serverId)}/bot-messages/republish`, {
            method: 'POST',
            body: JSON.stringify({ slot }),
        });
        toast('Message supprimé — il sera reposté au prochain rafraîchissement du module.', 'success');
    } catch (e) { /* handled by api() */ }
    await loadBotMessages(serverId);
}

registerActions({
    republishBotMessage,
});
