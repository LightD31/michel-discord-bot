// The "✉️ Tester en MP" button under a message template.
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { toast } from '../ui/toast.js';

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

registerActions({
    testTemplate,
});
