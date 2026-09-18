import React, { useState, useRef } from 'react';
import { X, Upload, Camera, Link2, Plus, AlertCircle, Loader2 } from 'lucide-react';
import { registerCamera } from '../services/api';

interface AddCameraModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export const AddCameraModal: React.FC<AddCameraModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [cameraId, setCameraId] = useState(`CAM_${Math.floor(1000 + Math.random() * 9000)}`);
  const [streamSource, setStreamSource] = useState('');
  const [locationName, setLocationName] = useState('NH-44 Expressway Corridor');
  const [direction, setDirection] = useState('Northbound');
  const [latitude, setLatitude] = useState('25.2914');
  const [longitude, setLongitude] = useState('79.8713');

  const [isUploading, setIsUploading] = useState(false);
  const [isRegistering, setIsRegistering] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!isOpen) return null;

  // Handle Drag Events
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  // Upload Dropped or Picked Local Video File
  const handleFileUpload = async (file: File) => {
    if (!file) return;

    const validExtensions = ['.mp4', '.avi', '.mkv', '.mov'];
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!validExtensions.includes(ext)) {
      setError(`Unsupported format '${ext}'. Must be MP4, AVI, MKV, or MOV.`);
      return;
    }

    const sanitizedId = (cameraId.trim() || `CAM_${Math.floor(1000 + Math.random() * 9000)}`)
      .replace(/\s+/g, '_')
      .toUpperCase();

    setIsUploading(true);
    setError(null);
    setUploadSuccess(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      let res;
      try {
        res = await fetch(`/api/upload_video?camera_id=${encodeURIComponent(sanitizedId)}`, {
          method: 'POST',
          body: formData,
        });
      } catch {
        res = await fetch(`http://127.0.0.1:8000/api/upload_video?camera_id=${encodeURIComponent(sanitizedId)}`, {
          method: 'POST',
          body: formData,
        });
      }

      const data = await res.json();
      if (!res.ok || data.status !== 'success') {
        throw new Error(data.detail || 'Failed to upload video file.');
      }

      // Auto-populate input with server path
      setStreamSource(data.file_path);
      const resInfo = data.resolution ? ` (${data.resolution} @ ${Math.round(data.fps || 25)}fps)` : '';
      setUploadSuccess(`Uploaded & Bound: ${file.name}${resInfo}`);
    } catch (err: any) {
      console.error('File upload error:', err);
      setError(err.message || 'Failed to upload local video.');
    } finally {
      setIsUploading(false);
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  };

  // Submit Final Camera Stream Registration
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const rawId = cameraId.trim();
    const rawLoc = locationName.trim();
    const rawSrc = streamSource.trim();

    if (!rawId || !rawLoc || !rawSrc) {
      setError('Please provide a Camera ID, Location Name, and Stream Source.');
      return;
    }

    setIsRegistering(true);
    setError(null);

    try {
      const sanitizedId = rawId.replace(/\s+/g, '_').toUpperCase();
      const cleanSource = rawSrc.replace(/^["']|["']$/g, '').replace(/\\/g, '/');

      await registerCamera({
        id: sanitizedId,
        name: rawLoc,
        location_name: rawLoc,
        source: cleanSource,
        source_type: cleanSource === '0' || cleanSource === '1' ? 'webcam' : cleanSource.startsWith('http') || cleanSource.startsWith('rtsp') ? 'network' : 'file',
        gps: [parseFloat(latitude) || 25.2914, parseFloat(longitude) || 79.8713],
        direction_covered: direction,
      });

      onSuccess();
      onClose();
    } catch (err: any) {
      const rawDetail = err?.response?.data?.detail || err?.message || err;
      if (Array.isArray(rawDetail)) {
        const formatted = rawDetail
          .map((item: any) => {
            if (typeof item === 'string') return item;
            const field = item?.loc ? item.loc[item.loc.length - 1] : '';
            const msg = item?.msg || item?.message || JSON.stringify(item);
            return field ? `${field}: ${msg}` : msg;
          })
          .join(' | ');
        setError(formatted);
      } else if (typeof rawDetail === 'object' && rawDetail !== null) {
        setError(rawDetail.msg || rawDetail.message || JSON.stringify(rawDetail));
      } else {
        setError(String(rawDetail || 'Stream registration failed. Check server connection.'));
      }
    } finally {
      setIsRegistering(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-in fade-in duration-150">
      <div className="relative w-full max-w-lg rounded-2xl bg-[#0F172A] border border-gray-800 p-6 shadow-2xl space-y-4 text-white">
        
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-800 pb-3">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-blue-600/20 text-blue-400 border border-blue-500/30">
              <Camera className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-bold text-base">Register Camera Stream</h3>
              <p className="text-xs text-gray-400">Ingest webcam ('0'), RTSP stream, or drop a video file</p>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/80 border border-rose-800 text-rose-200 text-xs font-mono break-words">
            <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Success Banner */}
        {uploadSuccess && (
          <div className="p-2.5 rounded-lg bg-emerald-950/70 border border-emerald-800 text-emerald-300 text-xs font-mono">
            {uploadSuccess}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3.5 text-xs">
          
          {/* Camera ID & Direction */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-gray-300 mb-1 font-semibold">Camera ID</label>
              <input
                type="text"
                value={cameraId}
                onChange={(e) => setCameraId(e.target.value)}
                placeholder="e.g. CAM_05"
                className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono focus:border-blue-500 focus:outline-none"
                required
              />
            </div>
            <div>
              <label className="block text-gray-300 mb-1 font-semibold">Direction Covered</label>
              <select
                value={direction}
                onChange={(e) => setDirection(e.target.value)}
                className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white focus:border-blue-500 focus:outline-none cursor-pointer"
              >
                <option value="Northbound">Northbound</option>
                <option value="Southbound">Southbound</option>
                <option value="Eastbound">Eastbound</option>
                <option value="Westbound">Westbound</option>
                <option value="Dual Carriageway">Dual Carriageway</option>
              </select>
            </div>
          </div>

          {/* Location Sector */}
          <div>
            <label className="block text-gray-300 mb-1 font-semibold">Location Name / Sector</label>
            <input
              type="text"
              value={locationName}
              onChange={(e) => setLocationName(e.target.value)}
              placeholder="e.g. NH-44 Mahoba Toll Northbound"
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white focus:border-blue-500 focus:outline-none"
              required
            />
          </div>

          {/* Drag & Drop File Zone */}
          <div>
            <label className="block text-gray-300 mb-1 font-semibold">Upload Local Video File (Drag & Drop)</label>
            <div
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              onClick={() => !isUploading && fileInputRef.current?.click()}
              className={`flex flex-col items-center justify-center border-2 border-dashed rounded-xl p-4 transition-all cursor-pointer ${
                dragActive
                  ? 'border-blue-500 bg-blue-950/30 shadow-lg shadow-blue-950/50'
                  : 'border-gray-700 hover:border-gray-600 bg-gray-900/60'
              }`}
            >
              {isUploading ? (
                <Loader2 className="w-6 h-6 text-blue-400 animate-spin mb-1" />
              ) : (
                <Upload className={`w-6 h-6 mb-1 ${dragActive ? 'text-blue-400 animate-bounce' : 'text-gray-400'}`} />
              )}
              <span className="text-gray-200 font-semibold mb-0.5">
                {isUploading ? 'Uploading & Binding Video...' : 'Drag & Drop MP4 file here'}
              </span>
              <span className="text-[10px] text-gray-500">or click to browse local files (.mp4, .avi, .mkv)</span>
              <input
                ref={fileInputRef}
                type="file"
                accept="video/mp4,video/avi,video/mkv,video/mov"
                className="hidden"
                disabled={isUploading}
                onChange={(e) => e.target.files?.[0] && handleFileUpload(e.target.files[0])}
              />
            </div>
          </div>

          {/* Stream Source URL or Local Path Input */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <label className="block text-gray-300 font-semibold">Stream Source URL or Path</label>
              <div className="flex items-center gap-2 text-[10px]">
                <button
                  type="button"
                  onClick={() => setStreamSource('0')}
                  className="text-blue-400 hover:underline"
                >
                  Use USB Webcam ('0')
                </button>
              </div>
            </div>
            <div className="relative">
              <Link2 className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={streamSource}
                onChange={(e) => setStreamSource(e.target.value)}
                placeholder="e.g. 0, rtsp://192.168.1.50:554/stream, or uploads/demo.mp4"
                className="w-full bg-gray-900 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-white font-mono placeholder-gray-600 focus:border-blue-500 focus:outline-none"
                required
              />
            </div>
          </div>

          {/* GPS Coordinates */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-gray-300 mb-1 font-semibold">GPS Latitude</label>
              <input
                type="text"
                value={latitude}
                onChange={(e) => setLatitude(e.target.value)}
                className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-gray-300 mb-1 font-semibold">GPS Longitude</label>
              <input
                type="text"
                value={longitude}
                onChange={(e) => setLongitude(e.target.value)}
                className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          {/* Modal Action Buttons */}
          <div className="flex justify-end gap-2.5 pt-3 border-t border-gray-800">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 font-semibold transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isUploading || isRegistering}
              className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 font-bold text-white flex items-center gap-1.5 shadow-lg shadow-blue-600/30 transition-all disabled:opacity-50"
            >
              {isRegistering ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Registering...</span>
                </>
              ) : (
                <>
                  <Plus className="w-4 h-4" />
                  <span>Register Camera</span>
                </>
              )}
            </button>
          </div>

        </form>
      </div>
    </div>
  );
};