// Forgot Password page. Reuses ddaApi()/showDdaError()/showDdaSuccess() from shared.js.
(function () {
  const form = document.getElementById('forgot-form');
  const emailInput = document.getElementById('email');
  const submitBtn = document.getElementById('forgot-submit');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideDdaError();
    const email = emailInput.value.trim();
    if (!email) {
      showDdaError('Enter your email address.');
      return;
    }

    submitBtn.disabled = true;
    try {
      const data = await ddaApi('POST', '/api/auth/forgot-password', { body: JSON.stringify({ email }) });
      showDdaSuccess(data.message || 'If an account exists for that email, a reset link has been sent.');
      form.reset();
    } catch (err) {
      // The endpoint always returns 200 regardless of whether the email
      // exists — a caught error here means something actually broke
      // (network, server error), not "no such account".
      showDdaError(err.message || 'Something went wrong. Please try again.');
    } finally {
      submitBtn.disabled = false;
    }
  });
})();
