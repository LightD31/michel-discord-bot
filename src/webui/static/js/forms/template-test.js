// The "✉️ Tester en MP" button under a message template, and its live preview (C4).
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { state } from '../core/state.js';
import { discordMarkdown, discordMessage } from '../ui/discord.js';
import { toast } from '../ui/toast.js';
import { fetchServerChannels } from './pickers/channels.js';
import { fetchRoleChoices } from './pickers/roles.js';

// ── Template test (F11) ───────────────────────────
// The texts currently in the field, saved or not, rendered by the bot and DMed to you.
export function templateTexts(fieldEl) {
    if (!fieldEl) return [];
    const pick = sel => [...fieldEl.querySelectorAll(sel)].map(i => i.value.trim()).filter(Boolean);
    const texts = pick('[data-msg-field="message"]')
        .concat(pick('[data-response-field="text"]'));
    if (texts.length) return texts;
    return pick('input[data-type="string"], textarea[data-field]');
}

export async function testTemplate(serverId, moduleName, field, btn) {
    const texts = templateTexts(btn?.closest('.form-field'));
    if (!texts.length) {
        toast('Rien à tester : écris au moins un message.', 'info');
        return;
    }
    if (btn) btn.disabled = true;
    try {
        const result = await api(`/api/servers/${encodeURIComponent(serverId)}/template-test`, {
            method: 'POST',
            body: JSON.stringify({ module: moduleName, field, texts }),
        });
        toast(`${result.sent} message(s) de test envoyé(s) en MP.`, 'success');
        for (const warning of result.warnings || []) toast(warning, 'warning');
    } catch (e) { /* handled by api() */ }
    finally { if (btn) btn.disabled = false; }
}

// ── Live preview (C4) ─────────────────────────────
// Under each template field, its messages as Discord will show them. The
// server renders them with the bot's own function (the admin stands in for
// the member); discord.js draws the result, mentions by name.
const PREVIEW_DELAY_MS = 400;
const previewTimers = new WeakMap();
const previewSeq = new WeakMap();

async function mentionNames(serverId) {
    const [channels, roles] = await Promise.all([fetchServerChannels(serverId), fetchRoleChoices(serverId)]);
    const me = state.user;
    return {
        users: new Map(me ? [[String(me.user_id), me.global_name || me.username]] : []),
        channels: new Map((channels?.channels || []).map(c => [String(c.id), c.name])),
        roles: new Map((roles || []).map(r => [String(r.id), r.name])),
    };
}

export async function refreshPreview(box) {
    // Not data-field: that names a form value, which a save reads.
    const { server, module, previewField: field } = box.dataset;
    const texts = templateTexts(box.closest('.form-field')).slice(0, 10);
    const seq = (previewSeq.get(box) || 0) + 1;
    previewSeq.set(box, seq);
    if (!texts.length) {
        box.innerHTML = html`<p class="dc-empty">L'aperçu apparaît dès que tu écris un message.</p>`;
        return;
    }
    let data;
    try {
        data = await api(`/api/servers/${encodeURIComponent(server)}/template-preview`, {
            method: 'POST',
            body: JSON.stringify({ module, field, texts }),
            quiet: true,
        });
    } catch (e) {
        if (previewSeq.get(box) === seq) box.innerHTML = html`<p class="dc-empty">Aperçu indisponible pour le moment.</p>`;
        return;
    }
    const names = await mentionNames(server);
    if (previewSeq.get(box) !== seq) return;  // a newer edit is on its way
    box.innerHTML = html`${(data?.previews || []).filter(p => p.error || p.text).map(p => discordMessage(
        discordMarkdown(p.text || '', names), { warnings: p.warnings || [], error: p.error }))}`;
}

export function refreshPreviews(root = document) {
    for (const box of root.querySelectorAll('.discord-preview')) refreshPreview(box);
}

// A click too: removing a message (its ✕) changes the field without typing.
for (const type of ['input', 'change', 'click']) {
    document.addEventListener(type, (event) => {
        const field = event.target instanceof Element ? event.target.closest('.form-field') : null;
        const box = field?.querySelector(':scope > .discord-preview');
        if (!box) return;
        clearTimeout(previewTimers.get(box));
        previewTimers.set(box, setTimeout(() => refreshPreview(box), PREVIEW_DELAY_MS));
    });
}

registerActions({
    testTemplate,
});
