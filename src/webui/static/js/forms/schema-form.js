// Module and section forms, rendered from their schema and read back.
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';
import { multiPickerHtml, multiPickerIds } from './pickers/lists.js';
import { memberPickerHtml } from './pickers/members.js';
import { rolePickerHtml } from './pickers/roles.js';
import { keyValueEntryHtml } from './widgets/maps.js';
import { modelEntryHtml } from './widgets/models.js';
import { renderRssFeedEntry } from './widgets/rss.js';
import { STREAMER_TOGGLE_FIELDS, renderStreamerEntry } from './widgets/streamers.js';
import { renderTeamEntry } from './widgets/teams.js';
import { renderYoutubeChannelEntry } from './widgets/youtube.js';

// ── Schema-based form rendering ───────────────────

// "Entre 2 et 50", from a number field's schema bounds.
// A field's help as written in its schema: **gras**, `code` and line
// breaks, on top of escaped text (nothing else is markup).
export function mdInline(text) {
    return escapeHtml(String(text ?? ''))
        .replace(/`([^`\n]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}

export function numberRange(field) {
    const fmt = (n) => Number(n).toLocaleString('fr-FR');
    if (field.min != null && field.max != null) return `Entre ${fmt(field.min)} et ${fmt(field.max)}`;
    if (field.min != null) return `Au moins ${fmt(field.min)}`;
    if (field.max != null) return `Au plus ${fmt(field.max)}`;
    return '';
}

