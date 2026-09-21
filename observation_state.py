"""Shared DOM state for text observations and supplemental clickable controls."""

# toggle-wrapper is the CRM's visible checkbox-card component. Read its native
# control rather than inferring availability from colour, price or opacity.
CONTROL_STATE_JS = """
    function cardControl(element) {
        if (!element.matches('.toggle-wrapper, label, [role=option]')) return null;
        const controls = element.querySelectorAll('input[type=checkbox], input[type=radio]');
        return controls.length === 1 ? controls[0] : null;
    }
    function isDisabled(element) {
        if (element.matches(':disabled') || element.closest('[aria-disabled=true], [inert]')) return true;
        const card = element.closest('.toggle-wrapper, label, [role=option]');
        const control = card && cardControl(card);
        return !!control && (control.matches(':disabled') ||
            !!control.closest('[aria-disabled=true], [inert]'));
    }
    function stateLabel(element) {
        const control = cardControl(element);
        if (!control) {
            const target = element.matches('button, input, select, textarea, [role], [inert]');
            return target && isDisabled(element) ? '[disabled] ' : '';
        }
        return '[' + (isDisabled(element) ? 'disabled' : 'enabled') + ', ' +
            (control.checked ? 'selected' : 'not selected') + '] ';
    }
"""
