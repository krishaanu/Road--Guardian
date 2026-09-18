"""
Signal Head Management & Emergency Vehicle Override Controller for RoadGuardian ITS.

Manages signal groups (associating lanes to signal phases), supports manual light toggling,
processes PRIORITY_REQUEST events from emergency vehicles, resolves multi-group conflicts
via earliest-detection priority, and logs all preemption and state transitions into an audit log.
"""

from typing import Dict, List, Optional, Literal, Any
import time
import uuid
import logging
from pydantic import BaseModel, Field
from app.traffic.emergency_vehicle import EmergencyDetection, PriorityRequestEvent

logger = logging.getLogger("TrafficController")


class SignalGroup(BaseModel):
    """
    Represents a coordinated traffic signal phase governing specific roadway lanes.
    """
    group_id: str = Field(..., example="SG_NORTH_MAIN")
    name: str = Field(..., example="Northbound Mainline Through Movement")
    assigned_lanes: List[int] = Field(..., min_length=1, description="List of 1-indexed lane numbers")
    current_state: Literal["RED", "YELLOW", "GREEN"] = "GREEN"
    min_green_sec: int = 15
    max_green_sec: int = 60
    current_cycle_countdown: int = 30
    active_override: bool = False
    override_expires_at: Optional[float] = None
    last_state_change: float = Field(default_factory=time.time)


class SignalGroupCreate(BaseModel):
    """
    Payload for creating or updating a Signal Group configuration.
    """
    group_id: str
    name: str
    assigned_lanes: List[int]
    initial_state: Literal["RED", "YELLOW", "GREEN"] = "GREEN"
    min_green_sec: int = 15
    max_green_sec: int = 60


class SignalStateToggle(BaseModel):
    """
    Payload for manually overriding a signal group's state.
    """
    target_state: Literal["RED", "YELLOW", "GREEN"]
    hold_duration_sec: Optional[int] = Field(None, description="Optional manual hold duration before returning to auto cycle")
    operator_id: Optional[str] = "OPERATOR_ADMIN"
    reason: Optional[str] = "Manual control room command"


class AuditLogEntry(BaseModel):
    """
    Persistent audit record capturing all signal overrides, conflict resolutions, and state transitions.
    """
    audit_id: str = Field(default_factory=lambda: f"AUDIT_{int(time.time()*1000)}_{uuid.uuid4().hex[:4]}")
    timestamp: float = Field(default_factory=time.time)
    event_type: Literal[
        "MANUAL_TOGGLE",
        "EMERGENCY_PRIORITY_OVERRIDE",
        "MULTI_GROUP_CONFLICT_RESOLVED",
        "LANE_ASSIGNMENT",
        "OVERRIDE_EXPIRED"
    ]
    signal_group_id: str
    previous_state: str
    new_state: str
    trigger_reason: str
    details: Dict[str, Any] = Field(default_factory=dict)


