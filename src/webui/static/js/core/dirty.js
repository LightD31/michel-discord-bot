// Unsaved edits: the "● Modifié" mark and the question before leaving.
import { askConfirm } from '../ui/confirm.js';

// ── Unsaved edits ─────────────────────────────────
// A form page is "modified" from the first edit until it is saved: leaving
// it (another page, the back button, closing the tab, a module switch that
// rebuilds it) asks first, instead of silently dropping what was typed.
export let formDirty = false;
export const DIRTY_SCOPE = '.main .schema-form, #rolemenu-builder-card';

export function setDirty(dirty) {
    formDirty = dirty;
    document.body.classList.toggle('has-unsaved', dirty);
}

for (const type of ['input', 'change']) {
    document.addEventListener(type, (event) => {
        if (event.target instanceof Element && event.target.closest(DIRTY_SCOPE)) setDirty(true);
    }, true);
}

window.addEventListener('beforeunload', (event) => {
    if (!formDirty) return;
    event.preventDefault();
    event.returnValue = '';
});

export async function confirmDiscard() {
    if (!formDirty) return true;
    const leave = await askConfirm('Des modifications ne sont pas enregistrées. Les abandonner ?',
        { title: 'Modifications non enregistrées', confirm: 'Abandonner', danger: true });
    if (!leave) return false;
    setDirty(false);
    return true;
}
