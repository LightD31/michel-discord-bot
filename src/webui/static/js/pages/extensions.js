// The developers' extensions page, and reloading them.
import { html, raw } from '../core/html.js';
import { CHECKED, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { loadBotInfo, loadExtensions } from '../core/data.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { commandSyncSuffix } from '../forms/save.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

// ── Extensions Page ───────────────────────────────
export function renderExtensionsPage() {
    const exts = state.extensions;
    return `
            <div class="page-header">
                <h2>Extensions</h2>
                <p>Active ou désactive les extensions pour tout le bot. Les modifications sont appliquées immédiatement et persistées au redémarrage.</p>
            </div>

            ${exts.length === 0 ? `
                <div class="empty-state">
                    <div class="icon">📭</div>
                    <h3>Aucune extension trouvée</h3>
                    <p>Vérifie le dossier <code>extensions/</code>.</p>
                </div>
            ` : `
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title">Extensions disponibles</div>
                            <div class="card-subtitle">${exts.filter(e => e.loaded).length} chargée(s) / ${exts.length} disponible(s)</div>
                        </div>
                    </div>
                    <div class="module-list">
                        ${exts.map(ext => String(html`
                                <div class="module-item extension-item">
                                    <div class="module-info">
                                        <span class="module-name">${ext.icon || '📦'} ${ext.label || ext.filename}</span>
                                        ${raw(ext.description ? String(html`<span class="module-description">${ext.description}</span>`) : '')}
                                        <span class="module-status">
                                            ${raw(ext.loaded
                                            ? '<span class="u-c-success">● chargée</span>'
                                            : '<span class="u-c-text-muted">○ non chargée</span>')}
                                            · <code title="Fichier">${ext.filename}</code>
                                        </span>
                                    </div>
                                    <label class="toggle" data-click="stop">
                                        <input type="checkbox" ${raw(ext.enabled ? 'checked' : '')} aria-label="Activer l'extension ${ext.label || ext.path}"
                                            data-change="toggleExtension" data-change-args="${actionArgs(ext.path, CHECKED)}">
                                        <span class="toggle-slider"></span>
                                    </label>
                                </div>
                            `)).join('')}
                    </div>
                </div>
            `}
        `;
}

export function extensionLabel(extPath) {
    return (state.extensions || []).find(e => e.path === extPath)?.label || extPath;
}

export async function toggleExtension(extPath, enabled) {
    const extLabel = extensionLabel(extPath);
    if (!(await askConfirm(`${enabled ? 'Activer' : 'Désactiver'} « ${extLabel} » pour tout le bot ?`,
        { title: enabled ? 'Activer l\'extension' : 'Désactiver l\'extension', confirm: enabled ? 'Activer' : 'Désactiver', danger: !enabled }))) {
        render();  // put the switch back
        return;
    }
    try {
        const result = await api(`/api/extensions/${extPath}/toggle`, {
            method: 'POST',
            body: JSON.stringify({ enabled }),
        });
        if (result?.error) {
            toast(`${extPath} ${enabled ? 'activée' : 'désactivée'} dans la config, mais erreur : ${result.error}`, 'warning');
        } else {
            const status = result?.loaded ? 'chargée' : 'déchargée';
            const syncSuffix = commandSyncSuffix(result);
            const syncFailed = !!(result?.commandSync && !result.commandSync.synced);
            toast(`${extPath} ${enabled ? 'activée' : 'désactivée'} et ${status}${syncSuffix}`, syncFailed ? 'warning' : 'success');
        }
        await loadExtensions();
        await loadBotInfo();
        render();
    } catch (e) { /* handled by api() */ }
}

// ── Reload extensions ─────────────────────────────
export async function reloadAllExtensions() {
    if (!(await askConfirm('Les commandes en cours peuvent être interrompues.', { title: 'Recharger toutes les extensions ?', confirm: 'Recharger' }))) return;
    const btn = document.getElementById('reload-all-btn');
    const label = btn?.querySelector('.quick-action-label');
    const original = label?.textContent;
    if (btn) btn.disabled = true;
    if (label) label.textContent = 'Rechargement…';
    try {
        const result = await api('/api/reload', { method: 'POST' });
        if (result) {
            const ok = result.reloaded?.length || 0;
            const fail = result.failed?.length || 0;
            const syncSuffix = commandSyncSuffix(result);
            const syncFailed = !!(result?.commandSync && !result.commandSync.synced);
            if (fail > 0) {
                toast(`${ok} rechargée(s), ${fail} en erreur : ${result.failed.map(f => f.name).join(', ')}`, 'error');
            } else {
                toast(`${ok} extension(s) rechargée(s) — config appliquée !${syncSuffix}`, syncFailed ? 'warning' : 'success');
            }
        }
    } catch (e) { /* handled by api() */ }
    if (btn) btn.disabled = false;
    if (label && original) label.textContent = original;
}

registerActions({
    reloadAllExtensions,
    toggleExtension,
});
