// The AI model list widget and its catalogue.
import { registerActions } from '../../core/actions.js';
import { api } from '../../core/api.js';
import { state } from '../../core/state.js';
import { escapeHtml } from '../../core/util.js';
import { toast } from '../../ui/toast.js';

export const AI_APIS = [
    { id: 'openrouter', label: 'OpenRouter' },
    { id: 'nanogpt', label: 'NanoGPT' },
];

export function modelEntryHtml(m, idx) {
    const apiName = m.api || 'openrouter';
    const apiOptions = AI_APIS.map(a =>
        `<option value="${a.id}" ${a.id === apiName ? 'selected' : ''}>${a.label}</option>`).join('');
    return `
            <div class="model-entry" data-model-idx="${idx}">
                <input type="text" data-model-field="provider" value="${escapeHtml(m.provider || '')}" placeholder="ex: gpt-4.1" data-input="refresh-ai-models" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]">
                <select data-model-field="api">${apiOptions}</select>
                <input type="text" data-model-field="model_id" value="${escapeHtml(m.model_id || '')}" placeholder="ex: openai/gpt-4.1">
                <input type="text" data-model-field="display_name" value="${escapeHtml(m.display_name || '')}" placeholder="ex: OpenAI GPT-4.1" data-input="refresh-ai-models" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]">
                <button class="btn-remove-model" data-click="remove-model-entry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
            </div>`;
}

export function modelsListFor(btn) {
    return btn.closest('.form-field').querySelector('.models-list');
}

export function addModelEntry(btn, model = {}) {
    const list = btn.classList && btn.classList.contains('models-list') ? btn : modelsListFor(btn);
    const idx = list.querySelectorAll('.model-entry').length;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = modelEntryHtml(model, idx).trim();
    const entry = wrapper.firstElementChild;
    list.appendChild(entry);
    refreshAiModelSelects(list.closest('.schema-form'));
    return entry;
}

// ── AI model picker ──────────────────────────────
export const _aiCatalogCache = {};
export const _aiGuildModelsCache = {};

