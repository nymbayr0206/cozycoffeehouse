# -*- coding: utf-8 -*-
import base64
import io
import logging

import qrcode

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.qpay_client import QPayApiError, QPayAuthError, QPayClient

_logger = logging.getLogger(__name__)


class QpayTransaction(models.Model):
    _name = 'qpay.transaction'
    _description = 'QPay Гүйлгээ'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Дугаар', readonly=True, copy=False, default='/'
    )
    state = fields.Selection(
        [
            ('draft', 'Ноорог'),
            ('pending', 'Хүлээгдэж байна'),
            ('paid', 'Төлөгдсөн'),
            ('cancelled', 'Цуцлагдсан'),
            ('failed', 'Амжилтгүй'),
        ],
        string='Төлөв',
        default='draft',
        tracking=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Компани',
        default=lambda self: self.env.company, required=True,
    )
    currency_id = fields.Many2one(
        'res.currency', string='Валют',
        default=lambda self: self.env.company.currency_id, required=True,
    )
    amount = fields.Monetary(string='Дүн', required=True)
    invoice_id = fields.Many2one(
        'account.move', string='Нэхэмжлэл',
        domain=[('move_type', 'in', ['out_invoice', 'out_refund'])],
        ondelete='set null',
    )
    sale_order_id = fields.Many2one(
        'sale.order', string='Захиалга', ondelete='set null',
    )
    partner_id = fields.Many2one('res.partner', string='Харилцагч')
    description = fields.Char(string='Тайлбар')

    # POS захиалгатай холбоос (kiosk mode)
    pos_order_id = fields.Many2one(
        'pos.order', string='POS Захиалга', ondelete='set null',
    )

    # QPay хариу
    qpay_invoice_id = fields.Char(string='QPay Invoice ID', readonly=True, copy=False)
    qr_text = fields.Text(string='QR текст', readonly=True, copy=False)
    qr_image = fields.Binary(string='QR зураг', readonly=True, copy=False, attachment=False)
    qpay_short_url = fields.Char(string='QPay богино холбоос', readonly=True, copy=False)
    qpay_payment_id = fields.Char(string='QPay Payment ID', readonly=True, copy=False)
    callback_url = fields.Char(string='Callback URL', readonly=True, copy=False)
    error_message = fields.Text(string='Алдааны мэдэгдэл', readonly=True, copy=False)

    # eBarimt
    ebarimt_created = fields.Boolean(string='eBarimt үүссэн', default=False, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('qpay.transaction') or '/'
        return super().create(vals_list)

    def _get_client(self):
        """QPay client буцаана (credentials-г ir.config_parameter-с авна)."""
        get = self.env['ir.config_parameter'].sudo().get_param
        env = get('qpay.environment', 'sandbox')
        username = get('qpay.username', '')
        password = get('qpay.password', '')
        invoice_code = get('qpay.invoice_code', '')

        if not username or not password or not invoice_code:
            raise UserError(
                _('QPay тохиргоо хийгдээгүй байна.\n'
                  'Тохиргоо → Техникийн → QPay хэсэгт нэвтрэх нэр, нууц үг, '
                  'invoice код оруулна уу.')
            )

        from ..services.qpay_client import QPayClient
        base_url = (
            QPayClient.SANDBOX_BASE_URL if env == 'sandbox'
            else QPayClient.PRODUCTION_BASE_URL
        )
        return QPayClient(
            base_url=base_url,
            username=username,
            password=password,
            invoice_code=invoice_code,
        )

    def _build_callback_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param(
            'qpay.callback_base_url', ''
        ).rstrip('/')
        if not base:
            base = self.env['ir.config_parameter'].sudo().get_param(
                'web.base.url', 'http://localhost:8069'
            ).rstrip('/')
        return f"{base}/qpay/callback/{self.id}"

    def _generate_qr_image(self, text):
        """QR текстийг зураг болгоно (PNG → base64)."""
        try:
            img = qrcode.make(text)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            return base64.b64encode(buf.getvalue())
        except Exception as e:
            _logger.warning("QR зураг үүсгэхэд алдаа: %s", e)
            return False

    def action_create_qpay_invoice(self):
        """QPay invoice үүсгэж QR код харуулна."""
        self.ensure_one()
        if self.state not in ('draft',):
            raise UserError(_('Зөвхөн ноорог гүйлгээнд QPay invoice үүсгэх боломжтой.'))

        client = self._get_client()
        invoice_code = self.env['ir.config_parameter'].sudo().get_param('qpay.invoice_code', '')
        callback_url = self._build_callback_url()

        payload = {
            'invoice_code': invoice_code,
            'sender_invoice_no': self.name,
            'invoice_description': self.description or self.name,
            'amount': self.amount,
            'callback_url': callback_url,
        }
        if self.partner_id:
            payload['invoice_receiver_data'] = {
                'name': self.partner_id.name or '',
                'email': self.partner_id.email or '',
                'phone': self.partner_id.phone or self.partner_id.mobile or '',
            }

        try:
            result = client.create_invoice(payload)
        except QPayAuthError as e:
            raise UserError(_('QPay нэвтрэлт амжилтгүй: %s') % str(e))
        except QPayApiError as e:
            raise UserError(_('QPay invoice үүсгэхэд алдаа: %s') % str(e))

        qr_text = result.get('qr_text', '')
        short_url = result.get('qPay_shortUrl', '')
        qpay_invoice_id = result.get('invoice_id', '')

        self.write({
            'qpay_invoice_id': qpay_invoice_id,
            'qr_text': qr_text,
            'qpay_short_url': short_url,
            'callback_url': callback_url,
            'state': 'pending',
            'error_message': False,
            'qr_image': self._generate_qr_image(qr_text) if qr_text else False,
        })
        self.message_post(body=_('QPay invoice үүсгэгдлээ. ID: %s') % qpay_invoice_id)

        # QR wizard нээнэ
        return {
            'type': 'ir.actions.act_window',
            'name': _('QPay QR Код'),
            'res_model': 'qpay.qr.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_transaction_id': self.id},
        }

    def action_check_payment(self):
        """QPay-с төлбөрийн мэдэгдэл шалгана."""
        self.ensure_one()
        if not self.qpay_invoice_id:
            raise UserError(_('QPay invoice ID байхгүй байна.'))
        if self.state == 'paid':
            raise UserError(_('Энэ гүйлгээ аль хэдийн төлөгдсөн байна.'))

        client = self._get_client()
        try:
            result = client.check_payment(self.qpay_invoice_id)
        except QPayApiError as e:
            raise UserError(_('Төлбөр шалгахад алдаа: %s') % str(e))

        count = result.get('count', 0)
        rows = result.get('rows', [])

        if count > 0 and rows:
            paid_row = next(
                (r for r in rows if r.get('payment_status') == 'PAID'), None
            )
            if paid_row:
                self.write({
                    'state': 'paid',
                    'qpay_payment_id': paid_row.get('payment_id', ''),
                    'error_message': False,
                })
                self.message_post(
                    body=_('Төлбөр амжилттай баталгаажлаа. QPay Payment ID: %s')
                    % paid_row.get('payment_id', '')
                )
                self._on_payment_confirmed()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Амжилттай'),
                        'message': _('Төлбөр баталгаажлаа!'),
                        'type': 'success',
                        'sticky': False,
                    },
                }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Хүлээгдэж байна'),
                'message': _('Төлбөр одоогоор баталгаажаагүй байна.'),
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_cancel(self):
        """QPay invoice цуцална."""
        self.ensure_one()
        if self.state not in ('draft', 'pending'):
            raise UserError(_('Зөвхөн ноорог эсвэл хүлээгдэж буй гүйлгээг цуцлах боломжтой.'))

        if self.qpay_invoice_id:
            client = self._get_client()
            try:
                client.cancel_invoice(self.qpay_invoice_id)
            except QPayApiError as e:
                _logger.warning("QPay invoice цуцлахад алдаа: %s", e)

        self.write({'state': 'cancelled'})
        self.message_post(body=_('QPay гүйлгээ цуцлагдлаа.'))

    def action_create_ebarimt(self):
        """eBarimt үүсгэнэ."""
        self.ensure_one()
        if self.state != 'paid':
            raise UserError(_('Зөвхөн төлөгдсөн гүйлгээнд eBarimt үүсгэх боломжтой.'))
        if not self.qpay_payment_id:
            raise UserError(_('QPay payment ID байхгүй байна.'))
        if self.ebarimt_created:
            raise UserError(_('eBarimt аль хэдийн үүсгэгдсэн байна.'))

        client = self._get_client()
        try:
            client.create_ebarimt(self.qpay_payment_id)
            self.write({'ebarimt_created': True})
            self.message_post(body=_('eBarimt амжилттай үүсгэгдлээ.'))
        except QPayApiError as e:
            raise UserError(_('eBarimt үүсгэхэд алдаа: %s') % str(e))

    def _create_qpay_invoice_for_kiosk(self):
        """Kiosk горимд QPay invoice үүсгэнэ (wizard буцаахгүй)."""
        if self.state not in ('draft',):
            return

        client = self._get_client()
        invoice_code = self.env['ir.config_parameter'].sudo().get_param('qpay.invoice_code', '')
        callback_url = self._build_callback_url()

        payload = {
            'invoice_code': invoice_code,
            'sender_invoice_no': self.name,
            'invoice_description': self.description or self.name,
            'amount': self.amount,
            'callback_url': callback_url,
        }

        result = client.create_invoice(payload)
        qr_text = result.get('qr_text', '')
        short_url = result.get('qPay_shortUrl', '')
        qpay_invoice_id = result.get('invoice_id', '')

        self.write({
            'qpay_invoice_id': qpay_invoice_id,
            'qr_text': qr_text,
            'qpay_short_url': short_url,
            'callback_url': callback_url,
            'state': 'pending',
            'error_message': False,
            'qr_image': self._generate_qr_image(qr_text) if qr_text else False,
        })
        self.message_post(body=_('QPay kiosk invoice үүсгэгдлээ. ID: %s') % qpay_invoice_id)

    def _on_payment_confirmed(self):
        """Төлбөр баталгаажсаны дараах үйлдлүүд."""
        if self.invoice_id and self.invoice_id.payment_state not in ('paid', 'in_payment'):
            try:
                self.invoice_id.message_post(
                    body=_('QPay-р төлбөр баталгаажлаа. Гүйлгээ: %s') % self.name
                )
            except Exception:
                pass

        # POS kiosk захиалга байвал автоматаар confirmed болгоно
        if self.pos_order_id:
            self._confirm_pos_kiosk_order()

    def _confirm_pos_kiosk_order(self):
        """POS kiosk захиалгыг QPay төлбөрийн дараа paid болгоно."""
        order = self.pos_order_id.sudo()
        if order.state in ('paid', 'done', 'invoiced'):
            return

        # QPay payment method хайна
        payment_method = self.env['pos.payment.method'].sudo().search([
            ('use_payment_terminal', '=', 'qpay'),
            ('config_ids', 'in', order.config_id.id),
        ], limit=1)

        if not payment_method:
            _logger.warning('QPay payment method олдсонгүй, config_id=%s', order.config_id.id)
            return

        try:
            # pos.payment бичлэг үүсгэнэ
            self.env['pos.payment'].sudo().create({
                'pos_order_id': order.id,
                'payment_method_id': payment_method.id,
                'amount': order.amount_total,
            })
            order.write({'state': 'paid'})
            order._process_saved_order(False)
            # PAYMENT_STATUS websocket явуулна
            order._send_payment_result('Success')
            _logger.info('QPay kiosk захиалга %s амжилттай баталгаажлаа', order.name)
        except Exception as e:
            _logger.error('QPay kiosk захиалга баталгаажуулахад алдаа %s: %s', order.name, e)

    def action_show_qr(self):
        """QR wizard нээнэ."""
        self.ensure_one()
        if not self.qr_image:
            raise UserError(_('QR код байхгүй байна. Эхлээд QPay invoice үүсгэнэ үү.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('QPay QR Код'),
            'res_model': 'qpay.qr.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_transaction_id': self.id},
        }

    @api.model
    def cron_check_pending_payments(self):
        """Cron: хүлээгдэж буй гүйлгээнүүдийг шалгана."""
        pending = self.search([('state', '=', 'pending'), ('qpay_invoice_id', '!=', False)])
        _logger.info('QPay cron: %d хүлээгдэж буй гүйлгээ шалгаж байна', len(pending))
        for txn in pending:
            try:
                txn.action_check_payment()
            except Exception as e:
                _logger.warning('QPay cron алдаа (txn %s): %s', txn.name, e)