class SignalHeadManager:
    """
    Central controller for Signal Group state transitions, emergency preemption,
    conflict arbitration, and audit trailing.
    """

    def __init__(self):
        self.signal_groups: Dict[str, SignalGroup] = {}
        self.audit_log: List[AuditLogEntry] = []
        self.pending_priority_requests: List[PriorityRequestEvent] = []

        # Initialize standard default signal groups for NH-44 Expressway
        self._init_default_groups()

    def _init_default_groups(self):
        """Pre-populates standard expressway and arterial signal groups."""
        self.register_signal_group(SignalGroupCreate(
            group_id="SG_MAIN_NORTH",
            name="NH-44 Northbound Mainline (Lanes 1-2)",
            assigned_lanes=[1, 2],
            initial_state="GREEN",
            min_green_sec=20,
            max_green_sec=60,
        ))
        self.register_signal_group(SignalGroupCreate(
            group_id="SG_TURNING_SLIPWAY",
            name="NH-44 Slipway / Ramp Merge (Lane 3)",
            assigned_lanes=[3],
            initial_state="RED",
            min_green_sec=10,
            max_green_sec=30,
        ))

    def register_signal_group(self, config: SignalGroupCreate) -> SignalGroup:
        """
        Creates or updates a signal group with assigned roadway lanes.
        """
        sg = SignalGroup(
            group_id=config.group_id,
            name=config.name,
            assigned_lanes=config.assigned_lanes,
            current_state=config.initial_state,
            min_green_sec=config.min_green_sec,
            max_green_sec=config.max_green_sec,
        )
        self.signal_groups[config.group_id] = sg

        self._record_audit(
            event_type="LANE_ASSIGNMENT",
            signal_group_id=config.group_id,
            previous_state=config.initial_state,
            new_state=config.initial_state,
            trigger_reason=f"Registered signal group with lanes {config.assigned_lanes}",
            details={"assigned_lanes": config.assigned_lanes, "name": config.name},
        )
        return sg

    def set_signal_state(
        self,
        group_id: str,
        target_state: Literal["RED", "YELLOW", "GREEN"],
        reason: str = "Manual toggle",
        hold_duration_sec: Optional[int] = None,
        operator_id: Optional[str] = None,
    ) -> SignalGroup:
        """
        Manually sets or toggles the state of a Signal Group.
        """
        if group_id not in self.signal_groups:
            raise KeyError(f"Signal Group '{group_id}' does not exist.")

        sg = self.signal_groups[group_id]
        prev_state = sg.current_state

        sg.current_state = target_state
        sg.last_state_change = time.time()
        if hold_duration_sec:
            sg.active_override = True
            sg.override_expires_at = time.time() + hold_duration_sec
        else:
            sg.active_override = False
            sg.override_expires_at = None

        self._record_audit(
            event_type="MANUAL_TOGGLE",
            signal_group_id=group_id,
            previous_state=prev_state,
            new_state=target_state,
            trigger_reason=reason,
            details={"operator_id": operator_id, "hold_duration_sec": hold_duration_sec},
        )
        logger.info(f"[SIGNAL HEAD] Group {group_id} transitioned: {prev_state} -> {target_state} ({reason})")
        return sg

    def find_group_by_lane(self, lane_id: int) -> Optional[SignalGroup]:
        """
        Finds the Signal Group currently governing a given roadway lane.
        """
        for sg in self.signal_groups.values():
            if lane_id in sg.assigned_lanes:
                return sg
        return None

    def process_emergency_detection(
        self,
        emergency_detection: EmergencyDetection,
    ) -> Optional[PriorityRequestEvent]:
        """
        Evaluates an emergency vehicle detection against its assigned signal group.
        If the signal group is currently RED, generates a PRIORITY_REQUEST and triggers preemption.
        """
        sg = self.find_group_by_lane(emergency_detection.lane_id)
        if not sg:
            logger.warning(f"No signal group assigned to lane {emergency_detection.lane_id}")
            return None

        # If current light is not GREEN (i.e. RED or YELLOW), generate priority preemption request
        if sg.current_state in ["RED", "YELLOW"]:
            event = PriorityRequestEvent(
                emergency_detection=emergency_detection,
                target_signal_group_id=sg.group_id,
                current_signal_state=sg.current_state,
                action_recommended="EARLY_GREEN",
                timestamp=emergency_detection.timestamp,
                notes=f"{emergency_detection.vehicle_type} detected approaching in {sg.current_state} lane {emergency_detection.lane_id}",
            )
            self.pending_priority_requests.append(event)
            return event
        elif sg.current_state == "GREEN":
            # If already green, recommend extending green duration so it does not turn red mid-transit
            event = PriorityRequestEvent(
                emergency_detection=emergency_detection,
                target_signal_group_id=sg.group_id,
                current_signal_state=sg.current_state,
                action_recommended="EXTEND_GREEN",
                timestamp=emergency_detection.timestamp,
                notes=f"{emergency_detection.vehicle_type} traversing GREEN lane {emergency_detection.lane_id}. Extending phase.",
            )
            return event

        return None

    def resolve_conflicts_and_apply_preemption(
        self,
        requests: Optional[List[PriorityRequestEvent]] = None,
    ) -> Dict[str, Any]:
        """
        Resolves multi-group emergency preemption conflicts using Earliest-Detection Priority (FCFS)
        and applies signal preemption overrides.
        """
        active_requests = requests if requests is not None else list(self.pending_priority_requests)
        if not active_requests:
            return {"status": "NO_REQUESTS", "active_overrides": [], "overrides_applied": []}

        # Multi-group conflict resolution via Earliest-Detection Priority (FCFS timestamp)
        sorted_requests = sorted(active_requests, key=lambda req: req.emergency_detection.timestamp)

        # Winner is the request with the earliest detection timestamp
        winner = sorted_requests[0]
        competing = sorted_requests[1:]

        target_sg_id = winner.target_signal_group_id
        winner_sg = self.signal_groups.get(target_sg_id)

        overrides_applied = []

        if winner_sg:
            prev_state = winner_sg.current_state
            # Apply preemption: switch winner group to GREEN
            winner_sg.current_state = "GREEN"
            winner_sg.active_override = True
            winner_sg.override_expires_at = time.time() + 25.0  # 25s emergency corridor clear window
            winner_sg.last_state_change = time.time()

            # Record conflict resolution if competing emergency requests existed
            if competing:
                competing_summary = [
                    {"track_id": c.emergency_detection.track_id, "group": c.target_signal_group_id, "ts": c.emergency_detection.timestamp}
                    for c in competing
                ]
                self._record_audit(
                    event_type="MULTI_GROUP_CONFLICT_RESOLVED",
                    signal_group_id=target_sg_id,
                    previous_state=prev_state,
                    new_state="GREEN",
                    trigger_reason=f"Multi-group conflict: Winner {winner.emergency_detection.vehicle_type} (Track #{winner.emergency_detection.track_id}) had earliest detection.",
                    details={"competing_requests": competing_summary, "winner_timestamp": winner.emergency_detection.timestamp},
                )
            else:
                self._record_audit(
                    event_type="EMERGENCY_PRIORITY_OVERRIDE",
                    signal_group_id=target_sg_id,
                    previous_state=prev_state,
                    new_state="GREEN",
                    trigger_reason=f"Emergency priority override for {winner.emergency_detection.vehicle_type} (Track #{winner.emergency_detection.track_id}) in Lane {winner.emergency_detection.lane_id}",
                    details={"emergency_detection": winner.emergency_detection.model_dump()},
                )

            overrides_applied.append({
                "group_id": target_sg_id,
                "state": "GREEN",
                "action": winner.action_recommended,
                "priority_vehicle": winner.emergency_detection.vehicle_type,
                "track_id": winner.emergency_detection.track_id,
            })

        # Clear handled requests from pending buffer
        self.pending_priority_requests.clear()

        return {
            "status": "PREEMPTION_ACTIVE",
            "winner_request": winner.model_dump(),
            "conflicts_count": len(competing),
            "overrides_applied": overrides_applied,
            "active_overrides": overrides_applied,
        }

    def _record_audit(
        self,
        event_type: Any,
        signal_group_id: str,
        previous_state: str,
        new_state: str,
        trigger_reason: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Appends an entry to the in-memory audit log and logs it."""
        entry = AuditLogEntry(
            event_type=event_type,
            signal_group_id=signal_group_id,
            previous_state=previous_state,
            new_state=new_state,
            trigger_reason=trigger_reason,
            details=details or {},
        )
        self.audit_log.insert(0, entry)
        if len(self.audit_log) > 200:
            self.audit_log.pop()


# Global singleton instance for the application
signal_manager = SignalHeadManager()
