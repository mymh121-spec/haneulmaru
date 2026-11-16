"""Optional direction-only bridge to the existing coordinate implementation."""
from photo_color_v1.drone_coordinates import GyroPose, NorthCoordinateSession


class CoordinateAdapter:
    def __init__(self, model, projection):
        self.model, self.projection = model, projection
        self.session = None

    def record(self, packet, state):
        if self.session != packet["session_id"]:
            self.pose = GyroPose(self.model)
            self.coordinates = NorthCoordinateSession(self.projection)
            self.session = packet["session_id"]
        normalized = dict(state)
        if normalized.get("source") in ("USB/Wi-Fi", "recorded_imu"):
            normalized["source"] = "USB"
        pose = self.pose.consume(normalized)
        # Preserve provenance and uncertainty; never manufacture range or position.
        pose["transport_source"] = state.get("source")
        width, height = packet["image_size_px"]
        result = self.coordinates.record(packet["tracks"], width, height, pose,
                                         captured_at_ms=packet["frame_timestamp_ms"])
        result["timestamp_basis"] = packet["timestamp_basis"]
        result["tracking_session_id"] = self.session
        return result
