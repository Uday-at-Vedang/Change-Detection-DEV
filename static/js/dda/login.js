// Login / create-account page. Reuses ddaApi(), showDdaError() from app.js.
(function () {
  let mode = 'login'; // 'login' | 'register'

  const form = document.getElementById('auth-form');
  const title = document.getElementById('auth-title');
  const submitBtn = document.getElementById('auth-submit');
  const registerOnlyEls = document.querySelectorAll('.register-only');
  const loginOnlyEls = document.querySelectorAll('.login-only');
  const toggleToRegister = document.getElementById('toggle-to-register');
  const toggleToLogin = document.getElementById('toggle-to-login');

  const fields = {
    full_name: document.getElementById('full_name'),
    email: document.getElementById('email'),
    password: document.getElementById('password'),
    confirm_password: document.getElementById('confirm_password'),
  };

  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function setFieldError(name, message) {
    const el = document.getElementById('err-' + name);
    const input = fields[name];
    if (message) {
      if (el) { el.textContent = message; el.classList.remove('hidden'); }
      if (input) input.classList.add('invalid');
    } else {
      if (el) { el.textContent = ''; el.classList.add('hidden'); }
      if (input) input.classList.remove('invalid');
    }
  }

  function clearFieldErrors() {
    Object.keys(fields).forEach((name) => setFieldError(name, null));
  }

  function setMode(next) {
    mode = next;
    const isRegister = mode === 'register';
    title.textContent = isRegister ? 'Create account' : 'Sign in';
    submitBtn.textContent = isRegister ? 'Create account' : 'Sign in';
    registerOnlyEls.forEach((el) => el.classList.toggle('hidden', !isRegister));
    loginOnlyEls.forEach((el) => el.classList.toggle('hidden', isRegister));
    toggleToRegister.classList.toggle('hidden', isRegister);
    toggleToLogin.classList.toggle('hidden', !isRegister);
    fields.password.autocomplete = isRegister ? 'new-password' : 'current-password';
    hideDdaError();
    clearFieldErrors();
  }

  document.getElementById('show-register').addEventListener('click', () => setMode('register'));
  document.getElementById('show-login').addEventListener('click', () => setMode('login'));

  // Validates the form client-side; returns true if OK, else sets inline
  // field errors and returns false. Mirrors (but doesn't replace) the
  // server-side checks in app/main.py's UserCreate/UserLogin validators.
  function validate() {
    clearFieldErrors();
    let ok = true;

    const email = fields.email.value.trim();
    if (!email) {
      setFieldError('email', 'Email is required.');
      ok = false;
    } else if (!EMAIL_RE.test(email)) {
      setFieldError('email', 'Enter a valid email address.');
      ok = false;
    }

    const password = fields.password.value;
    if (!password) {
      setFieldError('password', 'Password is required.');
      ok = false;
    } else if (mode === 'register' && password.length < 8) {
      setFieldError('password', 'Password must be at least 8 characters.');
      ok = false;
    }

    if (mode === 'register') {
      const fullName = fields.full_name.value.trim();
      if (!fullName) {
        setFieldError('full_name', 'Full name is required.');
        ok = false;
      }
      const confirm = fields.confirm_password.value;
      if (!confirm) {
        setFieldError('confirm_password', 'Please confirm your password.');
        ok = false;
      } else if (confirm !== password) {
        setFieldError('confirm_password', 'Passwords do not match.');
        ok = false;
      }
    }

    return ok;
  }

  // Live re-validation of confirm-password as the user types, so the
  // mismatch error clears the moment it's fixed rather than only on submit.
  fields.confirm_password.addEventListener('input', () => {
    if (mode !== 'register') return;
    const confirm = fields.confirm_password.value;
    if (!confirm) return; // let the required check on submit handle empty
    setFieldError('confirm_password', confirm === fields.password.value ? null : 'Passwords do not match.');
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideDdaError();
    if (!validate()) return;

    const email = fields.email.value.trim();
    const password = fields.password.value;

    submitBtn.disabled = true;
    try {
      if (mode === 'register') {
        const full_name = fields.full_name.value.trim();
        await ddaApi('POST', '/api/auth/register', { body: JSON.stringify({ email, password, full_name }) });
        // No auto-login (see app/main.py's register()) — send the user back
        // to the sign-in form rather than into the app.
        window.location.href = '/login?registered=1';
        return;
      }
      const remember_me = document.getElementById('remember_me').checked;
      await ddaApi('POST', '/api/auth/login', { body: JSON.stringify({ email, password, remember_me }) });
      window.location.href = '/';
    } catch (err) {
      const msg = err.message || (mode === 'register' ? 'Could not create account.' : 'Invalid email or password.');
      // Surface the one error we can usefully point at a specific field;
      // everything else (lockout, server errors, bad credentials) goes in
      // the generic banner since it deliberately doesn't say which part —
      // e.g. "Invalid email or password" never reveals whether the account
      // exists, so it shouldn't be pinned to the email field specifically.
      if (/already registered/i.test(msg)) {
        setFieldError('email', 'This email is already registered.');
      } else {
        showDdaError(msg);
      }
    } finally {
      submitBtn.disabled = false;
    }
  });

  if (new URLSearchParams(window.location.search).get('registered') === '1') {
    showDdaSuccess('Account created. Please sign in.');
    // Clean the query param off the URL so a refresh doesn't re-show it.
    window.history.replaceState({}, '', '/login');
  }
})();
