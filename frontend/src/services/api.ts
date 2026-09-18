import { Camera, VehicleRecord, MetricsSummary, EmergencyAlert, WebSocketMessage } from '../types/traffic';

const API_BASE = '/api';

/**
 * Safe fetch helper to handle blank/empty HTTP responses cleanly
 * and avoid "Unexpected end of JSON input" errors.
 */
async function safeFetchJson<T = any>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    let detail = `HTTP ${res.status}: ${res.statusText}`;
    try {
      if (errText) {
        const parsed = JSON.parse(errText);
        if (parsed.detail) {
          if (Array.isArray(parsed.detail)) {
            detail = parsed.detail
              .map((item: any) => {
                if (typeof item === 'string') return item;
                const field = item?.loc ? item.loc[item.loc.length - 1] : '';
                const msg = item?.msg || item?.message || JSON.stringify(item);
                return field ? `${field}: ${msg}` : msg;
              })
              .join(', ');
          } else if (typeof parsed.detail === 'object') {
            detail = parsed.detail.message || parsed.detail.msg || JSON.stringify(parsed.detail);
          } else {
            detail = String(parsed.detail);
          }
        } else if (parsed.message) {
          detail = String(parsed.message);
        }
      }
    } catch {
      if (errText && errText.trim()) {
        detail = errText.trim();
      }
    }
    throw new Error(String(detail));
  }

  const text = await res.text();
  if (!text || !text.trim()) {
    return { status: 'success', data: null } as unknown as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch (e) {
    if (text.startsWith('<') || text.includes('<html')) {
      throw new Error(`Endpoint returned HTML page instead of JSON: ${url}`);
    }
    throw new Error(`Invalid JSON response: ${text.slice(0, 80)}`);
  }
}

export async function fetchCameras(): Promise<Camera[]> {
  try {
    const json = await safeFetchJson<{ status: string; data: Camera[] }>(`${API_BASE}/cameras`);
    return json.data || [];
  } catch (e) {
    console.warn('Falling back to default cameras fetch route...');
    return [];
  }
}

