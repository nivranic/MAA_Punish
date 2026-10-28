"""Strict manual-effect reconciliation schema, shared by host and Agent.

No file operations, run discovery, OCR, input or automatic success inference live
here. An operator supplies annotated before/after evidence; both consumers check
its arithmetic and exact reservation binding independently.
"""

from datetime import datetime
import hashlib
import json
import re


MAX_INTEGER = 2**53 - 1
MAX_RECONCILE_BYTES = 65_536
MAX_RUN_LOG_BYTES = 262_144
MAX_ACCEPTANCE_BYTES = 1_048_576
RUN_LOG_NAME = re.compile(r"run_\d{8}_\d{6}_1(?:_\d+)?\.jsonl\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
PACKAGE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\Z")
IDENTITY = {"package", "app_instance", "session_id", "execution_id", "run_nonce"}
INTENT = {"cycle_id", "target", "currency", "unit_price", "quantity", "budget_amount", "budget_quantity"}
RUNTIME = {"source_revision", "native_generation", "agent_generation", "display_id"}
REQUEST = {"version", "operation", "request_id", "maintenance_identity", "runtime", "pending_slot",
           "pending_sha256", "claim_sha256", "case_record_sha256", "reservation", "effect",
           "expires_boottime_ms"}
CLAIM_BYTES = b"pending_unconfirmed\n"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def fields(value, expected):
    if type(value) is not dict or set(value) != expected:
        raise ValueError("unexpected reconciliation fields")


def text(value, pattern=NAME):
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError("invalid reconciliation identifier")
    return value


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValueError("invalid reconciliation integer")
    return value


def identity(value):
    fields(value, IDENTITY)
    text(value["package"], PACKAGE)
    for key in ("app_instance", "session_id", "run_nonce"):
        text(value[key], TOKEN)
    text(value["execution_id"])
    return value


def intent(value):
    fields(value, INTENT)
    for key in ("cycle_id", "target", "currency"):
        text(value[key])
    for key in ("unit_price", "budget_amount", "budget_quantity"):
        integer(value[key])
    integer(value["quantity"], 1)
    if (integer(value["unit_price"] * value["quantity"]) > value["budget_amount"] or
            value["quantity"] > value["budget_quantity"]):
        raise ValueError("reconciliation intent exceeds budget")
    return value


def timestamp(value):
    if type(value) is not str or len(value) > 48:
        raise ValueError("invalid evidence timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid evidence timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamp needs timezone")
    return parsed


def annotated_number(value, width, height):
    fields(value, {"value", "text", "box"})
    integer(value["value"])
    if (type(value["text"]) is not str or len(value["text"]) > 32 or
            not re.fullmatch(r"[0-9, ]+", value["text"]) or
            value["text"].replace(",", "").replace(" ", "") != str(value["value"])):
        raise ValueError("evidence text does not match annotated number")
    box = value["box"]
    if type(box) is not list or len(box) != 4:
        raise ValueError("invalid evidence annotation box")
    x, y, w, h = [integer(item) for item in box]
    if w < 1 or h < 1 or x + w > width or y + h > height:
        raise ValueError("evidence annotation outside frame")
    return value["value"]


def evidence_frame(value):
    fields(value, {"png_sha256", "width", "height", "captured_at", "product_text", "currency_text",
                   "balance", "inventory", "remaining_limit"})
    text(value["png_sha256"], HASH)
    width, height = integer(value["width"], 1), integer(value["height"], 1)
    if width > 16_384 or height > 16_384:
        raise ValueError("evidence image dimensions exceed bound")
    timestamp(value["captured_at"])
    for key in ("product_text", "currency_text"):
        if type(value[key]) is not str or not 1 <= len(value[key].strip()) <= 128:
            raise ValueError("missing visible product/currency evidence")
    for key in ("balance", "inventory", "remaining_limit"):
        if key == "balance" or value[key] is not None:
            annotated_number(value[key], width, height)
    return value


def validate_effect(value, purchase):
    fields(value, {"version", "state", "target", "currency", "unit_price", "quantity",
                   "inventory_units_per_purchase", "before", "after", "reviewer", "reviewed_at"})
    if type(value["version"]) is not int or value["version"] != 1 or value["state"] != "confirmed_consumed":
        raise ValueError("only explicit confirmed consumption can reconcile")
    for key in ("target", "currency", "unit_price", "quantity"):
        if value[key] != purchase[key] or type(value[key]) is not type(purchase[key]):
            raise ValueError("effect differs from original purchase")
    text(value["reviewer"])
    before, after = evidence_frame(value["before"]), evidence_frame(value["after"])
    if before["png_sha256"] == after["png_sha256"]:
        raise ValueError("before/after must be independent evidence frames")
    if not timestamp(before["captured_at"]) < timestamp(after["captured_at"]) <= timestamp(value["reviewed_at"]):
        raise ValueError("evidence timestamps are not ordered")
    if any(before[key] != after[key] for key in ("product_text", "currency_text")):
        raise ValueError("effect evidence changes product or currency")
    amount = purchase["unit_price"] * purchase["quantity"]
    if before["balance"]["value"] - after["balance"]["value"] != amount:
        raise ValueError("balance delta does not prove original cost")
    witnessed = False
    if before["inventory"] is not None or after["inventory"] is not None:
        if before["inventory"] is None or after["inventory"] is None:
            raise ValueError("inventory evidence must have both states")
        units = integer(value["inventory_units_per_purchase"], 1)
        if after["inventory"]["value"] - before["inventory"]["value"] != integer(purchase["quantity"] * units):
            raise ValueError("inventory delta does not prove purchased units")
        witnessed = True
    elif value["inventory_units_per_purchase"] is not None:
        raise ValueError("inventory unit count has no image evidence")
    if before["remaining_limit"] is not None or after["remaining_limit"] is not None:
        if before["remaining_limit"] is None or after["remaining_limit"] is None:
            raise ValueError("limit evidence must have both states")
        if before["remaining_limit"]["value"] - after["remaining_limit"]["value"] != purchase["quantity"]:
            raise ValueError("limit delta does not prove purchased count")
        witnessed = True
    if not witnessed:
        raise ValueError("cost alone does not prove receipt of goods")
    return value


def validate_pending(value):
    fields(value, IDENTITY | {"collector_id", "source_challenge", "stream_id", "sequence", "source_nonce",
        "intent_sha256", "source_boottime_ms", "expires_boottime_ms", "version", "state", "case_id",
        "intent", "observation", "requested", "controller_completed", "effect"})
    identity({key: value[key] for key in IDENTITY})
    for key in ("collector_id", "source_challenge", "stream_id", "source_nonce"):
        text(value[key], TOKEN)
    if (type(value["version"]) is not int or value["version"] != 1 or
            value["state"] != "pending_unconfirmed" or value["requested"] is not True or
            value["controller_completed"] is not None or value["effect"] != "unconfirmed" or
            type(value["case_id"]) is not str or value["case_id"] not in {"D12", "D13", "D14"}):
        raise ValueError("invalid original pending state")
    integer(value["sequence"], 1)
    integer(value["source_boottime_ms"])
    integer(value["expires_boottime_ms"])
    if value["expires_boottime_ms"] != value["source_boottime_ms"] + 5_000:
        raise ValueError("invalid original pending deadline")
    purchase = intent(value["intent"])
    if value["intent_sha256"] != digest(purchase):
        raise ValueError("pending purchase hash mismatch")
    observation = value["observation"]
    fields(observation, IDENTITY | {"observed_boottime_ms", "frame_sha256", "target", "currency", "unit_price", "quantity"})
    if any(observation[key] != value[key] for key in IDENTITY):
        raise ValueError("pending observation belongs to another run")
    text(observation["frame_sha256"], HASH)
    integer(observation["observed_boottime_ms"])
    if (not 0 <= value["source_boottime_ms"] - observation["observed_boottime_ms"] < 5_000 or
            any(observation[key] != purchase[key] or type(observation[key]) is not type(purchase[key])
                for key in ("target", "currency", "unit_price", "quantity"))):
        raise ValueError("pending observation differs from original purchase")
    return value


def validate_request(value, pending, claim):
    revalidating = type(value) is dict and value.get("operation") == "revalidate_purchase"
    fields(value, REQUEST | ({"prior_receipt_sha256"} if revalidating else set()))
    validate_pending(pending)
    if (type(value["version"]) is not int or value["version"] != 1 or
            value["operation"] not in {"reconcile_purchase", "revalidate_purchase"}):
        raise ValueError("invalid reconciliation operation")
    if revalidating:
        text(value["prior_receipt_sha256"], HASH)
    text(value["request_id"], TOKEN)
    identity(value["maintenance_identity"])
    if value["maintenance_identity"]["package"] != pending["package"]:
        raise ValueError("maintenance belongs to another package")
    fields(value["runtime"], RUNTIME)
    text(value["runtime"]["source_revision"], HASH)
    for key in ("native_generation", "agent_generation"):
        integer(value["runtime"][key], 1)
    integer(value["runtime"]["display_id"])
    if value["pending_slot"] != "legacy":
        text(value["pending_slot"], TOKEN)
        if value["pending_slot"] != pending["source_nonce"]:
            raise ValueError("pending slot/source mismatch")
    if (claim != CLAIM_BYTES or value["pending_sha256"] != digest(pending) or
            value["claim_sha256"] != hashlib.sha256(claim).hexdigest()):
        raise ValueError("original pending/claim hash mismatch")
    text(value["case_record_sha256"], HASH)
    reservation = value["reservation"]
    fields(reservation, {"status", "channel", "stream_id", "sequence", "source_nonce", "intent", "intent_sha256",
                         "source_boottime_ms", "host_received_at", "source_envelope"})
    fields(reservation["channel"], IDENTITY | {"collector_id", "source_challenge"})
    if reservation["status"] != "pending_unconfirmed":
        raise ValueError("original reservation is not pending")
    for key in ("stream_id", "sequence", "source_nonce", "intent", "intent_sha256", "source_boottime_ms"):
        if reservation[key] != pending[key] or type(reservation[key]) is not type(pending[key]):
            raise ValueError("host reservation differs from phone pending")
    if any(reservation["channel"][key] != pending[key] for key in reservation["channel"]):
        raise ValueError("host reservation has another original run/channel")
    envelope = reservation["source_envelope"]
    fields(envelope, {"event", "session_id", "stream_id", "sequence", "monotonic_ns", "level", "logger", "message"})
    if (envelope["event"] != "maa_punish_audit" or envelope["level"] != "INFO" or
            any(envelope[key] != pending[key] for key in ("session_id", "stream_id", "sequence")) or
            type(envelope["message"]) is not str or type(envelope["logger"]) is not str):
        raise ValueError("original source envelope identity mismatch")
    integer(envelope["monotonic_ns"])
    try:
        source = json.loads(envelope["message"])
    except (ValueError, TypeError) as error:
        raise ValueError("invalid original source message") from error
    fields(source, {"audit_event", "execution_id", "run_nonce", "collector_id", "source_challenge",
                    "source_boottime_ms", "case_id", "source_nonce", "intent", "intent_sha256"})
    if (source["audit_event"] != "prepare_commit" or canonical(source).decode("utf-8") != envelope["message"] or
            any(source[key] != pending[key] for key in source if key != "audit_event")):
        raise ValueError("original envelope does not bind case/nonce/intent")
    validate_effect(value["effect"], pending["intent"])
    reserved_at = timestamp(reservation["host_received_at"])
    if not timestamp(value["effect"]["before"]["captured_at"]) <= reserved_at <= timestamp(value["effect"]["after"]["captured_at"]):
        raise ValueError("effect frames do not bracket the original reservation")
    integer(value["expires_boottime_ms"])
    return value


def validate_receipt(value, pending, claim):
    fields(value, {"version", "state", "request", "reconciled_boottime_ms", "checksum_sha256"})
    if type(value["version"]) is not int or value["version"] != 1 or value["state"] != "confirmed_consumed":
        raise ValueError("invalid reconciliation receipt state")
    integer(value["reconciled_boottime_ms"])
    if value["checksum_sha256"] != digest({key: item for key, item in value.items() if key != "checksum_sha256"}):
        raise ValueError("reconciliation receipt checksum mismatch")
    validate_request(value["request"], pending, claim)
    if value["request"]["operation"] != "reconcile_purchase":
        raise ValueError("a revalidation cannot replace the original receipt")
    if value["reconciled_boottime_ms"] >= value["request"]["expires_boottime_ms"]:
        raise ValueError("receipt was created after reconciliation expired")
    return value


def validate_revalidation(request, receipt, pending, claim):
    """Fresh current-run request for the exact already proven consumption fact."""
    validate_request(request, pending, claim)
    validate_receipt(receipt, pending, claim)
    if (request["operation"] != "revalidate_purchase" or
            request["prior_receipt_sha256"] != digest(receipt) or
            request["request_id"] == receipt["request"]["request_id"]):
        raise ValueError("revalidation does not bind a fresh request to the original receipt")
    for key in ("pending_slot", "pending_sha256", "claim_sha256", "reservation", "effect"):
        if request[key] != receipt["request"][key]:
            raise ValueError("revalidation changed the original consumption evidence")
    return request


def _log_object(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate terminal-evidence key")
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite terminal evidence")))
    if type(value) is not dict:
        raise ValueError("terminal evidence must be an object")
    return value


def validate_terminal_log(raw, request, receipt, pending, claim):
    """Consume real App JSONL, never a user-supplied success boolean.

    The host reads this text twice from the fixed device log directory. Keeping
    its exact bytes here lets the phone independently check every binding/order.
    """
    validate_receipt(receipt, pending, claim)
    validate_request(request, pending, claim)
    if request["operation"] == "revalidate_purchase":
        validate_revalidation(request, receipt, pending, claim)
    elif request != receipt["request"]:
        raise ValueError("terminal request differs from original receipt")
    if type(raw) is not str or not raw.endswith("\n") or len(raw.encode("utf-8")) > MAX_RUN_LOG_BYTES:
        raise ValueError("invalid or incomplete maintenance log")
    records = [_log_object(line) for line in raw.splitlines()]
    if not 4 <= len(records) <= 2048:
        raise ValueError("invalid maintenance-log record count")
    header, footer = records[0], records[-1]
    fields(header, {"type", "startedAt", "tasks"})
    fields(footer, {"type", "endedAt", "outcome"})
    if (header["type"] != "header" or header["tasks"] != ["Screencap"] or
            footer["type"] != "footer" or footer["outcome"] != "COMPLETED" or
            integer(footer["endedAt"]) < integer(header["startedAt"])):
        raise ValueError("maintenance run was not a single completed short task")
    ready = []
    terminals = []
    results = []
    expected_ready = dict(audit_event="purchase_reconciliation_ready",
        maintenance_identity=request["maintenance_identity"], request_id=request["request_id"],
        receipt_sha256=digest(receipt), pending_sha256=digest(pending),
        claim_sha256=hashlib.sha256(claim).hexdigest(), runtime=request["runtime"])
    for index, record in enumerate(records[1:-1], 1):
        fields(record, {"type", "atMillis", "kind", "text", "detail"})
        if (record["type"] != "line" or type(record["text"]) is not str or
                type(record["kind"]) is not str or
                not header["startedAt"] <= integer(record["atMillis"]) <= footer["endedAt"]):
            raise ValueError("invalid maintenance log line")
        message = record["text"]
        # Agent stdout is batched by the App, so one outer record can contain
        # several complete audit envelopes. Keep their outer index/kind/time;
        # only real LF boundaries frame them, never join fragments or reorder.
        for audit_line in message.split("\n"):
            if not audit_line.startswith("{"):
                continue
            envelope = _log_object(audit_line)
            if envelope.get("event") == "maa_punish_audit":
                fields(envelope, {"event", "session_id", "stream_id", "sequence", "monotonic_ns", "level", "logger", "message"})
                event = (_log_object(envelope["message"]) if type(envelope["message"]) is str and
                         envelope["message"].startswith("{") else {})
                if event.get("audit_event") == "purchase_reconciliation_ready":
                    if (record["kind"] != "Agent" or event != expected_ready or envelope["session_id"] != request["maintenance_identity"]["session_id"] or
                            envelope["level"] != "INFO"):
                        raise ValueError("maintenance ready event does not bind this receipt")
                    text(envelope["stream_id"], TOKEN)
                    integer(envelope["sequence"], 1)
                    integer(envelope["monotonic_ns"], 1)
                    ready.append(index)
        if message.startswith("purchase_maintenance_terminal "):
            if record["kind"] != "Info":
                raise ValueError("agent output cannot attest a native maintenance terminal")
            terminal = _log_object(message[len("purchase_maintenance_terminal "):])
            fields(terminal, {"version", "maintenance_identity", "entry", "task_id", "wait_status", "native_status",
                "node_count", "completed_false_count", "single_entry_completed", "effective_success", "outcome"})
            if (type(terminal["version"]) is not int or terminal["version"] != 1 or
                    terminal["maintenance_identity"] != request["maintenance_identity"] or
                    terminal["entry"] != "审计购买对账" or terminal["single_entry_completed"] is not True or
                    terminal["effective_success"] is not True or terminal["outcome"] != "COMPLETED" or
                    integer(terminal["wait_status"]) != 3000 or integer(terminal["native_status"]) != 3000 or
                    integer(terminal["node_count"]) != 1 or integer(terminal["completed_false_count"]) != 0):
                raise ValueError("native maintenance result is not proven successful")
            integer(terminal["task_id"], 1)
            terminals.append((index, terminal))
        if message.startswith("task_result "):
            if record["kind"] != "Info":
                raise ValueError("agent output cannot attest an App task result")
            results.append((index, message))
    if len(ready) != 1 or len(terminals) != 1 or len(results) != 1:
        raise ValueError("missing or ambiguous maintenance terminal evidence")
    terminal_index, terminal = terminals[0]
    result_index, result = results[0]
    expected = (rf"task_result execution_id={re.escape(request['maintenance_identity']['execution_id'])} "
        rf"result_index=0 task=Screencap effective_success=true; succeeded; task_id={terminal['task_id']}; "
        r"wait_status=3000; recovery_history=no_shared_recovery; native_status=3000; purchase_maintenance_native=(\{[^\n]*\})")
    match = re.fullmatch(expected, result)
    if not match or not ready[0] < terminal_index < result_index < len(records) - 1:
        raise ValueError("maintenance result order or native task binding is invalid")
    native = _log_object(match[1])
    native_keys = {"entry", "task_id", "wait_status", "native_status", "node_count", "completed_false_count", "single_entry_completed"}
    fields(native, native_keys)
    for key in native_keys - {"entry", "single_entry_completed"}:
        integer(native[key])
    if native["single_entry_completed"] is not True or native != {key: terminal[key] for key in native_keys}:
        raise ValueError("native task result payload contradicts its App terminal")
    return terminal


def acceptance_for(request, receipt, pending, claim, filename, raw):
    text(filename, RUN_LOG_NAME)
    validate_terminal_log(raw, request, receipt, pending, claim)
    return dict(version=1, operation="accept_purchase_reconciliation",
        pending_sha256=digest(pending), claim_sha256=hashlib.sha256(claim).hexdigest(),
        receipt_sha256=digest(receipt), maintenance_request=request, run_log_filename=filename,
        run_log_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(), run_log=raw)


def validate_acceptance(value, receipt, pending, claim):
    fields(value, {"version", "operation", "pending_sha256", "claim_sha256", "receipt_sha256",
                   "maintenance_request", "run_log_filename", "run_log_sha256", "run_log"})
    expected = acceptance_for(value["maintenance_request"], receipt, pending, claim,
                              value["run_log_filename"], value["run_log"])
    if value != expected:
        raise ValueError("final acceptance does not bind the original durable receipt")
    return value


def loaded_runtime(source, reload, current):
    """Bind disk manifest to the same session's actually loaded acknowledgement."""
    fields(source, {"version", "package", "apk_version_code", "revision", "files"})
    if (type(source["version"]) is not int or source["version"] != 1 or
            source["package"] != current["package"]):
        raise ValueError("source package/version mismatch")
    integer(source["apk_version_code"], 1)
    text(source["revision"], HASH)
    entries = source["files"]
    if type(entries) is not list or not 1 <= len(entries) <= 4096:
        raise ValueError("source manifest has invalid entries")
    canonical_entries = []
    for entry in entries:
        fields(entry, {"path", "size", "sha256"})
        path = entry["path"]
        if type(path) is not str or not 1 <= len(path) <= 1024:
            raise ValueError("source manifest path is invalid")
        integer(entry["size"])
        text(entry["sha256"], HASH)
        canonical_entries.append({"path": path, "size": entry["size"], "sha256": entry["sha256"]})
    paths = [entry["path"] for entry in canonical_entries]
    if paths != sorted(set(paths)):
        raise ValueError("source manifest paths are not sorted/unique")
    # Matches tools/android_debug.py source_revision; its per-file keys are
    # path,size,sha256 and its JSON encoder deliberately does not sort keys.
    source_digest = hashlib.sha256(json.dumps(canonical_entries, separators=(",", ":"),
                                            ensure_ascii=False).encode("utf-8")).hexdigest()
    if source_digest != source["revision"]:
        raise ValueError("source manifest revision mismatch")
    fields(reload, {"version", "session_id", "operation_id", "request_id", "phase", "revision", "reason",
                   "rolled_back", "native_generation", "agent_generation", "display_id",
                   "controller_reused", "resource_loaded", "agent_loaded"})
    if (type(reload["version"]) is not int or reload["version"] != 1 or
            reload["session_id"] != current["session_id"] or reload["phase"] != "loaded" or
            reload["revision"] != source["revision"] or reload["rolled_back"] is not False or
            any(reload[key] is not True for key in ("controller_reused", "resource_loaded", "agent_loaded"))):
        raise ValueError("loaded runtime acknowledgement is not current")
    for key in ("operation_id", "request_id"):
        text(reload[key], TOKEN)
    for key in ("native_generation", "agent_generation"):
        integer(reload[key], 1)
    integer(reload["display_id"])
    return {"source_revision": source["revision"],
            **{key: reload[key] for key in ("native_generation", "agent_generation", "display_id")}}
