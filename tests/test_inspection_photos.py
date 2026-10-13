"""Multiple inspection photos, legacy evidence, and rejected-upload cleanup."""
import pytest
from sqlalchemy import select

from app.models import Inspection, InspectionResult
from app.services import inspections
from app.timeutil import today_local
from tests.test_web import add_user, client, csrf_of, login

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
VIDEO = bytes.fromhex("0000001866747970") + b"mp42" + b"0" * 300


def form_setup(session, sync, client):
    sync.sync_stores()
    inspections.generate_tasks(session, today_local())
    session.commit()
    add_user(session, "photo_staff", "staff")
    login(client, "photo_staff")
    inspection = session.scalar(select(Inspection).join(Inspection.template).where(Inspection.template.has(code="store_daily")))
    data = {"csrf": csrf_of(client, f"/inspections/{inspection.id}")}
    for item in inspection.template.items:
        if item.kind == "number":
            data[f"v_{item.id}"] = "24"
        elif item.kind == "check":
            data[f"p_{item.id}"] = "pass"
    video_item = next(item for item in inspection.template.items if item.kind == "video")
    return inspection, data, video_item


def test_multiple_photos_saved_per_item_and_displayed(session, sync, client, cfg):
    inspection, data, video = form_setup(session, sync, client)
    first = inspection.template.items[0]
    temperature = next(item for item in inspection.template.items if item.kind == "number")
    data[f"p_{first.id}"] = "fail"
    data[f"n_{first.id}"] = "Door needs cleaning"
    files = [(f"f_{first.id}", (f"photo-{n}.png", PNG, "image/png")) for n in range(3)]
    files += [(f"f_{temperature.id}", ("temperature.png", PNG, "image/png")),
              (f"f_{video.id}", ("walk.mp4", VIDEO, "video/mp4")),
              (f"photos_{video.id}", ("walk-photo.png", PNG, "image/png"))]
    response = client.post(f"/inspections/{inspection.id}/submit", data=data, files=files)
    assert response.status_code == 303 and "ok=" in response.headers["location"]
    session.expire_all()
    results = {result.item_id: result for result in session.get(Inspection, inspection.id).results}
    assert len(results[first.id].photos) == 3 and len(set(results[first.id].photos)) == 3
    assert results[first.id].photo_path == results[first.id].photos[0]
    assert len(results[temperature.id].photos) == 1
    assert len(results[video.id].photos) == 1 and results[video.id].photo_path.endswith(".mp4")
    html = client.get(f"/inspections/{inspection.id}").text
    for result in results.values():
        for path in result.photos:
            assert (cfg.upload_dir / path).exists()
            assert f'src="/uploads/{path}"' in html
            assert client.get(f"/uploads/{path}").status_code == 200
    assert f'src="/uploads/{results[video.id].photo_path}"' in html
    before = set(cfg.upload_dir.rglob("*.*"))
    repeat = client.post(f"/inspections/{inspection.id}/submit", data=data, files=files)
    assert "err=" in repeat.headers["location"]
    assert set(cfg.upload_dir.rglob("*.*")) == before


@pytest.mark.parametrize("bad_kind, expected", [("invalid", 400), ("large", 413), ("no_video", 303), ("image_as_video", 400)])
def test_invalid_submission_keeps_no_partial_photos(session, sync, client, cfg, bad_kind, expected):
    inspection, data, video = form_setup(session, sync, client)
    first = inspection.template.items[0]
    files = [(f"f_{first.id}", ("good.png", PNG, "image/png"))]
    if bad_kind in ("invalid", "large"):
        content = b"not a photo" if bad_kind == "invalid" else PNG + b"0" * (8 * 1024 * 1024)
        files.append((f"f_{first.id}", ("bad.png", content, "image/png")))
    elif bad_kind == "image_as_video":
        files.append((f"f_{video.id}", ("wrong.png", PNG, "image/png")))
    before = set(cfg.upload_dir.rglob("*.*"))
    response = client.post(f"/inspections/{inspection.id}/submit", data=data, files=files)
    assert response.status_code == expected
    if expected == 303:
        assert "err=" in response.headers["location"]
    assert set(cfg.upload_dir.rglob("*.*")) == before
    session.expire_all()
    assert not session.get(Inspection, inspection.id).results


def test_photo_controls_for_every_item_and_chinese_labels(session, sync, client):
    inspection, _, video = form_setup(session, sync, client)
    html = client.get(f"/inspections/{inspection.id}").text
    assert html.count('data-photos') == len(inspection.template.items) - 1  # the walkthrough item takes video only
    assert html.count(' multiple ') == len(inspection.template.items) - 1
    assert f'name="photos_{video.id}"' not in html
    assert "Add photos for the manager to review." in html and "Daily store check" in html
    assert "Mark all pass" not in html and 'name="n_' not in html and 'class="pf"' not in html  # photo-only: no pass/fail, no comment
    client.cookies.set("lang", "zh")
    html = client.get(f"/inspections/{inspection.id}").text
    assert "请添加照片供经理查看" in html and "门店每日巡检" in html


def test_old_single_photo_still_displays(session, sync, client):
    inspection, _, _ = form_setup(session, sync, client)
    inspection.status = "completed"
    result = InspectionResult(item_id=inspection.template.items[0].id, passed=True, photo_path="2026/10/old.png")
    inspection.results.append(result)
    session.commit()
    html = client.get(f"/inspections/{inspection.id}").text
    assert result.photos == ["2026/10/old.png"]
    assert 'src="/uploads/2026/10/old.png"' in html
