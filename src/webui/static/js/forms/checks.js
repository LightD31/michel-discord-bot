// The "Tester" buttons (C9): a feed read now, a module's connection tried now.
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { state } from '../core/state.js';
import { discordMarkdown, discordMessage } from '../ui/discord.js';
import { showModal } from '../ui/modal.js';
import { toast } from '../ui/toast.js';

function feedField(entry, name) {
    return entry.querySelector(`[data-rss-field="${name}"]`)?.value?.trim() || '';
}

// An embed-mode entry: the frame Discord draws around it.
function embedHtml(item) {
    return html`
        <div class="dc-embed">
            <a class="dc-embed-title" href="${item.link}" target="_blank" rel="noopener noreferrer">${item.title || '(sans titre)'}</a>
            ${item.summary && html`<div class="dc-embed-text">${item.summary}</div>`}
            ${item.author && html`<div class="dc-embed-foot">par ${item.author}</div>`}
        </div>`;
}

// The feed of this row, as typed (saved or not), read now by the server.
export async function testRssFeed(btn) {
    const entry = btn.closest('.rssfeed-entry');
    const url = feedField(entry, 'url');
    if (!url) {
        toast('Écris d\'abord l\'adresse du flux.', 'info');
        return;
    }
    const label = feedField(entry, 'label') || feedField(entry, 'feed_id');
    const mode = feedField(entry, 'mode') === 'embed' ? 'embed' : 'text';
    btn.disabled = true;
    let data;
    try {
        data = await api(`/api/servers/${encodeURIComponent(state.currentServerId)}/rss/test`, {
            method: 'POST',
            body: JSON.stringify({ url, label, template: feedField(entry, 'template') }),
        });
    } catch (e) {
        return;
    } finally {
        btn.disabled = false;
    }
    const items = (data?.entries || []).map(item => discordMessage(
        mode === 'embed' ? embedHtml(item) : discordMarkdown(item.text)));
    document.getElementById('modal-title').textContent = `🧪 ${label || 'Flux RSS'}`;
    document.getElementById('modal-save-btn').style.display = 'none';
    document.getElementById('modal-overlay').querySelector('.modal-body').innerHTML = html`
        <p class="history-intro">${data.count} article(s) dans le flux. Les ${items.length} plus récents,
            comme Michel les posterait (${mode === 'embed' ? 'en embed' : 'en texte'}) :</p>
        <div class="discord-preview">${items}</div>`;
    showModal();
}

// The module's saved settings, tried against their service.
export async function checkModuleConnection(sid, mod, btn) {
    if (btn) btn.disabled = true;
    try {
        const result = await api(`/api/servers/${encodeURIComponent(sid)}/modules/${encodeURIComponent(mod)}/check`,
            { method: 'POST' });
        toast(`✅ ${result.message}`, 'success');
    } catch (e) { /* the reason is in the toast api() shows */ }
    finally { if (btn) btn.disabled = false; }
}

registerActions({
    checkModuleConnection,
    testRssFeed,
});