export async function registerCamera(data: {
  id: string;
  name: string;
  location_name: string;
  source: string;
  source_type: string;
  gps: [number, number];
  direction_covered: string;
}): Promise<Camera> {
  const json = await safeFetchJson<{ status: string; data: Camera }>(`${API_BASE}/cameras/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return json.data;
}

export async function addCameraStream(data: {
  camera_id?: string;
  id?: string;
  source: string;
  name?: string;
  location_name?: string;
}): Promise<any> {
  const payload = {
    camera_id: data.camera_id || data.id || `CAM_${Date.now().toString().slice(-4)}`,
    source: data.source,
    name: data.name,
    location_name: data.location_name,
  };

  return await safeFetchJson(`${API_BASE}/cameras/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function removeCameraStream(id: string): Promise<void> {
  await safeFetchJson(`${API_BASE}/cameras/${id}`, { method: 'DELETE' }).catch(() => {
    // Graceful fallback for non-deleting endpoint mocks
  });
}

export async function deleteCamera(id: string): Promise<void> {
  return removeCameraStream(id);
}

export async function retryCameraStream(id: string): Promise<any> {
  try {
    return await safeFetchJson(`${API_BASE}/cameras/${id}/retry`, { method: 'POST' });
  } catch (e: any) {
    return { status: 'error', message: e.message || 'Failed to retry camera stream' };
  }
}

export async function fetchCameraStatus(id: string): Promise<any> {
  try {
    return await safeFetchJson(`${API_BASE}/cameras/${id}/status`);
  } catch (e: any) {
    return { status: 'offline', diagnostics: { reason: e.message || 'Server unreachable' } };
  }
}

export async function validateSource(source: string): Promise<{ valid: boolean; type?: string; message: string; resolution?: string; fps?: number }> {
  const clean = source.trim();
  try {
    const res = await safeFetchJson<{ valid: boolean; type?: string; message: string; resolution?: string; fps?: number }>(
      `${API_BASE}/validate_source?source=${encodeURIComponent(clean)}`
    );
    return res;
  } catch (e: any) {
    if (/^\d+$/.test(clean)) {
      return { valid: false, type: 'webcam', message: `Webcam device ${clean} (Backend unreachable to verify)` };
    } else if (clean.startsWith('rtsp://') || clean.startsWith('http://') || clean.startsWith('https://')) {
      return { valid: false, type: 'network', message: 'Stream URL (Backend unreachable to verify)' };
    } else {
      return { valid: false, type: 'file', message: e?.message || 'Cannot verify local file' };
    }
  }
}

export async function fetchVehicles(
  cameraId: string,
  search?: string,
  vehicleType?: string,
  color?: string
): Promise<VehicleRecord[]> {
  const params = new URLSearchParams();
  if (cameraId) params.set('camera_id', cameraId);
  if (search) params.set('search', search);
  if (vehicleType) params.set('vehicle_type', vehicleType);
  if (color) params.set('color', color);

  try {
    const json = await safeFetchJson<{ status: string; data: VehicleRecord[] }>(`${API_BASE}/observations/latest?${params.toString()}`);
    return json.data || [];
  } catch (e) {
    return [];
  }
}

export async function fetchMetricsSummary(): Promise<MetricsSummary> {
  try {
    let json: any;
    try {
      json = await safeFetchJson<any>(`${API_BASE}/metrics/summary`);
    } catch {
      json = await safeFetchJson<any>(`${API_BASE}/stats`);
    }

    return {
      total_vehicles_today: json.total_vehicles_monitored ?? json.total_vehicles_detected ?? 0,
      active_incidents: json.active_emergency_alerts ?? json.high_risk_alerts ?? 0,
      system_status: json.status === 'success' ? 'ONLINE' : (json.system_status || 'ONLINE'),
      active_cameras_count: json.active_cameras_count ?? 0,
      total_vehicles_monitored: json.total_vehicles_monitored ?? 0,
      total_alerts_logged: json.total_alerts_logged ?? 0,
      active_emergency_alerts: json.active_emergency_alerts ?? 0,
      high_congestion_zone: json.high_congestion_zone || { camera_id: 'CAMERA_01', location_name: 'Sector Corridor', density: 0.0 },
    } as MetricsSummary;
  } catch (e) {
    return {
      total_vehicles_today: 0,
      active_incidents: 0,
      system_status: 'OFFLINE',
      active_cameras_count: 0,
      total_vehicles_monitored: 0,
      total_alerts_logged: 0,
      active_emergency_alerts: 0,
      high_congestion_zone: { camera_id: 'NONE', location_name: 'Offline', density: 0.0 },
    } as MetricsSummary;
  }
}

export async function acknowledgeAlert(alertId: string): Promise<any> {
  try {
    return await safeFetchJson(`${API_BASE}/incidents/${alertId}/acknowledge`, { method: 'POST' });
  } catch {
    try {
      return await safeFetchJson(`${API_BASE}/alerts/${alertId}/acknowledge`, { method: 'POST' });
    } catch {
      return await safeFetchJson(`http://localhost:8000/api/incidents/${alertId}/acknowledge`, { method: 'POST' }).catch(() => ({ status: 'success' }));
    }
  }
}

export async function dispatchAmbulance(alertId: string): Promise<void> {
  await safeFetchJson(`${API_BASE}/alerts/${alertId}/dispatch-ambulance`, { method: 'POST' }).catch(() => {});
}

export async function simulateIncident(cameraId: string = 'CAMERA_01'): Promise<any> {
  try {
    return await safeFetchJson(`${API_BASE}/alerts/simulate?camera_id=${cameraId}`, { method: 'POST' });
  } catch {
    return await safeFetchJson(`http://localhost:8000/api/alerts/simulate?camera_id=${cameraId}`, { method: 'POST' }).catch(() => null);
  }
}

export async function fetchSignalTelemetry(): Promise<any> {
  try {
    return await safeFetchJson(`${API_BASE}/signals/telemetry`);
  } catch {
    return await safeFetchJson(`http://localhost:8000/api/signals/telemetry`).catch(() => null);
  }
}

export async function uploadVideo(file: File): Promise<any> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${API_BASE}/upload_video`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    throw new Error(errText || 'Failed to upload video');
  }
  const text = await res.text();
  return text ? JSON.parse(text) : { status: 'success' };
}

export async function fetchNeedsReview(): Promise<any[]> {
  try {
    const json = await safeFetchJson<{ status: string; data: any[] }>(`${API_BASE}/offenses/needs-review`);
    return json.data || [];
  } catch (e) {
    return [];
  }
}

export async function submitReviewAction(
  reviewId: number,
  status: 'APPROVED' | 'DISMISSED',
  notes?: string
): Promise<any> {
  return await safeFetchJson(`${API_BASE}/offenses/review/${reviewId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, reviewer_notes: notes }),
  });
}

