import React, { useState, useEffect } from 'react';
import { Shield, Radio, AlertTriangle, Video, Car, Plus, Flame, Clock } from 'lucide-react';
import { MetricsSummary } from '../types/traffic';

interface HeaderProps {
  summary: MetricsSummary | null;
  wsStatus: 'CONNECTED' | 'RECONNECTING' | 'OFFLINE';
  onAddCameraClick: () => void;
  onSimulateCrashClick: () => void;
  onNeedsReviewClick?: () => void;
  highCongestionZone: string;
}

export const Header: React.FC<HeaderProps> = ({
  summary,
  wsStatus,
  onAddCameraClick,
  onSimulateCrashClick,
  onNeedsReviewClick,
  highCongestionZone,
}) => {
  const [timeStr, setTimeStr] = useState('');

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(now.toLocaleTimeString('en-US', { hour12: false }) + ' UTC');
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <header className="border-b border-gray-800 bg-[#0E1424] px-6 py-3.5 sticky top-0 z-40 shadow-xl backdrop-blur-md">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        {/* Logo & System Brand */}
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-blue-600/20 border border-blue-500/40 text-blue-400 shadow-lg shadow-blue-900/30">
            <Shield className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-extrabold tracking-tight text-white flex items-center gap-2">
                Road<span className="text-blue-400">Guardian</span>
                <span className="text-xs font-semibold px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30">
                  ITS CONTROL ROOM
                </span>
              </h1>
            </div>
            <p className="text-xs text-gray-400 font-mono flex items-center gap-2">
              <span>HIGHWAY AI INTELLIGENCE</span>
              <span>•</span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3 text-gray-400" />
                {timeStr}
              </span>
            </p>
          </div>
        </div>

        {/* High Congestion Zone & Global Status Banner */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2.5 px-3.5 py-1.5 rounded-lg bg-orange-950/40 border border-orange-700/50 shadow-inner">
            <Flame className="w-4 h-4 text-orange-400 animate-pulse" />
            <div className="text-xs font-medium">
              <span className="text-gray-400">High Congestion Zone: </span>
              <span className="font-bold text-orange-300 font-mono tracking-wide">
                {highCongestionZone || summary?.high_congestion_zone?.camera_id || 'ANALYZING...'}
              </span>
            </div>
          </div>

          {/* WebSocket Status Indicator */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-900/70 border border-gray-800 text-xs font-mono">
            <span
              className={`w-2 h-2 rounded-full ${
                wsStatus === 'CONNECTED'
                  ? 'bg-emerald-400 animate-pulse'
                  : wsStatus === 'RECONNECTING'
                  ? 'bg-amber-400 animate-ping'
                  : 'bg-rose-500'
              }`}
            />
            <span
              className={
                wsStatus === 'CONNECTED'
                  ? 'text-emerald-300'
                  : wsStatus === 'RECONNECTING'
                  ? 'text-amber-300'
                  : 'text-rose-400'
              }
            >
              {wsStatus}
            </span>
          </div>
        </div>

        {/* Metric Counters & Control Buttons */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Quick Metrics */}
          <div className="hidden xl:flex items-center gap-3 text-xs">
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-gray-900 rounded-md border border-gray-800">
              <Video className="w-3.5 h-3.5 text-blue-400" />
              <span className="text-gray-400">Cameras:</span>
              <span className="font-bold text-white font-mono">{summary?.active_cameras_count ?? 4}</span>
            </div>
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-gray-900 rounded-md border border-gray-800">
              <Car className="w-3.5 h-3.5 text-emerald-400" />
              <span className="text-gray-400">Vehicles Today:</span>
              <span className="font-bold text-white font-mono">{summary?.total_vehicles_monitored ?? 248}</span>
            </div>
            {summary && summary.active_emergency_alerts > 0 && (
              <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-rose-950/60 rounded-md border border-rose-800 text-rose-300 animate-pulse">
                <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
                <span className="font-bold font-mono">{summary.active_emergency_alerts} Active Crash</span>
              </div>
            )}
          </div>

          {/* Action Buttons */}
          {onNeedsReviewClick && (
            <button
              onClick={onNeedsReviewClick}
              className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-amber-950/50 hover:bg-amber-900/70 border border-amber-700/60 text-amber-300 transition-all duration-150 shadow-sm"
              title="Open Confidence-Gated Human Review Queue"
            >
              <Shield className="w-3.5 h-3.5 text-amber-400" />
              <span>Needs Review</span>
            </button>
          )}

          <button
            onClick={onSimulateCrashClick}
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-rose-900/40 hover:bg-rose-900/70 border border-rose-700/60 text-rose-300 transition-all duration-150 shadow-sm"
            title="Simulate Confirmed Crash Incident for Test"
          >
            <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
            <span>Simulate Crash</span>
          </button>

          <button
            onClick={onAddCameraClick}
            className="flex items-center gap-1.5 text-xs font-semibold px-3.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white shadow-md shadow-blue-600/30 transition-all duration-150"
          >
            <Plus className="w-4 h-4" />
            <span>Add Camera</span>
          </button>
        </div>
      </div>
    </header>
  );
};
