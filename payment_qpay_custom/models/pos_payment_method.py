# -*- coding: utf-8 -*-
import logging

from odoo import api, models, _
from odoo.fields import Domain
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    # ── 1. Terminal сонголтод QPay нэмнэ ─────────────────────────────────────

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('qpay', 'QPay (QR)')]

    # ── 2. POS frontend-д ачаалах field-үүд ──────────────────────────────────

    @api.model
    def _load_pos_data_fields(self, config):
        params = super()._load_pos_data_fields(config)
        # QPay-д нэмэлт field хэрэггүй — use_payment_terminal аль хэдийн байна
        return params

    # ── 3. Kiosk self-order дотор QPay method харуулах ────────────────────────

    @api.model
    def _load_pos_self_data_domain(self, data, config):
        base_domain = super()._load_pos_self_data_domain(data, config)
        qpay_domain = [
            ('use_payment_terminal', '=', 'qpay'),
            ('config_ids', 'in', config.id),
        ]
        return Domain.OR([base_domain, qpay_domain])

    # ── 4. Kiosk checkout flow ────────────────────────────────────────────────

    def _payment_request_from_kiosk(self, order):
        if self.use_payment_terminal == 'qpay':
            return self._qpay_kiosk_create_invoice(order)
        return super()._payment_request_from_kiosk(order)

    def _qpay_kiosk_create_invoice(self, order):
        txn = self.env['qpay.transaction'].sudo().create({
            'amount': order.amount_total,
            'description': 'Kiosk %s' % (order.name or order.pos_reference or ''),
            'pos_order_id': order.id,
        })
        try:
            txn._create_qpay_invoice_for_kiosk()
        except Exception as e:
            _logger.error('QPay kiosk invoice үүсгэхэд алдаа: %s', e)
            return {'status': 'error', 'message': str(e)}

        qr_b64 = ''
        if txn.qr_image:
            qr_b64 = txn.qr_image.decode() if isinstance(txn.qr_image, bytes) else txn.qr_image

        return {
            'status': 'qpay_pending',
            'transaction_id': txn.id,
            'qr_image': qr_b64,
            'qpay_short_url': txn.qpay_short_url or '',
        }

    # ── 5. POS cashier terminal RPC methods ───────────────────────────────────

    def _check_pos_user(self):
        if not self.env.su and not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessDenied()

    def qpay_create_invoice(self, data):
        """
        POS frontend-аас дуудагдана.
        QPay invoice үүсгэж QR зураг + transaction_id буцаана.
        data = {'amount': float, 'order_ref': str}
        """
        self.ensure_one()
        self._check_pos_user()

        amount = data.get('amount', 0)
        order_ref = data.get('order_ref', '')

        if amount <= 0:
            return {'error': _('Дүн 0-с их байх шаардлагатай.')}

        try:
            txn = self.env['qpay.transaction'].sudo().create({
                'amount': amount,
                'description': order_ref or 'POS Order',
            })
            txn._create_qpay_invoice_for_kiosk()
        except Exception as e:
            _logger.error('QPay POS invoice үүсгэхэд алдаа: %s', e)
            return {'error': str(e)}

        qr_b64 = ''
        if txn.qr_image:
            qr_b64 = txn.qr_image.decode() if isinstance(txn.qr_image, bytes) else txn.qr_image

        return {
            'transaction_id': txn.id,
            'qr_image': qr_b64,
            'qpay_short_url': txn.qpay_short_url or '',
            'qpay_invoice_id': txn.qpay_invoice_id or '',
        }

    def qpay_check_payment(self, data):
        """
        Polling: POS frontend-аас 3 секунд тутамд дуудагдана.
        data = {'transaction_id': int}
        Буцаах утга: {'status': 'pending'|'paid'|'error', 'message': str}
        """
        self.ensure_one()
        self._check_pos_user()

        txn_id = data.get('transaction_id')
        if not txn_id:
            return {'status': 'error', 'message': 'transaction_id байхгүй'}

        txn = self.env['qpay.transaction'].sudo().browse(txn_id)
        if not txn.exists():
            return {'status': 'error', 'message': 'Гүйлгээ олдсонгүй'}

        if txn.state == 'paid':
            return {'status': 'paid'}
        if txn.state in ('cancelled', 'failed'):
            return {'status': 'error', 'message': 'Гүйлгээ цуцлагдсан'}

        # QPay API-с шалгана
        try:
            txn.action_check_payment()
        except Exception as e:
            _logger.warning('QPay check_payment алдаа: %s', e)

        if txn.state == 'paid':
            return {'status': 'paid'}
        return {'status': 'pending'}

    def qpay_cancel_invoice(self, data):
        """
        POS frontend-аас буцах дарахад дуудагдана.
        data = {'transaction_id': int}
        """
        self.ensure_one()
        self._check_pos_user()

        txn_id = data.get('transaction_id')
        if not txn_id:
            return {'success': True}

        txn = self.env['qpay.transaction'].sudo().browse(txn_id)
        if txn.exists() and txn.state in ('draft', 'pending'):
            try:
                txn.action_cancel()
            except Exception as e:
                _logger.warning('QPay cancel алдаа: %s', e)

        return {'success': True}
