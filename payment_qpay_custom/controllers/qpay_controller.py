# -*- coding: utf-8 -*-
import logging

from werkzeug.exceptions import NotFound, Unauthorized

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class QPayController(http.Controller):
    def _verify_kiosk_pos_config(self, access_token):
        pos_config_sudo = request.env["pos.config"].sudo().search(
            [("access_token", "=", access_token)],
            limit=1,
        )
        if (
            not pos_config_sudo
            or pos_config_sudo.self_ordering_mode != "kiosk"
            or not pos_config_sudo.has_active_session
        ):
            raise Unauthorized("Invalid access token")

        company = pos_config_sudo.company_id
        user = pos_config_sudo.self_ordering_default_user_id
        return pos_config_sudo.sudo(False).with_company(company).with_user(user).with_context(
            allowed_company_ids=company.ids
        )

    def _get_kiosk_payment_method(self, access_token, payment_method_id):
        pos_config = self._verify_kiosk_pos_config(access_token)
        payment_method = pos_config.env["pos.payment.method"].browse(payment_method_id)
        if not payment_method.exists() or payment_method not in pos_config.payment_method_ids:
            raise NotFound("Payment method not found")
        return payment_method

    @http.route(
        "/qpay/callback/<int:transaction_id>",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def qpay_callback(self, transaction_id, **kwargs):
        try:
            body = request.httprequest.get_data(as_text=True)
            _logger.info("QPay callback txn_id=%s body=%s", transaction_id, body[:500])

            txn = request.env["qpay.transaction"].sudo().browse(transaction_id)
            if not txn.exists():
                _logger.warning("QPay callback transaction not found: %s", transaction_id)
                return request.make_response("NOT FOUND", status=404)

            if txn.state == "paid":
                txn._on_payment_confirmed()
                return request.make_response("OK", status=200)

            try:
                txn.action_check_payment()
            except Exception as exc:
                _logger.error("QPay callback check_payment failed: %s", exc)

            return request.make_response("OK", status=200)
        except Exception as exc:
            _logger.exception("QPay callback processing failed: %s", exc)
            return request.make_response("ERROR", status=500)

    @http.route(
        "/qpay/kiosk/check",
        type="json",
        auth="public",
        website=True,
    )
    def qpay_kiosk_check(self, access_token, payment_method_id, transaction_id):
        payment_method = self._get_kiosk_payment_method(access_token, payment_method_id)
        return payment_method.sudo().qpay_check_payment({"transaction_id": transaction_id})

    @http.route(
        "/qpay/kiosk/cancel",
        type="json",
        auth="public",
        website=True,
    )
    def qpay_kiosk_cancel(self, access_token, payment_method_id, transaction_id):
        payment_method = self._get_kiosk_payment_method(access_token, payment_method_id)
        return payment_method.sudo().qpay_cancel_invoice({"transaction_id": transaction_id})
