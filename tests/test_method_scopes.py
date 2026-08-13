from fireclaw_core.gateway.method_scopes import (
    ADMIN_SCOPE,
    APPROVALS_SCOPE,
    EMERGENCY_SCOPE,
    EMERGENCY_RECOVERY_SCOPE,
    PAIRING_SCOPE,
    READ_SCOPE,
    WRITE_SCOPE,
    AuthorizationResult,
    MethodDescriptor,
    all_descriptors,
    authorize_method,
    resolve_required_scope,
)


class TestScopeConstants:
    def test_scope_constants_are_strings(self):
        assert isinstance(ADMIN_SCOPE, str)
        assert isinstance(READ_SCOPE, str)
        assert isinstance(WRITE_SCOPE, str)
        assert isinstance(APPROVALS_SCOPE, str)
        assert isinstance(PAIRING_SCOPE, str)
        assert isinstance(EMERGENCY_SCOPE, str)
        assert isinstance(EMERGENCY_RECOVERY_SCOPE, str)

    def test_admin_scope_value(self):
        assert ADMIN_SCOPE == "admin"

    def test_read_scope_value(self):
        assert READ_SCOPE == "state.read"

    def test_write_scope_value(self):
        assert WRITE_SCOPE == "task.submit"


class TestMethodDescriptor:
    def test_descriptor_creation(self):
        desc = MethodDescriptor(
            method="GET /state",
            required_scope=READ_SCOPE,
            category="read",
            description="Robot state",
        )
        assert desc.method == "GET /state"
        assert desc.required_scope == READ_SCOPE
        assert desc.category == "read"

    def test_descriptor_frozen(self):
        desc = MethodDescriptor(
            method="GET /state",
            required_scope=READ_SCOPE,
            category="read",
        )
        try:
            desc.method = "changed"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestResolveRequiredScope:
    def test_known_method_returns_scope(self):
        scope = resolve_required_scope("GET /state")
        assert scope == READ_SCOPE

    def test_unknown_method_returns_admin(self):
        scope = resolve_required_scope("GET /totally-unknown")
        assert scope == ADMIN_SCOPE

    def test_post_missions_requires_submit(self):
        scope = resolve_required_scope("POST /missions")
        assert scope == WRITE_SCOPE

    def test_emergency_stop_requires_emergency(self):
        scope = resolve_required_scope("POST /emergency-stop")
        assert scope == EMERGENCY_SCOPE

    def test_resource_admission_recovery_requires_dedicated_scope(self):
        assert resolve_required_scope(
            "POST /resource-admission/recovery/request"
        ) == EMERGENCY_RECOVERY_SCOPE
        assert resolve_required_scope(
            "POST /resource-admission/recovery/confirm"
        ) == EMERGENCY_RECOVERY_SCOPE
        assert resolve_required_scope("GET /resource-admission") == READ_SCOPE

    def test_parameterized_path_matches(self):
        scope = resolve_required_scope("GET /tasks/task-123")
        assert scope == READ_SCOPE

    def test_parameterized_path_matches_events(self):
        scope = resolve_required_scope("GET /tasks/task-123/events")
        assert scope == READ_SCOPE

    def test_parameterized_cancel_matches(self):
        scope = resolve_required_scope("POST /tasks/task-123/cancel")
        assert scope == WRITE_SCOPE

    def test_mission_trace_matches(self):
        scope = resolve_required_scope("GET /missions/mission-456/trace")
        assert scope == READ_SCOPE

    def test_mission_cancel_matches(self):
        scope = resolve_required_scope("POST /missions/mission-456/cancel")
        assert scope == WRITE_SCOPE

    def test_mission_approvals_matches(self):
        scope = resolve_required_scope("POST /missions/mission-456/approvals")
        assert scope == APPROVALS_SCOPE


