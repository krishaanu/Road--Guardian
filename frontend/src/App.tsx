import React, { useState, useEffect, useCallback, useRef } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom';
import { Camera, EmergencyAlert, MetricsSummary, WebSocketMessage } from './types/traffic';
import {
  fetchCameras,
  fetchMetricsSummary,
  simulateIncident,
  acknowledgeAlert,
  dispatchAmbulance,
  addCameraStream,
  removeCameraStream,
  TrafficWebSocket,
} from './services/api';
import { Header } from './components/Header';
import { EmergencyBanner } from './components/EmergencyBanner';
import { CameraGrid } from './components/CameraGrid';
import { SignalControllerView } from './components/SignalControllerView';
import { VehicleModal } from './components/VehicleModal';
import { AddCameraModal } from './components/AddCameraModal';
import { IncidentClipModal } from './components/IncidentClipModal';
import { NeedsReviewModal } from './components/NeedsReviewModal';

// Default mock fallback cameras for local UI preview when backend API is offline
const DEFAULT_FALLBACK_CAMERAS: Camera[] = [
  {
    id: 'CAMERA_01',
    name: 'NH-44 Mahoba Toll Northbound',
    location_name: 'NH-44 Mahoba Toll Northbound',
    source: 'C:/Users/krish/Downloads/demo.mp4',
    source_type: 'video',
    enabled: true,
    status: 'STREAMING',
    vehicle_count: 14,
    density: 18.5,
    los: 'LOS C',
    direction_covered: 'Northbound',
    gps: [25.2914, 79.8713],
    signal: {
      current_light: 'GREEN',
      countdown: 24,
      cycle_duration: 60,
      recommended_green: 35,
      mode: 'AI_ADAPTIVE',
    },
  },
];

const RouteModeBadge: React.FC = () => {
  const location = useLocation();
  const isSignal = location.pathname.includes('signal');
  return (
    <div className="text-xs text-gray-400 font-mono">
      {isSignal ? 'MODE: ADAPTIVE_SIGNAL_CONTROL' : 'MODE: LIVE_SURVEILLANCE'}
    </div>
  );
};

