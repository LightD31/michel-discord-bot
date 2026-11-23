// A module's page.
import { achievementsCardHtml } from '../features/achievements.js';
import { html, raw } from '../core/html.js';
import { CHECKED, EL, VALUE, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { isDeveloper, loadConfig, loadServers } from '../core/data.js';
import { render } from '../core/render.js';
import { routeHash } from '../core/router.js';
import { state } from '../core/state.js';
import { XP_STATS_RANGES } from '../features/xp.js';
import { memberPickerHtml } from '../forms/pickers/members.js';
import { commandSyncSuffix } from '../forms/save.js';
import { renderSchemaForm } from '../forms/schema-form.js';
import { openModal } from '../ui/modal.js';
import { toast } from '../ui/toast.js';
import { renderConfigPreview } from './global.js';
import { publicPageButton } from './public.js';

// ── Module Detail Page ────────────────────────────
export function renderModulePage() {
    const sid = state.currentServerId;
    const mod = state.currentModule;
    const server = state.servers?.[sid];
    if (!server) return '<div class="loading"><div class="spinner"></div></div>';

    const modConfig = server.config?.[mod] || {};
    const schema = state.moduleSchemas[mod];
    const noToggle = schema?.noToggle === true;
    const enabled = modConfig.enabled === true;
    const label = schema?.label || mod;
    const icon = schema?.icon || '🧩';
    const description = schema?.description || '';
    const formId = `module-${sid}-${mod}`;
    // A module whose only setting is its switch: no empty "Paramètres" card.
    const onlyToggle = !!schema && !schema.directValue
        && Object.keys(schema.fields || {}).every(k => k === 'enabled')
        && Object.keys(modConfig).every(k => k === 'enabled');

    return String(html`
            <div class="breadcrumb">
                <a href="${raw(routeHash('server', sid))}" data-click="navigate" data-click-args="${actionArgs("server", sid)}" data-prevent>${server.name || sid}</a>
                <span>›</span>
                <span>${raw(icon)} ${raw(label)}</span>
            </div>

            <div class="page-header">
                <div class="u-d-flex u-ai-center u-jc-space-between u-wrap-wrap u-gap-12">
                    <div>
                        <h2>${raw(icon)} ${raw(label)}</h2>
                        <p>${description || 'Configuration du module pour ' + (server.name || sid)}</p>
                    </div>
                    ${raw(noToggle ? '' : String(html`
                    <label class="u-tf-scale-1_2 toggle">
                        <input type="checkbox" ${raw(enabled ? 'checked' : '')} aria-label="Activer le module ${label}"
                            data-change="toggleModule" data-change-args="${actionArgs(sid, mod, CHECKED)}">
                        <span class="toggle-slider"></span>
                    </label>
                    `))}
                </div>
            </div>

            ${raw(onlyToggle ? `
            <p class="module-no-settings">Ce module n'a pas d'autre réglage que son interrupteur.</p>
            ` : `<div class="card">
                <div class="card-header">
                    <div class="card-title">Paramètres</div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="editModuleConfigJson" data-click-args="${actionArgs(sid, mod)}">
                            { } JSON
                        </button>
                        ${schema ? `
                            <button class="btn btn-ghost btn-sm" data-click="openModuleHistory" data-click-args="${actionArgs(sid, mod)}">
                                🕘 Historique
                            </button>
                            ${(state.connectionChecks || []).includes(mod) ? `
                            <button class="btn btn-ghost btn-sm" data-click="checkModuleConnection" data-click-args="${actionArgs(sid, mod, EL)}"
                                title="Essaie les réglages enregistrés">🔌 Vérifier la connexion</button>` : ''}
                            <span class="unsaved-badge" role="status">● Modifié</span>
                            <button class="btn btn-success btn-sm" data-click="saveModuleForm" data-click-args="${actionArgs(sid, mod, formId)}">
                                💾 Sauvegarder
                            </button>
                        ` : ''}
                        ${mod === 'moduleEmbedManager' ? `
                        <button class="btn btn-primary btn-sm" data-click="publishEmbeds" data-click-args="${actionArgs(sid)}">
                            📢 Publier
                        </button>
                        ` : ''}
                    </div>
                </div>
                ${schema
                ? renderSchemaForm(formId, schema,
                    schema.directValue ? { [mod]: modConfig } : modConfig,
                    { excludeEnabled: true, module: mod, serverId: sid })
                : (Object.keys(modConfig).length === 0
                    ? `<div class="empty-state">
                            <h3>Configuration vide</h3>
                            <p>Le bouton « JSON » permet d'ajouter des paramètres.</p>
                        </div>`
                    : renderConfigPreview(modConfig)
                )
            }
            </div>`)}

            ${raw(mod === 'moduleReactionRoles' ? `
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Menus de rôles</div>
                        <div class="card-subtitle">Boutons que les membres cliquent pour s'auto-attribuer un rôle.</div>
                    </div>
                    <div class="card-actions">
                        <button class="btn btn-primary btn-sm" data-click="openRoleMenuBuilder" data-click-args="${actionArgs(sid, null)}">
                            ✨ Nouveau menu
                        </button>
                    </div>
                </div>
                <div id="rolemenu-list" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            <div class="u-mt-16 card" hidden id="rolemenu-builder-card" data-server-id="${sid}">
                <div class="card-header">
                    <div>
                        <div class="card-title" id="rolemenu-builder-title">Nouveau menu</div>
                        <div class="card-subtitle">Le menu est posté immédiatement dans le salon choisi.</div>
                    </div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="closeRoleMenuBuilder">Annuler</button>
                        <button class="btn btn-success btn-sm" data-click="saveRoleMenu">💾 Enregistrer</button>
                    </div>
                </div>
                <div id="rolemenu-builder-body"></div>
            </div>
            ` : '')}

            ${raw(mod === 'moduleSecretSanta' ? `
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">🎅 Session en cours</div>
                        <div class="card-subtitle">Le Père Noël Secret du serveur. Qui offre à qui reste secret : seuls des totaux sont affichés.</div>
                    </div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="loadSecretSanta" data-click-args="${actionArgs(sid)}">↻ Actualiser</button>
                    </div>
                </div>
                <div id="secretsanta-session" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            ` : '')}

            ${mod === 'moduleAchievements' ? achievementsCardHtml(sid) : ''}

            ${raw(mod === 'moduleGazette' ? `
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">📰 Aperçu de cette semaine</div>
                        <div class="card-subtitle">Ce que dirait la Gazette si elle paraissait maintenant (les 7 derniers jours).</div>
                    </div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="loadGazette" data-click-args="${actionArgs(sid)}">↻ Actualiser</button>
                        <button class="btn btn-primary btn-sm" data-click="publishGazette" data-click-args="${actionArgs(sid, EL)}">📰 Publier maintenant</button>
                    </div>
                </div>
                <div id="gazette-preview" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            ` : '')}

            ${raw(mod === 'moduleModeration' ? `
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Infractions</div>
                        <div class="card-subtitle">Historique des sanctions enregistrées pour ce serveur.</div>
                    </div>
                </div>
                <div class="inf-filters">
                        <div class="inf-filter-member" aria-label="Filtrer par membre">${memberPickerHtml('', 'id="inf-filter-user"')}</div>
                        <select id="inf-filter-type" class="u-w-150 form-input">
                            <option value="">Tous les types</option>
                            <option value="warn">Avertissement</option>
                            <option value="timeout">Timeout</option>
                            <option value="untimeout">Fin de timeout</option>
                            <option value="kick">Expulsion</option>
                            <option value="ban">Bannissement</option>
                            <option value="unban">Débannissement</option>
                            <option value="automod">Automod</option>
                            <option value="note">Note</option>
                        </select>
                        <select id="inf-filter-active" class="u-w-120 form-input">
                            <option value="">Tous</option>
                            <option value="true">Actifs</option>
                            <option value="false">Révoqués</option>
                        </select>
                        <button class="btn btn-primary btn-sm" data-click="loadInfractions" data-click-args="${actionArgs(sid)}">🔍 Filtrer</button>
                </div>
                <div id="infractions-list" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">🚩 Signalements</div>
                        <div class="card-subtitle">Les messages signalés par les membres. On les traite avec les boutons de leur carte, dans le salon de modération.</div>
                    </div>
                    <div class="card-actions">
                        <select id="report-filter" class="form-input" aria-label="Signalements à afficher" data-change="loadReports" data-change-args="${actionArgs(sid)}">
                            <option value="open">À traiter</option>
                            <option value="">Tous</option>
                        </select>
                    </div>
                </div>
                <div id="reports-list" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            ` : '')}
            ${raw(mod === 'moduleSpotify' ? `
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">📊 Statistiques de la playlist</div>
                        <div class="card-subtitle">Playlist et votes tels que le bot les a enregistrés.</div>
                    </div>
                    <div class="card-actions">
                        ${publicPageButton(sid, 'spotify', modConfig)}
                        <button class="btn btn-ghost btn-sm" data-click="loadSpotifyStats" data-click-args="${actionArgs(sid, true)}">↻ Actualiser</button>
                    </div>
                </div>
                <div id="spotify-stats" class="viz-root" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            ` : '')}

            ${raw(mod === 'moduleXp' ? `
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">📊 Statistiques</div>
                        <div class="card-subtitle">Activité XP du serveur, heures de Paris. L'XP vocal n'est journalisé que depuis l'ajout de ce tableau de bord.</div>
                    </div>
                    <div class="card-actions">
                        ${publicPageButton(sid, 'xp', modConfig)}
                        <select id="xp-stats-range" class="u-w-150 form-input" data-change="setXpStatsRange" data-change-args="${actionArgs(sid, VALUE)}">
                            ${XP_STATS_RANGES.map(d => `<option value="${d}" ${state.xpStatsDays === d ? 'selected' : ''}>${d} jours</option>`).join('')}
                        </select>
                        <button class="btn btn-ghost btn-sm" data-click="loadXpStats" data-click-args="${actionArgs(sid)}">🔄 Actualiser</button>
                    </div>
                </div>
                <div id="xp-stats" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            ` : '')}
            ${raw(schema && !schema.directValue ? renderExtraKeysCard(schema, modConfig) : '')}
        `);
}

export function renderExtraKeysCard(schema, config) {
    const schemaKeys = new Set(Object.keys(schema.fields || {}));
    const extraKeys = Object.keys(config).filter(k => !schemaKeys.has(k));
    if (extraKeys.length === 0) return '';
    return `
            <div class="u-mt-16 card">
                <div class="card-header">
                    <div>
                        <div class="card-title">⚠️ Paramètres supplémentaires</div>
                        <div class="card-subtitle">Clés présentes dans la config mais pas dans le schéma</div>
                    </div>
                </div>
                <div class="kv-grid">
                    ${extraKeys.map(key => String(html`
                        <div class="u-c-warning kv-key">${key}</div>
                        <div class="kv-value"><input type="text" value="${JSON.stringify(config[key])}" readonly></div>
                    `)).join('')}
                </div>
            </div>
        `;
}

export async function editModuleConfigJson(serverId, moduleName) {
    const server = state.servers?.[serverId];
    const modConfig = server?.config?.[moduleName] || {};
    document.getElementById('modal-overlay').querySelector('.modal-body').innerHTML = `
            <textarea class="json-editor" id="json-editor" spellcheck="false"></textarea>
            <div class="json-error" id="json-error"></div>
        `;
    openModal(`${moduleName} — ${server?.name || serverId}`, modConfig, async (newData) => {
        const result = await api(`/api/servers/${serverId}/modules/${moduleName}`, {
            method: 'PUT',
            body: JSON.stringify({ config: newData }),
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
        await loadServers();
        if (isDeveloper()) await loadConfig();
        render();
    });
}

registerActions({
    editModuleConfigJson,
});
