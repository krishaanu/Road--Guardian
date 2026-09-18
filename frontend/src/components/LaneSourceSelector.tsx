import React, { useState } from 'react';
import { Upload, Camera, Globe, FileVideo, AlertCircle, CheckCircle } from 'lucide-react';

interface LaneSourceSelectorProps {
  laneId: string;
  onSourceSelect: (source: string, sourceType: 'file' | 'webcam' | 'network') => void;
  onSuccess?: () => void;
}

export const LaneSourceSelector: React.FC<LaneSourceSelectorProps> = ({ laneId, onSourceSelect, onSuccess }) => {
  const [activeTab, setActiveTab] = useState<'drop' | 'webcam' | 'rtsp'>('drop');
  const [webcamIndex, setWebcamIndex] = useState('0');
  const [rtspUrl, setRtspUrl] = useState('');
  const [manualPath, setManualPath] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  };

  const handleDragLeave = () => setDragActive(false);

  const handleFileUpload = async (file: File) => {
    const validExtensions = ['.mp4', '.avi', '.mkv', '.mov'];
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!validExtensions.includes(ext)) {
      setErrorMessage(`Unsupported format '${ext}'. Must be MP4, AVI, MKV, or MOV.`);
      return;
    }

    setSelectedFileName(file.name);
    setErrorMessage(null);
    setSuccessMessage(null);
    setUploading(true);

    const formData = new FormData();
    formData.append('file', file);

    try {
      let res;
      try {
        res = await fetch(`/api/upload_video?camera_id=${encodeURIComponent(laneId)}`, {
          method: 'POST',
          body: formData,
        });
      } catch {
        res = await fetch(`http://127.0.0.1:8000/api/upload_video?camera_id=${encodeURIComponent(laneId)}`, {
          method: 'POST',
          body: formData,
        });
      }

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Upload failed with HTTP ${res.status}`);
      }

      const data = await res.json();
      if (data.status === 'success' && data.file_path) {
        const resDetail = data.resolution ? ` (${data.resolution} @ ${Math.round(data.fps || 25)}fps)` : '';
        setSuccessMessage(`Bound: ${file.name}${resDetail}`);
        onSourceSelect(data.file_path, 'file');
        if (onSuccess) onSuccess();
        return;
      }
      throw new Error('Upload response missing file path.');
    } catch (err: any) {
      console.error('File upload failed:', err);
      const localPath = (file as any).path;
      if (localPath) {
        setSuccessMessage(`Bound: ${file.name}`);
        onSourceSelect(localPath, 'file');
        if (onSuccess) onSuccess();
        return;
      }
      setErrorMessage(err?.message || 'Video upload and binding failed.');
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleFileUpload(e.target.files[0]);
    }
  };

  return (
    <div className="w-full bg-gray-950/70 border border-gray-800 rounded-xl p-2.5 space-y-2">
      {/* Source Selector Tabs */}
      <div className="flex items-center gap-1 bg-gray-900/80 p-1 rounded-lg text-[11px] font-mono">
        <button
          type="button"
          onClick={() => setActiveTab('drop')}
          className={`flex-1 py-1 px-2 rounded flex items-center justify-center gap-1 transition-colors ${
            activeTab === 'drop' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'
          }`}
        >
          <Upload className="w-3 h-3"/> Drop File
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('webcam')}
          className={`flex-1 py-1 px-2 rounded flex items-center justify-center gap-1 transition-colors ${
            activeTab === 'webcam' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'
          }`}
        >
          <Camera className="w-3 h-3"/> USB Cam
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('rtsp')}
          className={`flex-1 py-1 px-2 rounded flex items-center justify-center gap-1 transition-colors ${
            activeTab === 'rtsp' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'
          }`}
        >
          <Globe className="w-3 h-3"/> IP Cam
        </button>
      </div>

      {/* Tab 1: Drag & Drop Zone */}
      {activeTab === 'drop' && (
        <div className="space-y-1.5">
          <label
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={`flex flex-col items-center justify-center border-2 border-dashed rounded-lg p-3 cursor-pointer transition-colors ${
              dragActive ? 'border-blue-500 bg-blue-950/20' : 'border-gray-800 hover:border-gray-700 bg-gray-900/40'
            }`}
          >
            <FileVideo className="w-5 h-5 text-gray-400 mb-1"/>
            <span className="text-[11px] text-gray-300 font-mono text-center">
              {uploading ? 'Uploading video...' : (selectedFileName || 'Drag MP4 file here or click to browse')}
            </span>
            <input type="file" accept="video/mp4,video/avi,video/mkv" onChange={handleFileChange} className="hidden" />
          </label>
          <div className="flex gap-1 items-center">
            <input
              type="text"
              placeholder="Or paste local path (e.g. C:/path/demo.mp4)"
              value={manualPath}
              onChange={(e) => setManualPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && manualPath.trim()) {
                  onSourceSelect(manualPath.trim(), 'file');
                }
              }}
              className="flex-1 bg-gray-900/90 border border-gray-700 rounded px-2 py-1 text-[10px] text-white placeholder-gray-500 font-mono focus:outline-none focus:border-blue-500"
            />
            {manualPath.trim() && (
              <button
                type="button"
                onClick={() => onSourceSelect(manualPath.trim(), 'file')}
                className="px-2 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-[10px] font-mono font-bold"
              >
                Set
              </button>
            )}
          </div>
        </div>
      )}

      {/* Tab 2: USB Cam Select */}
      {activeTab === 'webcam' && (
        <div className="flex gap-2 items-center">
          <select
            value={webcamIndex}
            onChange={(e) => {
              setWebcamIndex(e.target.value);
              onSourceSelect(e.target.value, 'webcam');
            }}
            className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500 font-mono cursor-pointer"
          >
            <option value="0">USB Camera 0 (Integrated Webcam)</option>
            <option value="1">USB Camera 1 (External Camera)</option>
            <option value="2">USB Camera 2</option>
          </select>
          <button
            type="button"
            onClick={() => onSourceSelect(webcamIndex, 'webcam')}
            className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-bold font-mono"
          >
            Bind
          </button>
        </div>
      )}

      {/* Tab 3: IP / RTSP Stream */}
      {activeTab === 'rtsp' && (
        <div className="flex gap-1.5">
          <input
            type="text"
            placeholder="rtsp://admin:pass@192.168.1.50:554/live"
            value={rtspUrl}
            onChange={(e) => setRtspUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && rtspUrl.trim()) {
                onSourceSelect(rtspUrl.trim(), 'network');
              }
            }}
            className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono"
          />
          <button
            type="button"
            onClick={() => onSourceSelect(rtspUrl, 'network')}
            className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-bold font-mono"
          >
            Bind
          </button>
        </div>
      )}

      {/* Actionable Error Badge */}
      {errorMessage && (
        <div className="flex items-center gap-1.5 p-2 rounded bg-rose-950/80 border border-rose-700 text-rose-200 text-xs font-mono">
          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Success Badge */}
      {successMessage && (
        <div className="flex items-center gap-1.5 p-2 rounded bg-emerald-950/80 border border-emerald-700 text-emerald-200 text-xs font-mono">
          <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>{successMessage}</span>
        </div>
      )}
    </div>
  );
};
export default LaneSourceSelector;
