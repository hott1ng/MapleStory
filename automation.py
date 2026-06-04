from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class ActionIntent:
    action: str
    reason: str
    target: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def choose_action_intent(
    frame_payload: dict[str, Any],
    attack_range_x: float = 120,
    same_layer_range_y: float = 80,
) -> ActionIntent:
    detections = frame_payload.get("detections") or []
    player_center = frame_payload.get("player_center") or []

    if len(player_center) < 2:
        return ActionIntent("wait", "缺少人物估算中心，等待下一帧。")

    if not detections:
        return ActionIntent("wait", "当前帧没有检测到目标。")

    player_x, player_y = float(player_center[0]), float(player_center[1])
    target = min(detections, key=lambda detection: float(detection.get("distance_from_center", 0)))
    center = target.get("center") or []
    if len(center) < 2:
        return ActionIntent("wait", "目标缺少中心坐标。", target)

    target_x, target_y = float(center[0]), float(center[1])
    dx = target_x - player_x
    dy = target_y - player_y

    if abs(dy) > same_layer_range_y:
        return ActionIntent("wait", f"目标不在同层，dy={dy:.1f}。", target)

    if abs(dx) <= attack_range_x:
        return ActionIntent("attack", f"目标进入攻击范围，dx={dx:.1f}。", target)

    if dx > 0:
        return ActionIntent("move_right", f"目标在右侧，dx={dx:.1f}。", target)

    return ActionIntent("move_left", f"目标在左侧，dx={dx:.1f}。", target)