export function aiModelKeyFrom(id, taken) {
    const base = String(id).toLowerCase().split('/').pop()
        .replace(/[^a-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'modele';
    let key = base;
    let n = 2;
    while (taken.has(key)) key = `${base}-${n++}`;
    return key;
}

export function formatPerMillion(value) {
    if (value === null || value === undefined) return '?';
    if (value === 0) return '0';
    return value < 0.01 ? value.toFixed(4) : value < 1 ? value.toFixed(2) : value.toFixed(value < 10 ? 2 : 1);
}

export function formatContext(tokens) {
    if (!tokens) return '';
    return tokens >= 1000000 ? `${(tokens / 1000000).toFixed(tokens % 1000000 ? 1 : 0)}M ctx`
        : `${Math.round(tokens / 1000)}k ctx`;
}

export function readModelEntries(list) {
    return [...list.querySelectorAll('.model-entry')].map(e => ({
        el: e,
        key: e.querySelector('[data-model-field="provider"]')?.value?.trim() || '',
        api: e.querySelector('[data-model-field="api"]')?.value || 'openrouter',
        model_id: e.querySelector('[data-model-field="model_id"]')?.value?.trim() || '',
        display_name: e.querySelector('[data-model-field="display_name"]')?.value?.trim() || '',
    }));
}

export function flagMissingModels(list, apiName, catalog) {
    const ids = new Set(catalog.map(m => m.id));
    for (const entry of readModelEntries(list)) {
        if (entry.api !== apiName || !entry.model_id) continue;
        const missing = !ids.has(entry.model_id) && !ids.has(entry.model_id.split(':')[0]);
        entry.el.classList.toggle('model-missing', missing);
        entry.el.title = missing ? `⚠️ Modèle introuvable dans le catalogue ${apiName}` : '';
    }
}

export async function openModelCatalog(btn) {
    const list = modelsListFor(btn);
    document.getElementById('ai-catalog-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'ai-catalog-overlay';
    overlay.className = 'ai-catalog-overlay';
    overlay.innerHTML = `
            <div class="ai-catalog" role="dialog" aria-label="Catalogue des modèles">
                <div class="ai-catalog-head">
                    <div class="ai-catalog-title">
                        <span>📚 Catalogue des modèles</span>
                        <button class="btn btn-ghost btn-sm" type="button" data-ai-close>✕</button>
                    </div>
                    <div class="ai-catalog-apis" data-ai-apis></div>
                    <input type="search" class="form-input" data-ai-search placeholder="Rechercher par nom ou identifiant…">
                </div>
                <div class="ai-catalog-list" data-ai-list><div class="loading"><div class="spinner"></div></div></div>
            </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => {
        if (e.target === overlay || e.target.closest('[data-ai-close]')) overlay.remove();
    });

    const picker = { list, apiName: null, catalog: [], overlay };
    overlay.querySelector('[data-ai-search]').addEventListener('input', () => renderModelCatalog(picker));

    let apis = AI_APIS.map(a => ({ ...a, configured: true }));
    try {
        const res = await api('/api/ai/apis');
        if (res?.apis) apis = res.apis;
    } catch { /* toast already shown */ }
    const apisEl = overlay.querySelector('[data-ai-apis]');
    apisEl.innerHTML = apis.map(a => `
            <button type="button" class="btn btn-ghost btn-sm" data-ai-api="${a.id}" ${a.configured ? '' : 'disabled title="Clé API et URL à renseigner dans la section correspondante"'}>
                ${escapeHtml(a.label)}${a.configured ? '' : ' (non configurée)'}
            </button>`).join('');
    apisEl.addEventListener('click', e => {
        const b = e.target.closest('[data-ai-api]');
        if (b && !b.disabled) loadModelCatalog(picker, b.dataset.aiApi);
    });
    const first = apis.find(a => a.configured);
    if (first) {
        loadModelCatalog(picker, first.id);
    } else {
        overlay.querySelector('[data-ai-list]').innerHTML =
            '<p style="color:var(--text-muted);padding:12px">Aucune API configurée : renseigne la clé et l\'URL d\'OpenRouter ou de NanoGPT.</p>';
    }
}

export async function loadModelCatalog(picker, apiName) {
    picker.apiName = apiName;
    picker.overlay.querySelectorAll('[data-ai-api]').forEach(b =>
        b.classList.toggle('active', b.dataset.aiApi === apiName));
    const listEl = picker.overlay.querySelector('[data-ai-list]');
    if (!_aiCatalogCache[apiName]) {
        listEl.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
        try {
            const res = await api(`/api/ai/catalog?api=${encodeURIComponent(apiName)}`);
            _aiCatalogCache[apiName] = res?.models || [];
        } catch {
            listEl.innerHTML = '<p style="color:var(--text-muted);padding:12px">Catalogue indisponible.</p>';
            return;
        }
    }
    if (picker.apiName !== apiName) return;
    picker.catalog = _aiCatalogCache[apiName];
    flagMissingModels(picker.list, apiName, picker.catalog);
    renderModelCatalog(picker);
}

export function renderModelCatalog(picker) {
    const listEl = picker.overlay.querySelector('[data-ai-list]');
    const query = picker.overlay.querySelector('[data-ai-search]').value.trim().toLowerCase();
    const added = new Set(readModelEntries(picker.list).map(e => `${e.api}|${e.model_id}`));
    const matches = picker.catalog.filter(m =>
        !query || m.name.toLowerCase().includes(query) || m.id.toLowerCase().includes(query));
    const shown = matches.slice(0, 200);
    if (!shown.length) {
        listEl.innerHTML = '<p style="color:var(--text-muted);padding:12px">Aucun modèle ne correspond.</p>';
        return;
    }
    listEl.innerHTML = shown.map(m => {
        const isAdded = added.has(`${picker.apiName}|${m.id}`);
        const price = m.free ? '<span class="ai-catalog-badge">gratuit</span>'
            : `$${formatPerMillion(m.prompt_per_m)} / $${formatPerMillion(m.completion_per_m)} par M`;
        return `
                <button type="button" class="ai-catalog-row ${isAdded ? 'added' : ''}" data-ai-model="${escapeHtml(m.id)}" ${isAdded ? 'disabled' : ''}>
                    <div>
                        <div>${escapeHtml(m.name)}${isAdded ? ' <span class="ai-catalog-badge">ajouté</span>' : ''}</div>
                        <div class="ai-catalog-id">${escapeHtml(m.id)}</div>
                    </div>
                    <div class="ai-catalog-meta">${price}<br>${formatContext(m.context_length)}</div>
                </button>`;
    }).join('') + (matches.length > shown.length
        ? `<p style="color:var(--text-muted);padding:8px 12px;font-size:12px">${matches.length - shown.length} autres résultats — affine la recherche.</p>`
        : '');
    listEl.querySelectorAll('[data-ai-model]').forEach(row => row.addEventListener('click', () => {
        const model = picker.catalog.find(m => m.id === row.dataset.aiModel);
        if (!model) return;
        const taken = new Set(readModelEntries(picker.list).map(e => e.key));
        addModelEntry(picker.list, {
            provider: aiModelKeyFrom(model.id, taken),
            api: picker.apiName,
            model_id: model.id,
            display_name: model.name,
        });
        toast(`${model.name} ajouté — pense à sauvegarder`, 'success');
        renderModelCatalog(picker);
    }));
}

export async function refreshAiModelSelects(root) {
    const scope = root || document;
    const selects = scope.querySelectorAll('select[data-type="aimodel"]');
    for (const select of selects) {
        const form = select.closest('.schema-form');
        const localList = form?.querySelector('.models-list');
        let models;
        let emptyLabel;
        if (localList) {
            models = readModelEntries(localList).filter(e => e.key)
                .map(e => ({ key: e.key, display_name: e.display_name || e.key }));
            emptyLabel = '— Premier modèle utilisable —';
        } else if (state.currentServerId) {
            const sid = state.currentServerId;
            if (!_aiGuildModelsCache[sid]) {
                try {
                    _aiGuildModelsCache[sid] = await api(`/api/servers/${sid}/ai/models`) || { models: [] };
                } catch {
                    _aiGuildModelsCache[sid] = { models: [] };
                }
            }
            models = _aiGuildModelsCache[sid].models || [];
            emptyLabel = '— Défaut global —';
        } else {
            continue;
        }
        const current = select.value || select.dataset.current || '';
        const known = models.some(m => m.key === current);
        select.innerHTML = `<option value="">${emptyLabel}</option>`
            + models.map(m => `<option value="${escapeHtml(m.key)}">${escapeHtml(m.display_name)} (${escapeHtml(m.key)})</option>`).join('')
            + (current && !known ? `<option value="${escapeHtml(current)}">⚠️ ${escapeHtml(current)} (introuvable)</option>` : '');
        select.value = current;
    }
}

registerActions({
    addModelEntry,
    openModelCatalog,
    'refresh-ai-models': (el) => refreshAiModelSelects(el.closest('.schema-form')),
    'remove-model-entry': (el) => {
        const form = el.closest('.schema-form');
        el.closest('.model-entry').remove();
        refreshAiModelSelects(form);
    },
});
