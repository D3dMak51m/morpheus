import { useState } from 'react';

interface ProfileResponse {
  id: number;
  agent_id: string;
  codename: string;
  caste: string;
  full_name: string;
  profession?: string;
  core_mission?: string;
}

export function SoulGenesisView() {
  const [caste, setCaste] = useState<'alpha' | 'beta' | 'gamma'>('alpha');
  const [agentId, setAgentId] = useState('');
  const [codename, setCodename] = useState('');
  const [focus, setFocus] = useState('');
  const [platforms, setPlatforms] = useState<string[]>(['telegram']);
  
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ProfileResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const togglePlatform = (p: string) => {
    setPlatforms(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]);
  };

  const handleSynthesize = async () => {
    if (!agentId || !codename || !focus) {
      setError("Agent ID, Codename, and Focus are required.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const res = await fetch('/api/v1/souls/genesis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          caste,
          agent_id: agentId,
          codename,
          focus,
          platforms
        })
      });
      
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Synthesis failed');
      
      setResult(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 text-white max-w-5xl mx-auto">
      <div className="mb-8 flex justify-between items-end">
        <div>
          <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-500 mb-2">
            Soul Genesis Engine
          </h1>
          <p className="text-gray-400 text-sm">Dynamic persona synthesis powered by Qwen2.5 Local LLM.</p>
        </div>
        {loading && (
          <div className="flex items-center text-purple-400 font-mono text-sm animate-pulse">
            <span className="mr-2">Computing Vector Math...</span>
            <div className="h-4 w-4 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Input Panel */}
        <div className="lg:col-span-2 bg-gray-900 rounded-xl border border-gray-800 p-6 shadow-2xl relative overflow-hidden">
          {loading && (
             <div className="absolute inset-0 bg-gray-900/80 backdrop-blur-sm z-10 flex flex-col items-center justify-center font-mono">
               <div className="text-pink-500 text-5xl mb-4 animate-bounce">⚡</div>
               <div className="text-purple-400 text-xl font-bold tracking-widest">SYNTHESIZING CONSCIOUSNESS</div>
               <div className="text-gray-500 text-xs mt-2">Connecting to Ollama (qwen2.5:3b)...</div>
             </div>
          )}
          
          <div className="space-y-6">
            <div>
              <label className="block text-xs text-gray-500 uppercase font-semibold mb-3">Caste Assignment</label>
              <div className="flex space-x-4">
                {(['alpha', 'beta', 'gamma'] as const).map(c => (
                  <button
                    key={c}
                    onClick={() => setCaste(c)}
                    className={`flex-1 py-3 rounded-lg border-2 font-bold uppercase tracking-wider transition-all ${caste === c ? 'border-purple-500 bg-purple-500/20 text-purple-300' : 'border-gray-700 bg-gray-800 text-gray-500 hover:border-gray-500'}`}
                  >
                    {c}
                  </button>
                ))}
              </div>
              <p className="text-xs text-gray-500 mt-2">
                {caste === 'alpha' ? 'Deep synthesis: Generates full biographical and psychological profiles.' : 'Fast-path template: Optimized behavioral rules for amplification swarms.'}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-gray-500 uppercase font-semibold mb-2">Agent ID (System)</label>
                <input
                  type="text"
                  value={agentId}
                  onChange={(e) => setAgentId(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-purple-500"
                  placeholder="agent_omega_9"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 uppercase font-semibold mb-2">Codename (Internal)</label>
                <input
                  type="text"
                  value={codename}
                  onChange={(e) => setCodename(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-purple-500"
                  placeholder="Omega"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-500 uppercase font-semibold mb-2">Vector Focus / Vibe</label>
              <textarea
                value={focus}
                onChange={(e) => setFocus(e.target.value)}
                className="w-full h-24 bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 focus:ring-2 focus:ring-purple-500 text-sm"
                placeholder="e.g., Urban planner from Samarkand, passionate about historical preservation and critical of modern glass architecture. Academic but sarcastic tone."
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 uppercase font-semibold mb-2">Operating Platforms</label>
              <div className="flex flex-wrap gap-2">
                {['telegram', 'instagram', 'twitter', 'threads', 'youtube'].map(p => (
                  <button
                    key={p}
                    onClick={() => togglePlatform(p)}
                    className={`px-3 py-1 rounded-full text-xs font-medium border ${platforms.includes(p) ? 'bg-purple-600 border-purple-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-400 hover:bg-gray-700'}`}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>

            {error && (
              <div className="p-3 bg-red-900/30 border border-red-800 text-red-400 rounded-lg text-sm">
                {error}
              </div>
            )}

            <button
              onClick={handleSynthesize}
              disabled={loading}
              className="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white font-bold py-4 rounded-xl shadow-lg transition-all transform hover:scale-[1.02] active:scale-95"
            >
              INITIALIZE GENESIS SEQUENCE
            </button>
          </div>
        </div>

        {/* Output Panel */}
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-6 shadow-2xl flex flex-col">
          <h3 className="text-xs text-gray-500 uppercase font-semibold mb-4 border-b border-gray-800 pb-2">Synthesis Result</h3>
          
          {result ? (
            <div className="space-y-4 font-mono text-sm">
              <div className="text-center mb-6">
                <div className="w-20 h-20 mx-auto rounded-full bg-purple-900 border-4 border-purple-500 flex items-center justify-center text-3xl mb-2">
                  🤖
                </div>
                <div className="text-white font-bold text-lg">{result.full_name}</div>
                <div className="text-purple-400 text-xs">[{result.caste.toUpperCase()}]</div>
              </div>
              
              <div className="bg-gray-800 p-3 rounded border border-gray-700">
                <span className="text-gray-500">ID:</span> <span className="text-blue-400">{result.agent_id}</span>
              </div>
              <div className="bg-gray-800 p-3 rounded border border-gray-700">
                <span className="text-gray-500">Profession:</span> <span className="text-green-400">{result.profession || 'N/A'}</span>
              </div>
              <div className="bg-gray-800 p-3 rounded border border-gray-700 overflow-y-auto max-h-48">
                <span className="text-gray-500 block mb-1">Core Mission:</span>
                <span className="text-gray-300 text-xs leading-relaxed">{result.core_mission || 'N/A'}</span>
              </div>
              
              <div className="mt-auto pt-4 text-center text-xs text-gray-500">
                Saved to Database (ID: {result.id})
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-gray-600 border-2 border-dashed border-gray-800 rounded-lg">
              <div className="text-4xl mb-2">🧬</div>
              <p className="text-sm font-medium">Awaiting Vector Seed</p>
            </div>
          )}
        </div>
        
      </div>
    </div>
  );
}
