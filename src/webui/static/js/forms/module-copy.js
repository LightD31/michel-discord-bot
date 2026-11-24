// A module's settings copied from another server the editor manages (P35).
// The server reads and writes them: a secret never comes to the page.
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { isDeveloper, loadConfig, loadServers } from '../core/data.js';
import { confirmDiscard, setDirty } from '../core/dirty.js';
import { html } from '../core/html.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { askConfirm } from '../ui/confirm.js';
import { showModal } from '../ui/modal.js';
import { toast } from '../ui/toast.js';
import { commandSyncSuffix } from './save.js';

function secretsNote(source) {
    if (!source.hasSecrets) return '';
    return source.secrets
        ? 'avec ses mots de passe et clés'
        : 'sans ses mots de passe et clés (réservés au propriétaire et aux administrateurs du serveur)';
}

export async function openModuleCopy(sid, mod) {
    let data;
    try {
        data = await api(`/api/servers/${encodeURIComponent(sid)}/modules/${encodeURIComponent(mod)}/copy-sources`);
    } catch (e) { return; }
    const sources = data?.sources || [];
    const label = state.moduleSchemas[mod]?.label || mod;
    document.getElementById('modal-title').textContent = `📋 Copier depuis un autre serveur — ${label}`;
    document.getElementById('modal-save-btn').hidden = true;
    document.getElementById('modal-overlay').querySelector('.modal-body').innerHTML = html`
        <p class="history-intro">Les réglages d'un autre serveur que tu gères remplacent ceux d'ici.
            Les salons, rôles et membres restent ceux de ce serveur, comme son interrupteur.</p>
        ${sources.length ? html`<ul class="copy-sources">${sources.map(source => html`
            <li class="copy-source">
                <div>
                    <strong>${source.name}</strong>
                    ${secretsNote(source) && html`<div class="u-c-text-muted u-fs-0_8rem">${secretsNote(source)}</div>`}
                </div>
                <button type="button" class="btn btn-primary btn-sm" data-click="copyModuleFrom"
                    data-click-args="${actionArgs(sid, mod, source.id, source.name, EL)}">Copier</button>
            </li>`)}</ul>`
        : html`<p class="history-empty">Aucun autre serveur que tu gères n'a réglé ce module.</p>`}`;
    showModal();
}

export async function copyModuleFrom(sid, mod, sourceId, sourceName, btn) {
    if (!(await confirmDiscard())) return;
    if (!(await askConfirm(`Les réglages du module sur ce serveur sont remplacés par ceux de « ${sourceName} ». L'ancienne version reste dans l'historique.`,
        { title: 'Copier les réglages', confirm: 'Copier' }))) return;
    if (btn) btn.disabled = true;
    let result;
    try {
        result = await api(`/api/servers/${encodeURIComponent(sid)}/modules/${encodeURIComponent(mod)}/copy-from`,
            { method: 'POST', body: JSON.stringify({ source: sourceId }) });
    } catch (e) {
        if (btn) btn.disabled = false;
        return;
    }
    const toFill = result?.toFill || [];
    toast(toFill.length
        ? `Réglages copiés. À régler ici : ${toFill.join(', ')}.${commandSyncSuffix(result)}`
        : `Réglages copiés.${commandSyncSuffix(result)}`, toFill.length ? 'warning' : 'success');
    document.querySelector('#modal-overlay [data-click="closeModal"]')?.click();
    setDirty(false);
    await loadServers();
    if (isDeveloper()) await loadConfig();
    render();
}

registerActions({
    copyModuleFrom,
    openModuleCopy,
});
