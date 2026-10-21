// Shared list of Nigerian banks for any "select a bank" dropdown
// (spec section 8: "bank name (dropdown listing all banks in
// Nigeria)"). Kept as its own small file since it's needed by more
// than one page (Sales refunds, and eventually Credit/Booking refunds
// which reuse the same refund flow).
const NIGERIAN_BANKS = [
  'Access Bank', 'Citibank Nigeria', 'Ecobank Nigeria', 'Fidelity Bank',
  'First Bank of Nigeria', 'First City Monument Bank (FCMB)', 'Globus Bank',
  'Guaranty Trust Bank (GTBank)', 'Heritage Bank', 'Keystone Bank',
  'Polaris Bank', 'Providus Bank', 'Stanbic IBTC Bank',
  'Standard Chartered Bank', 'Sterling Bank', 'SunTrust Bank',
  'Titan Trust Bank', 'Union Bank of Nigeria', 'United Bank for Africa (UBA)',
  'Unity Bank', 'Wema Bank', 'Zenith Bank',
  'Jaiz Bank', 'Lotus Bank', 'Optimus Bank', 'Parallex Bank', 'Premium Trust Bank',
  'Signature Bank', 'Coronation Merchant Bank', 'FSDH Merchant Bank',
  'Rand Merchant Bank', 'Nova Merchant Bank', 'Greenwich Merchant Bank',
  'Kuda Bank', 'Opay', 'PalmPay', 'Moniepoint', 'VFD Microfinance Bank',
  'Carbon', 'Rubies Bank', 'Sparkle Bank',
];

function populateBankSelect(select) {
  if (!select) return;
  select.innerHTML = '<option value="">Select bank…</option>' +
    NIGERIAN_BANKS.map((b) => `<option value="${b}">${b}</option>`).join('');
}
