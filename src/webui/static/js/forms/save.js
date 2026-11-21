// Saving a module form, and publishing the Embed manager.
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { isDeveloper, loadConfig, loadServers } from '../core/data.js';
import { setDirty } from '../core/dirty.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';
import { collectFormValues } from './schema-form.js';

// ── Module save/reload ────────────────────────────
// Commands are pushed to Discord right after a save/reload/toggle; report it.
export function commandSyncSuffix(result) {
    if (!result?.commandSync) return '';
    return result.commandSync.synced
        ? ' — commandes synchronisées avec Discord'
        : ` — synchronisation des commandes échouée : ${result.commandSync.error}`;
}

export async function saveModuleForm(serverId, moduleName, formId) {
    try {
        const values = collectFormValues(formId);
        const schema = state.moduleSchemas[moduleName];
        const server = state.servers?.[serverId];
        let config;
        if (schema?.directValue) {
            // For directValue modules (e.g. discord2name), the field value IS the config
            config = values[moduleName] ?? {};
        } else {
            const existing = server?.config?.[moduleName] || {};
            config = { ...existing, ...values };
            // A field of the form left empty is removed, not kept from
            // the stored copy: emptying it is how an admin clears it.
            const form = document.querySelector(`[data-form-id="${formId}"]`);
            for (const el of form ? form.querySelectorAll('[data-field]') : []) {
                if (!(el.dataset.field in values)) delete config[el.dataset.field];
            }
        }
        const result = await api(`/api/servers/${serverId}/modules/${moduleName}`, {
            method: 'PUT',
            body: JSON.stringify({ config }),
        });
        const syncSuffix = commandSyncSuffix(result);
        const syncFailed = !!(result?.commandSync && !result.commandSync.synced);
        if (result?.reload?.reloaded && !result.reload.error) {
            toast(`Configuration sauvegardée et extension rechargée !${syncSuffix}`,
                syncFailed ? 'warning' : 'success');
        } else if (result?.reload?.skipped || !result?.reload) {
            toast(`Configuration sauvegardée !${syncSuffix}`, syncFailed ? 'warning' : 'success');
        } else {
            toast(`Configuration sauvegardée, rechargement échoué : ${result.reload.error}`, 'warning');
        }
        setDirty(false);
        await loadServers();
        if (isDeveloper()) await loadConfig();
        render();
    } catch (e) { /* handled by api() */ }
}

export async function publishEmbeds(serverId) {
    if (!(await askConfirm('Publier (ou mettre à jour) les embeds de ce serveur dans leurs salons ?', { title: 'Publier les embeds', confirm: 'Publier' }))) return;
    try {
        const result = await api(`/api/servers/${serverId}/modules/moduleEmbedManager/publish`, { method: 'POST' });
        toast(`✓ ${result?.count ?? 0} embed(s) publié(s) avec succès`, 'success');
    } catch (e) { /* handled by api() */ }
}

registerActions({
    publishEmbeds,
    saveModuleForm,
});
