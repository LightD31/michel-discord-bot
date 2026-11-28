// The bot's persistent messages card.
import { html, raw } from '../core/html.js';
import { EL, actionArgs, dataArg, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { fetchServerChannels } from '../forms/pickers/channels.js';
import { loadCard } from '../ui/card.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

async function botMessagesHtml(serverId, items) {
    const channels = new Map(((await fetchServerChannels(serverId))?.channels || []).map(c => [String(c.id), c.name]));
    const where = (id) => channels.has(String(id))
        ? String(html`#${channels.get(String(id))}`)
        : String(html`<span title="${id}">salon introuvable</span>`);
    return items.map(m => String(html`
            <div class="u-mb-8 message-status configured">
                <span class="status-icon">📌</span>
                <span class="status-text"><strong>${m.label}</strong> · ${raw(where(m.channelId))}</span>
                <a class="status-link" href="${m.url}" target="_blank" rel="noreferrer">↗ voir</a>
                <button type="button" class="btn-status-reset" data-slot="${m.slot}"
                        data-click="republishBotMessage" data-click-args="${actionArgs(serverId, dataArg('slot'), EL)}"
                        title="Supprimer le message pour que le bot le reposte">🔄 Republier</button>
            </div>`)).join('');
}

export async function loadBotMessages(serverId) {
    await loadCard(
        'bot-messages-list',
        `/api/servers/${encodeURIComponent(serverId)}/bot-messages`,
        data => botMessagesHtml(serverId, data.messages),
        {
            empty: "Aucun message pour l'instant : chaque module poste le sien à son premier rafraîchissement.",
            isEmpty: data => !(data?.messages || []).length,
        },
    );
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