export function renderSchemaForm(formId, schema, values, options = {}) {
    const fields = schema.fields || {};
    const entries = Object.entries(fields);
    if (entries.length === 0) return '<p style="color:var(--text-muted)">Aucun champ défini.</p>';

    let html = `<div class="schema-form" data-form-id="${formId}">`;
    for (const [key, field] of entries) {
        if (options.excludeEnabled && key === 'enabled') continue;

        const value = values[key];
        const hasValue = value !== undefined && value !== null;
        const fieldType = field.type || 'string';
        const isSecret = field.secret || false;
        const isRequired = field.required || false;

        if (field.hidden) {
            if (hasValue) {
                html += `<input type="hidden" data-field="${key}" data-type="${fieldType}" value="${escapeHtml(String(value))}">`;
            }
            continue;
        }

        // The label names its control (for=), so a click on it focuses the
        // field and a screen reader reads it with the field.
        const fieldStart = html.length;
        const fieldId = `f-${formId}-${key}`.replace(/[^A-Za-z0-9_-]/g, '_');
        html += `<div class="form-field" data-key="${key}">`;
        html += `<div class="form-field-header">`;
        html += `<label class="form-label" for="${fieldId}">${escapeHtml(field.label || key)}`;
        if (isRequired) html += ' <span class="required-badge">requis</span>';
        html += `</label>`;
        if (RESETTABLE_TYPES.has(fieldType) && field.default !== undefined && field.default !== null) {
            html += `<button type="button" class="btn-reset-default" data-click="resetFieldToDefault" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]"
                    data-default="${escapeHtml(JSON.stringify(field.default))}"
                    title="Rétablir la valeur par défaut (à sauvegarder ensuite)">↺ Par défaut</button>`;
        }
        html += `</div>`;
        if (field.description) {
            html += `<div class="form-description">${mdInline(field.description)}</div>`;
        }

        switch (fieldType) {
            case 'boolean':
                // Unset: the schema's default, which a save then writes as is.
                html += `
                        <label class="toggle" style="margin-top:4px;">
                            <input type="checkbox" data-field="${key}" data-type="boolean"
                                ${(hasValue ? value === true : field.default === true) ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>`;
                break;

            case 'number': {
                // The schema's bounds: the browser flags a value outside
                // them, and the save route refuses it.
                const range = numberRange(field);
                html += `<input type="number" class="form-input" data-field="${key}" data-type="number"
                        value="${escapeHtml(hasValue ? value : (field.default ?? ''))}"
                        ${field.step != null ? `step="${escapeHtml(field.step)}"` : ''}
                        ${field.min != null ? `min="${escapeHtml(field.min)}"` : ''}
                        ${field.max != null ? `max="${escapeHtml(field.max)}"` : ''}
                        ${range ? `aria-describedby="${fieldId}-range"` : ''}
                        placeholder="${field.default != null ? 'Défaut: ' + escapeHtml(field.default) : ''}">`;
                if (range) html += `<div class="form-range" id="${fieldId}-range">${escapeHtml(range)}</div>`;
                break;
            }

            case 'secret':
                html += `<div style="position:relative;">
                        <input type="password" class="form-input" data-field="${key}" data-type="secret"
                            value="${hasValue ? escapeHtml(value) : ''}"
                            placeholder="${hasValue ? '••••••••' : 'Non défini'}">
                        <button class="btn-reveal" data-click="togglePasswordVisibility" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" type="button" title="Afficher/masquer" aria-label="Afficher/masquer">👁</button>
                    </div>`;
                break;

            case 'channel': {
                const currentVal = hasValue ? String(value) : '';
                html += `<div class="channel-picker" data-field="${key}" data-type="channel" data-current="${escapeHtml(currentVal)}">
                        <select class="form-input channel-picker-select" data-channel-select="${key}">
                            <option value="">— Chargement des salons… —</option>
                            ${currentVal ? `<option value="${escapeHtml(currentVal)}" selected>${escapeHtml(currentVal)}</option>` : ''}
                        </select>
                        <input type="text" class="form-input channel-picker-manual" data-channel-manual="${key}"
                            value="${escapeHtml(currentVal)}" placeholder="ID du salon" style="display:none;">
                        <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle" aria-label="Saisie manuelle">✎</button>
                    </div>`;
                break;
            }
            case 'role':
                html += rolePickerHtml(hasValue ? String(value) : '',
                    `data-field="${key}" data-type="role"`, field.roleUse, fieldId);
                break;

            case 'member':
                html += memberPickerHtml(hasValue ? String(value) : '', `data-field="${key}" data-type="member"`);
                break;

            case 'choice': {
                const current = hasValue ? String(value) : String(field.default ?? '');
                html += `<select class="form-input" data-field="${key}" data-type="choice">`;
                for (const option of field.choices || []) {
                    html += `<option value="${escapeHtml(option.value)}" ${option.value === current ? 'selected' : ''}>${escapeHtml(option.label)}</option>`;
                }
                html += `</select>`;
                break;
            }

            case 'channellist':
            case 'rolelist':
                html += multiPickerHtml(key, fieldType, Array.isArray(value) ? value : [], field.roleUse);
                break;

            case 'message':
            case 'url': {
                const typeHint = {message: 'ID du message', url: 'URL'}[fieldType];
                html += `<input type="text" class="form-input" data-field="${key}" data-type="${fieldType}"
                        value="${hasValue ? escapeHtml(String(value)) : ''}"
                        placeholder="${typeHint}">`;
                break;
            }

            case 'list':
                const listVal = Array.isArray(value) ? value.join('\n') : (hasValue ? String(value) : '');
                const defaultHint = Array.isArray(field.default) ? field.default.join(', ') : '';
                html += `<textarea class="form-textarea" data-field="${key}" data-type="list"
                        placeholder="${defaultHint ? 'Défaut: ' + defaultHint : 'Une valeur par ligne'}"
                        rows="3">${escapeHtml(listVal)}</textarea>`;
                break;

            case 'list:number':
                const numListVal = Array.isArray(value) ? value.join(', ') : (hasValue ? String(value) : '');
                html += `<input type="text" class="form-input" data-field="${key}" data-type="list:number"
                        value="${escapeHtml(numListVal)}"
                        placeholder="Nombres séparés par des virgules (ex: 1, 2, 3)">`;
                break;

            case 'dict':
                const jsonStr = hasValue ? JSON.stringify(value, null, 2) : '{}';
                html += `<textarea class="form-textarea json-field" data-field="${key}" data-type="dict"
                        rows="5" spellcheck="false">${escapeHtml(jsonStr)}</textarea>`;
                break;

            case 'models':
                const models = Array.isArray(value) ? value : [];
                html += `<div class="models-list" data-field="${key}" data-type="models">`;
                html += `<div class="models-header"><span>Clé</span><span>API</span><span>Identifiant du modèle</span><span>Nom d'affichage</span><span></span></div>`;
                models.forEach((m, idx) => {
                    html += modelEntryHtml(m, idx);
                });
                html += `</div>`;
                html += `<div class="models-actions">
                        <button type="button" class="btn-add-model" data-click="addModelEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un modèle</button>
                        <button type="button" class="btn-add-model" data-click="openModelCatalog" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">📚 Parcourir les modèles</button>
                    </div>`;
                break;

            case 'text':
                html += `<textarea class="form-textarea" data-field="${key}" data-type="text"
                        rows="8">${escapeHtml(hasValue ? String(value) : (field.default ?? ''))}</textarea>`;
                break;

            case 'aimodel': {
                const current = hasValue ? String(value) : '';
                html += `<select class="form-input" data-field="${key}" data-type="aimodel" data-current="${escapeHtml(current)}">
                        ${current ? `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}</option>` : '<option value="">— Chargement… —</option>'}
                    </select>`;
                break;
            }

            case 'teams': {
                const teams = Array.isArray(value) ? value : [];
                html += `<div class="teams-list" data-field="${key}" data-type="teams">`;
                teams.forEach((t, idx) => {
                    html += renderTeamEntry(t, idx);
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-entry" data-click="addTeamEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une équipe</button>`;
                break;
            }

            case 'discord2name':
                const nameMap = (typeof value === 'object' && value !== null && !Array.isArray(value)) ? value : {};
                html += `<div class="namemap-list" data-field="${key}" data-type="discord2name">`;
                html += `<div class="namemap-header"><span>Membre Discord</span><span>Prénom</span><span></span></div>`;
                Object.entries(nameMap).forEach(([discordId, name]) => {
                    html += `
                            <div class="namemap-entry">
                                ${memberPickerHtml(discordId, 'data-namemap-field="discord_id"')}
                                <input type="text" data-namemap-field="name" value="${escapeHtml(String(name))}" placeholder="ex: Jean">
                                <button class="btn-remove-entry" data-click="removeNameMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
                            </div>`;
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-entry" data-click="addNameMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un membre</button>`;
                break;

            case 'keyvaluemap':
                const kvMap = (typeof value === 'object' && value !== null && !Array.isArray(value)) ? value : {};
                const keyLabel = field.keyLabel || 'Clé';
                const valueLabel = field.valueLabel || 'Valeur';
                html += `<div class="keyvaluemap-list" data-field="${key}" data-type="keyvaluemap" data-key-label="${escapeHtml(keyLabel)}" data-value-label="${escapeHtml(valueLabel)}"
                        data-role-use="${escapeHtml(field.roleUse || '')}" data-role-keys="${field.roleKeys ? '1' : ''}" data-member-keys="${field.memberKeys ? '1' : ''}">`;
                html += `<div class="keyvaluemap-header"><span>${escapeHtml(keyLabel)}</span><span>${escapeHtml(valueLabel)}</span><span></span></div>`;
                Object.entries(kvMap).forEach(([mapKey, mapValue]) => {
                    html += keyValueEntryHtml(field.roleUse || '', !!field.roleKeys, !!field.memberKeys,
                        keyLabel, valueLabel, mapKey, String(mapValue));
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-entry" data-click="addKeyValueMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une entrée</button>`;
                break;

            case 'streamermap': {
                const streamerMap = (typeof value === 'object' && value !== null && !Array.isArray(value)) ? value : {};
                html += `<div class="streamers-list" data-field="${key}" data-type="streamermap">`;
                Object.entries(streamerMap).forEach(([login, cfg]) => {
                    html += renderStreamerEntry(login, cfg || {});
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-entry" data-click="addStreamerEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un streamer</button>`;
                break;
            }

            case 'youtubechannelmap': {
                const channelMap = (value && typeof value === 'object' && !Array.isArray(value)) ? value : {};
                html += `<div class="youtubechannel-list" data-field="${key}" data-type="youtubechannelmap">`;
                Object.entries(channelMap).forEach(([handle, cfg]) => {
                    html += renderYoutubeChannelEntry(handle, cfg || {});
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-entry" data-click="addYoutubeChannelEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une chaîne</button>`;
                break;
            }

            case 'rssfeedmap': {
                const feedMap = (typeof value === 'object' && value !== null && !Array.isArray(value)) ? value : {};
                html += `<div class="rssfeed-list" data-field="${key}" data-type="rssfeedmap">`;
                Object.entries(feedMap).forEach(([feedId, cfg]) => {
                    const normalized = (typeof cfg === 'string') ? { url: cfg } : (cfg || {});
                    html += renderRssFeedEntry(feedId, normalized);
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-entry" data-click="addRssFeedEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un flux</button>`;
                html += `<select class="rssfeed-preset-select" data-change="applyRssPreset" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Préréglages prêts à l'emploi">
                        <option value="">+ Préréglage…</option>
                        <optgroup label="LootScraper — Bundles">
                            <option value="lootscraper-all">Bundle classique (Epic + Amazon + itch.io + Steam)</option>
                            <option value="lootscraper-all-pc">Bundle PC (Epic + GOG + Humble + itch.io + Amazon + Steam)</option>
                            <option value="lootscraper-everything">Tout LootScraper (12 flux)</option>
                        </optgroup>
                        <optgroup label="LootScraper — Jeux PC gratuits">
                            <option value="lootscraper-epic">Epic Games</option>
                            <option value="lootscraper-gog">GOG</option>
                            <option value="lootscraper-humble">Humble</option>
                            <option value="lootscraper-itch">itch.io</option>
                        </optgroup>
                        <optgroup label="LootScraper — Amazon &amp; Steam">
                            <option value="lootscraper-amazon">Amazon Prime Gaming (jeux)</option>
                            <option value="lootscraper-amazon-loot">Amazon Prime Gaming (loot)</option>
                            <option value="lootscraper-steam">Steam (jeux gratuits)</option>
                            <option value="lootscraper-steam-loot">Steam (loot)</option>
                        </optgroup>
                        <optgroup label="LootScraper — Mobile">
                            <option value="lootscraper-epic-android">Epic Games — Android</option>
                            <option value="lootscraper-epic-ios">Epic Games — iOS</option>
                            <option value="lootscraper-apple">App Store (iOS)</option>
                            <option value="lootscraper-google">Google Play (Android)</option>
                        </optgroup>
                    </select>`;
                break;
            }

            case 'spotifymap':
                const spotifyUsers = Array.isArray(value) ? value : [];
                html += `<div class="spotifymap-list" data-field="${key}" data-type="spotifymap">`;
                html += `<div class="spotifymap-header"><span>Spotify ID</span><span>Membre Discord</span><span></span></div>`;
                spotifyUsers.forEach((user, idx) => {
                    html += `
                            <div class="spotifymap-entry" data-user-idx="${idx}">
                                <input type="text" data-spotifymap-field="spotifyId" value="${escapeHtml(user.spotifyId || '')}" placeholder="ex: exemple123">
                                ${memberPickerHtml(user.discordId, 'data-spotifymap-field="discordId"')}
                                <button class="btn-remove-entry" data-click="removeSpotifyMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
                            </div>`;
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-entry" data-click="addSpotifyUserEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un utilisateur</button>`;
                break;

            case 'messagelist':
                const messages = Array.isArray(value) ? value : [];
                html += `<div class="messagelist-list" data-field="${key}" data-type="messagelist">`;
                html += `<div class="messagelist-header"><span>Message</span><span>Poids</span><span></span></div>`;
                messages.forEach((msg) => {
                    const text = typeof msg === 'string' ? msg : String(msg?.text ?? '');
                    const w = (msg && typeof msg === 'object' && msg.weight !== undefined) ? msg.weight : 1;
                    html += `
                            <div class="messagelist-entry">
                                <input type="text" data-msg-field="message" value="${escapeHtml(text)}" placeholder="Message…">
                                <input type="number" data-msg-field="weight" value="${escapeHtml(String(w))}" min="0" max="1000" placeholder="1">
                                <button class="btn-remove-msg" data-click="remove-closest" data-click-args="[&quot;.messagelist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
                            </div>`;
                });
                html += `</div>`;
                if (field.variables) {
                    const varChips = field.variables.split(',').map(v => {
                        const vt = v.trim();
                        return `<button type="button" class="var-chip" data-var="${escapeHtml(vt)}" data-mousedown="insertVariable" data-mousedown-args="[{&quot;$&quot;:&quot;event&quot;},{&quot;$&quot;:&quot;el&quot;}]">${escapeHtml(vt)}</button>`;
                    }).join('');
                    html += `<div class="messagelist-variables"><span>Variables : </span>${varChips}</div>`;
                }
                html += `<button type="button" class="btn-add-entry" data-click="addMessageEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un message</button>`;
                break;

            case 'commandlist': {
                const commands = Array.isArray(value) ? value : [];
                html += `<div class="commandlist-list" data-field="${key}" data-type="commandlist">`;
                commands.forEach((cmd) => {
                    const cmdName = typeof cmd?.name === 'string' ? cmd.name : '';
                    const cmdDesc = typeof cmd?.description === 'string' ? cmd.description : '';
                    const responses = Array.isArray(cmd?.responses) ? cmd.responses : [];
                    html += `<div class="commandlist-entry">`;
                    html += `<div class="commandlist-entry-header">`;
                    html += `<div class="commandlist-entry-title">/${escapeHtml(cmdName) || 'nouvelle-commande'}</div>`;
                    html += `<button type="button" class="btn-remove-embed" data-click="remove-closest" data-click-args="[&quot;.commandlist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer cette commande" aria-label="Supprimer cette commande">✕</button>`;
                    html += `</div>`;
                    html += `<div class="commandlist-entry-fields">`;
                    html += `<div><label class="commandlist-field-label">Nom</label>`;
                    html += `<input type="text" class="form-input" data-cmd-field="name" value="${escapeHtml(cmdName)}" placeholder="blague" maxlength="32" data-input="updateCommandTitle" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]"></div>`;
                    html += `<div><label class="commandlist-field-label">Description</label>`;
                    html += `<input type="text" class="form-input" data-cmd-field="description" value="${escapeHtml(cmdDesc)}" placeholder="Raconte une blague au hasard" maxlength="100"></div>`;
                    html += `</div>`;
                    html += `<div class="commandlist-responses">`;
                    html += `<div class="commandlist-responses-label">Réponses (tirées au hasard)</div>`;
                    responses.forEach((response) => {
                        const text = typeof response === 'string' ? response : String(response?.text ?? '');
                        html += `
                                <div class="commandlist-response-entry">
                                    <input type="text" data-response-field="text" value="${escapeHtml(text)}" placeholder="https://exemple.com/gif.gif ou un texte" maxlength="2000">
                                    <button type="button" class="btn-remove-response" data-click="remove-closest" data-click-args="[&quot;.commandlist-response-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
                                </div>`;
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-link" data-click="addCommandResponse" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une réponse</button>`;
                    html += `</div>`;
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-embed" data-click="addCommandEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une commande</button>`;
                break;
            }

            case 'embedlist':
                const embeds = Array.isArray(value) ? value : [];
                html += `<div class="embedlist-list" data-field="${key}" data-type="embedlist">`;
                embeds.forEach((embed, embedIdx) => {
                    const embedTitle = embed.title || 'Sans titre';
                    // Free text in the stored config: anything but six hex digits falls
                    // back to the default rather than landing in the markup.
                    const rawColor = String(embed.color || '').replace(/^#/, '');
                    const embedColor = /^[0-9a-fA-F]{6}$/.test(rawColor) ? rawColor : '3498db';
                    const embedLinks = Array.isArray(embed.links) ? embed.links : [];

                    html += `<div class="embedlist-entry" data-embed-idx="${embedIdx}">`;

                    // Embed header
                    html += `<div class="embedlist-entry-header">`;
                    html += `<div class="embedlist-entry-title" title="${escapeHtml(embedTitle)}">${escapeHtml(embedTitle)}</div>`;
                    html += `<div class="embedlist-entry-controls">`;
                    html += `<button class="btn-remove-embed" data-click="remove-closest" data-click-args="[&quot;.embedlist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer cet embed" aria-label="Supprimer cet embed">✕</button>`;
                    html += `</div></div>`;

                    // Embed fields (title & color)
                    html += `<div class="embedlist-entry-fields">`;
                    html += `<div><label style="font-size:11px;color:var(--text-muted)">Titre</label>`;
                    html += `<input type="text" class="form-input" data-embed-field="title" value="${escapeHtml(embedTitle)}" placeholder="Titre de l'embed" data-bind="">`;
                    html += `</div>`;
                    html += `<div><label style="font-size:11px;color:var(--text-muted)">Couleur</label>`;
                    html += `<div class="embedlist-color-picker">`;
                    html += `<input type="color" class="embedlist-color-input" data-embed-field="color" value="#${embedColor}" data-change="updateEmbedColorText" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">`;
                    html += `<div class="embedlist-color-text">${embedColor}</div>`;
                    html += `</div></div></div>`;

                    // Links section
                    html += `<div class="embedlist-links">`;
                    html += `<div class="embedlist-links-label">Liens</div>`;
                    embedLinks.forEach((link, linkIdx) => {
                        const linkTitle = link.title || '';
                        const linkUrl = link.url || '';
                        html += `
                                <div class="embedlist-link-entry" data-link-idx="${linkIdx}">
                                    <input type="text" data-link-field="title" value="${escapeHtml(linkTitle)}" placeholder="Titre du lien" maxlength="256">
                                    <input type="text" data-link-field="url" value="${escapeHtml(linkUrl)}" placeholder="https://exemple.com" maxlength="2048">
                                    <button type="button" class="btn-remove-link" data-click="remove-closest" data-click-args="[&quot;.embedlist-link-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
                                </div>`;
                    });
                    html += `</div>`;

                    html += `<button type="button" class="btn-add-link" data-click="addEmbedLink" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un lien</button>`;
                    html += `</div>`;
                });
                html += `</div>`;
                html += `<button type="button" class="btn-add-embed" data-click="addEmbedEntry" data-click-args="${actionArgs(EL, key)}">+ Ajouter un embed</button>`;
                break;

            default: // string
                html += `<input type="text" class="form-input" data-field="${key}" data-type="string"
                        value="${hasValue ? escapeHtml(String(value)) : ''}"
                        placeholder="${field.default != null ? 'Défaut: ' + escapeHtml(field.default) : ''}">`;
        }

        if (options.module && state.templateTests?.[options.module]?.[key]) {
            html += `<button type="button" class="btn btn-ghost btn-sm template-test-btn" style="margin-top:6px;"
                    data-click="testTemplate" data-click-args="${actionArgs(options.serverId, options.module, key, EL)}"
                    title="Le bot t'envoie en MP ce que donnent les messages ci-dessus, avec toi à la place du membre">✉️ Tester en MP</button>`;
        }
        html += `</div>`;
        // The field's first control (a widget without one, a list say, is
        // named by aria-labelledby instead of the label's for=).
        const fieldHtml = html.slice(fieldStart);
        const marker = `data-field="${key}"`;
        const labelled = fieldHtml.includes(marker)
            ? fieldHtml.replace(marker, `id="${fieldId}" ${marker}`)
            : fieldHtml.replace(`for="${fieldId}"`, `id="${fieldId}-label"`)
                .replace(`<div class="form-field" data-key="${key}">`,
                    `<div class="form-field" data-key="${key}" role="group" aria-labelledby="${fieldId}-label">`);
        html = html.slice(0, fieldStart) + labelled;
    }
    html += `</div>`;
    return html;
}

// Field types whose default can be restored with one click.
export const RESETTABLE_TYPES = new Set(['text', 'string', 'number', 'boolean', 'url', 'choice']);

export async function resetFieldToDefault(btn) {
    const field = btn.closest('.form-field');
    const input = field?.querySelector('[data-field]');
    if (!input) return;
    let value;
    try { value = JSON.parse(btn.dataset.default); } catch { return; }
    if (input.type === 'checkbox') {
        input.checked = value === true;
    } else {
        const current = input.value;
        const next = value == null ? '' : String(value);
        if (current === next) {
            toast('Déjà à la valeur par défaut', 'info');
            return;
        }
        // Long texts (the AI persona) are easy to lose: confirm before overwriting.
        if (input.tagName === 'TEXTAREA' && current.trim()
            && !(await askConfirm('Remplacer le texte actuel par la valeur par défaut ?', { confirm: 'Remplacer', danger: true }))) {
            return;
        }
        input.value = next;
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    toast('Valeur par défaut rétablie — pense à sauvegarder', 'success');
}

export function collectFormValues(formId) {
    const form = document.querySelector(`[data-form-id="${formId}"]`);
    if (!form) return {};
    const result = {};
    const inputs = form.querySelectorAll('[data-field]');
    for (const input of inputs) {
        const key = input.dataset.field;
        const type = input.dataset.type;
        let val;
        switch (type) {
            case 'boolean':
                val = input.checked;
                break;
            case 'channel': {
                if (input.tagName === 'INPUT') {
                    val = (input.value || '').trim() || undefined;
                } else {
                    const select = input.querySelector('.channel-picker-select');
                    const manual = input.querySelector('.channel-picker-manual');
                    const manualVisible = manual && manual.style.display !== 'none';
                    const raw = manualVisible ? (manual?.value ?? '') : (select?.value ?? '');
                    val = raw.trim() || undefined;
                }
                break;
            }
            case 'number':
                val = input.value === '' ? undefined : Number(input.value);
                break;
            case 'list':
                val = input.value.trim() ? input.value.trim().split('\n').map(s => s.trim()).filter(Boolean) : undefined;
                break;
            case 'channellist':
            case 'rolelist': {
                const ids = multiPickerIds(input);
                val = ids.length ? ids : undefined;
                break;
            }
            case 'list:number':
                val = input.value.trim()
                    ? input.value.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n))
                    : undefined;
                break;
            case 'dict':
                try { val = JSON.parse(input.value); } catch { val = undefined; }
                break;
            case 'models':
                // Handled separately below
                continue;
            case 'aimodel':
                // Empty is meaningful (fall back to the default), so keep it.
                val = input.value;
                break;
            case 'teams':
                // Handled separately below
                continue;
            case 'discord2name':
                // Handled separately below
                continue;
            case 'keyvaluemap':
                // Handled separately below
                continue;
            case 'spotifymap':
                // Handled separately below
                continue;
            case 'streamermap':
                // Handled separately below
                continue;
            case 'youtubechannelmap':
                // Handled separately below
                continue;
            case 'rssfeedmap':
                // Handled separately below
                continue;
            case 'messagelist':
                // Handled separately below
                continue;
            case 'commandlist':
                // Handled separately below
                continue;
            case 'embedlist':
                // Handled separately below
                continue;
            case 'secret':
                val = input.value || undefined;
                break;
            default:
                val = input.value || undefined;
        }
        if (val !== undefined) {
            result[key] = val;
        }
    }
    // Collect models lists
    const modelsLists = form.querySelectorAll('[data-type="models"]');
    for (const list of modelsLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.model-entry');
        const models = [];
        for (const entry of entries) {
            const provider = entry.querySelector('[data-model-field="provider"]')?.value?.trim();
            const apiName = entry.querySelector('[data-model-field="api"]')?.value || 'openrouter';
            const model_id = entry.querySelector('[data-model-field="model_id"]')?.value?.trim();
            const display_name = entry.querySelector('[data-model-field="display_name"]')?.value?.trim();
            if (provider && model_id) {
                models.push({ provider, api: apiName, model_id, display_name: display_name || provider });
            }
        }
        if (models.length > 0) {
            result[key] = models;
        }
    }

    // Collect teams lists
    const teamsLists = form.querySelectorAll('[data-type="teams"]');
    for (const list of teamsLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.team-entry');
        const teams = [];
        for (const entry of entries) {
            const name = entry.querySelector('[data-team-field="name"]')?.value?.trim();
            const vlrTeamId = entry.querySelector('[data-team-field="vlrTeamId"]')?.value?.trim();
            const chanPicker = entry.querySelector('.channel-picker[data-team-field="channelId"]');
            let chan = '';
            if (chanPicker) {
                const manual = chanPicker.querySelector('.channel-picker-manual');
                const select = chanPicker.querySelector('.channel-picker-select');
                const manualVisible = manual && manual.style.display !== 'none';
                chan = (manualVisible ? (manual?.value ?? '') : (select?.value ?? '')).trim();
            }
            const pinCb = entry.querySelector('input[type="checkbox"][data-team-field="pin"]');
            const pin = !!(pinCb && pinCb.checked);
            if (name) {
                const team = { name };
                if (vlrTeamId) team.vlrTeamId = vlrTeamId;
                if (chan) team.channelId = chan;
                if (pin) team.pin = true;
                teams.push(team);
            }
        }
        result[key] = teams;
    }

    // Collect discord2name lists
    const namemapLists = form.querySelectorAll('[data-type="discord2name"]');
    for (const list of namemapLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.namemap-entry');
        const nameMap = {};
        for (const entry of entries) {
            const picker = entry.querySelector('.member-picker');
            let discordId = '';
            if (picker) {
                const manual = picker.querySelector('.member-picker-manual');
                const select = picker.querySelector('.member-picker-select');
                const manualVisible = manual && manual.style.display !== 'none';
                discordId = (manualVisible ? (manual?.value ?? '') : (select?.value ?? '')).trim();
            } else {
                discordId = entry.querySelector('[data-namemap-field="discord_id"]')?.value?.trim() || '';
            }
            const name = entry.querySelector('[data-namemap-field="name"]')?.value?.trim();
            if (discordId && name) {
                nameMap[discordId] = name;
            }
        }
        result[key] = nameMap;
    }

    // Collect keyvaluemap lists
    const kvmapLists = form.querySelectorAll('[data-type="keyvaluemap"]');
    for (const list of kvmapLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.keyvaluemap-entry');
        const kvMap = {};
        for (const entry of entries) {
            const mapKey = entry.querySelector('[data-kvmap-field="key"]')?.value?.trim();
            const mapValue = entry.querySelector('[data-kvmap-field="value"]')?.value?.trim();
            if (mapKey && mapValue) {
                kvMap[mapKey] = mapValue;
            }
        }
        result[key] = kvMap;
    }

    // Collect spotifymap lists
    const spotifymapLists = form.querySelectorAll('[data-type="spotifymap"]');
    for (const list of spotifymapLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.spotifymap-entry');
        const spotifyUsers = [];
        for (const entry of entries) {
            const spotifyId = entry.querySelector('[data-spotifymap-field="spotifyId"]')?.value?.trim();
            const picker = entry.querySelector('.member-picker');
            let discordId = '';
            if (picker) {
                const manual = picker.querySelector('.member-picker-manual');
                const select = picker.querySelector('.member-picker-select');
                const manualVisible = manual && manual.style.display !== 'none';
                discordId = (manualVisible ? (manual?.value ?? '') : (select?.value ?? '')).trim();
            }
            if (spotifyId && discordId) {
                spotifyUsers.push({ spotifyId, discordId });
            }
        }
        if (spotifyUsers.length > 0) {
            result[key] = spotifyUsers;
        }
    }

    // Collect youtubechannelmap lists
    const ytChannelLists = form.querySelectorAll('[data-type="youtubechannelmap"]');
    for (const list of ytChannelLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.youtubechannel-entry');
        const channelMap = {};
        for (const entry of entries) {
            const handle = entry.querySelector('[data-yt-field="handle"]')?.value?.trim();
            if (!handle) continue;
            const label = entry.querySelector('[data-yt-field="label"]')?.value?.trim() || '';
            const shorts = !!entry.querySelector('[data-yt-field="shorts"]')?.checked;
            const live = !!entry.querySelector('[data-yt-field="live"]')?.checked;
            const vod = !!entry.querySelector('[data-yt-field="vod"]')?.checked;
            const cfg = { shorts, live, vod };
            if (label) cfg.label = label;
            channelMap[handle] = cfg;
        }
        result[key] = channelMap;
    }

    // Collect rssfeedmap lists
    const rssFeedLists = form.querySelectorAll('[data-type="rssfeedmap"]');
    for (const list of rssFeedLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.rssfeed-entry');
        const feedMap = {};
        for (const entry of entries) {
            const feedId = entry.querySelector('[data-rss-field="feed_id"]')?.value?.trim();
            const url = entry.querySelector('[data-rss-field="url"]')?.value?.trim();
            if (!feedId || !url) continue;
            const label = entry.querySelector('[data-rss-field="label"]')?.value?.trim() || '';
            const channelPicker = entry.querySelector('[data-rss-field="channel_id"]');
            let channelId = '';
            if (channelPicker) {
                const manual = channelPicker.querySelector('.channel-picker-manual');
                const select = channelPicker.querySelector('.channel-picker-select');
                const manualVisible = manual && manual.style.display !== 'none';
                channelId = (manualVisible ? (manual?.value ?? '') : (select?.value ?? '')).trim();
            }
            const template = entry.querySelector('[data-rss-field="template"]')?.value?.trim() || '';
            const mode = entry.querySelector('[data-rss-field="mode"]')?.value || 'text';
            const embedColor = entry.querySelector('[data-rss-field="embed_color"]')?.value?.trim() || '';
            const embedTitle = entry.querySelector('[data-rss-field="embed_title"]')?.value?.trim() || '';
            const embedDescription = entry.querySelector('[data-rss-field="embed_description"]')?.value?.trim() || '';
            const embedShowImage = entry.querySelector('[data-rss-field="embed_show_image"]')?.checked;
            const embedShowLinkButton = entry.querySelector('[data-rss-field="embed_show_link_button"]')?.checked;
            const embedLinkButtonLabel = entry.querySelector('[data-rss-field="embed_link_button_label"]')?.value?.trim() || '';
            const cfg = { url };
            if (label) cfg.label = label;
            if (channelId) cfg.channel_id = channelId;
            if (template) cfg.template = template;
            if (mode === 'embed') {
                cfg.mode = 'embed';
                if (embedColor) cfg.embed_color = embedColor;
                if (embedTitle) cfg.embed_title = embedTitle;
                if (embedDescription) cfg.embed_description = embedDescription;
                cfg.embed_show_image = embedShowImage !== false;
                if (embedShowLinkButton) {
                    cfg.embed_show_link_button = true;
                    if (embedLinkButtonLabel) cfg.embed_link_button_label = embedLinkButtonLabel;
                }
            }
            feedMap[feedId] = cfg;
        }
        result[key] = feedMap;
    }

    // Collect streamermap (Twitch) lists
    const streamerLists = form.querySelectorAll('[data-type="streamermap"]');
    for (const list of streamerLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.streamer-entry');
        const streamerMap = {};
        for (const entry of entries) {
            const login = entry.querySelector('[data-streamer-field="login"]')?.value?.trim();
            if (!login) continue;
            const planningPicker = entry.querySelector('[data-streamer-field="twitchPlanningChannelId"]');
            const notifPicker = entry.querySelector('[data-streamer-field="twitchNotificationChannelId"]');
            const pickValue = (picker) => {
                if (!picker) return '';
                const manual = picker.querySelector('.channel-picker-manual');
                const select = picker.querySelector('.channel-picker-select');
                const manualVisible = manual && manual.style.display !== 'none';
                const raw = manualVisible ? (manual?.value ?? '') : (select?.value ?? '');
                return (raw || '').trim();
            };
            const planningChan = pickValue(planningPicker);
            const notifChan = pickValue(notifPicker);
            const planningEnabled = !!entry.querySelector('[data-streamer-planning-enabled]')?.checked;
            const cfg = {};
            if (planningEnabled && planningChan) cfg.twitchPlanningChannelId = planningChan;
            if (notifChan) cfg.twitchNotificationChannelId = notifChan;
            for (const t of STREAMER_TOGGLE_FIELDS) {
                if (t.planningOnly && !planningEnabled) continue;
                const cb = entry.querySelector(`[data-streamer-field="${t.key}"]`);
                if (cb && cb.checked) cfg[t.key] = true;
            }
            streamerMap[login] = cfg;
        }
        result[key] = streamerMap;
    }

    // Collect messagelist fields ({text, weight} objects)
    const msgLists = form.querySelectorAll('[data-type="messagelist"]');
    for (const list of msgLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.messagelist-entry');
        const messages = [];
        for (const entry of entries) {
            const msg = entry.querySelector('[data-msg-field="message"]')?.value?.trim();
            const w = entry.querySelector('[data-msg-field="weight"]')?.value?.trim();
            if (msg) {
                messages.push({ text: msg, weight: w ? Number(w) : 1 });
            }
        }
        if (messages.length > 0) {
            result[key] = messages;
        }
    }

    // Collect commandlist fields
    const commandLists = form.querySelectorAll('[data-type="commandlist"]');
    for (const list of commandLists) {
        const key = list.dataset.field;
        const commands = [];
        for (const entry of list.querySelectorAll('.commandlist-entry')) {
            const name = entry.querySelector('[data-cmd-field="name"]')?.value?.trim();
            const description = entry.querySelector('[data-cmd-field="description"]')?.value?.trim();
            const responses = [];
            for (const responseEntry of entry.querySelectorAll('.commandlist-response-entry')) {
                const text = responseEntry.querySelector('[data-response-field="text"]')?.value?.trim();
                if (text) responses.push(text);
            }
            if (name) {
                commands.push({ name, description: description || '', responses });
            }
        }
        // Always assigned: removing the last command must persist as [].
        result[key] = commands;
    }

    // Collect embedlist fields
    const embedLists = form.querySelectorAll('[data-type="embedlist"]');
    for (const list of embedLists) {
        const key = list.dataset.field;
        const entries = list.querySelectorAll('.embedlist-entry');
        const embeds = [];
        for (const entry of entries) {
            const title = entry.querySelector('[data-embed-field="title"]')?.value?.trim();
            const colorInput = entry.querySelector('[data-embed-field="color"]');
            let color = colorInput?.value || '#3498db';
            if (color.startsWith('#')) {
                color = color.substring(1);
            }
            const linkEntries = entry.querySelectorAll('.embedlist-link-entry');
            const links = [];
            for (const linkEntry of linkEntries) {
                const linkTitle = linkEntry.querySelector('[data-link-field="title"]')?.value?.trim();
                const linkUrl = linkEntry.querySelector('[data-link-field="url"]')?.value?.trim();
                if (linkTitle && linkUrl) {
                    links.push({ title: linkTitle, url: linkUrl });
                }
            }
            const embed = {
                title: title || 'Embed',
                color: color,
            };
            if (links.length > 0) {
                embed.links = links;
            }
            embeds.push(embed);
        }
        if (embeds.length > 0) {
            result[key] = embeds;
        }
    }

    return result;
}

export function togglePasswordVisibility(btn) {
    const input = btn.previousElementSibling;
    input.type = input.type === 'password' ? 'text' : 'password';
}

registerActions({
    resetFieldToDefault,
    togglePasswordVisibility,
});
