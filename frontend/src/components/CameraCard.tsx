import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Camera } from '../types/traffic';
import { Car, ArrowUpRight, Gauge, RefreshCw, AlertTriangle, Trash2, Loader2 } from 'lucide-react';
import { retryCameraStream, fetchCameraStatus } from '../services/api';
import { DensityHeatmap } from './DensityHeatmap';

interface CameraCardProps {
  camera: Camera;
  onSelectCamera: (camera: Camera) => void;
  onInspectSignal?: (camera: Camera) => void;
  onRemoveCamera?: (cameraId: string) => void;
}

export const CameraCard: React.FC<CameraCardProps> = ({
  camera,
  onSelectCamera,
  onRemoveCamera,
}) => {
  const getStreamUrl = useCallback((id: string) => `/api/stream/${id}`, []);

  const [streamSrc, setStreamSrc] = useState<string>(`${getStreamUrl(camera.id)}?t=${Date.now()}`);
  const [streamState, setStreamState] = useState<'CONNECTING' | 'LIVE' | 'ERROR'>('CONNECTING');
  const [diagReason, setDiagReason] = useState<string | null>(null);
  const [isRetrying, setIsRetrying] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  // Active frame detection and bounded 4.5s connection watchdog
  useEffect(() => {
    setStreamState('CONNECTING');
    setDiagReason(null);
    setStreamSrc(`${getStreamUrl(camera.id)}?t=${Date.now()}`);

    let isResolved = false;

    // Fast check: poll naturalWidth every 200ms
    const checkInterval = setInterval(() => {
      if (imgRef.current && imgRef.current.naturalWidth > 0) {
        setStreamState('LIVE');
        setDiagReason(null);
        isResolved = true;
        clearInterval(checkInterval);
      }
    }, 200);

    // Watchdog timer: bounded at 4.5 seconds max
    const watchdogTimer = setTimeout(async () => {
      clearInterval(checkInterval);
      if (isResolved) return;

      if (imgRef.current && imgRef.current.naturalWidth > 0) {
        setStreamState('LIVE');
        setDiagReason(null);
        return;
      }

      // Query camera status to get actual backend diagnostics
      try {
        const status = await fetchCameraStatus(camera.id);
        if (status?.is_open || status?.diagnostics?.state === 'STREAMING') {
          setStreamState('LIVE');
          setDiagReason(null);
        } else {
          setStreamState('ERROR');
          setDiagReason(
            status?.diagnostics?.reason ||
            status?.message ||
            `Feed ${camera.id} offline or unreadable`
          );
        }
      } catch (err: any) {
        setStreamState('ERROR');
        setDiagReason(err?.message || 'Stream connection timed out after 5.0s');
      }
    }, 4500);

    return () => {
      clearInterval(checkInterval);
      clearTimeout(watchdogTimer);
    };
  }, [camera.id, getStreamUrl]);

  const handleImageError = async () => {
    setStreamState('ERROR');
    try {
      const status = await fetchCameraStatus(camera.id);
      if (status?.diagnostics?.reason) {
        setDiagReason(status.diagnostics.reason);
      } else {
        setDiagReason('Stream dropped or stream worker initializing');
      }
    } catch {
      setDiagReason('Backend server offline or unreachable on port 8000');
    }
  };

  const handleImageLoad = () => {
    setStreamState('LIVE');
    setDiagReason(null);
  };

  const handleRetry = async (e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setIsRetrying(true);
    setStreamState('CONNECTING');
    try {
      const res = await retryCameraStream(camera.id);
      if (res?.status === 'success') {
        setDiagReason(null);
        setStreamSrc(`${getStreamUrl(camera.id)}?t=${Date.now()}`);
      } else {
        setStreamState('ERROR');
        setDiagReason(res?.diagnostics?.reason || res?.message || 'Failed to re-initialize video capture');
        setStreamSrc(`${getStreamUrl(camera.id)}?t=${Date.now()}`);
      }
    } catch (err: any) {
      setStreamState('ERROR');
      setDiagReason(err?.message || 'Failed to contact backend stream service');
    } finally {
      setIsRetrying(false);
    }
  };

  const handleRemove = async (camera_id: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    if (!window.confirm(`Are you sure you want to remove ${camera_id}?`)) return;
    try {
      try {
        await fetch(`/api/cameras/${camera_id}`, { method: 'DELETE' });
      } catch {
        await fetch(`http://127.0.0.1:8000/api/cameras/${camera_id}`, { method: 'DELETE' });
      }
      if (onRemoveCamera) onRemoveCamera(camera_id);
    } catch (err) {
      console.error('Failed to remove camera:', err);
      if (onRemoveCamera) onRemoveCamera(camera_id);
    }
  };

  const getLosBadge = (los: string = 'LOS A') => {
    if (los.includes('E') || los.includes('F')) {
      return 'bg-rose-900/60 text-rose-300 border-rose-700/80';
    }
    if (los.includes('C') || los.includes('D')) {
      return 'bg-amber-900/60 text-amber-300 border-amber-700/80';
    }
    return 'bg-emerald-900/60 text-emerald-300 border-emerald-700/80';
  };

  return (
    <div className="group relative flex flex-col rounded-xl overflow-hidden bg-[#111827] border border-gray-800 hover:border-blue-500/60 shadow-xl transition-all duration-200">
      {/* Card Header */}
      <div className="flex items-center justify-between px-3.5 py-2.5 bg-[#0D1322] border-b border-gray-800/80 text-xs">
        <div className="flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full ${
              streamState === 'LIVE'
                ? 'bg-emerald-400 animate-pulse'
                : streamState === 'CONNECTING'
                ? 'bg-amber-400 animate-ping'
                : 'bg-rose-500'
            }`}
          />
          <span className="font-bold text-white font-mono">{camera.id}</span>
          <span className="text-gray-400 truncate max-w-[150px]" title={camera.location_name}>
            {camera.name || camera.location_name}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700">
            {camera.direction_covered || 'Northbound'}
          </span>
          <button
            onClick={() => onSelectCamera(camera)}
            className="flex items-center gap-1 text-[11px] font-semibold text-blue-400 hover:text-blue-300 transition-colors cursor-pointer"
            title="Inspect vehicle inventory"
          >
            <span>Inventory</span>
            <ArrowUpRight className="w-3.5 h-3.5" />
          </button>
          {onRemoveCamera && (
            <button
              onClick={(e) => handleRemove(camera.id, e)}
              className="flex items-center gap-1 px-2 py-0.5 rounded bg-rose-950/60 hover:bg-rose-900/90 border border-rose-800/80 text-[11px] font-semibold text-rose-300 hover:text-white transition-colors cursor-pointer"
              title="Remove Camera"
            >
              <Trash2 className="w-3 h-3" />
              <span>Remove</span>
            </button>
          )}
        </div>
      </div>

      {/* Frame Viewport */}
      <div
        onClick={() => onSelectCamera(camera)}
        className="relative w-full aspect-video bg-black cursor-pointer overflow-hidden group-hover:opacity-95"
      >
        {/* The Live Video Image */}
        {streamState !== 'ERROR' && (
          <img
            ref={imgRef}
            src={streamSrc}
            alt={`Live Feed ${camera.id}`}
            className={`w-full h-full object-cover select-none transition-opacity duration-300 ${
              streamState === 'LIVE' ? 'opacity-100' : 'opacity-0 absolute inset-0'
            }`}
            onLoad={handleImageLoad}
            onError={handleImageError}
          />
        )}

        {/* State 1: Connecting / Buffering Spinner */}
        {streamState === 'CONNECTING' && (
          <div className="w-full h-full flex flex-col items-center justify-center bg-gray-950/95 text-gray-400 p-4 space-y-2">
            <Loader2 className="w-8 h-8 text-blue-400 animate-spin" />
            <p className="text-xs font-semibold text-gray-300 font-mono">Connecting to live stream...</p>
            <p className="text-[10px] text-gray-500 font-mono">
              Binding stream handle: {camera.id}
            </p>
          </div>
        )}

        {/* State 2: Error / Stream Unavailable */}
        {streamState === 'ERROR' && (
          <div className="w-full h-full flex flex-col items-center justify-center bg-gray-950/95 text-gray-400 p-4 space-y-2">
            <AlertTriangle className="w-7 h-7 text-amber-500 animate-bounce" />
            <p className="text-xs font-semibold text-gray-200">Live Stream Unavailable</p>
            {diagReason && (
              <p className="text-[11px] text-rose-300 font-medium bg-rose-950/70 border border-rose-800/80 rounded px-2.5 py-1 text-center max-w-[90%]">
                {diagReason}
              </p>
            )}
            <p className="text-[10px] text-gray-500 text-center font-mono">
              Stream URL: <code className="text-blue-400">/api/stream/{camera.id}</code>
            </p>
            <button
              onClick={handleRetry}
              disabled={isRetrying}
              className="mt-2 flex items-center gap-1.5 px-3 py-1 rounded bg-blue-600/30 hover:bg-blue-600/50 text-[11px] font-semibold text-blue-300 border border-blue-500/50 transition-colors disabled:opacity-50 cursor-pointer"
            >
              {isRetrying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              <span>{isRetrying ? 'Re-initializing Capture...' : 'Retry Stream'}</span>
            </button>
          </div>
        )}

        {/* Live Badge Overlay */}
        {streamState === 'LIVE' && (
          <div className="absolute top-2 left-2 pointer-events-none flex items-center gap-1.5 px-2 py-0.5 rounded bg-black/70 backdrop-blur-sm text-[10px] font-mono text-emerald-300 border border-emerald-500/30">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
            <span>LIVE MJPEG</span>
          </div>
        )}

        {/* Top-Right LOS Badge */}
        <div className="absolute top-2 right-2 pointer-events-none flex items-center gap-1.5">
          <span className={`px-2 py-0.5 rounded border text-[10px] font-mono font-bold backdrop-blur-sm ${getLosBadge(camera.los || 'LOS A')}`}>
            {camera.los || 'LOS A'}
          </span>
        </div>
      </div>

      {/* Telemetry Strip & Spatial Density Heatmap (No signal control widget) */}
      <div className="p-3 bg-[#0E1526] border-t border-gray-800/80 space-y-2.5 text-xs">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-gray-300">
            <Car className="w-4 h-4 text-blue-400" />
            <span className="text-gray-400">Monitored:</span>
            <span className="font-bold text-white font-mono">{camera.vehicle_count ?? 0} vehicles</span>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 text-gray-300">
              <Gauge className="w-3.5 h-3.5 text-amber-400" />
              <span className="font-mono">{camera.density || '0.0'} veh/km/lane</span>
            </div>
            <span className={`px-2 py-0.5 rounded border text-[11px] font-extrabold ${getLosBadge(camera.los)}`}>
              {camera.los || 'LOS A'}
            </span>
          </div>
        </div>

        {/* Live Spatial Density Heatmap */}
        <DensityHeatmap
          density={camera.density || 0}
          vehicleCount={camera.vehicle_count || 0}
          los={camera.los}
          cameraId={camera.id}
        />
      </div>
    </div>
  );
};

export default CameraCard;