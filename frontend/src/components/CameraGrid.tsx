import React, { useState, useRef } from 'react';
import { Camera } from '../types/traffic';
import { CameraCard } from './CameraCard';
import { Grid, Video, PlusCircle, UploadCloud, CheckCircle2, AlertCircle } from 'lucide-react';
import { uploadVideo, validateSource } from '../services/api';

interface CameraGridProps {
  cameras: Camera[];
  onSelectCamera: (camera: Camera) => void;
  onInspectSignal?: (camera: Camera) => void;
  onRemoveCamera?: (cameraId: string) => void;
  onQuickAddCamera?: (cameraId: string, source: string) => Promise<void>;
  onRefresh?: () => void;
}

export const CameraGrid: React.FC<CameraGridProps> = ({
  cameras,
  onSelectCamera,
  onInspectSignal,
  onRemoveCamera,
  onQuickAddCamera,
  onRefresh,
}) => {
  const [quickId, setQuickId] = useState('');
  const [quickSource, setQuickSource] = useState('');
  const [validation, setValidation] = useState<{ valid: boolean; message: string; type?: string } | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const checkValidation = async (src: string) => {
    const trimmed = src.trim();
    if (!trimmed) {
      setValidation(null);
      return;
    }
    try {
      const res = await validateSource(trimmed);
      setValidation(res);
    } catch {
      setValidation(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!quickSource.trim() || !onQuickAddCamera) return;

    try {
      setIsSubmitting(true);
      const camId = quickId.trim() || `CAM_${cameras.length + 1}`;
      await onQuickAddCamera(camId, quickSource.trim());
      setQuickId('');
      setQuickSource('');
      setValidation(null);
    } catch (err: any) {
      alert(`Failed to add camera: ${err?.message || 'Unknown error'}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleFileUpload = async (file: File) => {
    const validExts = ['.mp4', '.avi', '.mkv', '.mov'];
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!validExts.includes(ext)) {
      alert(`Unsupported file format. Please upload: ${validExts.join(', ')}`);
      return;
    }

    const newCamId = `CAM_${Math.floor(1000 + Math.random() * 9000)}`;
    const formData = new FormData();
    formData.append('file', file);

    try {
      setIsUploading(true);
      let uploadRes;
      try {
        uploadRes = await fetch(`/api/upload_video?camera_id=${encodeURIComponent(newCamId)}`, {
          method: 'POST',
          body: formData,
        });
      } catch {
        uploadRes = await fetch(`http://127.0.0.1:8000/api/upload_video?camera_id=${encodeURIComponent(newCamId)}`, {
          method: 'POST',
          body: formData,
        });
      }

      const uploadData = await uploadRes.json();
      if (uploadData.status === 'success' && uploadData.file_path) {
        try {
          await fetch('/api/cameras/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              camera_id: newCamId,
              source: uploadData.file_path,
              name: `Surveillance Feed (${file.name})`,
              location_name: `Sector Corridor (${newCamId})`,
            }),
          });
        } catch {
          await fetch('http://127.0.0.1:8000/api/cameras/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              camera_id: newCamId,
              source: uploadData.file_path,
              name: `Surveillance Feed (${file.name})`,
              location_name: `Sector Corridor (${newCamId})`,
            }),
          });
        }
        if (onRefresh) onRefresh();
      } else {
        throw new Error(uploadData.detail || 'Upload failed');
      }
    } catch (err: any) {
      alert(`Video upload failed: ${err.message || 'Unknown error'}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  return (
    <section className="space-y-4">
      {/* Top Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Grid className="w-5 h-5 text-blue-400" />
          <h2 className="text-base font-bold text-white tracking-tight">
            Active Camera Surveillance Grid
          </h2>
          <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-blue-900/40 border border-blue-700/40 text-blue-300">
            {cameras.length} Feeds
          </span>
        </div>
        <p className="text-xs text-gray-400 hidden sm:block">
          Select any feed to open the Vehicle Inventory Inspector
        </p>
      </div>

      {/* Camera Ingestion Bar */}
      <div className="p-3.5 rounded-xl bg-[#0D1526] border border-gray-800/90 shadow-lg flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-bold text-gray-200">
          <PlusCircle className="w-4 h-4 text-emerald-400" />
          <span className="font-mono uppercase tracking-wider text-gray-300">Camera Ingestion:</span>
        </div>

        {/* Form Inputs */}
        <form onSubmit={handleSubmit} className="flex flex-wrap items-center gap-2.5 flex-1">
          <input
            type="text"
            placeholder="Camera ID (e.g. CAM_02)"
            value={quickId}
            onChange={(e) => setQuickId(e.target.value)}
            className="px-3 py-1.5 rounded-lg bg-gray-950 border border-gray-700/90 text-xs text-white placeholder-gray-500 font-mono focus:outline-none focus:border-blue-500 w-36"
          />
          <div className="flex-1 min-w-[200px] relative">
            <input
              type="text"
              placeholder="Source (0 for USB webcam, or C:/path/demo.mp4)"
              value={quickSource}
              onChange={(e) => {
                const val = e.target.value;
                setQuickSource(val);
                if (val.trim()) {
                  checkValidation(val);
                } else {
                  setValidation(null);
                }
              }}
              onBlur={(e) => checkValidation(e.target.value)}
              className="w-full px-3 py-1.5 rounded-lg bg-gray-950 border border-gray-700/90 text-xs text-white placeholder-gray-500 font-mono focus:outline-none focus:border-blue-500"
            />
            {validation && (
              <div className={`absolute top-full left-0 mt-1 z-10 flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-mono border backdrop-blur-sm shadow-md ${
                validation.valid 
                  ? 'bg-emerald-950/95 border-emerald-700 text-emerald-300' 
                  : 'bg-rose-950/95 border-rose-800 text-rose-300'
              }`}>
                {validation.valid ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> : <AlertCircle className="w-3.5 h-3.5 text-rose-400" />}
                <span className="font-semibold uppercase text-[9px] px-1 py-0.2 rounded bg-black/40 border border-current">
                  {validation.type || 'SOURCE'}
                </span>
                <span>{validation.message}</span>
              </div>
            )}
          </div>
          <button
            type="submit"
            disabled={isSubmitting || !quickSource.trim()}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-xs font-semibold text-white shadow-md transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          >
            <Video className="w-3.5 h-3.5" />
            <span>{isSubmitting ? 'Adding...' : 'Add Camera'}</span>
          </button>
        </form>

        {/* Drag and Drop Zone */}
        <div
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          className={`flex items-center justify-center gap-2 px-4 py-1.5 rounded-lg border-2 border-dashed text-xs cursor-pointer transition-all ${
            isDragging
              ? 'border-emerald-400 bg-emerald-950/40 text-emerald-300'
              : 'border-blue-700/60 hover:border-blue-400 bg-blue-950/20 hover:bg-blue-900/30 text-blue-300'
          }`}
          title="Drag and drop MP4/AVI/MKV video file here"
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) {
                handleFileUpload(e.target.files[0]);
              }
            }}
            accept=".mp4,.avi,.mkv,.mov"
            className="hidden"
          />
          {isUploading ? (
            <span className="animate-pulse">Uploading Video...</span>
          ) : (
            <>
              <UploadCloud className="w-4 h-4 text-blue-400" />
              <span className="font-semibold">Drop Video (.mp4)</span>
            </>
          )}
        </div>
      </div>

      {/* Grid Display */}
      {cameras.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 bg-gray-900/40 border border-gray-800 rounded-xl text-center space-y-3">
          <Video className="w-10 h-10 text-gray-600 animate-pulse" />
          <p className="text-sm font-semibold text-gray-300">No camera streams active</p>
          <p className="text-xs text-gray-500">
            Use the Camera Management panel above to register a new webcam ('0') or RTSP stream.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-2 xl:grid-cols-2 2xl:grid-cols-3 gap-5">
          {cameras.map((camera) => (
            <CameraCard
              key={camera.id}
              camera={camera}
              onSelectCamera={onSelectCamera}
              onInspectSignal={onInspectSignal}
              onRemoveCamera={onRemoveCamera}
            />
          ))}
        </div>
      )}
    </section>
  );
};