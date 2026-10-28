"""Same-frame purchase observations; uncalibrated confirmation pages reject.

Contract: docs/plans/2026-10-23-iqoo16-persist-ack.md. Calibration is installed
source, never a value supplied by an audit plan. No screenshot or input is issued.
"""

from dataclasses import dataclass
import hashlib
import re

import numpy as np
from maa.custom_recognition import CustomRecognition

from audit_commit import (
    AUDIT_CONFIG, CommitDenied, PURCHASE_CASES, PurchaseCommit,
    boottime_ms, integer, runtime_paths, validate_binding,
)


@dataclass(frozen=True)
class PurchaseCalibration:
    """Recognition nodes and semantics established from a real page's images.

    Numeric nodes must isolate the displayed *unit* price and selected purchase
    count. A displayed total, inventory count or limit is not interchangeable.
    The full product title is exact OCR; currency is exact text or an installed
    same-frame icon template. Package contents and purchase count stay separate.
    """

    width: int
    height: int
    title_node: str
    title: str
    target: str
    currency_node: str
    currency_text: str
    currency: str
    unit_price_node: str
    quantity_node: str
    purchasable_node: str
    currency_template: str | None = None
    currency_roi: tuple[int, int, int, int] | None = None
    pack_node: str | None = None
    pack_text: str | None = None
    limit_node: str | None = None
    quantity_one_only: bool = False


# Calibrated only for the captured CN 1280x720 detail pages. The displayed spend
# is a total: it is a unit price only after the selected purchase count reads 1.
# D13's x5 is separately checked package contents, never five purchases.
CALIBRATIONS: dict[str, PurchaseCalibration] = {
    "D12": PurchaseCalibration(1280, 720, "审计购买_商品全名", "逆元碎片-明觉", "inverse_fragment_mingjue",
        "审计购买_螺母图标", "", "cogs", "审计购买_花费", "审计购买_份数", "审计购买_可提交",
        "购买审计/螺母.png", (653, 539, 31, 31), "审计购买_包内数", "x1", "审计购买_限购", True),
    "D13": PurchaseCalibration(1280, 720, "审计购买_商品全名", "涂装设计草稿", "coating_design_draft",
        "审计购买_宿舍币图标", "", "dorm_coins", "审计购买_花费", "审计购买_份数", "审计购买_可提交",
        "购买审计/宿舍币.png", (653, 539, 31, 31), "审计购买_包内数", "x5", "审计购买_限购", True),
    "D14": PurchaseCalibration(1280, 720, "审计购买_商品全名", "武器涂装设计图", "weapon_coating_blueprint",
        "审计购买_遣测凭证图标", "", "dispatch_voucher", "审计购买_花费", "审计购买_份数", "审计购买_可提交",
        "购买审计/遣测凭证.png", (653, 539, 31, 31), "审计购买_包内数", "x1", "审计购买_限购", True),
}
DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def single_text(context, node, image):
    """Use one native OCR result from this supplied frame, never cached values."""
    result = context.run_recognition(node, image=image)
    if not result or result.hit is not True or str(result.algorithm) != "OCR":
        raise CommitDenied("purchase_field_unreadable")
    results = result.filtered_results
    if len(results) != 1 or results[0] != result.best_result:
        raise CommitDenied("purchase_field_ambiguous")
    value = getattr(result.best_result, "text", None)
    if type(value) is not str:
        raise CommitDenied("purchase_field_unreadable")
    return value.strip()


def numeric_text(context, node, image, minimum):
    value = single_text(context, node, image)
    if len(value) > 16 or not DECIMAL.fullmatch(value):
        raise CommitDenied("purchase_number_ambiguous")
    return integer(int(value), minimum)


def currency_icon(context, calibration, image):
    node = context.get_node_data(calibration.currency_node)
    recognition = node.get("recognition", {}) if type(node) is dict else {}
    params = recognition.get("param", {})
    templates = params.get("template")
    if type(templates) is str:
        templates = [templates]
    if (recognition.get("type") != "TemplateMatch" or templates != [calibration.currency_template] or
            params.get("roi") != list(calibration.currency_roi)):
        raise CommitDenied("purchase_currency_node_changed")
    result = context.run_recognition(calibration.currency_node, image=image)
    if (not result or result.hit is not True or str(result.algorithm) != "TemplateMatch" or
            len(result.filtered_results) != 1 or result.filtered_results[0] != result.best_result):
        raise CommitDenied("purchase_currency_unreadable")
    score = getattr(result.best_result, "score", None)
    if type(score) not in (int, float) or not 0.98 <= score <= 1.0:
        raise CommitDenied("purchase_currency_mismatch")


