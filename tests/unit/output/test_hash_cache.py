"""Tests for hash caching optimization in Output.

The hash cache prevents redundant rebuilding of directory trees when
multiple stages share the same dependency. See issue #9033.
"""


def test_hash_cache_prevents_rebuild(tmp_dir, dvc, mocker):
    """Test that getting hash for same path uses cache instead of rebuilding."""
    from dvc.output import Output
    from dvc.stage import PipelineStage

    # Create a test file
    tmp_dir.gen("data", "test content")

    # Create two stages that depend on the same data
    stage1 = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage1")
    stage2 = PipelineStage(dvc, "dvc.yaml", cmd="echo 2", name="stage2")

    # Create outputs (used as deps) pointing to the same file
    out1 = Output(stage1, "data")
    out2 = Output(stage2, "data")

    # Spy on _build to count calls
    build_spy = mocker.spy(out1, "_build")

    # First call should build
    hash1 = out1.get_hash()
    assert build_spy.call_count == 1

    # Second call on different output but same path should use cache
    hash2 = out2.get_hash()
    assert build_spy.call_count == 1  # Still 1 - used cache

    # Hashes should be the same
    assert hash1 == hash2


def test_hash_cache_cleared_on_reset(tmp_dir, dvc, mocker):
    """Test that hash cache is cleared when repo is reset."""
    from dvc.output import Output
    from dvc.stage import PipelineStage

    tmp_dir.gen("data", "test content")

    stage = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage")
    out = Output(stage, "data")

    # Populate cache
    out.get_hash()
    assert len(dvc._hash_cache) == 1

    # Reset should clear cache
    dvc._reset()
    assert len(dvc._hash_cache) == 0


def test_hash_cache_different_paths(tmp_dir, dvc, mocker):
    """Test that different paths have separate cache entries."""
    from dvc.output import Output
    from dvc.stage import PipelineStage

    tmp_dir.gen({"data1": "content1", "data2": "content2"})

    stage = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage")
    out1 = Output(stage, "data1")
    out2 = Output(stage, "data2")

    build_spy = mocker.spy(out1, "_build")

    out1.get_hash()
    out2.get_hash()

    # Both should be built (different paths)
    assert build_spy.call_count == 1  # Only out1's build was spied
    assert len(dvc._hash_cache) == 2


def test_hash_cache_with_directory(tmp_dir, dvc, mocker):
    """Test hash caching works correctly with directories."""
    from dvc.output import Output
    from dvc.stage import PipelineStage

    # Create a directory with multiple files
    tmp_dir.gen({"data_dir": {"file1.txt": "a", "file2.txt": "b", "file3.txt": "c"}})

    stage1 = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage1")
    stage2 = PipelineStage(dvc, "dvc.yaml", cmd="echo 2", name="stage2")

    out1 = Output(stage1, "data_dir")
    out2 = Output(stage2, "data_dir")

    build_spy = mocker.spy(out1, "_build")

    # First call builds
    hash1 = out1.get_hash()
    assert build_spy.call_count == 1

    # Second call uses cache
    hash2 = out2.get_hash()
    assert build_spy.call_count == 1

    assert hash1 == hash2
    assert hash1.isdir  # Should be a directory hash


def test_hash_cache_key_includes_hash_name(tmp_dir, dvc, mocker):
    """Test that cache key includes hash_name for correctness."""
    from dvc.output import Output
    from dvc.stage import PipelineStage

    tmp_dir.gen("data", "test content")

    stage = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage")
    out = Output(stage, "data")

    # Get hash to populate cache
    out.get_hash()

    # Check cache key format includes hash_name
    cache_key = next(iter(dvc._hash_cache.keys()))
    fs_path, hash_name, protocol = cache_key
    assert fs_path == out.fs_path
    assert hash_name == out.hash_name
    assert protocol == out.fs.protocol


def test_hash_cache_initialized_on_repo(dvc):
    """Test that the hash cache is initialized on the repo."""
    assert hasattr(dvc, "_hash_cache")
    assert isinstance(dvc._hash_cache, dict)


def test_hash_cache_reset_on_repo_reset(dvc):
    """Test that hash cache is cleared when repo is reset."""
    # Add something to the cache
    dvc._hash_cache[("test", "md5", "local")] = ("meta", "hash")
    assert len(dvc._hash_cache) == 1

    # Reset should clear the cache
    dvc._reset()
    assert len(dvc._hash_cache) == 0


def test_hash_cache_updated_on_save(tmp_dir, dvc):
    """Test that save() updates the hash cache with new hash."""
    from dvc.output import Output
    from dvc.stage import PipelineStage

    tmp_dir.gen("data", "initial content")

    stage = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage")
    out = Output(stage, "data")

    # Get initial hash - populates cache
    initial_hash = out.get_hash()
    assert len(dvc._hash_cache) == 1
    cached_meta, cached_hash = dvc._hash_cache[
        (out.fs_path, out.hash_name, out.fs.protocol)
    ]
    assert cached_hash == initial_hash

    # Modify the file
    tmp_dir.gen("data", "modified content")

    # Save should update the cache with the new hash
    out.save()

    # Verify cache was updated
    cached_meta, cached_hash = dvc._hash_cache[
        (out.fs_path, out.hash_name, out.fs.protocol)
    ]
    assert cached_hash == out.hash_info
    assert cached_hash != initial_hash  # Hash changed


