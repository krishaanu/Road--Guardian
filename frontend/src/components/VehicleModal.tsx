import React, { useState, useEffect } from 'react';
import { Camera, VehicleRecord } from '../types/traffic';
import { fetchVehicles } from '../services/api';
import {
  X,
  Search,
  Filter,
  Car,
  Truck,
  Bus,
  Bike,
  Compass,
  Gauge,
  Palette,
  Tag,
  Clock,
  ShieldCheck,
  RefreshCw,
} from 'lucide-react';

interface VehicleModalProps {
  camera: Camera | null;
  onClose: () => void;
}

export const VehicleModal: React.FC<VehicleModalProps> = ({ camera, onClose }) => {
  const [vehicles, setVehicles] = useState<VehicleRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedType, setSelectedType] = useState('');
  const [selectedColor, setSelectedColor] = useState('');

  const loadData = async () => {
    if (!camera) return;
    setLoading(true);
    try {
      const data = await fetchVehicles(camera.id, searchTerm, selectedType, selectedColor);
      setVehicles(data || []);
    } catch (e) {
      console.error('Failed to load vehicles', e);
      setVehicles([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [camera, selectedType, selectedColor]);

  if (!camera) return null;

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    loadData();
  };

  // Helper icon for vehicle subtypes
  const getSubtypeIcon = (subtype: string = '') => {
    const s = subtype.toLowerCase();
    if (s.includes('truck') || s.includes('hauler')) return <Truck className="w-4 h-4 text-amber-400" />;
    if (s.includes('bus')) return <Bus className="w-4 h-4 text-blue-400" />;
    if (s.includes('motorcycle') || s.includes('bike') || s.includes('bicycle'))
      return <Bike className="w-4 h-4 text-emerald-400" />;
    return <Car className="w-4 h-4 text-indigo-400" />;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 bg-black/80 backdrop-blur-sm animate-in fade-in duration-150">
      <div className="relative w-full max-w-5xl rounded-2xl bg-[#0F172A] border border-gray-800 shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-[#0B1120] border-b border-gray-800">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-blue-600/20 border border-blue-500/40 text-blue-400">
              <Car className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                <span>Vehicle Inventory & Registry</span>
                <span className="text-xs font-mono px-2 py-0.5 rounded bg-blue-900/60 border border-blue-700/50 text-blue-300">
                  {camera.id}
                </span>
              </h3>
              <p className="text-xs text-gray-400 font-mono">
                {camera.location_name} • {camera.direction_covered}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-2 rounded-lg bg-gray-800/80 hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search & Filter Toolbar */}
        <div className="p-4 bg-[#111C35]/60 border-b border-gray-800/80 flex flex-col md:flex-row gap-3 items-stretch md:items-center justify-between">
          <form onSubmit={handleSearchSubmit} className="relative flex-1">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search by license plate (e.g. KA01AB1234) or GVID..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full pl-9 pr-4 py-2 rounded-lg bg-gray-900/90 border border-gray-700 text-xs text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          </form>

          <div className="flex items-center gap-2.5 flex-wrap">
            {/* Vehicle Type Filter */}
            <select
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              className="px-3 py-2 rounded-lg bg-gray-900/90 border border-gray-700 text-xs text-gray-200 focus:outline-none focus:border-blue-500 cursor-pointer"
            >
              <option value="">All Vehicle Types</option>
              <option value="Car">Car / Sedan / SUV</option>
              <option value="Truck">Truck / Heavy Vehicle</option>
              <option value="Bus">Bus / Transit</option>
              <option value="Motorcycle">Motorcycle</option>
              <option value="Bicycle">Bicycle</option>
            </select>

            {/* Vehicle Color Filter */}
            <select
              value={selectedColor}
              onChange={(e) => setSelectedColor(e.target.value)}
              className="px-3 py-2 rounded-lg bg-gray-900/90 border border-gray-700 text-xs text-gray-200 focus:outline-none focus:border-blue-500 cursor-pointer"
            >
              <option value="">All Colors</option>
              <option value="White">White</option>
              <option value="Silver">Silver / Grey</option>
              <option value="Black">Black</option>
              <option value="Blue">Blue</option>
              <option value="Red">Red</option>
              <option value="Yellow">Yellow</option>
            </select>

            <button
              onClick={loadData}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs font-semibold text-gray-300 transition-colors"
              title="Refresh vehicle inventory"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              <span>Refresh</span>
            </button>
          </div>
        </div>

        {/* Vehicles Table */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="flex flex-col items-center justify-center p-12 text-gray-400 space-y-2">
              <RefreshCw className="w-8 h-8 animate-spin text-blue-500" />
              <p className="text-xs">Loading vehicle records for {camera.id}...</p>
            </div>
          ) : vehicles.length === 0 ? (
            <div className="flex flex-col items-center justify-center p-12 text-gray-500 space-y-2">
              <Car className="w-10 h-10 text-gray-600" />
              <p className="text-sm font-semibold text-gray-400">No vehicles match current filters</p>
              <p className="text-xs">Try clearing search terms or selecting a different vehicle category.</p>
            </div>
          ) : (
            <div className="border border-gray-800 rounded-xl overflow-hidden shadow-inner">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-gray-900/90 text-gray-400 border-b border-gray-800 uppercase tracking-wider font-mono text-[11px]">
                    <th className="px-4 py-3">Global Vehicle ID (GVID)</th>
                    <th className="px-4 py-3">License Plate</th>
                    <th className="px-4 py-3">Subtype / Category</th>
                    <th className="px-4 py-3">Vehicle Color</th>
                    <th className="px-4 py-3">Estimated Speed</th>
                    <th className="px-4 py-3">View Angle</th>
                    <th className="px-4 py-3">Last Seen</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/80 font-sans">
                  {vehicles.map((v: any, i: number) => {
                    // Property normalization
                    const plateText = v.plate_text || v.license_plate || 'UNREAD';
                    const vehicleCategory = v.vehicle_subtype || v.vehicle_type || v.category || 'Car';
                    const vehicleColor = v.color || 'Silver';
                    const speedValue = typeof v.estimated_speed_kmh === 'number' 
                      ? v.estimated_speed_kmh 
                      : (typeof v.speed === 'number' ? v.speed : (v.speed_kmh || 0));
                    const isOverspeed = v.speed_status === 'OVERSPEED' || speedValue > 100;
                    const viewAngle = v.detected_view || v.view_angle || 'Front';
                    const lastSeenTime = v.last_seen || v.timestamp || 'Just now';

                    return (
                      <tr key={i} className="hover:bg-blue-950/20 transition-colors">
                        {/* GVID */}
                        <td className="px-4 py-3 font-mono font-bold text-blue-400">
                          {v.gvid}
                        </td>

                        {/* Plate Text */}
                        <td className="px-4 py-3">
                          <span
                            className={`font-mono font-black tracking-widest px-2.5 py-1 rounded border text-[11px] ${
                              plateText !== 'UNREAD' && plateText !== 'UNREADABLE'
                                ? 'bg-amber-950/40 text-amber-300 border-amber-600/60 shadow-sm'
                                : 'bg-gray-800/60 text-gray-500 border-gray-700'
                            }`}
                          >
                            {plateText}
                          </span>
                        </td>

                        {/* Subtype / Category */}
                        <td className="px-4 py-3 text-gray-200">
                          <div className="flex items-center gap-2">
                            {getSubtypeIcon(vehicleCategory)}
                            <span className="font-medium capitalize">{vehicleCategory}</span>
                          </div>
                        </td>

                        {/* Color */}
                        <td className="px-4 py-3 text-gray-300">
                          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-gray-800/80 border border-gray-700">
                            <span
                              className="w-2.5 h-2.5 rounded-full border border-black/40"
                              style={{
                                backgroundColor:
                                  vehicleColor.toLowerCase() === 'white'
                                    ? '#ffffff'
                                    : vehicleColor.toLowerCase() === 'black'
                                    ? '#1a1a1a'
                                    : vehicleColor.toLowerCase() === 'red'
                                    ? '#ef4444'
                                    : vehicleColor.toLowerCase() === 'blue'
                                    ? '#3b82f6'
                                    : vehicleColor.toLowerCase() === 'yellow'
                                    ? '#eab308'
                                    : '#9ca3af',
                              }}
                            />
                            <span className="capitalize">{vehicleColor}</span>
                          </span>
                        </td>

                        {/* Estimated Speed */}
                        <td className="px-4 py-3 font-mono">
                          <div className="flex items-center gap-1.5">
                            <span
                              className={`font-bold ${
                                isOverspeed ? 'text-rose-400 animate-pulse' : 'text-emerald-400'
                              }`}
                            >
                              {speedValue.toFixed(1)} km/h
                            </span>
                            {isOverspeed && (
                              <span className="text-[10px] px-1.5 py-0.2 rounded bg-rose-950 border border-rose-700 text-rose-300 font-bold uppercase">
                                Overspeed
                              </span>
                            )}
                          </div>
                        </td>

                        {/* Detected View Angle */}
                        <td className="px-4 py-3 text-gray-300 font-mono">
                          <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700 text-[11px]">
                            {viewAngle}
                          </span>
                        </td>

                        {/* Timestamp / Last Seen */}
                        <td className="px-4 py-3 text-gray-400 font-mono text-[11px]">
                          {lastSeenTime}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-3 bg-[#0B1120] border-t border-gray-800 flex items-center justify-between text-xs text-gray-400">
          <span>
            Total Displayed: <strong className="text-white font-mono">{vehicles.length}</strong> vehicle records
          </span>
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-white font-semibold transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};