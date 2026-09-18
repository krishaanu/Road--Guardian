import React, { useState, useEffect, useCallback, useRef } from 'react';
import { TrafficWebSocket, calculateAdaptiveSignalApi, fetchSimulatorDensities, fetchSignalTelemetry } from '../services/api';
import { LaneSourceSelector } from './LaneSourceSelector';

export default function JunctionSimulator() {
  const [junctions, setJunctions] = useState([
    {
      id: 'JUNCTION_01',
      name: 'Main Expressway Interchange (Junction 1)',
      hasSignal: true,
      activePhase: 'LANE_4',
      emergencyPreemption: false,
      lanes: [
        { id: 'LANE_1', name: 'Lane 1 (Northbound)', videoUrl: '', density: 18, detectedDensity: 18, signalState: 'RED', timer: 0, emergency: false },
        { id: 'LANE_2', name: 'Lane 2 (Southbound)', videoUrl: '', density: 24, detectedDensity: 24, signalState: 'RED', timer: 0, emergency: false },
        { id: 'LANE_3', name: 'Lane 3 (Eastbound)', videoUrl: '', density: 8, detectedDensity: 8, signalState: 'RED', timer: 0, emergency: false },
        { id: 'LANE_4', name: 'Lane 4 (Westbound)', videoUrl: '', density: 31, detectedDensity: 31, signalState: 'GREEN', timer: 7, emergency: false },
      ],
    }
  ]);

  const [wsStatus, setWsStatus] = useState('OFFLINE');
  const junctionsRef = useRef(junctions);
  junctionsRef.current = junctions;

  // Sync density metrics with backend adaptive signal engine
  const refreshAdaptiveSignals = useCallback(async (currentJunctions) => {
    const updated = await Promise.all(
      currentJunctions.map(async (junc) => {
        if (!junc.hasSignal) return junc;

        const laneDensities = {};
        const emergencyEvents = {};

        junc.lanes.forEach((lane) => {
          const d = (lane.detectedDensity !== undefined ? lane.detectedDensity : lane.density) || 0;
          laneDensities[lane.id] = d;
          if (lane.emergency) {
            emergencyEvents[lane.id] = true;
          }
        });

        try {
          const res = await calculateAdaptiveSignalApi(junc.id, laneDensities, emergencyEvents);
          if (res && res.signals) {
            const updatedLanes = junc.lanes.map((lane) => {
              const sig = res.signals[lane.id];
              return {
                ...lane,
                signalState: sig ? sig.state : lane.signalState,
                timer: sig ? sig.timer : lane.timer,
              };
            });
            return {
              ...junc,
              activePhase: res.active_phase || junc.activePhase,
              emergencyPreemption: res.emergency_preemption || false,
              lanes: updatedLanes,
            };
          }
        } catch {
          // Fallback local calculation
        }
        return junc;
      })
    );
    setJunctions(updated);
  }, []);

  // Handle incoming real-time WebSocket telemetry & autonomous density updates
  const handleWsMessage = useCallback((msg) => {
    if (!msg) return;

    if (msg.type === 'SIMULATOR_DENSITY_UPDATE' && msg.junction_id) {
      setJunctions((prev) =>
        prev.map((junc) => {
          if (junc.id !== msg.junction_id && msg.junction_id !== 'Junction_1' && msg.junction_id !== 'JUNCTION_01') return junc;
          const updatedLanes = junc.lanes.map((lane) => {
            const camId = `SIM_${junc.id}_${lane.id}`;
            const detected = msg.lane_densities
              ? (msg.lane_densities[lane.id] ?? msg.lane_densities[lane.id.toLowerCase()] ?? msg.lane_densities[camId])
              : undefined;
            const sigInfo = msg.signals
              ? (msg.signals[lane.id] || msg.signals[lane.id.toLowerCase()] || msg.signals[camId])
              : null;

            const finalDensity = detected !== undefined ? detected : (lane.detectedDensity !== undefined ? lane.detectedDensity : lane.density);

            return {
              ...lane,
              density: finalDensity,
              detectedDensity: finalDensity,
              signalState: sigInfo ? sigInfo.state : (lane.signalState || 'RED'),
              timer: sigInfo ? (sigInfo.timer ?? 0) : lane.timer,
            };
          });

          return {
            ...junc,
            activePhase: msg.active_phase || junc.activePhase,
            emergencyPreemption: msg.emergency_preemption !== undefined ? msg.emergency_preemption : junc.emergencyPreemption,
            lanes: updatedLanes,
          };
        })
      );
    } else if (msg.type === 'TELEMETRY_UPDATE' && Array.isArray(msg.cameras)) {
      setJunctions((prev) =>
        prev.map((junc) => {
          let hasChanges = false;
          const updatedLanes = junc.lanes.map((lane) => {
            const camId = `SIM_${junc.id}_${lane.id}`;
            const camUpdate = msg.cameras.find((c) => c && (c.camera_id === camId || c.camera_id === lane.id));
            if (camUpdate && camUpdate.vehicle_count !== undefined) {
              hasChanges = true;
              return {
                ...lane,
                density: camUpdate.vehicle_count,
                detectedDensity: camUpdate.vehicle_count,
              };
            }
            return lane;
          });
          return hasChanges ? { ...junc, lanes: updatedLanes } : junc;
        })
      );
    }
  }, []);

  // Connect WebSocket lifecycle with autonomous background polling fallback
  useEffect(() => {
    const ws = new TrafficWebSocket(handleWsMessage, setWsStatus);
    ws.connect();

    // Autonomous polling fallback every 2.5 seconds
    const interval = setInterval(async () => {
      try {
        for (const junc of junctionsRef.current) {
          const res = await fetchSimulatorDensities(junc.id);
          if (res && res.status === 'success' && res.lane_densities) {
            handleWsMessage({
              type: 'SIMULATOR_DENSITY_UPDATE',
              junction_id: junc.id,
              lane_densities: res.lane_densities,
              signals: res.signals,
              active_phase: res.active_phase,
              emergency_preemption: res.emergency_preemption,
            });
          }
        }
      } catch {
        // quiet fallback
      }
    }, 2500);

      return () => {
        ws.disconnect();
        clearInterval(interval);
      };
    }, [handleWsMessage]);

  // Real-time telemetry polling every 1 second (1000ms) to ensure live vehicle counts & signal switching
  useEffect(() => {
    const fetchTelemetry = async () => {
      try {
        let data = null;
        try {
          const res = await fetch('/api/signals/telemetry');
          data = await res.json();
        } catch {
          const res = await fetch('http://127.0.0.1:8000/api/signals/telemetry');
          data = await res.json();
        }

        if (data && data.status === 'success' && data.densities) {
          setJunctions((prev) =>
            prev.map((junc) => {
              if (junc.id !== 'JUNCTION_01' && junc.id !== 'Junction_1') return junc;
              const updatedLanes = junc.lanes.map((lane) => {
                const camId = `SIM_${junc.id}_${lane.id}`;
                const detected = data.densities[lane.id] ??
                                 data.densities[lane.id.toLowerCase()] ??
                                 data.densities[camId] ??
                                 (lane.id === 'LANE_1' ? data.densities['CAMERA_01'] : undefined);

                const sigInfo = data.signals ? (data.signals[lane.id] || data.signals[lane.id.toLowerCase()] || data.signals[camId]) : null;

                const finalDensity = detected !== undefined ? detected : (lane.detectedDensity !== undefined ? lane.detectedDensity : lane.density);

                return {
                  ...lane,
                  density: finalDensity,
                  detectedDensity: finalDensity,
                  signalState: sigInfo ? sigInfo.state : (lane.signalState || 'RED'),
                  timer: sigInfo ? (sigInfo.timer ?? 0) : lane.timer,
                };
              });

              return {
                ...junc,
                activePhase: data.active_phase || junc.activePhase,
                emergencyPreemption: data.emergency_preemption !== undefined ? data.emergency_preemption : junc.emergencyPreemption,
                lanes: updatedLanes,
              };
            })
          );
        }
      } catch (err) {
        console.error('Failed to sync signal telemetry:', err);
      }
    };

    const interval = setInterval(fetchTelemetry, 1000); // 1-second live telemetry refresh
    fetchTelemetry();
    return () => clearInterval(interval);
  }, []);

  // Toggle Traffic Signal Control for a Junction
  const toggleJunctionSignal = (jIndex) => {
    const updated = [...junctions];
    updated[jIndex].hasSignal = !updated[jIndex].hasSignal;
    setJunctions(updated);
  };

  // Add a New Junction System
  const addJunction = () => {
    const newId = `JUNCTION_0${junctions.length + 1}`;
    setJunctions([
      ...junctions,
      {
        id: newId,
        name: `City Sector ${junctions.length + 1} Junction`,
        hasSignal: true,
        activePhase: 'LANE_1',
        emergencyPreemption: false,
        lanes: [
          { id: 'LANE_1', name: 'Approach A', videoUrl: '', density: 12, signalState: 'GREEN', timer: 7, emergency: false },
          { id: 'LANE_2', name: 'Approach B', videoUrl: '', density: 6, signalState: 'RED', timer: 0, emergency: false },
        ],
      }
    ]);
  };

  // Add a Lane to a Junction
  const addLane = (jIndex) => {
    const updated = [...junctions];
    const laneCount = updated[jIndex].lanes.length + 1;
    updated[jIndex].lanes.push({
      id: `LANE_${laneCount}`,
      name: `Lane ${laneCount}`,
      videoUrl: '',
      density: 0,
      signalState: 'RED',
      timer: 0,
      emergency: false,
    });
    setJunctions(updated);
  };

  // Remove a Specific Lane from a Junction
  const removeLane = (jIndex, lIndex) => {
    const updated = [...junctions];
    if (updated[jIndex].lanes.length > 1) {
      updated[jIndex].lanes.splice(lIndex, 1);
      setJunctions(updated);
    } else {
      alert("A traffic junction must retain at least 1 lane.");
    }
  };

  // Emergency Preemption Trigger
  const toggleEmergencyPreemption = async (jIndex, lIndex) => {
    const updated = [...junctions];
    const currentStatus = updated[jIndex].lanes[lIndex].emergency || false;
    updated[jIndex].lanes.forEach((l, idx) => {
      l.emergency = (idx === lIndex) ? !currentStatus : false;
    });
    setJunctions(updated);
    await refreshAdaptiveSignals(updated);
  };

  // Route video paths, webcams, and RTSP streams through unified capture + detection pipeline
  const handleSourceSelect = async (jIndex, lIndex, rawSource, sourceType = 'file') => {
    const updated = [...junctions];
    let cleanSource = typeof rawSource === 'string' ? rawSource.replace(/\\/g, '/').trim().replace(/^["']|["']$/g, '') : String(rawSource);
    if (!cleanSource) return;

    const laneId = updated[jIndex].lanes[lIndex].id || `LANE_${lIndex + 1}`;
    const camId = `SIM_${updated[jIndex].id}_${laneId}`;
    updated[jIndex].lanes[lIndex].rawPath = cleanSource;
    updated[jIndex].lanes[lIndex].sourceType = sourceType;

    try {
      await fetch('/api/cameras/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          camera_id: camId,
          name: `${updated[jIndex].name} - ${laneId}`,
          source: cleanSource,
          source_type: sourceType,
        }),
      });
      updated[jIndex].lanes[lIndex].videoUrl = `/api/stream/${camId}?t=${Date.now()}`;
    } catch {
      if (sourceType === 'network' && cleanSource.startsWith('http')) {
        updated[jIndex].lanes[lIndex].videoUrl = cleanSource;
      } else {
        updated[jIndex].lanes[lIndex].videoUrl = `/api/stream/${camId}?t=${Date.now()}`;
      }
    }
    setJunctions(updated);
  };

  // Signal State & Heatmap Color Generator
  const getHeatmapColor = (density, hasSignal, signalState) => {
    if (!hasSignal) return '#455a64';  // Signal Disabled / Free Flow (Grey)
    if (signalState === 'GREEN') return '#00e676';
    if (signalState === 'YELLOW') return '#ffab00';
    if (signalState === 'RED') return '#ff1744';
    if (density > 25) return '#ff1744';
    if (density > 12) return '#ffab00';
    return '#00e676';
  };

  return (
    <div style={{ background: '#0a0e17', color: '#fff', padding: '24px', borderRadius: '12px', fontFamily: 'sans-serif' }}>
      {/* Header Bar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <h2 style={{ margin: 0, fontSize: '1.4rem' }}>🚦 Autonomous Density-Driven Signal Engine & Heatmap Planner</h2>
            <span style={{
              fontSize: '11px',
              fontFamily: 'monospace',
              padding: '2px 8px',
              borderRadius: '9999px',
              background: wsStatus === 'CONNECTED' ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)',
              color: wsStatus === 'CONNECTED' ? '#10B981' : '#EF4444',
              border: `1px solid ${wsStatus === 'CONNECTED' ? '#10B981' : '#EF4444'}`
            }}>
              {wsStatus === 'CONNECTED' ? '● AI STREAM LIVE' : '○ CONNECTING...'}
            </span>
          </div>
          <p style={{ color: '#8b9bb4', margin: '4px 0 0 0', fontSize: '13px' }}>
            Fully autonomous AI tracking with YOLO. Signals dynamically switch based on real-time vehicle density (7s min hold, 2s yellow transitions, instant emergency preemption).
          </p>
        </div>
        <button onClick={addJunction} style={{ background: '#2979ff', color: '#fff', border: 'none', padding: '10px 18px', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer' }}>
          + Add Traffic Light System
        </button>
      </div>

      {/* Junction List */}
      {junctions.map((junc, jIndex) => (
        <div 
          key={junc.id} 
          style={{ 
            background: '#131b2e', 
            padding: '20px', 
            borderRadius: '10px', 
            marginBottom: '24px', 
            border: junc.hasSignal ? '1px solid #1e2d4a' : '1px dashed #444' 
          }}
        >
          {/* Junction Header Controls */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', flexWrap: 'wrap', gap: '8px' }}>
            <div>
              <h3 style={{ margin: 0, color: junc.hasSignal ? '#00e676' : '#8b9bb4', display: 'flex', alignItems: 'center', gap: '8px' }}>
                {junc.name} {!junc.hasSignal && '(SIGNAL DISABLED)'}
                {junc.emergencyPreemption && (
                  <span style={{ fontSize: '11px', background: '#dc2626', color: '#fff', padding: '2px 8px', borderRadius: '4px', animation: 'pulse 1s infinite' }}>
                    🚨 EMERGENCY OVERRIDE ACTIVE
                  </span>
                )}
              </h3>
              <small style={{ color: '#64748b', fontFamily: 'monospace' }}>
                ACTIVE PHASE: <span style={{ color: '#38bdf8', fontWeight: 'bold' }}>{junc.activePhase || 'NONE'}</span>
              </small>
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <button 
                onClick={() => toggleJunctionSignal(jIndex)} 
                style={{ 
                  background: junc.hasSignal ? '#ff1744' : '#00e676', 
                  color: '#fff', 
                  border: 'none', 
                  padding: '6px 14px', 
                  borderRadius: '4px', 
                  fontWeight: 'bold', 
                  cursor: 'pointer' 
                }}
              >
                {junc.hasSignal ? 'Disable Signal' : 'Enable Signal'}
              </button>
              <button 
                onClick={() => addLane(jIndex)} 
                style={{ background: '#1c283f', color: '#fff', border: '1px solid #2979ff', padding: '6px 14px', borderRadius: '4px', cursor: 'pointer' }}
              >
                + Add Lane
              </button>
            </div>
          </div>

          {/* Lane Cards Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
            {junc.lanes.map((lane, lIndex) => {
              const currentDensity = (lane.detectedDensity !== undefined ? lane.detectedDensity : lane.density) || 0;
              const sigColor = getHeatmapColor(currentDensity, junc.hasSignal, lane.signalState);
              return (
                <div key={lane.id} style={{ background: '#0d1527', padding: '14px', borderRadius: '8px', border: `2px solid ${sigColor}` }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <b>{lane.name}</b>
                      {lane.emergency && (
                        <span style={{ background: '#ef4444', color: '#fff', fontSize: '10px', padding: '1px 5px', borderRadius: '3px', fontWeight: 'bold' }}>
                          AMBULANCE
                        </span>
                      )}
                    </div>
                    <button 
                      onClick={() => removeLane(jIndex, lIndex)} 
                      style={{ background: 'transparent', color: '#ff1744', border: 'none', cursor: 'pointer', fontWeight: 'bold' }}
                    >
                      ✕
                    </button>
                  </div>

                  {/* Signal Indicator & Timer */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '6px 0' }}>
                    <div style={{ color: sigColor, fontWeight: 'bold', fontSize: '13px' }}>
                      {!junc.hasSignal
                        ? '⚪ UNCONTROLLED'
                        : lane.signalState === 'GREEN'
                        ? '🟢 GREEN SIGNAL'
                        : lane.signalState === 'YELLOW'
                        ? '🟡 YELLOW SIGNAL (TRANSITION)'
                        : '🔴 RED SIGNAL'}
                    </div>
                    {junc.hasSignal && lane.timer > 0 && (
                      <span style={{ fontSize: '12px', fontFamily: 'monospace', color: '#94a3b8', background: '#1e293b', padding: '1px 6px', borderRadius: '4px' }}>
                        ⏳ {lane.timer}s
                      </span>
                    )}
                  </div>

                  {/* Video / Stream Viewport & Ingestion Source */}
                  {lane.videoUrl ? (
                    <div style={{ position: 'relative' }}>
                      {lane.videoUrl.includes('/api/stream/') ? (
                        <img
                          src={lane.videoUrl}
                          alt={`Lane ${lIndex + 1} Stream`}
                          style={{ width: '100%', height: '120px', objectFit: 'cover', borderRadius: '8px' }}
                        />
                      ) : (
                        <video 
                          src={lane.videoUrl} 
                          autoPlay 
                          loop 
                          muted 
                          playsInline
                          style={{ width: '100%', height: '120px', objectFit: 'cover', borderRadius: '8px' }} 
                        />
                      )}
                      <button
                        type="button"
                        onClick={() => {
                          const next = [...junctions];
                          next[jIndex].lanes[lIndex].videoUrl = '';
                          setJunctions(next);
                        }}
                        style={{
                          position: 'absolute',
                          top: '6px',
                          right: '6px',
                          background: 'rgba(15, 23, 42, 0.85)',
                          color: '#94a3b8',
                          border: '1px solid #334155',
                          borderRadius: '4px',
                          padding: '2px 8px',
                          fontSize: '10px',
                          fontFamily: 'monospace',
                          cursor: 'pointer',
                        }}
                      >
                        Change Feed
                      </button>
                    </div>
                  ) : (
                    <LaneSourceSelector
                      laneId={`SIM_${junc.id}_${lane.id}`}
                      onSourceSelect={(source, sourceType) => handleSourceSelect(jIndex, lIndex, source, sourceType)}
                    />
                  )}

                  {/* Automated Read-Only Density Bar */}
                  <div className="space-y-1.5 mt-2" style={{ marginTop: '12px' }}>
                    <div className="flex items-center justify-between text-xs font-mono" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '11px', fontFamily: 'monospace' }}>
                      <span className="text-gray-400 flex items-center gap-1" style={{ color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#10b981', display: 'inline-block' }} />
                        AI Real-Time Density:
                      </span>
                      <span className="text-white font-bold text-sm" style={{ color: '#fff', fontWeight: 'bold', fontSize: '13px' }}>
                        {currentDensity} vehicles
                      </span>
                    </div>

                    {/* Automated Progress Bar (Non-Interactive) */}
                    <div className="w-full h-2 rounded-full bg-gray-800 overflow-hidden border border-gray-700/60" style={{ width: '100%', height: '8px', borderRadius: '9999px', background: '#1f2937', overflow: 'hidden', border: '1px solid rgba(55,65,81,0.6)', marginTop: '4px' }}>
                      <div
                        className={`h-full transition-all duration-300 ease-out ${
                          lane.signalState === 'GREEN'
                            ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]'
                            : lane.signalState === 'YELLOW'
                            ? 'bg-amber-500'
                            : 'bg-rose-500'
                        }`}
                        style={{
                          height: '100%',
                          transition: 'all 0.3s ease-out',
                          background: lane.signalState === 'GREEN' ? '#10b981' : (lane.signalState === 'YELLOW' ? '#f59e0b' : '#f43f5e'),
                          width: `${Math.min(100, (currentDensity / 40) * 100)}%`,
                        }}
                      />
                    </div>
                  </div>

                  {/* Preemption Override Control */}
                  <div style={{ marginTop: '10px', display: 'flex', justifyContent: 'flex-end' }}>
                    <button
                      onClick={() => toggleEmergencyPreemption(jIndex, lIndex)}
                      style={{
                        background: lane.emergency ? '#ef4444' : '#1e293b',
                        color: lane.emergency ? '#fff' : '#94a3b8',
                        border: '1px solid #334155',
                        borderRadius: '4px',
                        padding: '4px 8px',
                        fontSize: '11px',
                        fontWeight: 'bold',
                        cursor: 'pointer'
                      }}
                    >
                      {lane.emergency ? '🚨 Clear Preemption' : '🚨 Test Preemption'}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          {/* AI Heatmap Banner */}
          <div style={{ background: '#080c14', padding: '14px', borderRadius: '6px', marginTop: '16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <h4 style={{ margin: 0, fontSize: '13px' }}>🗺️ Automated AI Signal & Density Allocation</h4>
              <span style={{ fontSize: '11px', color: '#64748b', fontFamily: 'monospace' }}>
                Density-Weighted Dynamic Cycle Control
              </span>
            </div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              {junc.lanes.map((lane) => {
                const currentDensity = (lane.detectedDensity !== undefined ? lane.detectedDensity : lane.density) || 0;
                const isGreen = lane.signalState === 'GREEN';
                const isYellow = lane.signalState === 'YELLOW';
                return (
                  <div 
                    key={lane.id} 
                    style={{ 
                      flex: 1, 
                      minWidth: '120px', 
                      padding: '10px', 
                      borderRadius: '4px', 
                      background: getHeatmapColor(currentDensity, junc.hasSignal, lane.signalState), 
                      color: isGreen || isYellow ? '#000' : '#fff', 
                      fontWeight: 'bold', 
                      textAlign: 'center',
                      fontSize: '12px',
                    }}
                  >
                    {lane.id}: {currentDensity} Veh ({!junc.hasSignal ? 'FREE FLOW' : (isGreen ? 'PROCEED (GREEN)' : (isYellow ? 'CAUTION (YELLOW)' : 'HOLD (RED)'))})
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}