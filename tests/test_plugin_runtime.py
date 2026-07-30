"""Tests for PluginRuntime — plugin descriptor loading and hook aggregation."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import time

from fireclaw_core.plugin.plugin_descriptor import FireClawPluginDescriptor
from fireclaw_core.plugin.plugin_runtime import PluginRuntime


@contextmanager
def _raises_value_error(match: str):
    """Dependency-free replacement for pytest.raises(ValueError, match=...)."""
    try:
        yield
    except ValueError as exc:
        assert match in str(exc), f"Expected {match!r} in {str(exc)!r}"
    else:
        raise AssertionError(f"Expected ValueError containing: {match}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_descriptor(**overrides):
    """Return a valid descriptor, optionally overriding specific fields."""
    defaults = dict(
        plugin_id="navigate_to_floor",
        capabilities=("navigate_to_floor", "navigation"),
        preconditions=("robot_online", "floor_reachable"),
        risk_level="low",
        required_sensors=(),
        adapter_bindings=("navigation",),
    )
    defaults.update(overrides)
    return FireClawPluginDescriptor(**defaults)


def _write_descriptor_json(directory: Path, filename: str, descriptor: FireClawPluginDescriptor) -> Path:
    """Serialize a descriptor to JSON and write it to *directory* / *filename*."""
    data = {
        "plugin_id": descriptor.plugin_id,
        "capabilities": list(descriptor.capabilities),
        "preconditions": list(descriptor.preconditions),
        "risk_level": descriptor.risk_level,
        "required_sensors": list(descriptor.required_sensors),
        "adapter_bindings": list(descriptor.adapter_bindings),
        "approval_scope": descriptor.approval_scope,
        "provider_hooks": list(descriptor.provider_hooks),
        "memory_hooks": list(descriptor.memory_hooks),
        "tool_approval_hooks": list(descriptor.tool_approval_hooks),
    }
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPluginRuntimeLoad:
    """Loading descriptors from a directory."""

    def test_plugin_runtime_loads_descriptors_from_directory(self, tmp_path: Path) -> None:
        d1 = _make_descriptor(plugin_id="alpha", provider_hooks=("enrich_context",))
        d2 = _make_descriptor(plugin_id="beta", memory_hooks=("rerank",))
        _write_descriptor_json(tmp_path, "alpha.json", d1)
        _write_descriptor_json(tmp_path, "beta.json", d2)

        runtime = PluginRuntime()
        count = runtime.load_from_directory(tmp_path)

        assert count == 2
        assert len(runtime.descriptors) == 2
        ids = {d.plugin_id for d in runtime.descriptors}
        assert ids == {"alpha", "beta"}

    def test_plugin_runtime_empty_directory(self, tmp_path: Path) -> None:
        runtime = PluginRuntime()
        count = runtime.load_from_directory(tmp_path)

        assert count == 0
        assert runtime.descriptors == []

    def test_plugin_runtime_skips_invalid_json_files(self, tmp_path: Path) -> None:
        valid = _make_descriptor(plugin_id="good")
        _write_descriptor_json(tmp_path, "good.json", valid)
        (tmp_path / "bad.json").write_text("NOT VALID JSON {{{", encoding="utf-8")

        runtime = PluginRuntime()
        count = runtime.load_from_directory(tmp_path)

        assert count == 1
        assert runtime.descriptors[0].plugin_id == "good"


class TestPluginRuntimeRegister:
    """Direct registration and hook name validation."""

    def test_plugin_runtime_register_descriptor_directly(self) -> None:
        d = _make_descriptor(
            plugin_id="manual",
            provider_hooks=("enrich_context",),
            memory_hooks=("rerank",),
        )
        runtime = PluginRuntime()
        runtime.register_descriptor(d)

        assert len(runtime.descriptors) == 1
        assert runtime.descriptors[0].plugin_id == "manual"

    def test_plugin_runtime_rejects_unknown_provider_hook(self) -> None:
        d = _make_descriptor(plugin_id="bad", provider_hooks=("nonexistent_hook",))
        runtime = PluginRuntime()

        with _raises_value_error("nonexistent_hook"):
            runtime.register_descriptor(d)

    def test_plugin_runtime_rejects_unknown_memory_hook(self) -> None:
        d = _make_descriptor(plugin_id="bad", memory_hooks=("bogus_rerank",))
        runtime = PluginRuntime()

        with _raises_value_error("bogus_rerank"):
            runtime.register_descriptor(d)

    def test_plugin_runtime_rejects_unknown_tool_approval_hook(self) -> None:
        d = _make_descriptor(plugin_id="bad", tool_approval_hooks=("unknown_approval",))
        runtime = PluginRuntime()

        with _raises_value_error("unknown_approval"):
            runtime.register_descriptor(d)

    def test_plugin_runtime_descriptors_returns_immutable_copy(self) -> None:
        d = _make_descriptor(plugin_id="x")
        runtime = PluginRuntime()
        runtime.register_descriptor(d)

        copy = runtime.descriptors
        copy.clear()

        assert len(runtime.descriptors) == 1


class TestPluginRuntimeInventory:
    """Inventory snapshot tests."""

    def test_inventory_empty_runtime(self) -> None:
        runtime = PluginRuntime()
        inv = runtime.inventory()
        assert inv["descriptor_ids"] == ()
        assert inv["hook_names"] == ()
        assert inv["policy_active"] is False

    def test_inventory_with_descriptors(self) -> None:
        runtime = PluginRuntime()
        d1 = _make_descriptor(plugin_id="alpha", provider_hooks=("enrich_context",))
        d2 = _make_descriptor(plugin_id="beta", memory_hooks=("rerank",))
        runtime.register_descriptor(d1)
        runtime.register_descriptor(d2)

        inv = runtime.inventory()
        assert "alpha" in inv["descriptor_ids"]
        assert "beta" in inv["descriptor_ids"]
        assert "enrich_context" in inv["hook_names"]
        assert "rerank" in inv["hook_names"]
        assert inv["policy_active"] is False

    def test_inventory_with_policy(self) -> None:
        from fireclaw_core.plugin.plugin_policy import PluginPolicy
        runtime = PluginRuntime()
        d = _make_descriptor(plugin_id="gamma", tool_approval_hooks=("add_reason",))
        runtime.register_descriptor(d)
        runtime.plugin_policy = PluginPolicy(descriptors=[d])

        inv = runtime.inventory()
        assert "gamma" in inv["descriptor_ids"]
        assert "add_reason" in inv["hook_names"]
        assert inv["policy_active"] is True


class TestPluginRuntimeHookAggregation:
    """Hook aggregation across multiple descriptors."""

    def test_plugin_runtime_provider_hooks_aggregated(self) -> None:
        d1 = _make_descriptor(plugin_id="a", provider_hooks=("enrich_context",))
        d2 = _make_descriptor(plugin_id="b", provider_hooks=("suggest_model", "validate_output"))
        d3 = _make_descriptor(plugin_id="c", provider_hooks=("enrich_context",))  # duplicate

        runtime = PluginRuntime()
        for d in (d1, d2, d3):
            runtime.register_descriptor(d)

        hooks = runtime.provider_hooks()
        assert "enrich_context" in hooks
        assert "suggest_model" in hooks
        assert "validate_output" in hooks

    def test_plugin_runtime_memory_hooks_aggregated(self) -> None:
        d1 = _make_descriptor(plugin_id="a", memory_hooks=("rerank",))
        d2 = _make_descriptor(plugin_id="b", memory_hooks=("filter", "summarize"))

        runtime = PluginRuntime()
        runtime.register_descriptor(d1)
        runtime.register_descriptor(d2)

        hooks = runtime.memory_hooks()
        assert "rerank" in hooks
        assert "filter" in hooks
        assert "summarize" in hooks

    def test_plugin_runtime_tool_approval_hooks_aggregated(self) -> None:
        d1 = _make_descriptor(plugin_id="a", tool_approval_hooks=("add_reason",))
        d2 = _make_descriptor(plugin_id="b", tool_approval_hooks=("require_scope", "auto_approve"))

        runtime = PluginRuntime()
        runtime.register_descriptor(d1)
        runtime.register_descriptor(d2)

        hooks = runtime.tool_approval_hooks()
        assert "add_reason" in hooks
        assert "require_scope" in hooks
        assert "auto_approve" in hooks


# ---------------------------------------------------------------------------
# Callable hook registration and execution
# ---------------------------------------------------------------------------


class TestPluginRuntimeCallableHooks:
    """Callable hook registration, validation, and execution."""

    def test_provider_hook_callable_must_be_registered_explicitly(self) -> None:
        runtime = PluginRuntime()
        runtime.register_descriptor(_make_descriptor(
            plugin_id="fire.context",
            capabilities=("context",),
            provider_hooks=("enrich_context",),
        ))

        effects = runtime.run_provider_hooks(
            "enrich_context",
            {"command": "去二楼搜索", "context": {}},
        )

        assert effects == []

    def test_registered_provider_hook_returns_structured_effect(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="provider",
            hook_name="enrich_context",
            plugin_id="fire.context",
            callback=lambda payload: {"extra_context": {"evacuation_route": "east stairs"}},
        )

        effects = runtime.run_provider_hooks(
            "enrich_context",
            {"command": "去二楼搜索", "context": {}},
        )

        assert effects == [
            {
                "plugin_id": "fire.context",
                "hook_name": "enrich_context",
                "effect": {"extra_context": {"evacuation_route": "east stairs"}},
            }
        ]

    def test_registered_memory_hook_returns_structured_effect(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="memory",
            hook_name="filter",
            plugin_id="fire.memory",
            callback=lambda payload: {
                "filtered": [r for r in payload.get("results", []) if r.get("score", 0) > 0.5]
            },
        )

        effects = runtime.run_memory_hooks(
            "filter",
            {"results": [{"id": "r1", "score": 0.8}, {"id": "r2", "score": 0.2}]},
        )

        assert len(effects) == 1
        assert effects[0]["plugin_id"] == "fire.memory"
        assert effects[0]["effect"]["filtered"] == [{"id": "r1", "score": 0.8}]

    def test_registered_tool_approval_hook_returns_structured_effect(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="tool_approval",
            hook_name="add_reason",
            plugin_id="fire.approval",
            callback=lambda payload: {"reason": "High heat area requires supervisor review."},
        )

        effects = runtime.run_tool_approval_hooks(
            "add_reason",
            {"mission_id": "m1", "action": "enter_building", "payload": {}},
        )

        assert len(effects) == 1
        assert effects[0]["plugin_id"] == "fire.approval"
        assert effects[0]["effect"]["reason"] == "High heat area requires supervisor review."

    def test_hook_callable_receives_independent_payload_copy(self) -> None:
        """Verify callback gets a copy, not the caller's mutable dict."""
        received_payloads: list[dict] = []
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="provider",
            hook_name="enrich_context",
            plugin_id="fire.context",
            callback=lambda payload: (received_payloads.append(payload), None)[1],
        )

        original = {"command": "test", "context": {"key": "value"}}
        runtime.run_provider_hooks("enrich_context", original)
        original["command"] = "mutated"

        assert received_payloads[0]["command"] == "test"

    def test_hook_callable_returning_non_dict_raises(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="provider",
            hook_name="enrich_context",
            plugin_id="fire.bad",
            callback=lambda payload: "not a dict",
        )

        with _raises_value_error("must return a dict or None"):
            runtime.run_provider_hooks("enrich_context", {"command": "test", "context": {}})

    def test_multiple_callables_for_same_hook(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="provider",
            hook_name="enrich_context",
            plugin_id="fire.context.a",
            callback=lambda payload: {"source": "a"},
        )
        runtime.register_callable(
            hook_type="provider",
            hook_name="enrich_context",
            plugin_id="fire.context.b",
            callback=lambda payload: {"source": "b"},
        )

        effects = runtime.run_provider_hooks("enrich_context", {"command": "test", "context": {}})

        assert len(effects) == 2

    def test_failing_callback_does_not_block_remaining_callbacks(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="provider",
            hook_name="enrich_context",
            plugin_id="fire.broken",
            callback=lambda payload: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        runtime.register_callable(
            hook_type="provider",
            hook_name="enrich_context",
            plugin_id="fire.working",
            callback=lambda payload: {"source": "working"},
        )

        effects = runtime.run_provider_hooks("enrich_context", {"command": "test", "context": {}})

        assert len(effects) == 1
        assert effects[0]["plugin_id"] == "fire.working"
        assert effects[0]["effect"] == {"source": "working"}

    def test_hook_runtime_bounds_timeout_and_result_size(self) -> None:
        runtime = PluginRuntime(
            hook_timeout_seconds=0.01,
            hook_result_bytes=32,
        )

        def slow(payload):
            time.sleep(0.05)
            return {"ok": True}

        runtime.register_callable(
            hook_type="memory",
            hook_name="filter",
            plugin_id="fire.slow",
            callback=slow,
        )
        runtime.register_callable(
            hook_type="memory",
            hook_name="filter",
            plugin_id="fire.large",
            callback=lambda payload: {"value": "x" * 128},
        )

        report = runtime.run_memory_hooks_with_diagnostics(
            "filter",
            {"memories": []},
        )

        assert report.effects == ()
        assert {item.exception_class for item in report.failures} == {
            "TrustedCallbackTimeout",
            "ValueError",
        }

    def test_unknown_hook_type_raises(self) -> None:
        runtime = PluginRuntime()
        with _raises_value_error("Unknown hook type"):
            runtime.register_callable(
                hook_type="unknown",
                hook_name="enrich_context",
                plugin_id="fire.context",
                callback=lambda payload: None,
            )

    def test_unknown_hook_name_raises(self) -> None:
        runtime = PluginRuntime()
        with _raises_value_error("declares unknown"):
            runtime.register_callable(
                hook_type="provider",
                hook_name="nonexistent_hook",
                plugin_id="fire.context",
                callback=lambda payload: None,
            )


# ---------------------------------------------------------------------------
# Plugin hook diagnostics (content-free failure reporting)
# ---------------------------------------------------------------------------


class TestPluginHookDiagnostics:
    """Content-free diagnostic APIs for hook execution."""

    def test_memory_hook_diagnostics_report_callback_exception_without_message(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="memory",
            hook_name="filter",
            plugin_id="broken.memory",
            callback=lambda payload: (_ for _ in ()).throw(
                RuntimeError("restricted victim name")
            ),
        )

        report = runtime.run_memory_hooks_with_diagnostics("filter", {"memories": []})

        assert report.effects == ()
        assert [failure.to_dict() for failure in report.failures] == [{
            "plugin_id": "broken.memory",
            "hook_name": "filter",
            "exception_class": "RuntimeError",
        }]
        assert "restricted victim name" not in repr(report)

    def test_existing_memory_hook_api_keeps_list_shape_on_callback_exception(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="memory",
            hook_name="filter",
            plugin_id="broken.memory",
            callback=lambda payload: (_ for _ in ()).throw(RuntimeError("private")),
        )

        assert runtime.run_memory_hooks("filter", {"memories": []}) == []

    def test_provider_hook_diagnostics_report_callback_exception_without_message(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="provider",
            hook_name="enrich_context",
            plugin_id="broken.provider",
            callback=lambda payload: (_ for _ in ()).throw(
                RuntimeError("secret context leak")
            ),
        )

        report = runtime.run_provider_hooks_with_diagnostics(
            "enrich_context", {"command": "test", "context": {}},
        )

        assert report.effects == ()
        assert [failure.to_dict() for failure in report.failures] == [{
            "plugin_id": "broken.provider",
            "hook_name": "enrich_context",
            "exception_class": "RuntimeError",
        }]
        assert "secret context leak" not in repr(report)

    def test_diagnostics_report_success_and_failure_mixed(self) -> None:
        runtime = PluginRuntime()
        runtime.register_callable(
            hook_type="memory",
            hook_name="filter",
            plugin_id="working.memory",
            callback=lambda payload: {"filtered": True},
        )
        runtime.register_callable(
            hook_type="memory",
            hook_name="filter",
            plugin_id="broken.memory",
            callback=lambda payload: (_ for _ in ()).throw(ValueError("bad")),
        )

        report = runtime.run_memory_hooks_with_diagnostics("filter", {"memories": []})

        assert len(report.effects) == 1
        assert report.effects[0]["plugin_id"] == "working.memory"
        assert len(report.failures) == 1
        assert report.failures[0].plugin_id == "broken.memory"

    def test_plugin_hook_failure_is_frozen(self) -> None:
        from fireclaw_core.plugin.plugin_runtime import PluginHookFailure
        failure = PluginHookFailure(
            plugin_id="x", hook_name="y", exception_class="RuntimeError"
        )
        try:
            failure.plugin_id = "z"  # type: ignore[misc]
            raise AssertionError("PluginHookFailure should be frozen")
        except AttributeError:
            pass

    def test_plugin_hook_run_is_frozen(self) -> None:
        from fireclaw_core.plugin.plugin_runtime import PluginHookRun
        run = PluginHookRun(effects=(), failures=())
        try:
            run.effects = ("bad",)  # type: ignore[misc]
            raise AssertionError("PluginHookRun should be frozen")
        except AttributeError:
            pass

    def test_callback_exception_log_omits_exception_message_and_traceback(self) -> None:
        """Exception messages and tracebacks must not appear in rendered logs."""
        import logging
        from fireclaw_core.plugin import plugin_runtime as pr_module

        captured_records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: captured_records.append(record)
        logger = pr_module.logger
        logger.addHandler(handler)
        try:
            runtime = PluginRuntime()
            runtime.register_callable(
                hook_type="memory",
                hook_name="filter",
                plugin_id="broken.memory",
                callback=lambda payload: (_ for _ in ()).throw(
                    RuntimeError("restricted victim name")
                ),
            )

            runtime.run_memory_hooks_with_diagnostics("filter", {"memories": []})

            assert len(captured_records) >= 1
            for record in captured_records:
                # Format the message
                formatted = record.getMessage()
                # Exception message must not appear
                assert "restricted victim name" not in formatted
                # exc_info must not be populated (no traceback)
                assert record.exc_info is None
                # Plugin ID and exception class should be present
                assert "broken.memory" in formatted
                assert "RuntimeError" in formatted
        finally:
            logger.removeHandler(handler)

    def test_hook_callback_cannot_mutate_nested_original_payload(self) -> None:
        """A callback that mutates nested payload fields cannot affect the caller's original."""
        runtime = PluginRuntime()
        original_note = "AUTHORITY CONTENT"

        def mutating_callback(payload):
            payload["memories"][0]["content"]["note"] = "PLUGIN FORGED CONTENT"
            payload["memories"].append({"record_id": "plugin-added"})
            return None

        runtime.register_callable(
            hook_type="memory",
            hook_name="filter",
            plugin_id="mutator",
            callback=mutating_callback,
        )

        caller_payload = {"memories": [{"record_id": "obs-1", "content": {"note": original_note}}]}
        report = runtime.run_memory_hooks_with_diagnostics("filter", caller_payload)

        # Caller's original payload must be unchanged
        assert caller_payload["memories"][0]["content"]["note"] == original_note
        assert len(caller_payload["memories"]) == 1
        # No effects from a None return
        assert report.effects == ()

    def test_peer_plugin_isolation(self) -> None:
        """Second plugin sees original payload, not first plugin's mutation."""
        runtime = PluginRuntime()
        second_plugin_saw = []

        def first_mutates(payload):
            payload["memories"][0]["content"]["note"] = "MUTATED BY FIRST"
            return None

        def second_records(payload):
            second_plugin_saw.append(dict(payload["memories"][0]))
            return {"filtered": True}

        runtime.register_callable(
            hook_type="memory", hook_name="filter",
            plugin_id="first", callback=first_mutates,
        )
        runtime.register_callable(
            hook_type="memory", hook_name="filter",
            plugin_id="second", callback=second_records,
        )

        caller_payload = {"memories": [{"record_id": "obs-1", "content": {"note": "ORIGINAL"}}]}
        report = runtime.run_memory_hooks_with_diagnostics("filter", caller_payload)

        # Second plugin must see the original content
        assert len(second_plugin_saw) == 1
        assert second_plugin_saw[0]["content"]["note"] == "ORIGINAL"
        # Caller's payload is also unchanged
        assert caller_payload["memories"][0]["content"]["note"] == "ORIGINAL"

    def test_hook_report_detaches_nested_effect_from_plugin_object(self) -> None:
        """Returned effect is detached from the plugin's original return dict."""
        runtime = PluginRuntime()
        plugin_return = {"memories": [{"record_id": "obs-1"}]}

        runtime.register_callable(
            hook_type="memory", hook_name="filter",
            plugin_id="test", callback=lambda payload: plugin_return,
        )

        report = runtime.run_memory_hooks_with_diagnostics("filter", {"memories": []})
        assert len(report.effects) == 1

        # Mutate the plugin's original return object
        plugin_return["memories"].append({"record_id": "added-later"})
        plugin_return["memories"][0]["record_id"] = "TAMPERED"

        # Report effect must be unchanged
        effect_memories = report.effects[0]["effect"]["memories"]
        assert len(effect_memories) == 1
        assert effect_memories[0]["record_id"] == "obs-1"

    def test_legacy_hook_result_detaches_nested_effect_from_plugin_object(self) -> None:
        """Legacy hook API also detaches returned effects from plugin objects."""
        runtime = PluginRuntime()
        plugin_return = {"memories": [{"record_id": "obs-1"}]}

        runtime.register_callable(
            hook_type="memory", hook_name="filter",
            plugin_id="test", callback=lambda payload: plugin_return,
        )

        result = runtime.run_memory_hooks("filter", {"memories": []})
        assert len(result) == 1

        # Mutate plugin's original
        plugin_return["memories"][0]["record_id"] = "TAMPERED"
        plugin_return["extra"] = True

        # Legacy result must be unchanged (structure: {plugin_id, hook_name, effect})
        effect = result[0]["effect"]
        assert effect["memories"][0]["record_id"] == "obs-1"
        assert "extra" not in effect

    def test_copy_failure_is_content_free_and_skips_plugin(self) -> None:
        """A payload that fails deep-copy is skipped with content-free diagnostic."""
        import logging
        from fireclaw_core.plugin import plugin_runtime as pr_module

        class UncopyableValue:
            """Object whose deepcopy raises."""
            def __deepcopy__(self, memo):
                raise RuntimeError("restricted copy detail")

        runtime = PluginRuntime()
        callback_called = []

        def should_not_run(payload):
            callback_called.append(True)
            return {"effect": True}

        runtime.register_callable(
            hook_type="memory", hook_name="filter",
            plugin_id="test.plugin", callback=should_not_run,
        )

        captured_records = []
        handler = logging.Handler()
        handler.emit = lambda record: captured_records.append(record)
        logger = pr_module.logger
        logger.addHandler(handler)
        try:
            # Payload contains an uncopyable nested value
            payload = {"memories": [], "uncopyable": UncopyableValue()}
            report = runtime.run_memory_hooks_with_diagnostics("filter", payload)

            # Callback should not have been invoked
            assert callback_called == []
            # Failure recorded with content-free info
            assert len(report.failures) == 1
            assert report.failures[0].plugin_id == "test.plugin"
            assert report.failures[0].exception_class == "RuntimeError"
            # No exception message in failure or logs
            assert "restricted copy detail" not in repr(report)
            for record in captured_records:
                assert "restricted copy detail" not in record.getMessage()
        finally:
            logger.removeHandler(handler)
