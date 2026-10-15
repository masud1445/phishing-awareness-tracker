const provider = document.getElementById('provider');
if (provider) provider.addEventListener('change', () => {
  const presets = {gmail: ['smtp.gmail.com', 587, 'starttls'], outlook: ['smtp.office365.com', 587, 'starttls']};
  const preset = presets[provider.value];
  if (preset) ['smtp_host', 'smtp_port', 'tls_mode'].forEach((name, index) => document.getElementById(name).value = preset[index]);
});
const identity = document.getElementById('sender_account_id');
if (identity) identity.addEventListener('change', () => {
  document.getElementById('sender_email').value = identity.selectedOptions[0]?.dataset.email || '';
});
