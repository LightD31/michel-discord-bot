// The schema form's fields: how each type is drawn, and how its value is read back (P17).
//
// renderSchemaForm draws a field's label and help, then FIELDS[type].render;
// collectFormValues reads each [data-field] back with FIELDS[type].collect,
// which answers undefined for "nothing to save". The simple types live here;
// each list widget keeps its own in its file under widgets/. Every value is
// interpolated through html`…`, so nothing is escaped by hand.
import { EL, actionArgs } from '../core/actions.js';
import { html, raw } from '../core/html.js';
import { multiPickerHtml, multiPickerIds } from './pickers/lists.js';
import { channelPickerHtml, channelPickerValue } from './pickers/channels.js';
import { memberPickerHtml } from './pickers/members.js';
import { rolePickerHtml } from './pickers/roles.js';
import { accountFieldHtml } from './widgets/accounts.js';
import { commandListField } from './widgets/commands.js';
import { embedListField } from './widgets/embeds.js';
import { keyValueMapField, nameMapField } from './widgets/maps.js';
import { messageListField } from './widgets/messages.js';
import { modelsField } from './widgets/models.js';
import { rssFeedMapField } from './widgets/rss.js';
import { spotifyMapField } from './widgets/spotify-users.js';
import { streamerMapField } from './widgets/streamers.js';
import { teamsField } from './widgets/teams.js';
import { youtubeChannelMapField } from './widgets/youtube.js';

// "Entre 2 et 50", from a number field's schema bounds.
export function numberRange(field) {
    const fmt = (n) => Number(n).toLocaleString('fr-FR');
    if (field.min != null && field.max != null) return `Entre ${fmt(field.min)} et ${fmt(field.max)}`;
    if (field.min != null) return `Au moins ${fmt(field.min)}`;
    if (field.max != null) return `Au plus ${fmt(field.max)}`;
    return '';
}

const text = (value) => (value === undefined || value === null ? '' : String(value));
const orUndefined = (value) => (value === '' ? undefined : value);
const defaultHint = (field) => (field.default != null ? `Défaut: ${field.default}` : '');

const channelField = {
    // A forum field (P15) lists the server's forums only; a channel one, never them.
    render: ({ key, field, value }) => channelPickerHtml(html`data-field="${key}"`, text(value), { type: field.type }),
    collect: (el) => orUndefined(channelPickerValue(el)),
};

const stringField = {
    render: ({ key, field, value }) => html`<input type="text" class="form-input" data-field="${key}" data-type="string"
            value="${text(value)}" placeholder="${defaultHint(field)}">`,
    collect: (el) => el.value || undefined,
};

const listField = {
    render: ({ key, field, value }) => {
        const shown = Array.isArray(value) ? value.join('\n') : text(value);
        const hint = Array.isArray(field.default) && field.default.length ? `Défaut: ${field.default.join(', ')}` : 'Une valeur par ligne';
        return html`<textarea class="form-textarea" data-field="${key}" data-type="list" placeholder="${hint}" rows="3">${shown}</textarea>`;
    },
    collect: (el) => {
        const lines = el.value.trim().split('\n').map(s => s.trim()).filter(Boolean);
        return lines.length ? lines : undefined;
    },
};