def test_hash_cache_chained_stages_see_updated_hash(tmp_dir, dvc):
    """Test that downstream stages see updated hash after upstream stage saves.

    This tests a pipeline like:
        stage_a (produces intermediate/) -> stage_b (depends on intermediate/)

    When stage_a runs and saves, stage_b should see the new hash of
    intermediate/, not a stale cached value.
    """
    from dvc.output import Output
    from dvc.stage import PipelineStage

    # Create initial intermediate directory
    tmp_dir.gen({"intermediate": {"file.txt": "initial"}})

    # Stage A has intermediate/ as output
    stage_a = PipelineStage(dvc, "dvc.yaml", cmd="echo a", name="stage_a")
    out_a = Output(stage_a, "intermediate")

    # Stage B has intermediate/ as dependency
    stage_b = PipelineStage(dvc, "dvc.yaml", cmd="echo b", name="stage_b")
    dep_b = Output(stage_b, "intermediate")  # Same path, different Output object

    # Stage A checks its output (this could happen during changed_outs check)
    hash_before = out_a.get_hash()
    assert len(dvc._hash_cache) == 1

    # Simulate stage A running and modifying intermediate/
    tmp_dir.gen({"intermediate": {"file.txt": "modified", "new_file.txt": "new"}})

    # Stage A saves its output (this should update the cache)
    out_a.save()

    # Stage B checks its dependency - should see the NEW hash, not stale cache
    hash_from_dep = dep_b.get_hash()

    # The hash stage B sees should match stage A's saved hash
    assert hash_from_dep == out_a.hash_info
    assert hash_from_dep != hash_before  # Should NOT be the old cached hash


def test_dependency_save_uses_cache(tmp_dir, dvc, mocker):
    """Test that Dependency.save() uses cached hash instead of rebuilding.

    During repro, save_deps() is called multiple times. For dependencies
    (not outputs), we can skip the expensive _build() call if the hash
    is already cached, since dependencies don't change during repro.
    """
    from dvc.dependency import Dependency
    from dvc.stage import PipelineStage

    # Create test data
    tmp_dir.gen({"data_dir": {"file1.txt": "a", "file2.txt": "b"}})

    stage = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage")
    dep = Dependency(stage, "data_dir")

    # First get_hash() populates the cache
    hash1 = dep.get_hash()
    assert len(dvc._hash_cache) == 1

    # Spy on _build to verify it's not called during save
    build_spy = mocker.spy(dep, "_build")

    # save() should use cached hash, not rebuild
    dep.save()

    # _build should NOT have been called - used cache instead
    assert build_spy.call_count == 0

    # Hash should be the same
    assert dep.hash_info == hash1


def test_output_save_always_rebuilds(tmp_dir, dvc, mocker):
    """Test that Output.save() always rebuilds (outputs may have changed)."""
    from dvc.output import Output
    from dvc.stage import PipelineStage

    tmp_dir.gen("output.txt", "initial content")

    stage = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage")
    out = Output(stage, "output.txt")

    # Populate cache
    out.get_hash()
    assert len(dvc._hash_cache) == 1

    # Spy on _build
    build_spy = mocker.spy(out, "_build")

    # Output.save() should always rebuild (IS_DEPENDENCY is False for Output)
    out.save()

    # _build SHOULD have been called - outputs always rebuild
    assert build_spy.call_count == 1


def test_dependency_save_without_cache_still_builds(tmp_dir, dvc, mocker):
    """Test that Dependency.save() builds if cache is empty."""
    from dvc.dependency import Dependency
    from dvc.stage import PipelineStage

    tmp_dir.gen("data", "test content")

    stage = PipelineStage(dvc, "dvc.yaml", cmd="echo 1", name="stage")
    dep = Dependency(stage, "data")

    # Don't call get_hash() first - cache is empty
    assert len(dvc._hash_cache) == 0

    # Spy on _build
    build_spy = mocker.spy(dep, "_build")

    # save() should build since cache is empty
    dep.save()

    # _build should have been called
    assert build_spy.call_count == 1

    # Cache should now be populated
    assert len(dvc._hash_cache) == 1


def test_repro_chained_pipeline(tmp_dir, dvc, scm):
    """Integration test: repro pipeline where stages depend on previous outputs."""
    # Create initial data
    tmp_dir.gen("input.txt", "input data")

    # Create a chained pipeline:
    # stage1: input.txt -> intermediate/
    # stage2: intermediate/ -> output.txt
    # stage3: intermediate/ -> output2.txt (also depends on intermediate/)
    # Note: stage1 copies input.txt content to intermediate/, so when input changes,
    # intermediate/ content also changes, triggering stage2 and stage3.
    tmp_dir.gen(
        "dvc.yaml",
        """\
stages:
  stage1:
    cmd: mkdir -p intermediate && cp input.txt intermediate/data.txt
    deps:
      - input.txt
    outs:
      - intermediate
  stage2:
    cmd: cat intermediate/data.txt > output.txt
    deps:
      - intermediate
    outs:
      - output.txt
  stage3:
    cmd: cat intermediate/data.txt > output2.txt
    deps:
      - intermediate
    outs:
      - output2.txt
""",
    )

    # First repro - all stages should run
    dvc.reproduce()

    # Verify outputs exist
    assert (tmp_dir / "intermediate" / "data.txt").exists()
    assert (tmp_dir / "output.txt").exists()
    assert (tmp_dir / "output2.txt").exists()

    # Verify initial content
    assert (tmp_dir / "output.txt").read_text().strip() == "input data"

    # Modify input - this should trigger stage1, which changes intermediate/,
    # which should trigger stage2 and stage3
    tmp_dir.gen("input.txt", "modified input data")

    # Clear cache to simulate fresh repro
    dvc._hash_cache = {}

    # Second repro - all stages should run because input changed
    stages = dvc.reproduce()

    # All 3 stages should have been reproduced
    assert len(stages) == 3

    # Verify output changed
    assert (tmp_dir / "output.txt").read_text().strip() == "modified input data"