class PurchaseObservation(CustomRecognition):
    case_id = ""

    def analyze(self, context, argv):
        try:
            case = PURCHASE_CASES[self.case_id]
            if argv.node_name != case.observation_node:
                raise CommitDenied("unexpected_observation_node")
            if not AUDIT_CONFIG.enabled:
                # This neutral child preserves the original normal-mode And and
                # button box. It is not an observation and is never consumed by
                # the audit boundary, which requires the exact identity schema.
                return self.AnalyzeResult(box=(0, 0, 1, 1), detail={"audit_enabled": False})
            if context.tasker.stopping:
                raise CommitDenied("task_stopping")
            calibration = CALIBRATIONS.get(self.case_id)
            if calibration is None:
                raise CommitDenied("purchase_page_uncalibrated")
            image = argv.image
            if (not isinstance(image, np.ndarray) or image.dtype != np.uint8 or
                    image.shape != (calibration.height, calibration.width, 3)):
                raise CommitDenied("purchase_frame_layout_mismatch")
            paths = runtime_paths()
            node = context.get_node_data(case.action_node)
            identity, root = validate_binding(node.get("attach", {}).get("audit_run"), AUDIT_CONFIG, paths)
            guard = PurchaseCommit(None, config=AUDIT_CONFIG)
            guard.state(root, identity)
            observed = boottime_ms()
            frame_sha256 = hashlib.sha256(image.tobytes()).hexdigest()
            if single_text(context, calibration.title_node, image) != calibration.title:
                raise CommitDenied("purchase_product_mismatch")
            if calibration.currency_template is not None:
                currency_icon(context, calibration, image)
            elif single_text(context, calibration.currency_node, image) != calibration.currency_text:
                raise CommitDenied("purchase_currency_mismatch")
            quantity = numeric_text(context, calibration.quantity_node, image, 1)
            if calibration.quantity_one_only and quantity != 1:
                raise CommitDenied("purchase_quantity_not_calibrated")
            price = numeric_text(context, calibration.unit_price_node, image, 0)
            if calibration.pack_node is not None and single_text(context, calibration.pack_node, image) != calibration.pack_text:
                raise CommitDenied("purchase_package_mismatch")
            if calibration.limit_node is not None:
                limit = single_text(context, calibration.limit_node, image)
                matched = re.fullmatch(r"还可购买([1-9][0-9]*)", limit)
                if not matched or integer(int(matched[1]), 1) < quantity:
                    raise CommitDenied("purchase_not_available")
            purchasable = context.run_recognition(calibration.purchasable_node, image=image)
            if not purchasable or purchasable.hit is not True:
                raise CommitDenied("purchase_not_available")
            # Recognition latency/lease changes do not refresh this observation.
            guard.state(root, identity)
            if context.tasker.stopping or not 0 <= boottime_ms() - observed < 5_000:
                raise CommitDenied("stale_observation")
            latest = context.get_node_data(case.action_node)
            if latest != node:
                raise CommitDenied("purchase_node_changed")
            if hashlib.sha256(image.tobytes()).hexdigest() != frame_sha256:
                raise CommitDenied("purchase_frame_changed")
            detail = dict(identity, observed_boottime_ms=observed,
                          frame_sha256=frame_sha256,
                          target=calibration.target, currency=calibration.currency,
                          unit_price=price, quantity=quantity)
            return self.AnalyzeResult(box=(0, 0, 1, 1), detail=detail)
        except Exception:
            # A failed observation never starts/stops tasks or supplies defaults.
            return None


class D12PurchaseObservation(PurchaseObservation):
    case_id = "D12"


class D13PurchaseObservation(PurchaseObservation):
    case_id = "D13"


class D14PurchaseObservation(PurchaseObservation):
    case_id = "D14"
