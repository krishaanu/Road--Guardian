import React, { useState, useEffect } from 'react';
import {
  X,
  ShieldAlert,
  CheckCircle,
  XCircle,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  AlertTriangle,
  FileText,
  Clock,
  Camera as CameraIcon,
  RefreshCw,
} from 'lucide-react';
import { fetchNeedsReview, submitReviewAction } from '../services/api';

interface NeedsReviewModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const NeedsReviewModal: React.FC<NeedsReviewModalProps> = ({ isOpen, onClose }) => {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedItem, setSelectedItem] = useState<any | null>(null);
  const [zoomLevel, setZoomLevel] = useState(1.0);
  const [notes, setNotes] = useState('');
  const [processing, setProcessing] = useState(false);

  const loadQueue = async () => {
    setLoading(true);
    try {
      const data = await fetchNeedsReview();
      setItems(data);
      if (data.length > 0 && !selectedItem) {
        setSelectedItem(data[0]);
      } else if (data.length === 0) {
        setSelectedItem(null);
      }
    } catch (e) {
      console.error('Failed to load needs review queue', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      loadQueue();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleAction = async (status: 'APPROVED' | 'DISMISSED') => {
    if (!selectedItem) return;
    try {
      setProcessing(true);
      await submitReviewAction(selectedItem.id, status, notes);
      setNotes('');
      setZoomLevel(1.0);
      await loadQueue();
    } catch (e: any) {
      alert(`Action failed: ${e.message}`);
    } finally {
      setProcessing(false);
    }
  };

  const parsedScores = () => {
    if (!selectedItem?.gate_scores) return null;
    try {
      return typeof selectedItem.gate_scores === 'string'
        ? JSON.parse(selectedItem.gate_scores)
        : selectedItem.gate_scores;
    } catch {
      return null;
    }
  };

  const scoreData = parsedScores();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 bg-black/85 backdrop-blur-sm animate-in fade-in duration-150">
      <div className="relative w-full max-w-6xl rounded-2xl bg-[#0F172A] border border-gray-800 shadow-2xl overflow-hidden flex flex-col max-h-[92vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-[#0B1120] border-b border-gray-800">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-amber-600/20 border border-amber-500/40 text-amber-400">
              <ShieldAlert className="w-5 h-5 text-amber-400" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                <span>Human Review Queue</span>
                <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-amber-900/60 border border-amber-700/50 text-amber-300">
                  {items.length} Pending
                </span>
              </h3>
              <p className="text-xs text-gray-400">
                Confidence-Gated Offense Evaluation & Verification (Smart India Hackathon Spec)
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={loadQueue}
              className="p-2 rounded-lg bg-gray-800/80 hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
              title="Refresh queue"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-2 rounded-lg bg-gray-800/80 hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-hidden grid grid-cols-1 lg:grid-cols-3 divide-y lg:divide-y-0 lg:divide-x divide-gray-800">
          {/* Left Column: List of Items */}
          <div className="overflow-y-auto max-h-[40vh] lg:max-h-[calc(92vh-140px)] p-3 space-y-2 bg-[#0B1222]/70">
            {items.length === 0 ? (
              <div className="p-8 text-center text-gray-500 text-xs">
                <CheckCircle className="w-8 h-8 text-emerald-500 mx-auto mb-2 opacity-80" />
                <p>No candidate offenses pending review.</p>
                <p className="text-[10px] text-gray-600 mt-1">All incoming detections have cleared confidence gates.</p>
              </div>
            ) : (
              items.map((it) => (
                <div
                  key={it.id}
                  onClick={() => {
                    setSelectedItem(it);
                    setZoomLevel(1.0);
                  }}
                  className={`p-3 rounded-xl border transition-all cursor-pointer ${
                    selectedItem?.id === it.id
                      ? 'bg-blue-950/40 border-blue-500 text-white shadow-md'
                      : 'bg-gray-900/40 border-gray-800 hover:border-gray-700 text-gray-300'
                  }`}
                >
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="font-bold text-amber-400 flex items-center gap-1">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      {it.offense_type}
                    </span>
                    <span className="font-mono text-[10px] text-gray-400">{it.camera_id}</span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-gray-400 font-mono">
                    <span>Plate: {it.license_plate || 'UNREAD'}</span>
                    <span>{new Date(it.timestamp).toLocaleTimeString()}</span>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Middle & Right: Selected Item Inspection */}
          {selectedItem ? (
            <div className="lg:col-span-2 p-6 flex flex-col justify-between overflow-y-auto max-h-[calc(92vh-140px)] space-y-6">
              {/* Image Viewer with Zoom Controls */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-gray-300 flex items-center gap-1.5">
                    <FileText className="w-4 h-4 text-blue-400" />
                    Offense Imagery & Crop Evidence
                  </span>
                  {/* Zoom controls */}
                  <div className="flex items-center gap-1.5 bg-gray-900 px-2 py-1 rounded-lg border border-gray-800 text-xs text-gray-300">
                    <button
                      onClick={() => setZoomLevel((z) => Math.max(0.6, z - 0.2))}
                      className="p-1 hover:text-white"
                      title="Zoom Out"
                    >
                      <ZoomOut className="w-3.5 h-3.5" />
                    </button>
                    <span className="font-mono text-[11px] px-1">{Math.round(zoomLevel * 100)}%</span>
                    <button
                      onClick={() => setZoomLevel((z) => Math.min(3.0, z + 0.2))}
                      className="p-1 hover:text-white"
                      title="Zoom In"
                    >
                      <ZoomIn className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => setZoomLevel(1.0)}
                      className="p-1 hover:text-white ml-1 border-l border-gray-700 pl-1.5"
                      title="Reset Zoom"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                <div className="relative w-full h-72 rounded-xl bg-black/90 border border-gray-800 overflow-hidden flex items-center justify-center">
                  {selectedItem.image_crop_path ? (
                    <img
                      src={selectedItem.image_crop_path}
                      alt="Crop evidence"
                      style={{ transform: `scale(${zoomLevel})` }}
                      className="max-h-full max-w-full object-contain transition-transform duration-100"
                    />
                  ) : (
                    <div className="text-center text-gray-600 text-xs">
                      <CameraIcon className="w-8 h-8 mx-auto mb-1 opacity-50" />
                      <span>Crop image not available</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Confidence Gate Diagnostics */}
              <div className="space-y-3">
                <h4 className="text-xs font-bold text-gray-300 uppercase tracking-wider">
                  Confidence Gate Evaluation Breakdown
                </h4>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono">
                  {scoreData?.scores &&
                    Object.entries(scoreData.scores).map(([gate, score]: [string, any]) => (
                      <div key={gate} className="p-2.5 rounded-lg bg-gray-900 border border-gray-800">
                        <div className="text-[10px] text-gray-400 capitalize">{gate.replace('gate', 'Gate ')}</div>
                        <div
                          className={`text-sm font-bold ${
                            score >= 0.70 ? 'text-emerald-400' : 'text-amber-400'
                          }`}
                        >
                          {(score * 100).toFixed(1)}%
                        </div>
                      </div>
                    ))}
                </div>

                {scoreData?.reasons && (
                  <div className="p-3 rounded-lg bg-amber-950/30 border border-amber-800/40 text-xs text-amber-300 space-y-1">
                    <span className="font-bold flex items-center gap-1">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      Flagged Gate Triggers:
                    </span>
                    <ul className="list-disc list-inside text-[11px] text-amber-200/80">
                      {scoreData.reasons.map((r: string, idx: number) => (
                        <li key={idx}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* Action Toolbar */}
              <div className="space-y-3 pt-4 border-t border-gray-800">
                <input
                  type="text"
                  placeholder="Reviewer notes (optional, e.g., 'Helmet confirmed upon manual inspection')"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-gray-950 border border-gray-700 text-xs text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 font-mono"
                />

                <div className="flex items-center justify-end gap-3">
                  <button
                    disabled={processing}
                    onClick={() => handleAction('DISMISSED')}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white text-xs font-semibold transition-all disabled:opacity-50"
                  >
                    <XCircle className="w-4 h-4 text-rose-400" />
                    <span>Dismiss Violation</span>
                  </button>
                  <button
                    disabled={processing}
                    onClick={() => handleAction('APPROVED')}
                    className="flex items-center gap-1.5 px-5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-lg shadow-emerald-900/30 transition-all disabled:opacity-50"
                  >
                    <CheckCircle className="w-4 h-4 text-white" />
                    <span>Confirm Offense</span>
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="lg:col-span-2 p-12 flex flex-col items-center justify-center text-center text-gray-500">
              <ShieldAlert className="w-12 h-12 text-gray-700 mb-3" />
              <p className="text-sm font-semibold text-gray-400">No Item Selected</p>
              <p className="text-xs text-gray-600 mt-1">Select an offense from the list to review evidence</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default NeedsReviewModal;
