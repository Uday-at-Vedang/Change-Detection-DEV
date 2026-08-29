// My Profile page — view/edit full name, change password.
// Reuses ddaApi()/escapeHtml() from shared.js.
(function () {
  const avatarEl = document.getElementById('profile-avatar');
  const nameEl = document.getElementById('profile-name');
  const emailEl = document.getElementById('profile-email');
  const roleBadgeEl = document.getElementById('profile-role-badge');
  const lastLoginEl = document.getElementById('profile-last-login');
  const fullNameInput = document.getElementById('profile-full-name');
  const emailFieldInput = document.getElementById('profile-email-field');
  const phoneInput = document.getElementById('profile-phone');
  const countrySelect = document.getElementById('profile-country-code');
  if (typeof window.populateCountrySelect === 'function') {
    window.populateCountrySelect(countrySelect, '+91');
  }

  function showMsg(elId, msg, isError) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.textContent = msg;
    el.classList.remove('hidden');
    if (!isError) setTimeout(() => el.classList.add('hidden'), 4000);
  }
  function hideMsg(elId) {
    document.getElementById(elId)?.classList.add('hidden');
  }

  async function loadProfile() {
    try {
      const me = await ddaApi('GET', '/api/me');
      const initial = (me.full_name || me.email || '?').trim().charAt(0).toUpperCase();
      avatarEl.textContent = initial;
      nameEl.textContent = me.full_name || me.email;
      emailEl.textContent = me.email;
      roleBadgeEl.textContent = (me.role || 'analyst').replace(/^\w/, (c) => c.toUpperCase());
      lastLoginEl.textContent = me.last_login ? new Date(me.last_login).toLocaleString() : 'This session';
      fullNameInput.value = me.full_name || '';
      emailFieldInput.value = me.email || '';
      if (phoneInput) {
        const parts = typeof window.splitPhoneE164 === 'function'
          ? window.splitPhoneE164(me.phone || '')
          : { dial: '+91', local: (me.phone || '').replace(/\D/g, '') };
        if (countrySelect) countrySelect.value = parts.dial;
        phoneInput.value = parts.local;
      }
    } catch (err) {
      showMsg('profile-error', err.message || 'Could not load your profile.', true);
    }
  }
  loadProfile();

  document.getElementById('profile-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    hideMsg('profile-error');
    const full_name = fullNameInput.value.trim();
    const phone = (phoneInput?.value || '').replace(/\D/g, '');
    const country_code = countrySelect?.value || '+91';
    if (!full_name) {
      showMsg('profile-error', 'Full name is required.', true);
      return;
    }
    try {
      await ddaApi('PUT', '/api/me', { body: JSON.stringify({ full_name, phone, country_code }) });
      nameEl.textContent = full_name;
      avatarEl.textContent = full_name.trim().charAt(0).toUpperCase();
      showMsg('profile-success', 'Profile updated.', false);
    } catch (err) {
      showMsg('profile-error', err.message || 'Could not save changes.', true);
    }
  });

  document.getElementById('password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    hideMsg('password-error');
    const current_password = document.getElementById('current-password').value;
    const new_password = document.getElementById('new-password').value;
    const confirm = document.getElementById('confirm-new-password').value;

    if (new_password.length < 8) {
      showMsg('password-error', 'New password must be at least 8 characters.', true);
      return;
    }
    if (new_password !== confirm) {
      showMsg('password-error', 'Passwords do not match.', true);
      return;
    }
    try {
      await ddaApi('POST', '/api/auth/change-password', { body: JSON.stringify({ current_password, new_password }) });
      showMsg('password-success', 'Password changed.', false);
      document.getElementById('password-form').reset();
    } catch (err) {
      showMsg('password-error', err.message || 'Could not change password.', true);
    }
  });
})();
