// The app's own confirmation dialog (never the browser's confirm()).
import { registerActions } from '../core/actions.js';

// ── Confirmation ──────────────────────────────────
// askConfirm() resolves to true or false. A destructive action gets a red
// button and the focus on "Annuler", so Entrée doesn't confirm it.
export let confirmResolve = null;
export let confirmReturnFocus = null;

export function askConfirm(message, { title = 'Confirmer', confirm = 'Confirmer', danger = false } = {}) {
    if (confirmResolve) settleConfirm(false);
    const overlay = document.getElementById('confirm-overlay');
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-message').textContent = message;
    const ok = document.getElementById('confirm-ok');
    ok.textContent = confirm;
    ok.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
    confirmReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    overlay.classList.add('active');
    (danger ? document.getElementById('confirm-cancel') : ok).focus();
    return new Promise((resolve) => { confirmResolve = resolve; });
}

export function settleConfirm(answer) {
    document.getElementById('confirm-overlay')?.classList.remove('active');
    const resolve = confirmResolve;
    confirmResolve = null;
    confirmReturnFocus?.focus?.();
    confirmReturnFocus = null;
    resolve?.(answer === true);
}

registerActions({
    settleConfirm,
});