export async function fetchJunctions(): Promise<any[]> {
  try {
    const json = await safeFetchJson<{ status: string; data: any[] }>(`${API_BASE}/junctions`);
    return json.data || [];
  } catch (e) {
    return [];
  }
}

export async function evaluateJunction(
  junctionId: string,
  countA: number,
  countB: number,
  emergencyCamId?: string,
  emergencyType: string = 'AMBULANCE'
): Promise<any> {
  return await safeFetchJson(`${API_BASE}/junctions/${junctionId}/evaluate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      count_a: countA,
      count_b: countB,
      emergency_cam_id: emergencyCamId,
      emergency_type: emergencyType,
    }),
  });
}

export async function calculateAdaptiveSignalApi(
  junctionId: string,
  laneDensities: Record<string, number>,
  emergencyEvents?: Record<string, boolean>
): Promise<any> {
  return await safeFetchJson(`${API_BASE}/junctions/adaptive-signal`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      junction_id: junctionId,
      lane_densities: laneDensities,
      emergency_events: emergencyEvents,
    }),
  });
}

export async function fetchSimulatorDensities(junctionId: string = 'JUNCTION_01'): Promise<any> {
  return await safeFetchJson(`${API_BASE}/simulator/densities?junction_id=${encodeURIComponent(junctionId)}`);
}

// WebSocket Connection Manager
export class TrafficWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private onMessageCallback: (msg: WebSocketMessage) => void;
  private onStatusChangeCallback: (status: 'CONNECTED' | 'RECONNECTING' | 'OFFLINE') => void;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect: boolean = true;

  constructor(
    onMessage: (msg: WebSocketMessage) => void,
    onStatusChange: (status: 'CONNECTED' | 'RECONNECTING' | 'OFFLINE') => void
  ) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = (window.location.port === '5173' || window.location.port === '4173')
      ? `${window.location.hostname || 'localhost'}:8000`
      : (window.location.host || 'localhost:8000');
    this.url = `${protocol}//${host}/ws/traffic-stream`;
    this.onMessageCallback = onMessage;
    this.onStatusChangeCallback = onStatusChange;
  }

  public connect(isRetry: boolean = false) {
    this.shouldReconnect = true;
    try {
      if (isRetry) {
        this.onStatusChangeCallback('RECONNECTING');
      }
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.onStatusChangeCallback('CONNECTED');
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const data: WebSocketMessage = JSON.parse(event.data);
          this.onMessageCallback(data);
        } catch (e) {
          console.error('Failed to parse WS payload', e);
        }
      };

      this.ws.onclose = () => {
        this.onStatusChangeCallback('OFFLINE');
        if (this.shouldReconnect) {
          if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
          this.reconnectTimer = setTimeout(() => this.connect(true), 3000);
        }
      };

      this.ws.onerror = () => {
        this.ws?.close();
      };
    } catch (e) {
      this.onStatusChangeCallback('OFFLINE');
      if (this.shouldReconnect) {
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => this.connect(true), 3000);
      }
    }
  }

  public disconnect() {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.onStatusChangeCallback('OFFLINE');
  }

  public ping() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'PING' }));
    }
  }
}