export const AppContent: React.FC = () => {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [alerts, setAlerts] = useState<EmergencyAlert[]>([]);
  const [summary, setSummary] = useState<MetricsSummary | null>(null);
  const [highCongestionZone, setHighCongestionZone] = useState<string>('CAMERA_01');
  const [wsStatus, setWsStatus] = useState<'CONNECTED' | 'RECONNECTING' | 'OFFLINE'>('OFFLINE');

  // Modals state
  const [selectedCameraForModal, setSelectedCameraForModal] = useState<Camera | null>(null);
  const [selectedCameraForSignal, setSelectedCameraForSignal] = useState<Camera | null>(null);
  const [isAddCameraOpen, setIsAddCameraOpen] = useState(false);
  const [isNeedsReviewOpen, setIsNeedsReviewOpen] = useState(false);
  const [clipAlert, setClipAlert] = useState<EmergencyAlert | null>(null);

  // Ref to prevent initial duplicate data loading in React StrictMode
  const isMountedRef = useRef(false);

  // Initial data load with safe fallback
  const loadInitialData = useCallback(async () => {
    try {
      const [cams, sum] = await Promise.all([
        fetchCameras().catch(() => []),
        fetchMetricsSummary().catch(() => null),
      ]);

      const activeCams = Array.isArray(cams) && cams.length > 0 ? cams : DEFAULT_FALLBACK_CAMERAS;

      setCameras(activeCams);
      setSummary(sum);

      if (sum?.high_congestion_zone?.camera_id) {
        setHighCongestionZone(sum.high_congestion_zone.camera_id);
      }
      if (activeCams.length > 0) {
        setSelectedCameraForSignal((prev) => prev || activeCams[0]);
      }
    } catch (e) {
      console.warn('Backend API offline or unreachable. Falling back to demo camera layout.', e);
      setCameras(DEFAULT_FALLBACK_CAMERAS);
      setSelectedCameraForSignal(DEFAULT_FALLBACK_CAMERAS[0]);
    }
  }, []);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  // WebSocket message handler
  const handleWsMessage = useCallback((msg: WebSocketMessage) => {
    if (!msg) return;

    if (msg.type === 'INITIAL_STATE') {
      if (Array.isArray(msg.active_alerts)) setAlerts(msg.active_alerts);
      if (msg.high_congestion_zone) setHighCongestionZone(msg.high_congestion_zone);
    } else if (msg.type === 'TELEMETRY_UPDATE') {
      if (msg.high_congestion_zone) setHighCongestionZone(msg.high_congestion_zone);
      if (Array.isArray(msg.cameras)) {
        setCameras((prev) =>
          prev.map((cam) => {
            const update = msg.cameras?.find((c) => c && c.camera_id === cam.id);
            if (!update) return cam;
            return {
              ...cam,
              vehicle_count: update.vehicle_count ?? cam.vehicle_count,
              density: update.density ?? cam.density,
              los: update.los ?? cam.los,
              signal: update.signal ? { ...cam.signal, ...update.signal } : cam.signal,
            };
          })
        );
      }
    } else if (msg.type === 'EMERGENCY_ALERT') {
      if (msg.alert) {
        const newAlert = msg.alert;
        setAlerts((prev) => [newAlert, ...prev.filter((a) => a.alert_id !== newAlert.alert_id)]);
      }
    } else if (msg.type === 'ALERT_STATUS_UPDATE') {
      if (msg.alert_id) {
        setAlerts((prev) =>
          prev.map((a) => {
            if (a.alert_id !== msg.alert_id) return a;
            return {
              ...a,
              acknowledged: msg.acknowledged !== undefined ? msg.acknowledged : a.acknowledged,
              ambulance_dispatched:
                msg.ambulance_dispatched !== undefined ? msg.ambulance_dispatched : a.ambulance_dispatched,
            };
          })
        );
      }
    }
  }, []);

  // Initialize WebSocket connection lifecycle cleanly
  useEffect(() => {
    const wsManager = new TrafficWebSocket(handleWsMessage, setWsStatus);
    wsManager.connect();

    return () => {
      wsManager.disconnect();
    };
  }, [handleWsMessage]);

  const handleSimulateCrash = async () => {
    try {
      const targetCamId = cameras[0]?.id || 'CAMERA_01';
      const res = await simulateIncident(targetCamId);
      if (res && res.payload) {
        setAlerts((prev) => [res.payload, ...prev.filter((a) => a.alert_id !== res.payload.alert_id)]);
      }
    } catch (e) {
      console.error('Failed to simulate crash incident:', e);
    }
  };

  const handleAcknowledgeAlert = async (alertId: string) => {
    try {
      await acknowledgeAlert(alertId);
      // Remove incident from active review state immediately
      setAlerts((prev) => prev.filter((item) => item.alert_id !== alertId));
      if (clipAlert?.alert_id === alertId) {
        setClipAlert(null);
      }
    } catch (err) {
      console.error('Failed to acknowledge incident:', err);
    }
  };

  const handleDispatchAmbulanceFromClip = async (alertId: string) => {
    try {
      await dispatchAmbulance(alertId);
      setAlerts((prev) =>
        prev.map((a) => (a.alert_id === alertId ? { ...a, ambulance_dispatched: true } : a))
      );
      setClipAlert((prev) => (prev && prev.alert_id === alertId ? { ...prev, ambulance_dispatched: true } : prev));
    } catch (e) {
      console.error('Failed to dispatch emergency response unit:', e);
    }
  };

  const handleQuickAddCamera = async (cameraId: string, source: string) => {
    try {
      await addCameraStream({ camera_id: cameraId, source });
      await loadInitialData();
    } catch (e) {
      console.error('Failed to quick-add camera stream:', e);
      alert('Failed to register camera stream. Ensure the backend engine is active.');
    }
  };

  const handleRemoveCamera = async (cameraId: string) => {
    try {
      await removeCameraStream(cameraId);
      setCameras((prev) => {
        const updated = prev.filter((c) => c.id !== cameraId);
        if (selectedCameraForSignal?.id === cameraId) {
          setSelectedCameraForSignal(updated[0] || null);
        }
        return updated;
      });
    } catch (e) {
      console.error('Failed to remove camera stream:', e);
      alert('Failed to remove camera stream from backend.');
    }
  };

  return (
    <div className="min-h-screen bg-[#0B0F19] flex flex-col">
      {/* Top ITS Control Room Header */}
      <Header
        summary={summary}
        wsStatus={wsStatus}
        onAddCameraClick={() => setIsAddCameraOpen(true)}
        onSimulateCrashClick={handleSimulateCrash}
        onNeedsReviewClick={() => setIsNeedsReviewOpen(true)}
        highCongestionZone={highCongestionZone}
      />

      {/* Mode Switcher Navigation Bar (Route-Driven) */}
      <div className="bg-[#0D1527] border-b border-gray-800 px-6 py-2.5 flex items-center justify-between">
        <div className="flex space-x-3">
          <NavLink
            to="/surveillance"
            className={({ isActive }) =>
              `px-4 py-1.5 rounded-md text-sm font-semibold transition-colors cursor-pointer flex items-center gap-2 ${
                isActive
                  ? 'bg-[#00E676] text-black shadow-lg shadow-[#00E676]/20'
                  : 'bg-[#1C283F] text-gray-300 hover:text-white'
              }`
            }
          >
            <span>📹</span>
            <span>Live Surveillance Grid</span>
          </NavLink>
          <NavLink
            to="/signal-controller"
            className={({ isActive }) =>
              `px-4 py-1.5 rounded-md text-sm font-semibold transition-colors cursor-pointer flex items-center gap-2 ${
                isActive
                  ? 'bg-[#00E676] text-black shadow-lg shadow-[#00E676]/20'
                  : 'bg-[#1C283F] text-gray-300 hover:text-white'
              }`
            }
          >
            <span>🚦</span>
            <span>Adaptive Traffic Signal Controller</span>
          </NavLink>
        </div>

        <RouteModeBadge />
      </div>

      {/* Real-time Emergency Incident Alert Banner */}
      <EmergencyBanner
        alerts={alerts}
        onViewClip={(alert) => setClipAlert(alert)}
        onRefresh={loadInitialData}
        onAcknowledge={handleAcknowledgeAlert}
      />

      {/* Main Workspace with Dedicated Standalone Routes */}
      <main className="flex-1 p-6 space-y-6 max-w-[1920px] mx-auto w-full">
        <Routes>
          {/* Default Route redirects to Surveillance Grid */}
          <Route path="/" element={<Navigate to="/surveillance" replace />} />

          {/* Route 1: Standalone Active Camera Surveillance Grid (Pure camera tiles, no signal widget) */}
          <Route
            path="/surveillance"
            element={
              <CameraGrid
                cameras={cameras}
                onSelectCamera={(cam) => setSelectedCameraForModal(cam)}
                onRemoveCamera={handleRemoveCamera}
                onQuickAddCamera={handleQuickAddCamera}
                onRefresh={loadInitialData}
              />
            }
          />

          {/* Route 2: Standalone Adaptive Signal Controller & Simulator */}
          <Route
            path="/signal-controller"
            element={
              <SignalControllerView
                cameras={cameras}
                selectedCamera={selectedCameraForSignal}
                onSelectCamera={(cam) => setSelectedCameraForSignal(cam)}
              />
            }
          />

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/surveillance" replace />} />
        </Routes>
      </main>

      {/* Modals */}
      <VehicleModal
        camera={selectedCameraForModal}
        onClose={() => setSelectedCameraForModal(null)}
      />

      <NeedsReviewModal
        isOpen={isNeedsReviewOpen}
        onClose={() => setIsNeedsReviewOpen(false)}
      />

      <AddCameraModal
        isOpen={isAddCameraOpen}
        onClose={() => setIsAddCameraOpen(false)}
        onSuccess={loadInitialData}
      />

      <IncidentClipModal
        alert={clipAlert}
        onClose={() => setClipAlert(null)}
        onDispatchAmbulance={handleDispatchAmbulanceFromClip}
        onAcknowledge={handleAcknowledgeAlert}
      />

      {/* Footer */}
      <footer className="border-t border-gray-800/80 bg-[#090D17] py-3.5 px-6 text-center text-xs font-mono text-gray-500">
        RoadGuardian Intelligent Transportation System • Autonomous Multi-Signal Fusion & Traffic Control Engine
      </footer>
    </div>
  );
};

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <AppContent />
    </BrowserRouter>
  );
};

export default App;