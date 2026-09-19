from backend.geometry import box_distance, calculate_relationships, contains, distance, nearest, trajectory_clearance, trajectory_intersections, trajectory_length
from backend.models import BoundingBox, Geometry, PatientObject


def object_with_box(identifier: str, label: str, minimum, maximum) -> PatientObject:
    box = BoundingBox(min=minimum, max=maximum)
    centroid = tuple((minimum[i] + maximum[i]) / 2 for i in range(3))
    return PatientObject(id=identifier, type="anatomy", label=label, geometry=Geometry(centroid=centroid, bounding_box=box))


def test_geometry_primitives_are_deterministic():
    assert distance((0, 0, 0), (3, 4, 0)) == 5
    inner = BoundingBox(min=(1, 1, 1), max=(2, 2, 2))
    outer = BoundingBox(min=(0, 0, 0), max=(4, 4, 4))
    assert contains(outer, inner)
    assert box_distance(outer, inner) == 0


def test_relationships_and_nearest_use_geometry():
    nodule = object_with_box("nodule", "Nodule", (10, 10, 10), (12, 12, 12))
    lung = object_with_box("lung", "Right lung", (0, 0, 0), (30, 30, 30))
    artery = object_with_box("artery", "Artery", (16, 10, 10), (18, 12, 12))
    relationships = calculate_relationships([nodule, lung, artery], "study-1", near_mm=10)
    assert any(item.type == "inside" and item.source_object_id == "nodule" for item in relationships)
    assert any(item.type == "near" and item.target_object_id == "artery" for item in relationships)
    assert nearest([nodule, lung, artery], "nodule")[0]["object_id"] == "artery"


def test_trajectory_length_and_clearance():
    vessel = object_with_box("vessel", "Vessel", (50, 50, 50), (60, 60, 60))
    assert trajectory_length((0, 0, 0), (3, 4, 0)) == 5
    clearance = trajectory_clearance((0, 0, 0), (100, 100, 100), vessel)
    assert clearance is not None and clearance >= 0


def test_trajectory_intersections_use_patient_object_geometry():
    vessel = object_with_box("vessel", "Vessel", (4, -1, -1), (6, 1, 1))
    assert trajectory_intersections((0, 0, 0), (10, 0, 0), [vessel]) == [{"object_id": "vessel", "label": "Vessel"}]


def test_trajectory_clearance_is_not_sampled():
    vessel = object_with_box("vessel", "Vessel", (5, 5, -1), (6, 6, 1))
    clearance = trajectory_clearance((0, 0, 0), (10, 0, 0), vessel)
    assert clearance == 5.0