export const FIELDS = {
    boolean: {
        // Unset: the schema's default, which a save then writes as is.
        render: ({ key, field, value, hasValue }) => html`
            <label class="toggle toggle-field">
                <input type="checkbox" data-field="${key}" data-type="boolean"
                    ${(hasValue ? value === true : field.default === true) ? 'checked' : ''}>
                <span class="toggle-slider"></span>
            </label>`,
        collect: (el) => el.checked,
    },
    number: {
        // The schema's bounds: the browser flags a value outside them, and the save route refuses it.
        render: ({ key, field, value, hasValue, fieldId }) => {
            const range = numberRange(field);
            return html`<input type="number" class="form-input" data-field="${key}" data-type="number"
                    value="${hasValue ? value : (field.default ?? '')}"
                    ${field.step != null && html`step="${field.step}"`}
                    ${field.min != null && html`min="${field.min}"`}
                    ${field.max != null && html`max="${field.max}"`}
                    ${range && html`aria-describedby="${fieldId}-range"`}
                    placeholder="${defaultHint(field)}">
                ${range && html`<div class="form-range" id="${fieldId}-range">${range}</div>`}`;
        },
        collect: (el) => (el.value === '' ? undefined : Number(el.value)),
    },
    secret: {
        render: ({ key, value, hasValue }) => html`<div class="secret-field">
                <input type="password" class="form-input" data-field="${key}" data-type="secret"
                    value="${hasValue ? value : ''}" placeholder="${hasValue ? '••••••••' : 'Non défini'}">
                <button class="btn-reveal" data-click="togglePasswordVisibility" data-click-args="${actionArgs(EL)}" type="button" title="Afficher/masquer" aria-label="Afficher/masquer">👁</button>
            </div>`,
        collect: (el) => el.value || undefined,
    },
    channel: channelField,
    forum: channelField,
    role: {
        render: ({ key, field, value, fieldId }) => raw(rolePickerHtml(text(value), String(html`data-field="${key}" data-type="role"`), field.roleUse, fieldId)),
        collect: (el) => el.value || undefined,
    },
    member: {
        render: ({ key, value }) => raw(memberPickerHtml(text(value), String(html`data-field="${key}" data-type="member"`))),
        collect: (el) => el.value || undefined,
    },
    choice: {
        render: ({ key, field, value, hasValue }) => {
            const current = hasValue ? String(value) : String(field.default ?? '');
            return html`<select class="form-input" data-field="${key}" data-type="choice">
                ${(field.choices || []).map(o => html`<option value="${o.value}" ${o.value === current ? 'selected' : ''}>${o.label}</option>`)}
            </select>`;
        },
        collect: (el) => el.value || undefined,
    },
    channellist: {
        render: ({ key, field, value }) => raw(multiPickerHtml(key, field.type, Array.isArray(value) ? value : [], field.roleUse)),
        collect: (el) => {
            const ids = multiPickerIds(el);
            return ids.length ? ids : undefined;
        },
    },
    message: {
        render: ({ key, value }) => html`<input type="text" class="form-input" data-field="${key}" data-type="message" value="${text(value)}" placeholder="ID du message">`,
        collect: (el) => el.value || undefined,
    },
    url: {
        render: ({ key, value }) => html`<input type="text" class="form-input" data-field="${key}" data-type="url" value="${text(value)}" placeholder="URL">`,
        collect: (el) => el.value || undefined,
    },
    account: {
        render: ({ key, value }) => accountFieldHtml(key, value),
        collect: stringField.collect,
    },
    accountlist: {
        render: ({ key, value }) => accountFieldHtml(key, value, { multi: true }),
        collect: listField.collect,
    },
    list: listField,
    'list:number': {
        render: ({ key, value }) => html`<input type="text" class="form-input" data-field="${key}" data-type="list:number"
                value="${Array.isArray(value) ? value.join(', ') : text(value)}"
                placeholder="Nombres séparés par des virgules (ex: 1, 2, 3)">`,
        collect: (el) => (el.value.trim()
            ? el.value.split(',').map(s => Number(s.trim())).filter(n => !Number.isNaN(n))
            : undefined),
    },
    dict: {
        render: ({ key, value, hasValue }) => html`<textarea class="form-textarea json-field" data-field="${key}" data-type="dict"
                rows="5" spellcheck="false">${hasValue ? JSON.stringify(value, null, 2) : '{}'}</textarea>`,
        collect: (el) => {
            try { return JSON.parse(el.value); } catch { return undefined; }
        },
    },
    text: {
        render: ({ key, field, value, hasValue }) => html`<textarea class="form-textarea" data-field="${key}" data-type="text"
                rows="8">${hasValue ? String(value) : (field.default ?? '')}</textarea>`,
        collect: (el) => el.value || undefined,
    },
    aimodel: {
        render: ({ key, value }) => {
            const current = text(value);
            return html`<select class="form-input" data-field="${key}" data-type="aimodel" data-current="${current}">
                ${current ? html`<option value="${current}" selected>${current}</option>` : html`<option value="">— Chargement… —</option>`}
            </select>`;
        },
        // Empty is meaningful (fall back to the default), so keep it.
        collect: (el) => el.value,
    },
    string: stringField,
    models: modelsField,
    teams: teamsField,
    discord2name: nameMapField,
    keyvaluemap: keyValueMapField,
    streamermap: streamerMapField,
    youtubechannelmap: youtubeChannelMapField,
    rssfeedmap: rssFeedMapField,
    spotifymap: spotifyMapField,
    messagelist: messageListField,
    commandlist: commandListField,
    embedlist: embedListField,
};
FIELDS.rolelist = FIELDS.channellist;

export function fieldOf(type) {
    return FIELDS[type] || FIELDS.string;
}
