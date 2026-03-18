"""Tests for Forest and ContextWindow."""

from unittest.mock import MagicMock
import math

from context_window import Forest, ContextWindow


# ---------------------------------------------------------------------------
# Forest tests
# ---------------------------------------------------------------------------

class TestForestInsert:
    def test_insert_creates_singleton(self):
        forest = Forest(summarizer=MagicMock())
        embedding = [1.0, 0.0]
        forest.insert("m0", "hello", embedding)

        assert "m0" in forest.roots()
        assert forest.compact("m0") == "hello"

    def test_insert_multiple_creates_separate_singletons(self):
        forest = Forest(summarizer=MagicMock())
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])

        roots = forest.roots()
        assert len(roots) == 2
        assert "m0" in roots
        assert "m1" in roots


class TestForestUnion:
    def test_union_merges_two_roots_into_one(self):
        forest = Forest(summarizer=MagicMock())
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])

        forest.union("m0", "m1")

        roots = forest.roots()
        assert len(roots) == 1

    def test_union_marks_merged_root_dirty(self):
        forest = Forest(summarizer=MagicMock())
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])

        forest.union("m0", "m1")

        root = forest.roots()[0]
        assert forest.is_dirty(root)

    def test_union_does_not_call_summarizer(self):
        summarizer = MagicMock()
        forest = Forest(summarizer=summarizer)
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])

        forest.union("m0", "m1")

        summarizer.summarize.assert_not_called()

    def test_union_collects_dirty_inputs_from_both_sides(self):
        forest = Forest(summarizer=MagicMock())
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])

        forest.union("m0", "m1")

        root = forest.roots()[0]
        dirty_inputs = forest.dirty_inputs(root)
        # Both raw contents should be in the dirty inputs
        assert "hello" in dirty_inputs
        assert "world" in dirty_inputs


class TestForestResolveDirty:
    def test_resolve_dirty_calls_summarizer_per_dirty_root(self):
        summarizer = MagicMock()
        summarizer.summarize.return_value = "summary of hello and world"
        forest = Forest(summarizer=summarizer)
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])
        forest.union("m0", "m1")

        forest.resolve_dirty()

        summarizer.summarize.assert_called_once()

    def test_resolve_dirty_clears_dirty_flag(self):
        summarizer = MagicMock()
        summarizer.summarize.return_value = "summary"
        forest = Forest(summarizer=summarizer)
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])
        forest.union("m0", "m1")

        forest.resolve_dirty()

        root = forest.roots()[0]
        assert not forest.is_dirty(root)

    def test_resolve_dirty_updates_compact_output(self):
        summarizer = MagicMock()
        summarizer.summarize.return_value = "combined summary"
        forest = Forest(summarizer=summarizer)
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])
        forest.union("m0", "m1")

        forest.resolve_dirty()

        root = forest.roots()[0]
        assert forest.compact(root) == "combined summary"

    def test_resolve_dirty_passes_previous_summary_plus_new_inputs(self):
        """When merging a cluster that already has a summary with a new node,
        resolve_dirty should pass both the previous summary and the new content."""
        summarizer = MagicMock()
        summarizer.summarize.side_effect = ["summary_01", "summary_012"]
        forest = Forest(summarizer=summarizer)
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.9, 0.1])
        forest.insert("m2", "again", [0.8, 0.2])

        # First merge and resolve
        forest.union("m0", "m1")
        forest.resolve_dirty()

        # Second merge with existing cluster
        root = forest.roots()[0]
        forest.union(root, "m2")
        forest.resolve_dirty()

        # Second call should include previous summary + new content
        second_call_args = summarizer.summarize.call_args_list[1]
        texts = second_call_args[0][0]
        assert "summary_01" in texts
        assert "again" in texts

    def test_singletons_are_not_dirty(self):
        forest = Forest(summarizer=MagicMock())
        forest.insert("m0", "hello", [1.0, 0.0])

        assert not forest.is_dirty("m0")

    def test_resolve_dirty_noop_when_no_dirty_clusters(self):
        summarizer = MagicMock()
        forest = Forest(summarizer=summarizer)
        forest.insert("m0", "hello", [1.0, 0.0])

        forest.resolve_dirty()

        summarizer.summarize.assert_not_called()


