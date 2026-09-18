import React from 'react';
import { Flame } from 'lucide-react';
interface DensityHeatmapProps {
  density: number;
  vehicleCount: number;
  los?: string;
  cameraId?: string;
}

export const DensityHeatmap: React.FC<DensityHeatmapProps> = ({
  density,
  vehicleCount,
}) => {
  const capacityPercent = Math.min(100, Math.round(((density || 0) / 32.0) * 100));

  const zone1 = Math.min(100, Math.round(capacityPercent * 0.75 + (vehicleCount % 3) * 4));
  const zone2 = Math.min(100, Math.round(capacityPercent * 0.95 + (vehicleCount % 4) * 3));
  const zone3 = Math.min(100, Math.round(capacityPercent * 1.10 + (vehicleCount % 2) * 5));
  const zone4 = Math.min(100, Math.round(capacityPercent * 0.85));

  const zones = [
    { label: 'Approach', val: zone1 },
    { label: 'Mid-Lane', val: zone2 },
    { label: 'Queue', val: zone3 },
    { label: 'Outflow', val: zone4 },
  ];

  const getHeatColor = (val: number) => {
    if (val >= 75) return 'bg-rose-500 text-rose-200 border-rose-600 shadow-[0_0_8px_rgba(244,63,94,0.5)]';
    if (val >= 40) return 'bg-amber-500 text-amber-100 border-amber-600 shadow-[0_0_8px_rgba(245,158,11,0.4)]';
    return 'bg-emerald-500 text-emerald-100 border-emerald-600 shadow-[0_0_8px_rgba(16,185,129,0.3)]';
  };

  const getStatusText = () => {
    if (capacityPercent >= 75) return { text: 'HIGH CONGESTION', color: 'text-rose-400' };
    if (capacityPercent >= 40) return { text: 'MODERATE FLOW', color: 'text-amber-400' };
    return { text: 'OPTIMAL FLOW', color: 'text-emerald-400' };
  };

  const status = getStatusText();

  return (
    <div className="p-2.5 rounded-lg bg-gray-950/90 border border-gray-800 space-y-2">
      <div className="flex items-center justify-between text-[11px] font-mono">
        <div className="flex items-center gap-1.5 text-gray-300">
          <Flame className="w-3.5 h-3.5 text-amber-400" />
          <span className="font-semibold text-gray-200">Live Density Heatmap</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`text-[10px] font-bold ${status.color}`}>
            {status.text}
          </span>
          <span className="text-gray-400">({capacityPercent}% cap)</span>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-1.5">
        {zones.map((z, idx) => {
          const colorClass = getHeatColor(z.val);
          return (
            <div
              key={idx}
              className="flex flex-col items-center justify-center p-1.5 rounded bg-gray-900/80 border border-gray-800/80 relative overflow-hidden"
              title={`${z.label}: ${z.val}% occupancy`}
            >
              <div
                className={`w-full h-1.5 rounded-full transition-all duration-500 mb-1 ${colorClass}`}
              />
              <span className="text-[9px] font-mono text-gray-400 truncate w-full text-center">
                {z.label}
              </span>
              <span className="text-[10px] font-mono font-bold text-gray-200">
                {z.val}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
export default DensityHeatmap;
