import React, { useState, useEffect } from 'react';
import { Upload } from 'lucide-react';

export const JunctionSimulator: React.FC = () => {
  const [snapshot, setSnapshot] = useState<any>(null);
  const [uploadingLane, setUploadingLane] = useState<string | null>(null);
  const [activeFeeds, setActiveFeeds] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const fetchSnapshot = async () => {
      try {
        let res;
        try {
          res = await fetch('/api/signals/telemetry');
        } catch {
          res = await fetch('http://127.0.0.1:8000/api/signals/telemetry');
        }
        const data = await res.json();
        if (data.status === 'success' && data.snapshot) {
          setSnapshot(data.snapshot);
        }
      } catch (err) {
        console.error('Failed to pull signal snapshot:', err);
      }
    };

    fetchSnapshot();
    const interval = setInterval(fetchSnapshot, 1000);
    return () => clearInterval(interval);
  }, []);

  const handleFileUpload = async (laneId: string, file: File) => {
    if (!file) return;
    setUploadingLane(laneId);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('lane_id', laneId);

    try {
      let uploadRes;
      try {
        uploadRes = await fetch(`/api/upload_video?camera_id=${encodeURIComponent(laneId)}`, {
          method: 'POST',
          body: formData,
        });
      } catch {
        uploadRes = await fetch(`http://127.0.0.1:8000/api/upload_video?camera_id=${encodeURIComponent(laneId)}`, {
          method: 'POST',
          body: formData,
        });
      }
      const uploadData = await uploadRes.json();

      if (!uploadRes.ok || uploadData.status !== 'success') {
        throw new Error(uploadData.detail || 'Upload failed');
      }

      if (uploadData.status === 'success' && uploadData.file_path) {
        try {
          await fetch('/api/cameras/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              camera_id: laneId,
              source: uploadData.file_path,
            }),
          });
        } catch {
          await fetch('http://127.0.0.1:8000/api/cameras/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              camera_id: laneId,
              source: uploadData.file_path,
            }),
          });
        }
        setActiveFeeds((prev) => ({ ...prev, [laneId]: true }));
      }
    } catch (err) {
      console.error('File upload error:', err);
    } finally {
      setUploadingLane(null);
    }
  };

  const lanes = ['LANE_1', 'LANE_2', 'LANE_3', 'LANE_4'];

  return (
    <div className="p-6 bg-gray-950 min-h-screen text-white space-y-6">
      <h2 className="text-xl font-bold font-mono text-emerald-400">
        🚦 Autonomous Density-Driven Signal Engine & Heatmap Planner
      </h2>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {lanes.map((laneId) => {
          const laneData = snapshot?.signals?.[laneId] || { state: 'RED', density: 0, action: 'HOLD (RED)' };
          const isGreen = laneData.state === 'GREEN';
          const isYellow = laneData.state === 'YELLOW';
          const hasStream = activeFeeds[laneId];

          return (
            <div
              key={laneId}
              className={`p-4 rounded-xl border transition-all ${
                isGreen
                  ? 'bg-emerald-950/30 border-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.3)]'
                  : isYellow
                  ? 'bg-amber-950/30 border-amber-500'
                  : 'bg-gray-900/60 border-gray-800'
              }`}
            >
              <div className="flex justify-between items-center mb-2">
                <span className="font-mono font-bold">{laneId}</span>
                <span
                  className={`text-xs px-2 py-0.5 rounded font-mono font-bold ${
                    isGreen ? 'bg-emerald-500 text-black' : isYellow ? 'bg-amber-500 text-black' : 'bg-rose-900 text-rose-200'
                  }`}
                >
                  {laneData.state} SIGNAL
                </span>
              </div>

              {hasStream ? (
                <div className="relative mb-3 rounded-lg overflow-hidden border border-gray-800 bg-black">
                  <img
                    src={`/api/stream/${laneId}`}
                    alt={`${laneId} Live Feed`}
                    className="w-full h-32 object-cover"
                    onError={(e) => {
                      (e.target as HTMLElement).style.display = 'none';
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => setActiveFeeds((prev) => ({ ...prev, [laneId]: false }))}
                    className="absolute top-1 right-1 bg-black/70 hover:bg-black text-[10px] font-mono px-1.5 py-0.5 rounded text-gray-300 border border-gray-700"
                  >
                    Change Feed
                  </button>
                </div>
              ) : (
                <label className="flex flex-col items-center justify-center border-2 border-dashed border-gray-800 rounded-lg p-4 cursor-pointer hover:border-blue-500 transition-colors bg-gray-950/50 mb-3">
                  <Upload className="w-5 h-5 text-gray-400 mb-1" />
                  <span className="text-xs text-gray-400 font-mono">
                    {uploadingLane === laneId ? 'Uploading video...' : 'Drop local MP4 file'}
                  </span>
                  <input
                    type="file"
                    accept="video/mp4,video/*"
                    className="hidden"
                    onChange={(e) => e.target.files?.[0] && handleFileUpload(laneId, e.target.files[0])}
                  />
                </label>
              )}

              <div className="space-y-1">
                <div className="flex justify-between text-xs font-mono text-gray-400">
                  <span>Detected Density:</span>
                  <span className="text-white font-bold">{laneData.density} vehicles</span>
                </div>
                <div className="w-full bg-gray-800 h-2 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all duration-300 ${isGreen ? 'bg-emerald-500' : isYellow ? 'bg-amber-500' : 'bg-rose-500'}`}
                    style={{ width: `${Math.min(100, (laneData.density / 40) * 100)}%` }}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="p-4 rounded-xl bg-gray-900 border border-gray-800 space-y-2">
        <h3 className="text-sm font-mono text-gray-400 font-bold uppercase">Automated AI Signal & Density Allocation</h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-2 font-mono text-xs">
          {lanes.map((laneId) => {
            const laneData = snapshot?.signals?.[laneId] || { density: 0, action: 'HOLD (RED)' };
            const isGreen = laneData.state === 'GREEN';
            return (
              <div
                key={laneId}
                className={`p-2.5 rounded-lg text-center font-bold ${
                  isGreen ? 'bg-emerald-600 text-white' : 'bg-rose-950/60 text-rose-300 border border-rose-900'
                }`}
              >
                {laneId}: {laneData.density} Veh ({laneData.action})
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default JunctionSimulator;
