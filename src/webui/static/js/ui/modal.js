// The modal dialog (the JSON editor and other content).
import { registerActions } from '../core/actions.js';
import { settleConfirm } from './confirm.js';
import { toast } from './toast.js';

export let modalSaveCallback = null;

// ── Modal ─────────────────────────────────────────
// A dialog: focus moves into it and stays there (Tab cycles), Échap closes
// it, and focus goes back to the control that opened it.
export let modalReturnFocus = null;
export const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function showModal() {
    const overlay = document.getElementById('modal-overlay');
    if (!overlay.classList.contains('active')) {
        modalReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    overlay.classList.add('active');
    const first = overlay.querySelector('.modal-body ' + FOCUSABLE) || overlay.querySelector('.modal');
    first?.focus();
}

document.addEventListener('keydown', (event) => {
    // The confirmation sits above the JSON modal: it takes the keys first.
    const confirming = document.getElementById('confirm-overlay')?.classList.contains('active');
    const overlay = document.getElementById(confirming ? 'confirm-overlay' : 'modal-overlay');
    if (!overlay?.classList.contains('active')) return;
    if (event.key === 'Escape') {
        event.preventDefault();
        if (confirming) settleConfirm(false);
        else closeModal();
        return;
    }
    if (event.key !== 'Tab') return;
    const items = [...overlay.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    } else if (!overlay.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
    }
});

export function openModal(title, json, onSave) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('json-editor').value = JSON.stringify(json, null, 4);
    document.getElementById('json-error').textContent = '';
    modalSaveCallback = onSave;
    showModal();
}

export function closeModal() {
    document.getElementById('modal-overlay').classList.remove('active');
    modalReturnFocus?.focus?.();
    modalReturnFocus = null;
    document.getElementById('modal-save-btn').style.display = '';
    modalSaveCallback = null;
    const body = document.getElementById('modal-overlay').querySelector('.modal-body');
    body.innerHTML = `
            <textarea class="json-editor" id="json-editor" spellcheck="false"></textarea>
            <div class="json-error" id="json-error"></div>
        `;
}

export async function saveModal() {
    const editor = document.getElementById('json-editor');
    const errorEl = document.getElementById('json-error');

    if (!editor) {
        try {
            if (modalSaveCallback) {
                await modalSaveCallback();
            }
            closeModal();
            toast('Configuration sauvegardée !', 'success');
        } catch (e) {
            if (errorEl) errorEl.textContent = e.message;
            toast('Erreur lors de la sauvegarde', 'error');
        }
        return;
    }

    try {
        const parsed = JSON.parse(editor.value);
        if (errorEl) errorEl.textContent = '';
        if (modalSaveCallback) {
            await modalSaveCallback(parsed);
        }
        closeModal();
        toast('Configuration sauvegardée !', 'success');
    } catch (e) {
        if (e instanceof SyntaxError) {
            if (errorEl) errorEl.textContent = `JSON invalide : ${e.message}`;
        } else {
            if (errorEl) errorEl.textContent = e.message;
            toast('Erreur lors de la sauvegarde', 'error');
        }
    }
}

// What the modal's "Enregistrer" does, for content other than the JSON editor.
export function onModalSave(callback) {
    modalSaveCallback = callback;
}

registerActions({
    closeModal,
    saveModal,
});
