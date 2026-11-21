// The CSP-safe event dispatch: controls name an action, modules register theirs.
import { raw } from './html.js';
import { escapeHtml } from './util.js';

// ── Actions ─────────────────────────────────────────
// The page is served with a Content-Security-Policy that forbids inline
// code (src/webui/security_headers.py): markup never carries an inline on… handler.
// A control names what it does instead, data-<event>="action", with its
// arguments as escaped JSON in data-<event>-args, built with actionArgs().
// One listener per event type looks up the nearest such element and calls
// the action from this allowlist, which each module fills with its own
// actions through registerActions(). EL, CHECKED, VALUE, EVENT and dataArg()
// stand for the element, its state and the event, resolved at that point.
// An action returning false, or an element with data-prevent, cancels the
// event's default (a link's navigation), as `return false` did inline.
export const EL = { $: 'el' };
export const CHECKED = { $: 'checked' };
export const VALUE = { $: 'value' };
export const EVENT = { $: 'event' };
export const dataArg = (key) => ({ $: 'data', k: key });

// Every action a control may name, registered by the module that defines it.
const ACTIONS = {
    // Small ones that were inline code:
    'stop': () => {},  // a toggle inside a clickable row: the row's action doesn't run
    'remove-closest': (selector, el) => el.closest(selector)?.remove(),
    'select-self': (el) => el.select(),
};

export function registerActions(actions) {
    for (const [name, action] of Object.entries(actions)) {
        if (Object.hasOwn(ACTIONS, name)) throw new Error(`Action déjà enregistrée : ${name}`);
        ACTIONS[name] = action;
    }
}

// Already escaped: a raw() value, so html`…` doesn't escape it twice.
export function actionArgs(...args) {
    return raw(escapeHtml(JSON.stringify(args)));
}

export function resolveActionArg(arg, el, event) {
    if (arg === null || typeof arg !== 'object' || !('$' in arg)) return arg;
    switch (arg.$) {
        case 'el': return el;
        case 'checked': return el.checked;
        case 'value': return el.value;
        case 'event': return event;
        case 'data': return el.dataset[arg.k];
        default: return undefined;
    }
}

for (const type of ['click', 'change', 'input', 'mousedown', 'keydown']) {
    document.addEventListener(type, (event) => {
        const el = event.target instanceof Element ? event.target.closest(`[data-${type}]`) : null;
        if (!el) return;
        const name = el.dataset[type];
        const action = Object.hasOwn(ACTIONS, name) ? ACTIONS[name] : null;
        if (!action) {
            console.error('Action inconnue :', name);
            return;
        }
        const raw = el.dataset[`${type}Args`];
        const args = raw ? JSON.parse(raw).map((arg) => resolveActionArg(arg, el, event)) : [];
        const result = action(...args);
        if (result === false || el.hasAttribute('data-prevent')) event.preventDefault();
    });
}
