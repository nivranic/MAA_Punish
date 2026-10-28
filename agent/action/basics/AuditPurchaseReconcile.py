"""Explicit, zero-input manual reconciliation through an audited short task."""

import hashlib
import time

from maa.custom_action import CustomAction

from audit_commit import (
    AUDIT_CONFIG, CommitDenied, PendingStore, PurchaseCommit, boottime_ms,
    canonical, read_object, runtime_paths, validate_binding,
)
import purchase_reconciliation_contract as contract
from logger_component import LoggerComponent


NODE = "审计购买对账"


class AuditPurchaseReconcile(CustomAction):
    def __init__(self):
        super().__init__()
        self._logger_component = LoggerComponent(__name__)

    def run(self, context, argv):
        try:
            paths = runtime_paths()
            node = context.get_node_data(NODE)
            if (argv.node_name != NODE or not AUDIT_CONFIG.enabled or type(node) is not dict or
                    node.get("action", {}).get("type") != "Custom" or
                    node.get("action", {}).get("param", {}).get("custom_action") != "AuditPurchaseReconcile"):
                raise CommitDenied("invalid_reconciliation_entry")
            identity, root = validate_binding(node.get("attach", {}).get("audit_run"), AUDIT_CONFIG, paths)
            guard = PurchaseCommit(None, config=AUDIT_CONFIG)
            guard.stopped(context)
            guard.state(root, identity)
            # The explicit host command attaches after this maintenance run is
            # published. This wait performs no captures, recognition or input.
            deadline = boottime_ms() + 30_000
            request = None
            while boottime_ms() < deadline:
                guard.stopped(context)
                guard.state(root, identity)
                try:
                    candidate = read_object(root / "purchase-reconcile.json", contract.MAX_RECONCILE_BYTES)
                except FileNotFoundError:
                    time.sleep(0.05)
                    continue
                if candidate.get("maintenance_identity") != identity:
                    time.sleep(0.05)
                    continue  # An old request cannot acquire this new run.
                request = candidate
                break
            if request is None:
                raise CommitDenied("reconciliation_request_timeout")
            runtime = contract.loaded_runtime(
                read_object(root / "source-state.json", 1_048_576),
                read_object(root / "reload-state.json"), identity)
            if request.get("runtime") != runtime:
                raise CommitDenied("reconciliation_runtime_mismatch")

            def current():
                guard.stopped(context)
                guard.state(root, identity)
                if (context.get_node_data(NODE) != node or
                        contract.loaded_runtime(read_object(root / "source-state.json", 1_048_576),
                            read_object(root / "reload-state.json"), identity) != runtime):
                    raise CommitDenied("reconciliation_runtime_or_node_changed")
                now = boottime_ms()
                if not now < request["expires_boottime_ms"] <= now + 30_000:
                    raise CommitDenied("reconciliation_request_expired")
                # This is the software linearization point, after the final I/O.
                # An asynchronous stop after this check cannot be made atomic
                # with Python or storage; no such atomicity is claimed.
                guard.stopped(context)
                return now

            store = PendingStore(paths.marker_root)
            with store.boundary(allow_pending=True) as entries:
                matching = [item for item in entries if item["slot"] == request.get("pending_slot")]
                if len(matching) != 1:
                    raise CommitDenied("reconciliation_pending_missing")
                entry = matching[0]
                contract.validate_request(request, entry["pending"], entry["claim"])
                directory = entry["directory"]
                now = current()
                revalidating = request["operation"] == "revalidate_purchase"
                if revalidating:
                    if entry["receipt"] is None:
                        raise CommitDenied("revalidation_needs_original_receipt")
                    contract.validate_revalidation(request, entry["receipt"], entry["pending"], entry["claim"])
                elif entry["receipt"] is not None:
                    if entry["receipt"]["request"] != request or not entry["prepared"]:
                        raise CommitDenied("explicit_fresh_revalidation_required")
                    return self.RunResult(success=True)
                try:
                    if revalidating:
                        receipt = entry["receipt"]
                        # entries() already re-fsynced every original witness.
                        # Revalidation issues a live vote, never another receipt.
                    else:
                        receipt = dict(version=1, state="confirmed_consumed", request=request,
                                       reconciled_boottime_ms=now)
                        receipt["checksum_sha256"] = contract.digest(receipt)
                        store.write_durable(directory / "reconcile.claim", (request["request_id"] + "\n").encode("ascii"))
                        store.write_durable(directory / "reconciled.json", canonical(receipt))
                        current()
                        store.write_durable(directory / "reconciled.done", canonical({"receipt_sha256": contract.digest(receipt)}))
                        store.entries()  # Re-fsync/read back all three witnesses.
                    self._logger_component.emit_audit(dict(
                        audit_event="purchase_reconciliation_ready", maintenance_identity=identity,
                        request_id=request["request_id"], receipt_sha256=contract.digest(receipt),
                        pending_sha256=contract.digest(entry["pending"]),
                        claim_sha256=hashlib.sha256(entry["claim"]).hexdigest(), runtime=runtime))
                    current()
                    # No disk, log or native call follows the final current-run
                    # check. A crash before this vote leaves the boundary closed.
                    store.accept_reconciliation(directory, receipt, revalidated=revalidating)
                except Exception:
                    try:
                        store.fail_reconciliation(directory)
                    except Exception:
                        pass  # The in-memory failure vote was set before I/O.
                    raise
            return self.RunResult(success=True)
        except Exception as error:
            # The outer owner joins stop; callbacks must not wait on themselves.
            context.tasker.post_stop()
            try:
                self._logger_component.logger.error("Purchase reconciliation blocked: %s", error)
            except Exception:
                pass
            return self.RunResult(success=False)