class TestAuthorizeMethod:
    def test_admin_allows_everything(self):
        result = authorize_method("POST /missions", {ADMIN_SCOPE})
        assert result.allowed is True

    def test_read_scope_allows_read_endpoint(self):
        result = authorize_method("GET /state", {READ_SCOPE})
        assert result.allowed is True

    def test_write_scope_allows_read_endpoint(self):
        result = authorize_method("GET /state", {WRITE_SCOPE})
        assert result.allowed is True

    def test_read_scope_denies_write_endpoint(self):
        result = authorize_method("POST /missions", {READ_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == WRITE_SCOPE

    def test_empty_scopes_denies_read(self):
        result = authorize_method("GET /state", set())
        assert result.allowed is False
        assert result.missing_scope == READ_SCOPE

    def test_unknown_endpoint_requires_admin(self):
        result = authorize_method("GET /unknown", {WRITE_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == ADMIN_SCOPE

    def test_approval_scope_required_for_approvals(self):
        result = authorize_method("POST /missions/abc/approvals", {WRITE_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == APPROVALS_SCOPE

    def test_emergency_scope_required_for_emergency(self):
        result = authorize_method("POST /emergency-stop", {WRITE_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == EMERGENCY_SCOPE

    def test_pairing_scope_required_for_enrollment(self):
        result = authorize_method("POST /enrollment", {WRITE_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == PAIRING_SCOPE

    def test_write_implies_read_for_missions_trace(self):
        result = authorize_method("GET /missions/abc/trace", {WRITE_SCOPE})
        assert result.allowed is True

    def test_write_implies_read_for_fleet_state(self):
        result = authorize_method("GET /fleet/state", {WRITE_SCOPE})
        assert result.allowed is True

    def test_approvals_does_not_imply_write(self):
        result = authorize_method("POST /missions", {APPROVALS_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == WRITE_SCOPE


class TestAllDescriptors:
    def test_returns_list(self):
        descriptors = all_descriptors()
        assert isinstance(descriptors, list)
        assert len(descriptors) > 0

    def test_contains_robot_gateway_endpoints(self):
        descriptors = all_descriptors()
        methods = [d.method for d in descriptors]
        assert "GET /state" in methods
        assert "POST /tasks" in methods
        assert "POST /emergency-stop" in methods

    def test_contains_mission_gateway_endpoints(self):
        descriptors = all_descriptors()
        methods = [d.method for d in descriptors]
        assert "POST /missions" in methods
        assert "GET /fleet/state" in methods

    def test_sorted_by_method(self):
        descriptors = all_descriptors()
        methods = [d.method for d in descriptors]
        assert methods == sorted(methods)


class TestAuthorizationResult:
    def test_allowed_result(self):
        result = AuthorizationResult(allowed=True)
        assert result.allowed is True
        assert result.missing_scope is None

    def test_denied_result(self):
        result = AuthorizationResult(allowed=False, missing_scope=WRITE_SCOPE)
        assert result.allowed is False
        assert result.missing_scope == WRITE_SCOPE


class TestGatewayScopeEnforcement:
    """Test that gateways enforce method scopes end-to-end."""

    def _make_gateway(self, tmp_path):
        from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
        gateway = FireClawGateway(
            GatewayConfig(
                adapter="dry-run",
                port=0,
                memory_path=str(tmp_path / "memory.jsonl"),
            )
        )
        gateway.start()
        return gateway

    def test_caller_scope_header_does_not_affect_get_state(self, tmp_path):
        import urllib.request
        gateway = self._make_gateway(tmp_path)
        try:
            req = urllib.request.Request(
                f"{gateway.base_url}/state",
                headers={"X-Operator-Scopes": "state.read"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            gateway.stop()

    def test_caller_scope_header_cannot_narrow_loopback_principal(self, tmp_path):
        import urllib.request
        import json
        gateway = self._make_gateway(tmp_path)
        try:
            data = json.dumps({"command": "test"}).encode()
            req = urllib.request.Request(
                f"{gateway.base_url}/tasks",
                data=data,
                headers={
                    "X-Operator-Scopes": "state.read",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 202
        finally:
            gateway.stop()

    def test_loopback_principal_allows_post_tasks(self, tmp_path):
        import urllib.request
        import json
        gateway = self._make_gateway(tmp_path)
        try:
            data = json.dumps({"command": "test"}).encode()
            req = urllib.request.Request(
                f"{gateway.base_url}/tasks",
                data=data,
                headers={
                    "X-Operator-Scopes": "task.submit",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 202
        finally:
            gateway.stop()

    def test_no_scope_header_uses_server_owned_loopback_scopes(self, tmp_path):
        import urllib.request
        import json
        gateway = self._make_gateway(tmp_path)
        try:
            # The local OS boundary authenticates this request.
            req = urllib.request.Request(f"{gateway.base_url}/state")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200

            # Endpoint scopes come from the trusted loopback principal, not a
            # caller-provided scope header.
            data = json.dumps({"command": "test"}).encode()
            req = urllib.request.Request(
                f"{gateway.base_url}/tasks",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 202
        finally:
            gateway.stop()

    def test_health_bypasses_scope_enforcement(self, tmp_path):
        import urllib.request
        gateway = self._make_gateway(tmp_path)
        try:
            # Health check should work even with empty scopes
            req = urllib.request.Request(
                f"{gateway.base_url}/health",
                headers={"X-Operator-Scopes": ""},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            gateway.stop()

    def test_caller_scope_header_cannot_narrow_emergency_privilege(self, tmp_path):
        import urllib.request
        import json
        gateway = self._make_gateway(tmp_path)
        try:
            # The declared task.submit scope is untrusted and ignored.
            data = json.dumps({}).encode()
            req = urllib.request.Request(
                f"{gateway.base_url}/emergency-stop",
                data=data,
                headers={
                    "X-Operator-Scopes": "task.submit",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            gateway.stop()
