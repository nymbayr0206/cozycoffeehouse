# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class QPayController(http.Controller):

    @http.route(
        '/qpay/callback/<int:transaction_id>',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def qpay_callback(self, transaction_id, **kwargs):
        """QPay-с ирэх webhook callback."""
        try:
            body = request.httprequest.get_data(as_text=True)
            _logger.info('QPay callback txn_id=%s body=%s', transaction_id, body[:500])

            txn = request.env['qpay.transaction'].sudo().browse(transaction_id)
            if not txn.exists():
                _logger.warning('QPay callback: гүйлгээ олдсонгүй id=%s', transaction_id)
                return request.make_response('NOT FOUND', status=404)

            if txn.state == 'paid':
                return request.make_response('OK', status=200)

            # Төлбөр шалгана
            try:
                txn.action_check_payment()
            except Exception as e:
                _logger.error('QPay callback check_payment алдаа: %s', e)

            return request.make_response('OK', status=200)

        except Exception as e:
            _logger.exception('QPay callback боловсруулахад алдаа: %s', e)
            return request.make_response('ERROR', status=500)
