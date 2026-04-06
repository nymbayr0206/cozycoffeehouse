# -*- coding: utf-8 -*-
import logging

from odoo import _, api, models
from odoo.fields import Domain
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    def _get_payment_terminal_selection(self):
        selection = list(super()._get_payment_terminal_selection())
        if not any(value == "qpay" for value, _label in selection):
            selection.append(("qpay", self.env._("QPay")))
        return selection

    @api.model
    def _load_pos_self_data_domain(self, data, config):
        base_domain = super()._load_pos_self_data_domain(data, config)
        qpay_domain = [
            ("use_payment_terminal", "=", "qpay"),
            ("config_ids", "in", config.id),
        ]
        return Domain.OR([base_domain, qpay_domain])

    def _payment_request_from_kiosk(self, order):
        if self.use_payment_terminal == "qpay":
            return self._qpay_kiosk_create_invoice(order)
        return super()._payment_request_from_kiosk(order)

    def _qpay_kiosk_create_invoice(self, order):
        self.ensure_one()

        txn = self.env["qpay.transaction"].sudo().create(
            {
                "amount": order.amount_total,
                "description": order.name or order.pos_reference or _("Kiosk Order"),
                "partner_id": order.partner_id.id,
                "payment_method_id": self.id,
                "pos_order_id": order.id,
                "pos_order_uuid": order.uuid,
                "pos_reference": order.pos_reference or order.name,
            }
        )
        try:
            txn._create_qpay_invoice_for_kiosk()
        except Exception as exc:
            _logger.error("QPay kiosk invoice creation failed: %s", exc)
            return {"status": "error", "message": str(exc)}

        qr_b64 = ""
        if txn.qr_image:
            qr_b64 = txn.qr_image.decode() if isinstance(txn.qr_image, bytes) else txn.qr_image

        return {
            "status": "qpay_pending",
            "transaction_id": txn.id,
            "qr_image": qr_b64,
            "qpay_short_url": txn.qpay_short_url or "",
            "qpay_invoice_id": txn.qpay_invoice_id or "",
        }

    def _check_pos_user(self):
        if not self.env.su and not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessDenied()

    def _get_qpay_transaction_vals(self, data):
        self.ensure_one()

        pos_order = self.env["pos.order"].sudo()
        pos_order_id = data.get("pos_order_id")
        order_uuid = data.get("order_uuid")
        order_ref = data.get("order_ref")

        if pos_order_id:
            pos_order = pos_order.browse(pos_order_id)
        elif order_uuid:
            pos_order = pos_order.search([("uuid", "=", order_uuid)], limit=1)
        elif order_ref:
            pos_order = pos_order.search(
                [("pos_reference", "=", order_ref), ("state", "=", "draft")],
                limit=1,
            )

        if not pos_order.exists():
            pos_order = self.env["pos.order"].sudo()

        return {
            "amount": data.get("amount", 0),
            "description": order_ref or pos_order.pos_reference or _("POS Order"),
            "partner_id": data.get("partner_id") or pos_order.partner_id.id,
            "payment_method_id": self.id,
            "pos_order_id": pos_order.id,
            "pos_order_uuid": order_uuid or pos_order.uuid,
            "pos_reference": order_ref or pos_order.pos_reference or pos_order.name,
        }

    def _serialize_qpay_paid_status(self, txn):
        return {
            "status": "paid",
            "qpay_payment_id": txn.qpay_payment_id or "",
            "qpay_invoice_id": txn.qpay_invoice_id or "",
            "transaction_id": txn.id,
        }

    def qpay_create_invoice(self, data):
        self.ensure_one()
        self._check_pos_user()

        amount = data.get("amount", 0)
        if amount <= 0:
            return {"error": _("Amount must be greater than zero.")}

        try:
            txn = self.env["qpay.transaction"].sudo().create(self._get_qpay_transaction_vals(data))
            txn._create_qpay_invoice_for_kiosk()
        except Exception as exc:
            _logger.error("QPay POS invoice creation failed: %s", exc)
            return {"error": str(exc)}

        qr_b64 = ""
        if txn.qr_image:
            qr_b64 = txn.qr_image.decode() if isinstance(txn.qr_image, bytes) else txn.qr_image

        return {
            "transaction_id": txn.id,
            "qr_image": qr_b64,
            "qpay_short_url": txn.qpay_short_url or "",
            "qpay_invoice_id": txn.qpay_invoice_id or "",
        }

    def qpay_check_payment(self, data):
        self.ensure_one()
        self._check_pos_user()

        txn_id = data.get("transaction_id")
        if not txn_id:
            return {"status": "error", "message": _("Missing transaction_id.")}

        txn = self.env["qpay.transaction"].sudo().browse(txn_id)
        if not txn.exists():
            return {"status": "error", "message": _("Transaction not found.")}

        if txn.state == "paid":
            txn._on_payment_confirmed()
            return self._serialize_qpay_paid_status(txn)

        if txn.state in ("cancelled", "failed"):
            return {"status": "error", "message": _("The QPay transaction is no longer active.")}

        try:
            txn.action_check_payment()
        except Exception as exc:
            _logger.warning("QPay payment polling failed for %s: %s", txn.name, exc)

        if txn.state == "paid":
            return self._serialize_qpay_paid_status(txn)

        if txn.state in ("cancelled", "failed"):
            return {"status": "error", "message": _("The QPay transaction is no longer active.")}

        return {"status": "pending"}

    def qpay_cancel_invoice(self, data):
        self.ensure_one()
        self._check_pos_user()

        txn_id = data.get("transaction_id")
        if not txn_id:
            return {"success": True}

        txn = self.env["qpay.transaction"].sudo().browse(txn_id)
        if txn.exists() and txn.state in ("draft", "pending"):
            try:
                txn.action_cancel()
            except Exception as exc:
                _logger.warning("QPay cancel failed for %s: %s", txn.name, exc)

        return {"success": True}
