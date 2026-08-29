// Login / create-account page. Two sign-in modes:
//   * "password" — email + password → session cookie
//   * "otp"      — mobile number → SMS OTP → session cookie
// Reuses ddaApi(), showDdaError(), showDdaSuccess() from shared.js.
(function () {
  let mode = 'login';        // 'login' | 'register'
  let loginMethod = 'password'; // 'password' | 'otp'
  let pendingOtp = null;

  const form = document.getElementById('auth-form');
  const otpForm = document.getElementById('otp-form');
  const title = document.getElementById('auth-title');
  const submitBtn = document.getElementById('auth-submit');
  const tabsEl = document.getElementById('auth-tabs');
  const toggleToRegister = document.getElementById('toggle-to-register');
  const toggleToLogin = document.getElementById('toggle-to-login');
  const countrySelect = document.getElementById('country_code');
  const phoneInput = document.getElementById('phone');

  const fields = {
    full_name: document.getElementById('full_name'),
    email: document.getElementById('email'),
    password: document.getElementById('password'),
    confirm_password: document.getElementById('confirm_password'),
    phone: phoneInput,
  };

  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  if (typeof window.populateCountrySelect === 'function') {
    window.populateCountrySelect(countrySelect, '+91');
  }

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

  function digitsOnly(value) {
    return String(value || '').replace(/\D/g, '');
  }

  // Client-side validity check for the local part of the mobile number.
  // The full E.164 validation still happens on the server.
  function isPhoneValid(dial, local) {
    const digits = digitsOnly(local);
    if (dial === '+91') return digits.length === 10 && /^[6-9]/.test(digits);
    return digits.length >= 6 && digits.length <= 15;
  }

  function showFields() {
    const showByField = (name, on) => {
      document.querySelectorAll(`[data-field="${name}"]`).forEach((el) => {
        el.classList.toggle('hidden', !on);
      });
    };
    const isRegister = mode === 'register';
    const isLogin = mode === 'login';
    const passwordMode = isLogin && loginMethod === 'password';
    const otpMode = isLogin && loginMethod === 'otp';

    showByField('full_name', isRegister);
    showByField('email', true);
    showByField('password', isRegister || passwordMode);
    showByField('confirm_password', isRegister);
    showByField('phone', isRegister);
    showByField('remember', isLogin);
    showByField('forgot', passwordMode);
    document.getElementById('hint-password').classList.toggle('hidden', !isRegister);

    fields.email.required = true;
    fields.password.required = isRegister || passwordMode;
    fields.full_name.required = isRegister;
    fields.confirm_password.required = isRegister;
    fields.phone.required = false;

    tabsEl.classList.toggle('hidden', isRegister);

    if (isRegister) {
      title.textContent = 'Create account';
      submitBtn.textContent = 'Create account';
    } else if (otpMode) {
      title.textContent = 'Sign in with OTP';
      submitBtn.textContent = 'Send OTP to email';
    } else {
      title.textContent = 'Sign in';
      submitBtn.textContent = 'Sign in';
    }
  }

  function setMode(next) {
    mode = next;
    const isRegister = mode === 'register';
    toggleToRegister.classList.toggle('hidden', isRegister);
    toggleToLogin.classList.toggle('hidden', !isRegister);
    fields.password.autocomplete = isRegister ? 'new-password' : 'current-password';
    hideDdaError();
    clearFieldErrors();
    showFields();
  }

  function setLoginMethod(next) {
    loginMethod = next;
    tabsEl.querySelectorAll('.dda-auth-tab').forEach((btn) => {
      const active = btn.dataset.tab === next;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    hideDdaError();
    clearFieldErrors();
    showFields();
  }

  tabsEl?.querySelectorAll('.dda-auth-tab').forEach((btn) => {
    btn.addEventListener('click', () => setLoginMethod(btn.dataset.tab));
  });

  function showOtpStep(on, masked) {
    form.classList.toggle('hidden', on);
    otpForm.classList.toggle('hidden', !on);
    tabsEl.classList.toggle('hidden', on);
    document.querySelector('.auth-toggle')?.classList.toggle('hidden', on);
    if (on) {
      title.textContent = 'Enter email code';
      const help = document.getElementById('otp-help');
      if (help) {
        help.textContent = masked
          ? `We sent a 6-digit code to ${masked}. It expires in 10 minutes.`
          : 'Enter the 6-digit code sent to your email.';
      }
      document.getElementById('otp_code').value = '';
      document.getElementById('otp_code').focus();
    } else {
      pendingOtp = null;
      showFields();
    }
    hideDdaError();
  }

  otpForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideDdaError();
    const code = digitsOnly(document.getElementById('otp_code')?.value);
    if (!/^\d{6}$/.test(code)) {
      showDdaError('Enter the 6-digit code from your email.');
      return;
    }
    const btn = document.getElementById('otp-submit');
    if (btn) btn.disabled = true;
    try {
      await ddaApi('POST', '/api/auth/login/verify-otp', {
        body: JSON.stringify({ otpToken: pendingOtp?.token, code }),
      });
      window.location.href = '/';
    } catch (err) {
      showDdaError(err.message || 'Invalid sign-in code.');
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById('otp-resend')?.addEventListener('click', async () => {
    hideDdaError();
    const btn = document.getElementById('otp-resend');
    if (btn) btn.disabled = true;
    try {
      const data = await ddaApi('POST', '/api/auth/login/resend-otp', {
        body: JSON.stringify({ otpToken: pendingOtp?.token }),
      });
      if (data?.otpToken) pendingOtp = { ...pendingOtp, token: data.otpToken };
      showDdaSuccess('A new code was sent to your email.');
      showOtpStep(true, data?.emailMasked || pendingOtp?.emailMasked);
    } catch (err) {
      showDdaError(err.message || 'Could not resend the code.');
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById('otp-back')?.addEventListener('click', () => showOtpStep(false));

  document.getElementById('show-register').addEventListener('click', () => setMode('register'));
  document.getElementById('show-login').addEventListener('click', () => setMode('login'));

  function validate() {
    clearFieldErrors();
    let ok = true;
    const isRegister = mode === 'register';
    const passwordMode = !isRegister && loginMethod === 'password';
    const otpMode = !isRegister && loginMethod === 'otp';

    if (isRegister && !fields.full_name.value.trim()) {
      setFieldError('full_name', 'Full name is required.');
      ok = false;
    }

    const email = fields.email.value.trim();
    if (!email) {
      setFieldError('email', 'Email is required.');
      ok = false;
    } else if (!EMAIL_RE.test(email)) {
      setFieldError('email', 'Enter a valid email address.');
      ok = false;
    }

    if (isRegister || passwordMode) {
      const password = fields.password.value;
      if (!password) {
        setFieldError('password', 'Password is required.');
        ok = false;
      } else if (isRegister && password.length < 8) {
        setFieldError('password', 'Password must be at least 8 characters.');
        ok = false;
      }
      if (isRegister) {
        const confirm = fields.confirm_password.value;
        if (!confirm) {
          setFieldError('confirm_password', 'Please confirm your password.');
          ok = false;
        } else if (confirm !== password) {
          setFieldError('confirm_password', 'Passwords do not match.');
          ok = false;
        }
      }
    }

    if (isRegister) {
      const local = digitsOnly(fields.phone.value);
      if (local) {
        const dial = countrySelect.value || '+91';
        if (!isPhoneValid(dial, local)) {
          setFieldError('phone', dial === '+91'
            ? 'Enter a valid 10-digit Indian mobile number.'
            : 'Enter a valid mobile number.');
          ok = false;
        }
      }
    }

    return ok;
  }

  fields.confirm_password.addEventListener('input', () => {
    if (mode !== 'register') return;
    const confirm = fields.confirm_password.value;
    if (!confirm) return;
    setFieldError('confirm_password', confirm === fields.password.value ? null : 'Passwords do not match.');
  });

  phoneInput.addEventListener('input', () => {
    phoneInput.value = digitsOnly(phoneInput.value);
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideDdaError();
    document.getElementById('dda-success')?.classList.add('hidden');
    if (!validate()) return;

    submitBtn.disabled = true;
    try {
      if (mode === 'register') {
        await ddaApi('POST', '/api/auth/register', {
          body: JSON.stringify({
            email: fields.email.value.trim(),
            password: fields.password.value,
            full_name: fields.full_name.value.trim(),
            phone: digitsOnly(fields.phone.value),
            country_code: countrySelect.value || '+91',
          }),
        });
        window.location.href = '/login?registered=1';
        return;
      }

      const remember_me = document.getElementById('remember_me').checked;

      if (loginMethod === 'password') {
        await ddaApi('POST', '/api/auth/login', {
          body: JSON.stringify({
            email: fields.email.value.trim(),
            password: fields.password.value,
            remember_me,
          }),
        });
        window.location.href = '/';
        return;
      }

      const data = await ddaApi('POST', '/api/auth/login/request-otp', {
        body: JSON.stringify({
          email: fields.email.value.trim(),
          remember_me,
        }),
      });
      pendingOtp = {
        token: data.otpToken,
        emailMasked: data.emailMasked,
      };
      showOtpStep(true, data.emailMasked);
    } catch (err) {
      const msg = err.message || 'Sign-in failed.';
      if (/already registered/i.test(msg) && /mobile/i.test(msg)) {
        setFieldError('phone', 'This mobile number is already registered.');
      } else if (/already registered/i.test(msg)) {
        setFieldError('email', 'This email is already registered.');
      } else if (/no account/i.test(msg) || /unknown email/i.test(msg)) {
        setFieldError('email', 'No account is registered with this email.');
        showDdaError('No account is registered with this email. Try Password sign-in or create an account.');
      } else if (/mobile number/i.test(msg)) {
        setFieldError('phone', msg);
      } else {
        showDdaError(msg);
      }
    } finally {
      submitBtn.disabled = false;
    }
  });

  showFields();

  if (new URLSearchParams(window.location.search).get('registered') === '1') {
    showDdaSuccess('Account created. Please sign in.');
    window.history.replaceState({}, '', '/login');
  }
})();
