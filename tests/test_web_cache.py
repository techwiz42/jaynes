import httpx

from bicameral import server
from bicameral.server import create_app

from .fakes import FakeActor, FakeObserver


async def test_the_page_is_rechecked_and_hashed_assets_are_kept(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    monkeypatch.setattr(server, "WEB_DIST", tmp_path)
    app = create_app(FakeActor(), FakeObserver())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        page = await http.get("/")
        asset = await http.get("/assets/index-abc123.js")
        api = await http.get("/healthz")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"] and "max-age=31536000" in asset.headers["cache-control"]
    assert "cache-control" not in api.headers or "immutable" not in api.headers["cache-control"]


async def test_a_missing_file_is_still_a_404(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("x")
    monkeypatch.setattr(server, "WEB_DIST", tmp_path)
    app = create_app(FakeActor(), FakeObserver())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/assets/nope.js")).status_code == 404
