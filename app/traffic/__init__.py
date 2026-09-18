"""
RoadGuardian Traffic Analytics & Signal Head Management Package.
"""

from app.traffic.zone_calibration import (
    ZoneAnchor,
    ZoneCalibrationConfig,
    CalibratedHomography,
)
from app.traffic.corridor_speed import (
    VehicleTrackState,
    CorridorSpeedDetector,
)
from app.traffic.confidence_decay import (
    ConfidenceEvaluation,
    DistanceConfidenceDecayModel,
)
from app.traffic.traffic_status_ai import (
    VehicleAnalyticsInput,
    SignalRecommendationBadge,
    TrafficStatusAIScorer,
)
from app.traffic.emergency_vehicle import (
    EmergencyDetection,
    PriorityRequestEvent,
    EmergencyVehicleTracker,
)
from app.traffic.traffic_controller import (
    SignalGroup,
    SignalGroupCreate,
    SignalStateToggle,
    AuditLogEntry,
    SignalHeadManager,
    signal_manager,
)
from app.traffic.router import traffic_router
from app.traffic.v1_router import v1_router

__all__ = [
    "ZoneAnchor",
    "ZoneCalibrationConfig",
    "CalibratedHomography",
    "VehicleTrackState",
    "CorridorSpeedDetector",
    "ConfidenceEvaluation",
    "DistanceConfidenceDecayModel",
    "VehicleAnalyticsInput",
    "SignalRecommendationBadge",
    "TrafficStatusAIScorer",
    "EmergencyDetection",
    "PriorityRequestEvent",
    "EmergencyVehicleTracker",
    "SignalGroup",
    "SignalGroupCreate",
    "SignalStateToggle",
    "AuditLogEntry",
    "SignalHeadManager",
    "signal_manager",
    "traffic_router",
    "v1_router",
]
