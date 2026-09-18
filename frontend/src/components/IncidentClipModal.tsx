import React, { useState } from 'react';
import { EmergencyAlert } from '../types/traffic';
import { X, Siren, Video, MapPin, Activity, ShieldAlert, CheckCircle, RefreshCw } from 'lucide-react';

interface IncidentClipModalProps {
  alert: EmergencyAlert | null;
  onClose: () => void;
  onDispatchAmbulance: (alertId: string) => void;
  onAcknowledge?: (alertId: string) => void;
}

export const IncidentClipModal: React.FC<IncidentClipModalProps> = ({
  alert,
  onClose,
  onDispatchAmbulance,
  onAcknowledge,
}) => {
  if (!alert) return null;

  const [streamSrc, setStreamSrc] = useState<string>(`/api/stream/${alert.camera_id}`);
  const [imgError, setImgError] = useState(false);

  const handleImageError = () => {
    if (streamSrc.startsWith('/')) {
      setStreamSrc(`http://localhost:8000/api/stream/${alert.camera_id}`);
    } else {
      setImgError(true);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 bg-black/85 backdrop-blur-md animate-in fade-in duration-150">
      <div className="relative w-full max-w-4xl rounded-2xl bg-[#0F172A] border-2 border-red-600 shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-red-950/80 border-b border-red-800">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-red-600 text-white shadow-lg shadow-red-600/50">
              <Siren className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <h3 className="text-base font-extrabold text-white flex items-center gap-2">
                <span>INCIDENT EVIDENCE CLIP & TELEMETRY</span>
                <span className="text-xs font-mono px-2 py-0.5 rounded bg-red-800 text-red-100">
                  {alert.alert_id}
                </span>
              </h3>
              <p className="text-xs text-red-200 font-mono">
                {alert.camera_id} • {alert.location_name}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg bg-black/40 hover:bg-black/70 text-gray-300 hover:text-white"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Video Canvas */}
        <div className="relative w-full aspect-video bg-black flex items-center justify-center overflow-hidden">
          {!imgError ? (
            <img
              src={streamSrc}
              alt="Incident Feed"
              className="w-full h-full object-contain"
              onError={handleImageError}
            />
          ) : (
            <div className="flex flex-col items-center justify-center text-gray-400 space-y-2">
              <p className="text-sm font-semibold text-red-400">Live Incident Stream Offline</p>
              <button
                onClick={() => {
                  setImgError(false);
                  setStreamSrc(`/api/stream/${alert.camera_id}?t=${Date.now()}`);
                }}
                className="flex items-center gap-1.5 px-3 py-1 rounded bg-gray-800 hover:bg-gray-700 text-xs text-white"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Retry Feed</span>
              </button>
            </div>
          )}

          {/* Incident Overlay Badge */}
          <div className="absolute top-4 left-4 p-2.5 rounded-lg bg-red-950/90 border border-red-600 backdrop-blur-md text-xs font-mono text-red-200 shadow-xl space-y-1">
            <div className="flex items-center gap-2 text-white font-bold">
              <ShieldAlert className="w-4 h-4 text-red-400" />
              <span>CONFIRMED CRASH EVENT</span>
            </div>
            <div>Track ID: #{alert.track_id}</div>
            <div>Direction: {alert.direction_zone}</div>
            <div className="text-[11px] text-red-300">
              Signals: {alert.detected_signals.join(', ')}
            </div>
          </div>
        </div>

        {/* Telemetry & Action Footer */}
        <div className="p-6 bg-[#0B1120] border-t border-gray-800 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="space-y-1 text-xs text-gray-400">
            <div className="flex items-center gap-2 text-gray-200 font-mono">
              <MapPin className="w-4 h-4 text-red-400" />
              <span>GPS Locus: [{alert.gps.join(', ')}]</span>
              <span>•</span>
              <span>Fusion Confidence: {alert.fusion_score}/4 Cues</span>
            </div>
            <p className="text-[11px] text-gray-500">
              Live MJPEG recording buffer secured in RoadGuardian forensic archive.
            </p>
          </div>

          <div className="flex items-center gap-3">
            {alert.ambulance_dispatched ? (
              <div className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-950 border border-emerald-600 text-emerald-300 text-xs font-bold">
                <CheckCircle className="w-4 h-4 text-emerald-400" />
                <span>Ambulance Dispatched</span>
              </div>
            ) : (
              <button
                onClick={() => onDispatchAmbulance(alert.alert_id)}
                className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-red-600 hover:bg-red-500 text-white text-xs font-extrabold shadow-lg shadow-red-700/50 uppercase tracking-wide transition-all"
              >
                <Siren className="w-4 h-4" />
                <span>Dispatch Ambulance</span>
              </button>
            )}

            {!alert.acknowledged && onAcknowledge && (
              <button
                onClick={() => {
                  onAcknowledge(alert.alert_id);
                  onClose();
                }}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-bold shadow transition-all active:scale-95"
              >
                <CheckCircle className="w-4 h-4 text-emerald-200" />
                <span>Acknowledge Incident</span>
              </button>
            )}

            <button
              onClick={onClose}
              className="px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs font-semibold"
            >
              Close Viewer
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
