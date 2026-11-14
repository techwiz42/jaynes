import os

import pytest

from bicameral.tools.base import ToolRegistry
from bicameral.tools.docs import DocIndex, RetrieveDocs


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "deploy.md").write_text("# Deploying\n\nRun the update script on melville.\n\n## Rollback\n\nRun the script again with an earlier sha.\n")
    (tmp_path / "cooking.txt").write_text("Boil the pasta for ten minutes.\n\nAdd salt to the water.\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / ".hidden.md").write_text("# hidden\nshould not be indexed")
    return tmp_path


async def search(root, query, k=4):
    return await ToolRegistry([RetrieveDocs(DocIndex(str(root)))]).run("retrieve_docs", {"query": query, "k": k})


async def test_the_best_matching_passage_comes_first_with_its_path_and_heading(docs):
    result = await search(docs, "how do I roll back a deploy with an earlier sha")
    assert result.ok
    first = result.content.split("\n\n---\n\n")[0]
    assert "source_path: notes/deploy.md" in first and "heading: Rollback" in first and "earlier sha" in first


async def test_excerpts_are_verbatim_from_the_file(docs):
    result = await search(docs, "pasta")
    excerpt = result.content.split("excerpt:\n", 1)[1].strip()
    assert excerpt in (docs / "cooking.txt").read_text()


async def test_no_match_says_so(docs):
    assert (await search(docs, "kubernetes")).content == "(no matching passages)"


async def test_only_markdown_and_text_files_are_read_and_hidden_ones_are_not(docs):
    index = DocIndex(str(docs))
    assert {c.path for c in index._chunks} == {os.path.join("notes", "deploy.md"), "cooking.txt"}


async def test_a_symlink_out_of_the_directory_is_not_followed(docs, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.md").write_text("# Secret\n\nthe launch code is 0000")
    os.symlink(outside / "secret.md", docs / "link.md")
    os.symlink(outside, docs / "linkdir")
    result = await search(docs, "launch code")
    assert "launch code" not in result.content and result.content == "(no matching passages)"


async def test_new_files_are_picked_up_without_a_restart(docs):
    index = DocIndex(str(docs))
    registry = ToolRegistry([RetrieveDocs(index)])
    assert (await registry.run("retrieve_docs", {"query": "zebra"})).content == "(no matching passages)"
    (docs / "zoo.md").write_text("# Zoo\n\nThe zebra is striped.\n")
    assert "zoo.md" in (await registry.run("retrieve_docs", {"query": "zebra"})).content


async def test_a_missing_directory_gives_an_empty_index_not_a_crash(tmp_path):
    assert (await search(tmp_path / "nope", "anything")).ok


async def test_k_is_limited_and_validated(docs):
    registry = ToolRegistry([RetrieveDocs(DocIndex(str(docs)))])
    assert not (await registry.run("retrieve_docs", {"query": "x", "k": 99})).ok
    assert not (await registry.run("retrieve_docs", {"query": ""})).ok


async def test_the_source_directory_is_never_written(docs):
    before = sorted((p, p.stat().st_mtime_ns) for p in docs.rglob("*") if p.is_file())
    await search(docs, "pasta")
    assert before == sorted((p, p.stat().st_mtime_ns) for p in docs.rglob("*") if p.is_file())
