from schemas.shot import ShotSpec

def test_shot_model():
    s = ShotSpec(shot_id='S001', intent='establish')
    assert s.shot_id == 'S001'