class TestForestCompact:
    def test_compact_returns_raw_content_for_singleton(self):
        forest = Forest(summarizer=MagicMock())
        forest.insert("m0", "hello", [1.0, 0.0])

        assert forest.compact("m0") == "hello"

    def test_compact_returns_cached_summary_after_resolve(self):
        summarizer = MagicMock()
        summarizer.summarize.return_value = "cached"
        forest = Forest(summarizer=summarizer)
        forest.insert("m0", "hello", [1.0, 0.0])
        forest.insert("m1", "world", [0.0, 1.0])
        forest.union("m0", "m1")
        forest.resolve_dirty()

        # Should return cached summary without calling summarizer again
        result = forest.compact(forest.roots()[0])
        assert result == "cached"
        assert summarizer.summarize.call_count == 1  # Only the resolve call


class TestForestNearestRoot:
    def test_nearest_root_returns_closest_by_cosine(self):
        forest = Forest(summarizer=MagicMock())
        forest.insert("m0", "topic A", [1.0, 0.0])
        forest.insert("m1", "topic B", [0.0, 1.0])

        # Should be closest to m0
        nearest, similarity = forest.nearest_root([0.9, 0.1])
        assert nearest == "m0"

    def test_nearest_root_returns_none_when_empty(self):
        forest = Forest(summarizer=MagicMock())

        result = forest.nearest_root([1.0, 0.0])
        assert result is None

    def test_nearest_root_returns_similarity_score(self):
        forest = Forest(summarizer=MagicMock())
        forest.insert("m0", "topic A", [1.0, 0.0])

        _, similarity = forest.nearest_root([1.0, 0.0])
        assert similarity == 1.0  # identical vectors

        _, similarity = forest.nearest_root([0.0, 1.0])
        assert similarity == 0.0  # orthogonal vectors


# ---------------------------------------------------------------------------
# ContextWindow tests
# ---------------------------------------------------------------------------

def _make_context_window(summarizer=None, graduate_at=26, evict_at=30,
                         max_cold_clusters=10, merge_threshold=0.15):
    """Helper to create a ContextWindow with a mock embedder and summarizer."""
    embedder = MagicMock()
    # Return a simple incrementing embedding
    call_count = [0]

    def mock_embed(text):
        call_count[0] += 1
        # Create a unit vector that's slightly different each time
        angle = call_count[0] * 0.1
        return [math.cos(angle), math.sin(angle)]

    embedder.embed.side_effect = mock_embed

    if summarizer is None:
        summarizer = MagicMock()
        summarizer.summarize.return_value = "cluster summary"

    return ContextWindow(
        embedder=embedder,
        summarizer=summarizer,
        graduate_at=graduate_at,
        evict_at=evict_at,
        max_cold_clusters=max_cold_clusters,
        merge_threshold=merge_threshold,
    )


class TestContextWindowAppend:
    def test_append_adds_to_hot_zone(self):
        cw = _make_context_window()
        cw.append("hello")

        assert cw.hot_count == 1

    def test_append_many_stays_in_hot_until_graduate_threshold(self):
        cw = _make_context_window(graduate_at=26)

        for i in range(26):
            cw.append(f"message {i}")

        # All 26 should still be in hot (graduation happens when > graduate_at)
        assert cw.hot_count == 26

    def test_append_past_graduate_threshold_graduates_oldest(self):
        cw = _make_context_window(graduate_at=5, evict_at=8, max_cold_clusters=10)

        for i in range(7):
            cw.append(f"message {i}")

        # After 7 appends with graduate_at=5: messages 0 and 1 graduated
        # hot should have 5 messages
        assert cw.hot_count == 5
        assert cw.cold_count >= 1  # at least some graduated

    def test_append_past_evict_threshold_evicts_oldest(self):
        cw = _make_context_window(graduate_at=3, evict_at=5, max_cold_clusters=10)

        for i in range(7):
            cw.append(f"message {i}")

        # hot zone should never exceed evict_at
        assert cw.hot_count <= 5


