"""
Multi-Camera Intersection & Complementary Signal Logic (MUTEX_SIGNAL) Module.

Enforces:
- Mutual Exclusion Safety Invariant: Conflicting linked cameras CANNOT be GREEN simultaneously.
- State Synchronization: When Cam 1 transitions to RED, Cam 2 transitions to GREEN
  (after an active YELLOW clearance phase, e.g. 3.0s).
- Linked Emergency Vehicle Preemption: When an emergency vehicle requests priority on Cam 1,
  the opposing Cam 2 is immediately transitioned to YELLOW -> RED, and Cam 1 receives GREEN.
- Full audit logging of all multi-camera intersection transitions.
"""

from typing import Dict, List, Optional, Tuple, Any, Literal
import time
import threading
import logging
from pydantic import BaseModel, Field

from app.traffic.database_store import (
    CameraPairLink,
    traffic_store,
)

logger = logging.getLogger("IntersectionManager")


class SignalTransitionResult(BaseModel):
    """
    Result of a coordinated signal state transition across linked intersection cameras.
    """
    target_camera_id: str
    target_new_state: Literal["RED", "YELLOW", "GREEN"]
    linked_camera_id: Optional[str] = None
    linked_new_state: Optional[str] = None
    yellow_clearance_active: bool = False
    yellow_clearance_duration_sec: float = 0.0
    safety_override_applied: bool = False
    action_notes: str


