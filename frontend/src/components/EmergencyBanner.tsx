import React, { useState } from 'react';
import { AlertOctagon, Siren, CheckCircle2, Video, MapPin, Activity, Radio, ExternalLink } from 'lucide-react';
import { EmergencyAlert } from '../types/traffic';
import { acknowledgeAlert, dispatchAmbulance } from '../services/api';

interface EmergencyBannerProps {
  alerts: EmergencyAlert[];
  onViewClip: (alert: EmergencyAlert) => void;
  onRefresh: () => void;
  onAcknowledge?: (alertId: string) => void;
}

export const EmergencyBanner: React.FC<EmergencyBannerProps> = ({ alerts, onViewClip, onRefresh, onAcknowledge }) => {
  const [loadingMap, setLoadingMap] = useState<Record<string, boolean>>({});

  // Filter for unacknowledged active confirmed incidents
  const activeAlerts = alerts.filter(
    (a) => a.event_type === 'CONFIRMED_INCIDENT' && !a.acknowledged
  );

  if (activeAlerts.length === 0) {
    return null;
  }

  const handleAcknowledge = async (alertId: string) => {
    try {
      setLoadingMap((prev) => ({ ...prev, [alertId]: true }));
      await acknowledgeAlert(alertId);
      if (onAcknowledge) {
        onAcknowledge(alertId);
      }
      onRefresh();
    } catch (e) {
      console.error('Failed to acknowledge alert', e);
    } finally {
      setLoadingMap((prev) => ({ ...prev, [alertId]: false }));
    }
  };

  const handleDispatch = async (alertId: string) => {
    try {
      setLoadingMap((prev) => ({ ...prev, [alertId]: true }));
      await dispatchAmbulance(alertId);
      onRefresh();
    } catch (e) {
      console.error('Failed to dispatch ambulance', e);
    } finally {
      setLoadingMap((prev) => ({ ...prev, [alertId]: false }));
    }
  };

  return (
    <div className="mx-6 mt-4 space-y-3">
      {activeAlerts.map((alert) => (
        <div
          key={alert.alert_id}
          className="relative overflow-hidden rounded-xl bg-gradient-to-r from-red-950/90 via-red-900/80 to-red-950/90 border-2 border-red-600/90 p-4 shadow-2xl shadow-red-950/70 animate-siren"
        >
          {/* Animated Background Scan Line */}
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-red-500/10 via-transparent to-transparent pointer-events-none" />

          <div className="relative z-10 flex flex-col xl:flex-row xl:items-center xl:justify-between gap-4">
            {/* Alert Header & Message */}
            <div className="flex items-start gap-3.5">
              <div className="p-3 bg-red-600 rounded-xl text-white shadow-lg shadow-red-600/50 animate-bounce">
                <Siren className="w-7 h-7" />
              </div>
              <div className="space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="px-2.5 py-0.5 rounded-full bg-red-700 text-white text-xs font-black uppercase tracking-wider shadow">
                    🚨 EMERGENCY INCIDENT CONFIRMED
                  </span>
                  <span className="text-xs font-mono text-red-200 bg-red-900/60 px-2 py-0.5 rounded border border-red-700/50">
                    ID: {alert.alert_id}
                  </span>
                  <span className="text-xs font-mono text-red-200 bg-red-900/60 px-2 py-0.5 rounded border border-red-700/50 flex items-center gap-1">
                    <Activity className="w-3 h-3 text-red-300" />
                    FUSION SCORE: {alert.fusion_score}/4
                  </span>
                </div>

                <h2 className="text-lg md:text-xl font-black tracking-tight text-white flex items-center gap-2">
                  <span>CRASH DETECTED AT {alert.camera_id}</span>
                  <span className="text-red-300 font-normal">({alert.location_name})</span>
                  <span className="text-amber-300 font-extrabold uppercase animate-pulse">
                    — AMBULANCE REQUIRED
                  </span>
                </h2>

                {/* Evidence Cues & Coordinates */}
                <div className="flex items-center gap-3 text-xs text-red-200 flex-wrap pt-0.5">
                  <span className="flex items-center gap-1 font-mono">
                    <MapPin className="w-3.5 h-3.5 text-red-400" />
                    GPS: [{alert.gps.join(', ')}]
                  </span>
                  <span>•</span>
                  <span className="font-semibold text-white">Detected Signals:</span>
                  <div className="flex gap-1.5 flex-wrap">
                    {alert.detected_signals.map((sig, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 rounded bg-black/40 border border-red-600/60 text-red-200 font-mono text-[11px]"
                      >
                        {sig}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center gap-2.5 flex-wrap self-end xl:self-center">
              {/* Dispatch Ambulance Button */}
              {alert.ambulance_dispatched ? (
                <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-950/80 border border-emerald-600 text-emerald-300 font-bold text-xs shadow-md">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span>Ambulance Dispatched</span>
                </div>
              ) : (
                <button
                  onClick={() => handleDispatch(alert.alert_id)}
                  disabled={loadingMap[alert.alert_id]}
                  className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 text-white font-extrabold text-xs shadow-xl shadow-red-700/60 transition-all transform active:scale-95 uppercase tracking-wide border border-red-400/40 disabled:opacity-50"
                >
                  <Siren className="w-4 h-4 animate-spin" />
                  <span>[Dispatch Ambulance]</span>
                </button>
              )}

              {/* Acknowledge Alert Button */}
              {!alert.acknowledged && (
                <button
                  onClick={() => handleAcknowledge(alert.alert_id)}
                  disabled={loadingMap[alert.alert_id]}
                  className="flex items-center gap-1.5 px-3.5 py-2.5 rounded-lg bg-gray-900/90 hover:bg-gray-800 border border-gray-700 text-gray-200 font-bold text-xs shadow transition-all active:scale-95 disabled:opacity-50"
                >
                  <CheckCircle2 className="w-4 h-4 text-gray-400" />
                  <span>[Acknowledge Alert]</span>
                </button>
              )}

              {/* View Incident Clip Button */}
              <button
                onClick={() => onViewClip(alert)}
                className="flex items-center gap-1.5 px-3.5 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs shadow-md shadow-blue-900/40 transition-all active:scale-95"
              >
                <Video className="w-4 h-4" />
                <span>[View Incident Clip]</span>
              </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};
