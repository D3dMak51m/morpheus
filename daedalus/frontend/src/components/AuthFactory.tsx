import { useState } from 'react';

// Backend /telegram/request-code returns `phone_code_hash` (Pyrogram), which
// /telegram/verify-code requires. Earlier this was mis-named `transaction_id`,
// so the hash was lost and verify failed with a 422 before Pyrogram ran.

export function AuthFactory({ token }: { token: string | null }) {
  const [activeTab, setActiveTab] = useState<'telegram' | 'mobile'>('telegram');
  const [agentId, setAgentId] = useState('');
  const [deviceId, setDeviceId] = useState('');

  // Telegram State
  const [phoneNumber, setPhoneNumber] = useState('');
  const [transactionId, setTransactionId] = useState('');
  const [otpCode, setOtpCode] = useState('');
  const [twoFaPassword, setTwoFaPassword] = useState('');
  const [tgStep, setTgStep] = useState<1 | 2 | 3>(1); // 1: Request, 2: OTP, 3: Success
  const [tgLoading, setTgLoading] = useState(false);

  // Mobile Session State (Stage 23 — autonomous extraction)
  const [mobilePlatform, setMobilePlatform] = useState('instagram');
  const [mobileUsername, setMobileUsername] = useState('');
  const [mobileLoading, setMobileLoading] = useState(false);

  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const handleRequestCode = async () => {
    setTgLoading(true);
    setMessage(null);
    try {
      const res = await fetch('/api/v1/auth-factory/telegram/request-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_number: phoneNumber })
      });
      const data: any = await res.json();

      if (!res.ok) throw new Error(data.detail || data.message || 'Request failed');

      setTransactionId(data.phone_code_hash || '');
      setTgStep(2);
      setMessage({ text: data.message, type: 'success' });
    } catch (err: any) {
      setMessage({ text: err.message, type: 'error' });
    } finally {
      setTgLoading(false);
    }
  };

  const handleVerifyCode = async () => {
    if (!agentId || !deviceId) {
      setMessage({ text: "Agent ID and Device ID are required.", type: 'error' });
      return;
    }
    setTgLoading(true);
    setMessage(null);
    try {
      const payload: any = {
        phone_code_hash: transactionId,
        phone_number: phoneNumber,
        code: otpCode,
        agent_id: agentId,
        device_id: deviceId
      };
      if (twoFaPassword) payload.password = twoFaPassword;

      const res = await fetch('/api/v1/auth-factory/telegram/verify-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data: any = await res.json();

      if (!res.ok) throw new Error(data.detail || data.message || 'Verification failed');

      setTgStep(3);
      setMessage({ text: data.message, type: 'success' });
    } catch (err: any) {
      setMessage({ text: err.message, type: 'error' });
    } finally {
      setTgLoading(false);
    }
  };

  const handleAutoExtract = async () => {
    if (!deviceId || !mobileUsername) {
      setMessage({ text: "Device ID and Username are required.", type: 'error' });
      return;
    }
    setMobileLoading(true);
    setMessage(null);
    try {
      const res = await fetch('/api/v1/auth-factory/mobile/extract-session', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          platform: mobilePlatform,
          device_id: deviceId,
          username: mobileUsername,
          // agent_id is optional — when supplied the account is bound immediately,
          // otherwise it is stored floating ('unbound') for later binding.
          agent_id: agentId || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.message || 'Extraction failed');
      setMessage({ text: data.message, type: 'success' });
    } catch (err: any) {
      setMessage({ text: err.message, type: 'error' });
    } finally {
      setMobileLoading(false);
    }
  };

  return (
    <div className="p-6 text-white max-w-4xl mx-auto">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-indigo-500 mb-2">
          Auth Factory
        </h1>
        <p className="text-gray-400 text-sm">Interactive platform onboarding and hardware containment wizard.</p>
      </div>

      <div className="bg-gray-900 rounded-xl border border-gray-800 p-6 shadow-2xl">
        <div className="flex space-x-4 border-b border-gray-800 mb-6 pb-2">
          <button
            onClick={() => { setActiveTab('telegram'); setMessage(null); }}
            className={`px-4 py-2 font-medium rounded-t-lg transition-colors ${activeTab === 'telegram' ? 'bg-blue-600/20 text-blue-400 border-b-2 border-blue-500' : 'text-gray-400 hover:text-gray-200'}`}
          >
            Telegram (Pyrogram)
          </button>
          <button
            onClick={() => { setActiveTab('mobile'); setMessage(null); }}
            className={`px-4 py-2 font-medium rounded-t-lg transition-colors ${activeTab === 'mobile' ? 'bg-indigo-600/20 text-indigo-400 border-b-2 border-indigo-500' : 'text-gray-400 hover:text-gray-200'}`}
          >
            Mobile Session Import
          </button>
        </div>

        {message && (
          <div className={`mb-6 p-4 rounded-lg flex items-center ${message.type === 'success' ? 'bg-green-900/30 text-green-400 border border-green-800' : 'bg-red-900/30 text-red-400 border border-red-800'}`}>
            <span>{message.text}</span>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
          <div>
            <label className="block text-xs text-gray-500 uppercase font-semibold mb-2">Target Agent ID</label>
            <input
              type="text"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-blue-500 focus:outline-none transition-all"
              placeholder="e.g. agent_alpha_01"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 uppercase font-semibold mb-2">Hardware Binding (Device ID)</label>
            <input
              type="text"
              value={deviceId}
              onChange={(e) => setDeviceId(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-blue-500 focus:outline-none transition-all"
              placeholder="e.g. emulator-5554"
            />
          </div>
        </div>

        {activeTab === 'telegram' && (
          <div className="space-y-6 animate-fade-in">
            {/* Step 1: Request */}
            <div className={`p-4 rounded-lg border ${tgStep >= 1 ? 'border-gray-700 bg-gray-800/50' : 'border-gray-800 opacity-50'}`}>
              <h3 className="text-lg font-medium mb-4 flex items-center">
                <span className={`flex items-center justify-center w-6 h-6 rounded-full text-xs mr-3 ${tgStep > 1 ? 'bg-green-500 text-white' : 'bg-blue-500 text-white'}`}>1</span>
                Request OTP
              </h3>
              <div className="flex space-x-4">
                <input
                  type="text"
                  value={phoneNumber}
                  onChange={(e) => setPhoneNumber(e.target.value)}
                  disabled={tgStep > 1}
                  className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                  placeholder="+1234567890"
                />
                <button
                  onClick={handleRequestCode}
                  disabled={tgLoading || tgStep > 1 || !phoneNumber}
                  className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2 rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center"
                >
                  {tgLoading && tgStep === 1 ? 'Requesting...' : 'Send Code'}
                </button>
              </div>
            </div>

            {/* Step 2: Verify */}
            <div className={`p-4 rounded-lg border ${tgStep >= 2 ? 'border-gray-700 bg-gray-800/50' : 'border-gray-800 opacity-50'}`}>
              <h3 className="text-lg font-medium mb-4 flex items-center">
                <span className={`flex items-center justify-center w-6 h-6 rounded-full text-xs mr-3 ${tgStep > 2 ? 'bg-green-500 text-white' : (tgStep === 2 ? 'bg-blue-500 text-white' : 'bg-gray-700 text-gray-400')}`}>2</span>
                Verify Code
              </h3>
              <div className="grid grid-cols-2 gap-4 mb-4">
                <input
                  type="text"
                  value={otpCode}
                  onChange={(e) => setOtpCode(e.target.value)}
                  disabled={tgStep !== 2}
                  className="bg-gray-900 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                  placeholder="OTP Code"
                />
                <input
                  type="password"
                  value={twoFaPassword}
                  onChange={(e) => setTwoFaPassword(e.target.value)}
                  disabled={tgStep !== 2}
                  className="bg-gray-900 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                  placeholder="2FA Password (Optional)"
                />
              </div>
              <button
                onClick={handleVerifyCode}
                disabled={tgLoading || tgStep !== 2 || !otpCode}
                className="w-full bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-2 rounded-lg font-medium transition-colors disabled:opacity-50"
              >
                {tgLoading && tgStep === 2 ? 'Verifying...' : 'Authenticate & Save'}
              </button>
            </div>
            
            {tgStep === 3 && (
              <div className="p-4 bg-green-900/20 border border-green-800 rounded-lg text-center">
                <h3 className="text-green-400 font-bold text-lg mb-1">Authentication Complete</h3>
                <p className="text-gray-400 text-sm">Session string securely exported and saved to database.</p>
                <button 
                  onClick={() => { setTgStep(1); setPhoneNumber(''); setOtpCode(''); setTwoFaPassword(''); setTransactionId(''); }}
                  className="mt-4 text-sm text-blue-400 hover:text-blue-300"
                >
                  Authenticate another account
                </button>
              </div>
            )}
          </div>
        )}

        {activeTab === 'mobile' && (
          <div className="space-y-6 animate-fade-in">
            <div className="grid grid-cols-2 gap-6">
               <div>
                 <label className="block text-xs text-gray-500 uppercase font-semibold mb-2">Platform</label>
                 <select
                   value={mobilePlatform}
                   onChange={(e) => setMobilePlatform(e.target.value)}
                   className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-indigo-500 text-white"
                 >
                   <option value="instagram">Instagram</option>
                   <option value="twitter">X (Twitter)</option>
                   <option value="threads">Threads</option>
                   <option value="youtube">YouTube</option>
                 </select>
               </div>
               <div>
                 <label className="block text-xs text-gray-500 uppercase font-semibold mb-2">Username</label>
                 <input
                   type="text"
                   value={mobileUsername}
                   onChange={(e) => setMobileUsername(e.target.value)}
                   className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-indigo-500"
                   placeholder="@username"
                 />
               </div>
            </div>
            
            <div className="p-4 rounded-lg border border-indigo-900/50 bg-indigo-950/20">
              <h3 className="text-indigo-300 font-medium mb-1">Autonomous Session Extraction</h3>
              <p className="text-gray-400 text-sm mb-1">
                MYRMIDON drives the emulator (<span className="font-mono text-gray-300">{deviceId || 'device'}</span>) and
                dumps the live session directly — WebView cookies &amp; localStorage for hybrid apps, or rooted
                <span className="font-mono"> shared_prefs</span> XML for native apps.
              </p>
              <p className="text-gray-500 text-xs">
                Make sure the {mobilePlatform} app is logged in and foregrounded on the device. Leave Agent ID blank to
                store the account floating (unbound); fill it to bind immediately.
              </p>
            </div>

            <button
              onClick={handleAutoExtract}
              disabled={mobileLoading}
              className="w-full bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-3 rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {mobileLoading ? 'Extracting from emulator…' : '⚡ Auto-Extract Session from Emulator'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
