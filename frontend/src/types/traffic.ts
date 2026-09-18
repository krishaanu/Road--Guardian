export interface TrafficSignal {
  current_light: 'RED' | 'YELLOW' | 'GREEN';
  countdown: number;
  cycle_duration: number;
  recommended_green: number;
  mode: string;
}

export interface Camera {
  id: string;
  name: string;
  location_name: string;
  source: string;
  source_type: 'video' | 'rtsp' | 'usb' | 'http';
  gps: [number, number];
  direction_covered: string;
  enabled: boolean;
  status: 'STREAMING' | 'STANDBY' | 'ERROR';
  vehicle_count: number;
  density: number;
  los: string;
  signal: TrafficSignal;
}

export interface VehicleRecord {
  gvid: string;
  plate_text: string;
  vehicle_type: string;
  vehicle_subtype: string;
  color: string;
  estimated_speed_kmh: number;
  speed_status: 'NORMAL' | 'OVERSPEED';
  detected_view: 'Front' | 'Rear' | 'Side';
  confidence: number;
  last_seen: string;
}

export interface EmergencyAlert {
  alert_id: string;
  event_type: 'CONFIRMED_INCIDENT';
  camera_id: string;
  location_name: string;
  gps: [number, number];
  timestamp: number;
  fusion_score: number;
  detected_signals: string[];
  track_id: number;
  direction_zone: string;
  status: 'AMBULANCE_REQUIRED' | 'AMBULANCE_DISPATCHED' | 'ACKNOWLEDGED_BY_OPERATOR';
  acknowledged: boolean;
  ambulance_dispatched: boolean;
  message: string;
}

export interface MetricsSummary {
  high_congestion_zone: {
    camera_id: string;
    location_name: string;
    density: number;
  };
  active_cameras_count: number;
  total_vehicles_monitored: number;
  total_alerts_logged: number;
  active_emergency_alerts: number;
}

export interface WebSocketMessage {
  type: 'INITIAL_STATE' | 'TELEMETRY_UPDATE' | 'SIMULATOR_DENSITY_UPDATE' | 'EMERGENCY_ALERT' | 'ALERT_STATUS_UPDATE' | 'PONG';
  timestamp?: number;
  high_congestion_zone?: string;
  cameras?: {
    camera_id: string;
    location_name: string;
    direction_covered: string;
    gps: [number, number];
    vehicle_count: number;
    density: number;
    los: string;
    signal: TrafficSignal;
  }[];
  active_alerts?: EmergencyAlert[];
  alert?: EmergencyAlert;
  alert_id?: string;
  status?: string;
  acknowledged?: boolean;
  ambulance_dispatched?: boolean;
  junction_id?: string;
  lane_densities?: Record<string, number>;
  signals?: Record<string, { state: 'GREEN' | 'YELLOW' | 'RED'; density: number; timer?: number }>;
  active_phase?: string;
  emergency_preemption?: boolean;
}