class TestContextWindowGraduation:
    def test_graduation_creates_forest_cluster(self):
        cw = _make_context_window(graduate_at=3, evict_at=6, max_cold_clusters=10)

        for i in range(5):
            cw.append(f"message {i}")

        assert cw.cold_count >= 1

    def test_graduation_merges_with_nearest_if_above_threshold(self):
        """When a graduating message is similar enough to an existing cluster,
        it should merge rather than create a new cluster."""
        embedder = MagicMock()
        # First 3 messages get nearly identical embeddings (will merge)
        # Remaining get different embeddings
        embeddings = [
            [1.0, 0.0],  # msg 0
            [0.99, 0.14],  # msg 1 — very similar to msg 0
            [0.98, 0.20],  # msg 2 — similar to msg 0
            [0.0, 1.0],  # msg 3 — different
            [0.1, 0.99],  # msg 4
        ]
        embedder.embed.side_effect = embeddings

        summarizer = MagicMock()
        summarizer.summarize.return_value = "merged summary"

        cw = ContextWindow(
            embedder=embedder,
            summarizer=summarizer,
            graduate_at=3,
            evict_at=6,
            max_cold_clusters=10,
            merge_threshold=0.15,
        )

        for i in range(5):
            cw.append(f"message {i}")

        # Similar messages should have merged into fewer clusters
        assert cw.cold_count <= 2

    def test_force_merge_when_exceeding_max_clusters(self):
        """When cold cluster count exceeds max, force merge closest pair."""
        embedder = MagicMock()
        # Create maximally distinct embeddings
        call_idx = [0]

        def spread_embed(text):
            angle = call_idx[0] * (2 * math.pi / 20)  # Spread around unit circle
            call_idx[0] += 1
            return [math.cos(angle), math.sin(angle)]

        embedder.embed.side_effect = spread_embed

        summarizer = MagicMock()
        summarizer.summarize.return_value = "forced merge summary"

        cw = ContextWindow(
            embedder=embedder,
            summarizer=summarizer,
            graduate_at=2,
            evict_at=20,
            max_cold_clusters=3,
            merge_threshold=0.99,  # Very high threshold so nothing merges voluntarily
        )

        for i in range(10):
            cw.append(f"distinct topic {i}")

        # Should never exceed max_cold_clusters
        assert cw.cold_count <= 3


class TestContextWindowRender:
    def test_render_returns_cold_summaries_then_hot_contents(self):
        summarizer = MagicMock()
        summarizer.summarize.return_value = "cold summary"
        cw = _make_context_window(summarizer=summarizer, graduate_at=3, evict_at=6)

        for i in range(5):
            cw.append(f"message {i}")

        rendered = cw.render()
        assert len(rendered) > 0
        # Last items should be the hot messages
        assert rendered[-1] == "message 4"

    def test_render_empty_returns_empty(self):
        cw = _make_context_window()
        assert cw.render() == []

    def test_render_only_hot_returns_hot(self):
        cw = _make_context_window()
        cw.append("only message")
        rendered = cw.render()
        assert rendered == ["only message"]


class TestContextWindowResolveDirty:
    def test_resolve_dirty_delegates_to_forest(self):
        summarizer = MagicMock()
        summarizer.summarize.return_value = "resolved"
        cw = _make_context_window(summarizer=summarizer, graduate_at=3, evict_at=6)

        for i in range(5):
            cw.append(f"message {i}")

        cw.resolve_dirty()
        # If there are dirty clusters, summarizer should have been called
        if cw.cold_count > 0:
            assert summarizer.summarize.called
