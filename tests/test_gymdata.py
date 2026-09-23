import gymdata as gd


def _sample():
    return gd.bundle(
        [gd.workout("Trenn A", "märkus õäöü", [
            gd.plan(0, "Rowing With Rowing Ergometer", seconds=300),
            gd.plan(1, "Barbell Squat", 3, 8, 120, ["6-10 reps", "50 kg"]),
        ])],
        grp="test",
    )


def test_roundtrip(tmp_path):
    obj = _sample()
    p = gd.write(obj, tmp_path)
    assert p.name.startswith("wo_") and p.suffix == ".gymdata"
    assert gd.read(p) == obj
    assert gd.encode(gd.read(p)) == p.read_bytes()


def test_header_layout():
    raw = gd.encode(_sample())
    size = int(raw[:12].rstrip(b"\0"), 8)
    assert raw[12:19] == b"wo.json"
    assert raw[113:114] == b"{"
    assert len(raw) == 113 + size


def test_plan_shapes():
    cardio = gd.plan(0, "Walking On Treadmill", seconds=600)
    assert cardio["sets"] == [{"reps": 1, "rest": 0, "type": 0, "qnty": 0, "time": 600}]
    squat = gd.plan(1, "Barbell Squat", 3, 8, 120)
    assert squat["exercise"] == 37 and squat["chained"] == "false"
    assert len(squat["sets"]) == 3 and squat["sets"][0]["rest"] == 120
