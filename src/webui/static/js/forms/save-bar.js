// The save bar of a module page, Ctrl+S, and the module's history (C5).
//
// While a module's form has unsaved edits, a bar stays at the bottom of the
// screen: how many fields changed, Annuler, Tester (when a message template
// changed) and Enregistrer, which is the page's own "💾 Sauvegarder". The
// "🕘 Historique" button lists the settings each save replaced (kept 90 days,
// secrets left out: src/webui/config_history.py) with what changed, and
// "Restaurer" saves one back through the normal save route.
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { isDeveloper, loadConfig, loadServers } from '../core/data.js';
import { changedFields, confirmDiscard, formDirty, setDirty } from '../core/dirty.js';
import { html } from '../core/html.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { askConfirm } from '../ui/confirm.js';
import { showModal } from '../ui/modal.js';
import { toast } from '../ui/toast.js';
import { fetchServerChannels } from './pickers/channels.js';
import { fetchRoleChoices } from './pickers/roles.js';
import { commandSyncSuffix } from './save.js';
import { testTemplate } from './template-test.js';

const SAVE_BUTTON = '.main [data-click="saveModuleForm"]';

function templateFieldsChanged() {
    const tests = state.templateTests?.[state.currentModule] || {};
    return [...changedFields].filter(key => tests[key]);
}

// Built once: the bar's buttons are never replaced while it shows, or a click
// that blurs a field (whose change event updates the bar) would be lost.
function saveBar() {
    let bar = document.getElementById('save-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'save-bar';
        bar.className = 'save-bar';
        bar.hidden = true;
        bar.setAttribute('role', 'region');
        bar.setAttribute('aria-label', 'Modifications non enregistrées');
        bar.innerHTML = html`
            <span class="save-bar-count" role="status"></span>
            <span class="save-bar-actions">
                <button type="button" class="btn btn-ghost btn-sm" data-click="cancelEdits">Annuler</button>
                <button type="button" class="btn btn-ghost btn-sm save-bar-test" data-click="testChangedTemplates"
                    title="Le bot t'envoie en MP les messages modifiés">✉️ Tester</button>
                <button type="button" class="btn btn-success btn-sm" data-click="saveFromBar"
                    title="Ctrl+S">💾 Enregistrer</button>
            </span>`;
        document.body.appendChild(bar);
    }
    return bar;
}

export function refreshSaveBar() {
    const show = formDirty && state.currentPage === 'module' && changedFields.size > 0
        && !!document.querySelector(SAVE_BUTTON);
    const bar = saveBar();
    bar.hidden = !show;
    document.body.classList.toggle('has-save-bar', show);
    if (!show) return;
    const n = changedFields.size;
    bar.querySelector('.save-bar-count').textContent = `${n} champ${n > 1 ? 's' : ''} modifié${n > 1 ? 's' : ''}`;
    bar.querySelector('.save-bar-test').hidden = templateFieldsChanged().length === 0;
}

document.addEventListener('dirtychange', refreshSaveBar);

export function saveFromBar() {
    document.querySelector(SAVE_BUTTON)?.click();
}

export async function cancelEdits() {
    if (!(await askConfirm('Revenir aux réglages enregistrés ? Tes modifications seront perdues.',
        { title: 'Annuler les modifications', confirm: 'Annuler les modifications', danger: true }))) return;
    setDirty(false);
    render();
}

export async function testChangedTemplates() {
    for (const key of templateFieldsChanged()) {
        const btn = document.querySelector(`.main .form-field[data-key="${CSS.escape(key)}"] .template-test-btn`);
        if (btn) await testTemplate(state.currentServerId, state.currentModule, key, btn);
    }
}

// Ctrl+S (⌘S) saves the module page instead of saving the web page.
document.addEventListener('keydown', (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 's') return;
    const button = document.querySelector(SAVE_BUTTON);
    if (state.currentPage !== 'module' || !button) return;
    event.preventDefault();
    // As a click on the button would: the field being typed in commits its
    // change first, rather than after the save, which would mark the page modified.
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    if (formDirty) button.click();
    else toast('Rien à enregistrer.', 'info');
});

// ── History ───────────────────────────────────────
function names(list, byId, prefix = '') {
    return list.map(id => {
        const found = byId.get(String(id));
        return found ? `${prefix}${found}` : 'introuvable';
    }).join(', ');
}

