// Reset Password page — reached via the emailed link (/reset-password?token=...).
// Reuses ddaApi()/showDdaError()/showDdaSuccess() from shared.js.
(function () {
  const form = document.getElementById('reset-form');
  const newPasswordInput = document.getElementById('new_password');
  const confirmInput = document.getElementById('confirm_password');
  const submitBtn = document.getElementById('reset-submit');

  const token = new URLSearchParams(window.location.search).get('token') || '';
  if (!token) {
    showDdaError('This reset link is missing its token. Request a new one from the Forgot Password page.');
    form.querySelectorAll('input, button').forEach((el) => { el.disabled = true; });
  }

  function setFieldError(id, message) {
    const el = document.getElementById('err-' + id);
    if (el) { el.textContent = message || ''; el.classList.toggle('hidden', !message); }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideDdaError();
    setFieldError('new_password', null);
    setFieldError('confirm_password', null);

    const newPassword = newPasswordInput.value;
    const confirm = confirmInput.value;
    let ok = true;
    if (newPassword.length < 8) {
      setFieldError('new_password', 'Password must be at least 8 characters.');
      ok = false;
    }
    if (confirm !== newPassword) {
      setFieldError('confirm_password', 'Passwords do not match.');
      ok = false;
    }
    if (!ok) return;

    submitBtn.disabled = true;
    try {
      await ddaApi('POST', '/api/auth/reset-password-token', {
        body: JSON.stringify({ token, new_password: newPassword }),
      });
      showDdaSuccess('Password reset. Redirecting to sign in…');
      form.reset();
      form.querySelectorAll('input, button').forEach((el) => { el.disabled = true; });
      setTimeout(() => { window.location.href = '/login'; }, 1500);
    } catch (err) {
      showDdaError(err.message || 'Could not reset password.');
      submitBtn.disabled = false;
    }
  });
})();
