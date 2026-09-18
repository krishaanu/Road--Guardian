import React from 'react';
import { Camera } from '../types/traffic';
import { Radio, Gauge, Cpu, Timer, ArrowRight, ShieldCheck, Zap, Sparkles } from 'lucide-react';

interface SignalWidgetProps {
  cameras: Camera[];
  selectedCamera: Camera | null;
  onSelectCamera: (camera: Camera) => void;
}

export const SignalWidget: React.FC<SignalWidgetProps> = ({ cameras, selectedCamera, onSelectCamera }) => {
  const activeCam = selectedCamera || (cameras.length > 0 ? cameras[0] : null);

  if (!activeCam) return null;

  const { signal, density, los, id, location_name } = activeCam;

  // Visual traffic light colors
  const isRed = signal.current_light === 'RED';
  const isYellow = signal.current_light === 'YELLOW';
  const isGreen = signal.current_light === 'GREEN';

  // LOS styling
  const getLosColor = (l: string) => {
    if (l.includes('E') || l.includes('F')) return 'text-rose-400 border-rose-600/70 bg-rose-950/40';
    if (l.includes('C') || l.includes('D')) return 'text-amber-400 border-amber-600/70 bg-amber-950/40';
    return 'text-emerald-400 border-emerald-600/70 bg-emerald-950/40';
  };

  // Rule explanation based on density
  const getAiPolicyDetails = (d: number, l: string) => {
    if (l.includes('E') || l.includes('F')) {
      return {
        title: 'High Density Protocol Active (LOS E/F)',
        description: 'Extend Green Light (+20s to +40s) to prevent gridlock and flush highway queues.',
        action: 'Extend Green Duration to 45s-60s',
        badgeColor: 'bg-rose-500/20 text-rose-300 border-rose-500/30',
      };
    }
    if (l.includes('C') || l.includes('D')) {
      return {
        title: 'Moderate Flow Protocol (LOS C/D)',
        description: 'Balanced Cycle (15s–25s) maintained for synchronized arterial flow.',
        action: 'Balanced Cycle Time 20s-30s',
        badgeColor: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
      };
    }
    return {
      title: 'Free Flow Protocol (LOS A/B)',
      description: 'Minimize Green Time (5s–10s) to prioritize higher-density converging corridors.',
      action: 'Minimize Green Time to 8s-12s',
      badgeColor: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
    };
  };

  const policy = getAiPolicyDetails(density, los);

  return (
    <div className="rounded-xl bg-[#111827] border border-gray-800 shadow-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-[#0D1424] border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Cpu className="w-5 h-5 text-blue-400" />
          <h3 className="text-sm font-bold text-white tracking-tight flex items-center gap-2">
            <span>Adaptive Traffic Signal Controller</span>
            <span className="flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded bg-blue-900/50 text-blue-300 border border-blue-700/50">
              <Sparkles className="w-3 h-3 text-blue-300" />
              AI PREDICTION
            </span>
          </h3>
        </div>

        {/* Camera Selector Dropdown */}
        <select
          value={activeCam.id}
          onChange={(e) => {
            const c = cameras.find((cam) => cam.id === e.target.value);
            if (c) onSelectCamera(c);
          }}
          className="px-2.5 py-1 rounded bg-gray-900 border border-gray-700 text-xs text-gray-200 font-mono focus:outline-none focus:border-blue-500 cursor-pointer"
        >
          {cameras.map((c) => (
            <option key={c.id} value={c.id}>
              {c.id} - {c.name}
            </option>
          ))}
        </select>
      </div>

      {/* Main Signal Display Body */}
      <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-4 items-center">
        {/* Visual Traffic Light Graphic */}
        <div className="flex items-center justify-center p-4 bg-gray-950/80 rounded-xl border border-gray-800/80 shadow-inner">
          <div className="relative flex flex-col items-center gap-3 p-3 bg-black/90 rounded-2xl border-2 border-gray-800 shadow-2xl">
            {/* Red Light */}
            <div
              className={`w-10 h-10 rounded-full transition-all duration-300 border-2 ${
                isRed
                  ? 'bg-red-500 border-red-300 shadow-[0_0_25px_rgba(239,68,68,0.9)] animate-pulse'
                  : 'bg-red-950/30 border-red-900/30 opacity-40'
              }`}
            />
            {/* Yellow Light */}
            <div
              className={`w-10 h-10 rounded-full transition-all duration-300 border-2 ${
                isYellow
                  ? 'bg-amber-400 border-amber-200 shadow-[0_0_25px_rgba(245,158,11,0.9)] animate-pulse'
                  : 'bg-amber-950/30 border-amber-900/30 opacity-40'
              }`}
            />
            {/* Green Light */}
            <div
              className={`w-10 h-10 rounded-full transition-all duration-300 border-2 ${
                isGreen
                  ? 'bg-emerald-400 border-emerald-200 shadow-[0_0_25px_rgba(16,185,129,0.9)] animate-pulse'
                  : 'bg-emerald-950/30 border-emerald-900/30 opacity-40'
              }`}
            />
          </div>

          {/* Phase Countdown Display */}
          <div className="ml-5 text-center space-y-1">
            <span className="text-[11px] font-mono text-gray-400 uppercase tracking-wider block">Phase Countdown</span>
            <div className="text-4xl font-extrabold font-mono text-white tracking-tighter">
              {signal.countdown}
              <span className="text-sm font-normal text-gray-400 ml-0.5">s</span>
            </div>
            <span
              className={`text-[11px] font-bold uppercase px-2 py-0.5 rounded border inline-block ${
                isGreen
                  ? 'bg-emerald-950 text-emerald-300 border-emerald-700'
                  : isYellow
                  ? 'bg-amber-950 text-amber-300 border-amber-700'
                  : 'bg-red-950 text-red-300 border-red-700'
              }`}
            >
              {signal.current_light}
            </span>
          </div>
        </div>

        {/* Live HCM Density & LOS Metric Card */}
        <div className="p-4 bg-gray-900/60 rounded-xl border border-gray-800 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-400">HCM Traffic Strength</span>
            <span className={`px-2.5 py-0.5 rounded border text-xs font-black font-mono ${getLosColor(los)}`}>
              {los}
            </span>
          </div>

          <div>
            <div className="text-2xl font-black text-white font-mono flex items-baseline gap-1.5">
              <span>{density}</span>
              <span className="text-xs text-gray-400 font-sans font-normal">veh/km/lane</span>
            </div>
            <div className="w-full bg-gray-800 rounded-full h-2 mt-2 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  density >= 22.0 ? 'bg-rose-500' : density >= 11.0 ? 'bg-amber-400' : 'bg-emerald-400'
                }`}
                style={{ width: `${Math.min(100, (density / 35.0) * 100)}%` }}
              />
            </div>
          </div>

          <div className="pt-1 text-[11px] text-gray-400 flex items-center justify-between">
            <span>Corridor: {location_name}</span>
            <span className="font-mono text-gray-300">{activeCam.vehicle_count} active veh</span>
          </div>
        </div>

        {/* AI Signal Prediction & Dynamic Duration */}
        <div className="p-4 bg-gray-900/60 rounded-xl border border-gray-800 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-400 flex items-center gap-1.5">
              <Timer className="w-3.5 h-3.5 text-blue-400" />
              Dynamic Green AI Prediction
            </span>
            <span className={`text-[10px] px-2 py-0.5 rounded border font-mono ${policy.badgeColor}`}>
              {signal.mode}
            </span>
          </div>

          <div className="space-y-1">
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-extrabold text-emerald-400 font-mono">
                +{signal.recommended_green}s
              </span>
              <span className="text-xs text-gray-400">Recommended Green</span>
            </div>
            <p className="text-xs font-bold text-white">{policy.title}</p>
            <p className="text-[11px] text-gray-400 leading-relaxed">{policy.description}</p>
          </div>
        </div>
      </div>
    </div>
  );
};