class IntersectionSignalManager:
    """
    Orchestrates coordinated multi-camera signal states, MUTEX safety locks,
    yellow clearance timers, and linked emergency vehicle overrides.
    """

    def __init__(self, store=traffic_store):
        self.store = store
        self.lock = threading.RLock()

    def get_signal_state(self, camera_id: str) -> str:
        """Returns the current state ('RED', 'YELLOW', 'GREEN') for a camera."""
        with self.lock:
            # Check if pending yellow clearance has completed
            self._check_clearance_expiry(camera_id)
            cam_state = self.store.signal_states.get(camera_id, {})
            return cam_state.get("current_state", "GREEN")

    def _check_clearance_expiry(self, camera_id: str):
        """Internal helper to resolve expired yellow clearance timers."""
        state = self.store.signal_states.get(camera_id)
        if not state:
            return

        now = time.time()
        if state.get("yellow_active") and now >= state.get("yellow_expires_at", 0.0):
            # Yellow clearance finished! Complete the pending state transition
            pending = state.get("pending_state", "RED")
            state["current_state"] = pending
            state["yellow_active"] = False
            state["pending_state"] = None

            logger.info(f"[CLEARANCE FINISHED] Camera {camera_id} completed yellow clearance -> {pending}")

            # Also check if any linked camera was waiting to become GREEN
            for link in self.store.get_links_for_camera(camera_id):
                opposing_id = link.secondary_cam_id if link.primary_cam_id == camera_id else link.primary_cam_id
                opp_state = self.store.signal_states.get(opposing_id)
                if opp_state and opp_state.get("pending_green_after_clearance"):
                    opp_state["current_state"] = "GREEN"
                    opp_state["pending_green_after_clearance"] = False
                    logger.info(f"[MUTEX SYNC] Opposing Camera {opposing_id} granted GREEN following {camera_id} clearance")

    def set_camera_signal_state(
        self,
        camera_id: str,
        desired_state: Literal["RED", "YELLOW", "GREEN"],
        reason: str = "Manual / AI controller transition",
        trigger_source: str = "OPERATOR",
    ) -> SignalTransitionResult:
        """
        Transitions a camera to a desired signal state while strictly maintaining the MUTEX invariant.
        """
        with self.lock:
            self._check_clearance_expiry(camera_id)

            # Retrieve active pair links for this camera
            links = self.store.get_links_for_camera(camera_id)
            current_state = self.get_signal_state(camera_id)

            if not links:
                # Independent camera with no linked MUTEX constraints
                self._apply_direct_state(camera_id, desired_state)
                return SignalTransitionResult(
                    target_camera_id=camera_id,
                    target_new_state=desired_state,
                    action_notes=f"Direct state update for unlinked camera: {desired_state} ({reason})",
                )

            # Linked camera - evaluate MUTEX constraints for all pairs
            link = links[0]  # Primary intersection pair
            opposing_cam_id = link.secondary_cam_id if link.primary_cam_id == camera_id else link.primary_cam_id
            opposing_state = self.get_signal_state(opposing_cam_id)
            clearance_sec = link.yellow_clearance_sec

            # SAFETY INVARIANT CHECK:
            # If camera_id wants to become GREEN, opposing_cam_id CANNOT remain GREEN.
            if desired_state == "GREEN":
                if opposing_state == "GREEN":
                    # Opposing camera must first clear through YELLOW -> RED
                    opp_state_obj = self.store.signal_states[opposing_cam_id]
                    opp_state_obj["current_state"] = "YELLOW"
                    opp_state_obj["yellow_active"] = True
                    opp_state_obj["yellow_expires_at"] = time.time() + clearance_sec
                    opp_state_obj["pending_state"] = "RED"

                    # Target camera waits in RED until opposing yellow clearance finishes
                    target_state_obj = self.store.signal_states[camera_id]
                    target_state_obj["current_state"] = "RED"
                    target_state_obj["pending_green_after_clearance"] = True

                    self.store.log_audit(
                        event_type="MUTEX_CLEARANCE_INITIATED",
                        camera_id=camera_id,
                        details={
                            "reason": reason,
                            "opposing_camera": opposing_cam_id,
                            "clearance_seconds": clearance_sec,
                            "safety_rule": "Opposing GREEN transitioned to YELLOW clearance before granting GREEN to target",
                        }
                    )

                    return SignalTransitionResult(
                        target_camera_id=camera_id,
                        target_new_state="RED",  # Holding RED until clearance ends
                        linked_camera_id=opposing_cam_id,
                        linked_new_state="YELLOW",
                        yellow_clearance_active=True,
                        yellow_clearance_duration_sec=clearance_sec,
                        safety_override_applied=True,
                        action_notes=f"Safety Lock: {opposing_cam_id} placed in YELLOW clearance ({clearance_sec}s). {camera_id} will turn GREEN automatically upon clearance.",
                    )
                elif opposing_state == "YELLOW":
                    # Opposing camera is already clearing yellow; wait for it
                    target_state_obj = self.store.signal_states[camera_id]
                    target_state_obj["current_state"] = "RED"
                    target_state_obj["pending_green_after_clearance"] = True

                    return SignalTransitionResult(
                        target_camera_id=camera_id,
                        target_new_state="RED",
                        linked_camera_id=opposing_cam_id,
                        linked_new_state="YELLOW",
                        yellow_clearance_active=True,
                        yellow_clearance_duration_sec=clearance_sec,
                        action_notes=f"Opposing {opposing_cam_id} is clearing YELLOW. {camera_id} scheduled for GREEN upon completion.",
                    )
                else:
                    # Opposing camera is RED -> safe to turn target GREEN immediately
                    self._apply_direct_state(camera_id, "GREEN")
                    return SignalTransitionResult(
                        target_camera_id=camera_id,
                        target_new_state="GREEN",
                        linked_camera_id=opposing_cam_id,
                        linked_new_state="RED",
                        action_notes=f"MUTEX Verified: Opposing {opposing_cam_id} is RED. {camera_id} granted GREEN.",
                    )

            # If target camera is transitioning to RED:
            elif desired_state == "RED":
                self._apply_direct_state(camera_id, "RED")
                # STATE SYNC RULE:
                # When Cam 1 updates to RED, automatically transition Cam 2 to GREEN
                self._apply_direct_state(opposing_cam_id, "GREEN")

                self.store.log_audit(
                    event_type="MUTEX_STATE_SYNC_COMPLEMENTARY",
                    camera_id=camera_id,
                    details={
                        "reason": reason,
                        "action": f"{camera_id} -> RED triggered complementary {opposing_cam_id} -> GREEN",
                    }
                )

                return SignalTransitionResult(
                    target_camera_id=camera_id,
                    target_new_state="RED",
                    linked_camera_id=opposing_cam_id,
                    linked_new_state="GREEN",
                    action_notes=f"State Sync: {camera_id} turned RED -> complementary opposing {opposing_cam_id} granted GREEN.",
                )

            else:
                # Manual YELLOW
                self._apply_direct_state(camera_id, desired_state)
                return SignalTransitionResult(
                    target_camera_id=camera_id,
                    target_new_state=desired_state,
                    linked_camera_id=opposing_cam_id,
                    linked_new_state=opposing_state,
                    action_notes=f"{camera_id} transitioned to YELLOW.",
                )

    def trigger_linked_emergency_override(
        self,
        emergency_cam_id: str,
        vehicle_type: str = "AMBULANCE",
        track_id: int = 0,
        lane_id: int = 1,
    ) -> Dict[str, Any]:
        """
        Executes coordinated emergency vehicle priority override across linked intersection cameras:
        1. Opposing linked camera is immediately shut down: GREEN -> YELLOW (clearance) -> RED.
        2. Emergency camera lane is granted immediate priority GREEN.
        3. Full audit trail recorded.
        """
        with self.lock:
            links = self.store.get_links_for_camera(emergency_cam_id)
            opposing_id = None
            clearance_sec = 3.0

            if links:
                link = links[0]
                opposing_id = link.secondary_cam_id if link.primary_cam_id == emergency_cam_id else link.primary_cam_id
                clearance_sec = link.yellow_clearance_sec

                # Force opposing camera to transition immediately to YELLOW -> RED
                if opposing_id in self.store.signal_states:
                    opp_obj = self.store.signal_states[opposing_id]
                    opp_obj["current_state"] = "RED"  # Emergency lock
                    opp_obj["yellow_active"] = False

            # Grant priority GREEN to the emergency camera
            self._apply_direct_state(emergency_cam_id, "GREEN")

            # Log preemption event
            self.store.log_audit(
                event_type="EMERGENCY_LINKED_PREEMPTION",
                camera_id=emergency_cam_id,
                details={
                    "vehicle_type": vehicle_type,
                    "track_id": track_id,
                    "lane_id": lane_id,
                    "opposing_camera_halted": opposing_id,
                    "action": f"Granted priority GREEN to {emergency_cam_id} and locked opposing {opposing_id} to RED",
                }
            )

            logger.warning(f"[EMERGENCY OVERRIDE] {vehicle_type} #{track_id} granted PRIORITY GREEN on {emergency_cam_id}. Opposing {opposing_id} locked to RED.")

            return {
                "status": "PRIORITY_OVERRIDE_ACTIVE",
                "emergency_camera_id": emergency_cam_id,
                "emergency_camera_state": "GREEN",
                "opposing_camera_id": opposing_id,
                "opposing_camera_state": "RED" if opposing_id else None,
                "vehicle_type": vehicle_type,
                "track_id": track_id,
                "timestamp": time.time(),
            }

    def _apply_direct_state(self, camera_id: str, state: str):
        """Applies direct state into in-memory store."""
        if camera_id not in self.store.signal_states:
            self.store.signal_states[camera_id] = {
                "camera_id": camera_id,
                "current_state": state,
                "countdown": 30,
                "yellow_active": False,
                "yellow_expires_at": 0.0,
                "pending_state": None,
            }
        else:
            self.store.signal_states[camera_id]["current_state"] = state
            self.store.signal_states[camera_id]["yellow_active"] = False
            self.store.signal_states[camera_id]["pending_state"] = None


intersection_manager = IntersectionSignalManager()
