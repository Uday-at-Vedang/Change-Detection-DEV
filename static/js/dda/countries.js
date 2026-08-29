// Small country dial-code list used by the phone-number inputs on the login,
// register, and profile pages. Keep India first so it's the default.
window.DDA_COUNTRIES = [
  { code: 'IN', name: 'India', dial: '+91' },
  { code: 'US', name: 'United States', dial: '+1' },
  { code: 'CA', name: 'Canada', dial: '+1' },
  { code: 'GB', name: 'United Kingdom', dial: '+44' },
  { code: 'AU', name: 'Australia', dial: '+61' },
  { code: 'NZ', name: 'New Zealand', dial: '+64' },
  { code: 'AE', name: 'United Arab Emirates', dial: '+971' },
  { code: 'SA', name: 'Saudi Arabia', dial: '+966' },
  { code: 'SG', name: 'Singapore', dial: '+65' },
  { code: 'MY', name: 'Malaysia', dial: '+60' },
  { code: 'BD', name: 'Bangladesh', dial: '+880' },
  { code: 'LK', name: 'Sri Lanka', dial: '+94' },
  { code: 'NP', name: 'Nepal', dial: '+977' },
  { code: 'PK', name: 'Pakistan', dial: '+92' },
  { code: 'DE', name: 'Germany', dial: '+49' },
  { code: 'FR', name: 'France', dial: '+33' },
  { code: 'IT', name: 'Italy', dial: '+39' },
  { code: 'ES', name: 'Spain', dial: '+34' },
  { code: 'NL', name: 'Netherlands', dial: '+31' },
  { code: 'BE', name: 'Belgium', dial: '+32' },
  { code: 'CH', name: 'Switzerland', dial: '+41' },
  { code: 'SE', name: 'Sweden', dial: '+46' },
  { code: 'NO', name: 'Norway', dial: '+47' },
  { code: 'DK', name: 'Denmark', dial: '+45' },
  { code: 'FI', name: 'Finland', dial: '+358' },
  { code: 'IE', name: 'Ireland', dial: '+353' },
  { code: 'PT', name: 'Portugal', dial: '+351' },
  { code: 'PL', name: 'Poland', dial: '+48' },
  { code: 'CZ', name: 'Czechia', dial: '+420' },
  { code: 'RU', name: 'Russia', dial: '+7' },
  { code: 'TR', name: 'Türkiye', dial: '+90' },
  { code: 'IL', name: 'Israel', dial: '+972' },
  { code: 'EG', name: 'Egypt', dial: '+20' },
  { code: 'ZA', name: 'South Africa', dial: '+27' },
  { code: 'NG', name: 'Nigeria', dial: '+234' },
  { code: 'KE', name: 'Kenya', dial: '+254' },
  { code: 'GH', name: 'Ghana', dial: '+233' },
  { code: 'BR', name: 'Brazil', dial: '+55' },
  { code: 'MX', name: 'Mexico', dial: '+52' },
  { code: 'AR', name: 'Argentina', dial: '+54' },
  { code: 'CL', name: 'Chile', dial: '+56' },
  { code: 'CO', name: 'Colombia', dial: '+57' },
  { code: 'JP', name: 'Japan', dial: '+81' },
  { code: 'KR', name: 'South Korea', dial: '+82' },
  { code: 'CN', name: 'China', dial: '+86' },
  { code: 'HK', name: 'Hong Kong', dial: '+852' },
  { code: 'TW', name: 'Taiwan', dial: '+886' },
  { code: 'TH', name: 'Thailand', dial: '+66' },
  { code: 'VN', name: 'Vietnam', dial: '+84' },
  { code: 'ID', name: 'Indonesia', dial: '+62' },
  { code: 'PH', name: 'Philippines', dial: '+63' },
];

/** Populate a <select> with dial codes; keeps the current value if still present. */
window.populateCountrySelect = function (selectEl, defaultDial) {
  if (!selectEl || !window.DDA_COUNTRIES) return;
  const previous = selectEl.value || defaultDial || '+91';
  selectEl.innerHTML = window.DDA_COUNTRIES
    .map((c) => `<option value="${c.dial}" data-name="${c.name}">${c.dial} &nbsp; ${c.name}</option>`)
    .join('');
  const match = window.DDA_COUNTRIES.find((c) => c.dial === previous);
  selectEl.value = match ? match.dial : (defaultDial || '+91');
};

/** Split an E.164 string like '+919876543210' into { dial, local }. */
window.splitPhoneE164 = function (raw) {
  const p = String(raw || '').trim();
  if (!p.startsWith('+')) return { dial: '+91', local: p.replace(/\D/g, '') };
  const digits = p.replace(/\D/g, '');
  const options = (window.DDA_COUNTRIES || []).map((c) => c.dial.replace('+', ''));
  options.sort((a, b) => b.length - a.length);
  for (const d of options) {
    if (digits.startsWith(d)) return { dial: '+' + d, local: digits.slice(d.length) };
  }
  return { dial: '+91', local: digits };
};