// A setting as the admin reads it: channels and roles by name, never an id.
function describe(field, value, lookups) {
    if (value === undefined || value === null || value === '') {
        // Unset: the module goes by the field's default.
        const fallback = field?.default;
        if (fallback === undefined || fallback === null || fallback === '') return '—';
        return `${describe(field, fallback, lookups)} (par défaut)`;
    }
    const type = field?.type;
    if (type === 'channel' || type === 'forum') return names([value], lookups.channels, '#');
    if (type === 'channellist' && Array.isArray(value)) return names(value, lookups.channels, '#') || '—';
    if (type === 'role') return names([value], lookups.roles, '@');
    if (type === 'rolelist' && Array.isArray(value)) return names(value, lookups.roles, '@') || '—';
    if (type === 'member') return 'un membre';
    if (type === 'boolean') return value ? 'oui' : 'non';
    if (typeof value === 'string') return value.length > 160 ? `${value.slice(0, 160)}…` : value;
    if (typeof value === 'number') return String(value);
    // A list or a map can hold ids (rewards per role, names per member): counted, not shown.
    const n = Array.isArray(value) ? value.length : Object.keys(value).length;
    return n ? `${n} élément${n > 1 ? 's' : ''}` : 'vide';
}

function versionHtml(sid, mod, version, newer, lookups) {
    const fields = state.moduleSchemas[mod]?.fields || {};
    const when = version.at ? new Date(version.at).toLocaleString('fr-FR') : '';
    const rows = version.changed.map(key => html`
            <li><strong>${fields[key]?.label || key}</strong> :
                <span class="history-before">${describe(fields[key], version.settings[key], lookups)}</span>
                → <span class="history-after">${describe(fields[key], newer[key], lookups)}</span></li>`);
    return html`
        <div class="history-version">
            <div class="history-head">
                <span>Avant l'enregistrement du ${when}</span>
                <button type="button" class="btn btn-ghost btn-sm" data-click="restoreModuleVersion"
                    data-click-args="${actionArgs(sid, mod, version.id, when, EL)}">↩ Restaurer</button>
            </div>
            <ul class="history-changes">${rows}</ul>
        </div>`;
}

export async function openModuleHistory(sid, mod) {
    let data;
    try {
        data = await api(`/api/servers/${encodeURIComponent(sid)}/modules/${encodeURIComponent(mod)}/history`);
    } catch (e) { return; }
    const [channels, roles] = await Promise.all([fetchServerChannels(sid), fetchRoleChoices(sid)]);
    const lookups = {
        channels: new Map((channels?.channels || []).map(c => [String(c.id), c.name])),
        roles: new Map((roles || []).map(r => [String(r.id), r.name])),
    };
    const current = state.servers?.[sid]?.config?.[mod] || {};
    const versions = data?.versions || [];
    const items = versions.map((version, i) => versionHtml(sid, mod, version,
        i === 0 ? current : versions[i - 1].settings, lookups));
    const label = state.moduleSchemas[mod]?.label || mod;
    document.getElementById('modal-title').textContent = `🕘 Historique — ${label}`;
    document.getElementById('modal-save-btn').hidden = true;
    document.getElementById('modal-overlay').querySelector('.modal-body').innerHTML = html`
        <p class="history-intro">Les réglages que chaque enregistrement a remplacés, sur 90 jours.
            Les mots de passe et clés ne sont jamais gardés : une restauration garde les actuels.</p>
        ${versions.length ? items : html`<p class="history-empty">Aucun enregistrement depuis le dashboard pour l'instant.</p>`}`;
    showModal();
}

export async function restoreModuleVersion(sid, mod, versionId, when, btn) {
    if (!(await confirmDiscard())) return;
    if (!(await askConfirm(`Les réglages du module redeviennent ceux d'avant l'enregistrement du ${when}.`,
        { title: 'Restaurer cette version', confirm: 'Restaurer' }))) return;
    if (btn) btn.disabled = true;
    let result;
    try {
        result = await api(`/api/servers/${encodeURIComponent(sid)}/modules/${encodeURIComponent(mod)}/history/${encodeURIComponent(versionId)}/restore`,
            { method: 'POST' });
    } catch (e) {
        if (btn) btn.disabled = false;
        return;
    }
    toast(`Version restaurée.${commandSyncSuffix(result)}`, 'success');
    document.querySelector('#modal-overlay [data-click="closeModal"]')?.click();
    setDirty(false);
    await loadServers();
    if (isDeveloper()) await loadConfig();
    render();
}

registerActions({
    cancelEdits,
    openModuleHistory,
    restoreModuleVersion,
    saveFromBar,
    testChangedTemplates,
});
