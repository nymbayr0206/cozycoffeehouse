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
    _name = "qpay.transaction"
    _description = "QPay Гүйлгээ"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(string="Дугаар", readonly=True, copy=False, default="/")
    state = fields.Selection(
        [
            ("draft", "Ноорог"),
            ("pending", "Хүлээгдэж байна"),
            ("paid", "Төлөгдсөн"),
            ("cancelled", "Цуцлагдсан"),
            ("failed", "Амжилтгүй"),
        ],
        string="Төлөв",
        default="draft",
        tracking=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Компани",
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Валют",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    amount = fields.Monetary(string="Дүн", required=True)
    invoice_id = fields.Many2one(
        "account.move",
        string="Нэхэмжлэл",
        domain=[("move_type", "in", ["out_invoice", "out_refund"])],
        ondelete="set null",
    )
    sale_order_id = fields.Many2one("sale.order", string="Захиалга", ondelete="set null")
    partner_id = fields.Many2one("res.partner", string="Харилцагч")
    description = fields.Char(string="Тайлбар")

    pos_order_id = fields.Many2one("pos.order", string="POS захиалга", ondelete="set null")
    payment_method_id = fields.Many2one(
        "pos.payment.method", string="POS төлбөрийн арга", ondelete="set null"
    )
    pos_payment_id = fields.Many2one(
        "pos.payment",
        string="POS төлбөр",
        ondelete="set null",
        readonly=True,
        copy=False,
    )
    pos_order_uuid = fields.Char(string="POS order UUID", readonly=True, copy=False)
    pos_reference = fields.Char(string="POS reference", readonly=True, copy=False)

    qpay_invoice_id = fields.Char(string="QPay Invoice ID", readonly=True, copy=False)
    qr_text = fields.Text(string="QR текст", readonly=True, copy=False)
    qr_image = fields.Binary(
        string="QR зураг", readonly=True, copy=False, attachment=False
    )
    qpay_short_url = fields.Char(string="QPay богино холбоос", readonly=True, copy=False)
    qpay_payment_id = fields.Char(string="QPay Payment ID", readonly=True, copy=False)
    callback_url = fields.Char(string="Callback URL", readonly=True, copy=False)
    error_message = fields.Text(string="Алдааны мэдээлэл", readonly=True, copy=False)

    ebarimt_created = fields.Boolean(
        string="eBarimt үүссэн", default=False, readonly=True
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("qpay.transaction") or "/"
                )
        return super().create(vals_list)

    def _get_client(self):
        get_param = self.env["ir.config_parameter"].sudo().get_param
        environment = get_param("qpay.environment", "sandbox")
        username = get_param("qpay.username", "")
        password = get_param("qpay.password", "")
        invoice_code = get_param("qpay.invoice_code", "")

        if not username or not password or not invoice_code:
            raise UserError(
                _(
                    "QPay тохиргоо дутуу байна.\n"
                    "Settings > Technical > QPay хэсэгт нэвтрэх нэр, нууц үг, invoice code-оо бөглөнө үү."
                )
            )

        base_url = (
            QPayClient.SANDBOX_BASE_URL
            if environment == "sandbox"
            else QPayClient.PRODUCTION_BASE_URL
        )
        return QPayClient(
            base_url=base_url,
            username=username,
            password=password,
            invoice_code=invoice_code,
        )

    def _build_callback_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param(
            "qpay.callback_base_url", ""
        ).rstrip("/")
        if not base_url:
            base_url = self.env["ir.config_parameter"].sudo().get_param(
                "web.base.url", "http://localhost:8069"
            ).rstrip("/")
        return f"{base_url}/qpay/callback/{self.id}"

    def _generate_qr_image(self, text):
        try:
            img = qrcode.make(text)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue())
        except Exception as exc:
            _logger.warning("Failed to generate QPay QR image: %s", exc)
            return False

    def _get_invoice_receiver_code(self):
        self.ensure_one()
        return (
            self.partner_id.ref
            or self.partner_id.vat
            or self.pos_reference
            or self.pos_order_uuid
            or self.name
            or "terminal"
        )

    def _prepare_invoice_payload(self):
        self.ensure_one()

        payload = {
            "invoice_code": self.env["ir.config_parameter"]
            .sudo()
            .get_param("qpay.invoice_code", ""),
            "sender_invoice_no": self.name,
            "invoice_receiver_code": self._get_invoice_receiver_code(),
            "invoice_description": self.description or self.name,
            "amount": self.amount,
            "callback_url": self._build_callback_url(),
        }
        if self.partner_id:
            payload["invoice_receiver_data"] = {
                "register": self.partner_id.vat or self.partner_id.ref or "",
                "name": self.partner_id.name or "",
                "email": self.partner_id.email or "",
                "phone": self.partner_id.phone or self.partner_id.mobile or "",
            }
        return payload

    def _write_invoice_response(self, result):
        self.ensure_one()

        qr_text = result.get("qr_text", "")
        short_url = result.get("qPay_shortUrl", "")
        qpay_invoice_id = result.get("invoice_id", "")

        self.write(
            {
                "qpay_invoice_id": qpay_invoice_id,
                "qr_text": qr_text,
                "qpay_short_url": short_url,
                "callback_url": self._build_callback_url(),
                "state": "pending",
                "error_message": False,
                "qr_image": self._generate_qr_image(qr_text) if qr_text else False,
            }
        )
        return qpay_invoice_id

    def action_create_qpay_invoice(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Зөвхөн ноорог гүйлгээнд QPay invoice үүсгэнэ."))

        try:
            result = self._get_client().create_invoice(self._prepare_invoice_payload())
        except QPayAuthError as exc:
            raise UserError(_("QPay нэвтрэлт амжилтгүй: %s") % exc) from exc
        except QPayApiError as exc:
            raise UserError(_("QPay invoice үүсгэхэд алдаа гарлаа: %s") % exc) from exc

        qpay_invoice_id = self._write_invoice_response(result)
        self.message_post(body=_("QPay invoice үүсгэлээ. ID: %s") % qpay_invoice_id)

        return {
            "type": "ir.actions.act_window",
            "name": _("QPay QR Код"),
            "res_model": "qpay.qr.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_transaction_id": self.id},
        }

    def action_check_payment(self):
        self.ensure_one()
        if not self.qpay_invoice_id:
            raise UserError(_("QPay invoice ID байхгүй байна."))
        if self.state == "paid":
            raise UserError(_("Энэ гүйлгээ аль хэдийн төлөгдсөн байна."))

        try:
            result = self._get_client().check_payment(self.qpay_invoice_id)
        except QPayApiError as exc:
            raise UserError(_("Төлбөр шалгахад алдаа гарлаа: %s") % exc) from exc

        paid_row = next(
            (
                row
                for row in result.get("rows", [])
                if row.get("payment_status") == "PAID"
            ),
            None,
        )

        if paid_row:
            self.write(
                {
                    "state": "paid",
                    "qpay_payment_id": paid_row.get("payment_id", ""),
                    "error_message": False,
                }
            )
            self.message_post(
                body=_("Төлбөр баталгаажлаа. QPay Payment ID: %s")
                % paid_row.get("payment_id", "")
            )
            self._on_payment_confirmed()
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Амжилттай"),
                    "message": _("Төлбөр баталгаажлаа."),
                    "type": "success",
                    "sticky": False,
                },
            }

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Хүлээгдэж байна"),
                "message": _("Төлбөр одоогоор баталгаажаагүй байна."),
                "type": "warning",
                "sticky": False,
            },
        }

    def action_cancel(self):
        self.ensure_one()
        if self.state not in ("draft", "pending"):
            raise UserError(_("Зөвхөн ноорог эсвэл хүлээгдэж буй гүйлгээг цуцална."))

        if self.qpay_invoice_id:
            try:
                self._get_client().cancel_invoice(self.qpay_invoice_id)
            except QPayApiError as exc:
                _logger.warning("Failed to cancel QPay invoice %s: %s", self.name, exc)

        self.write({"state": "cancelled"})
        self.message_post(body=_("QPay гүйлгээ цуцлагдлаа."))

    def action_create_ebarimt(self):
        self.ensure_one()
        if self.state != "paid":
            raise UserError(_("Зөвхөн төлөгдсөн гүйлгээнд eBarimt үүсгэнэ."))
        if not self.qpay_payment_id:
            raise UserError(_("QPay payment ID байхгүй байна."))
        if self.ebarimt_created:
            raise UserError(_("eBarimt аль хэдийн үүссэн байна."))

        try:
            self._get_client().create_ebarimt(self.qpay_payment_id)
        except QPayApiError as exc:
            raise UserError(_("eBarimt үүсгэхэд алдаа гарлаа: %s") % exc) from exc

        self.write({"ebarimt_created": True})
        self.message_post(body=_("eBarimt амжилттай үүслээ."))

    def _create_qpay_invoice_for_kiosk(self):
        self.ensure_one()
        if self.state != "draft":
            return

        result = self._get_client().create_invoice(self._prepare_invoice_payload())
        qpay_invoice_id = self._write_invoice_response(result)
        self.message_post(body=_("QPay kiosk invoice үүсгэлээ. ID: %s") % qpay_invoice_id)

    def _on_payment_confirmed(self):
        self.ensure_one()

        if self.invoice_id and self.invoice_id.payment_state not in ("paid", "in_payment"):
            try:
                self.invoice_id.message_post(
                    body=_("QPay төлбөр баталгаажлаа. Гүйлгээ: %s") % self.name
                )
            except Exception:
                pass

        if self.pos_order_id or self.pos_order_uuid or self.pos_reference:
            self._confirm_pos_kiosk_order()

    def _get_pos_order_to_confirm(self):
        self.ensure_one()

        order = self.pos_order_id.sudo()
        if order.exists():
            return order

        if self.pos_order_uuid:
            order = self.env["pos.order"].sudo().search(
                [("uuid", "=", self.pos_order_uuid)], limit=1
            )
            if order:
                return order

        if self.pos_reference:
            return self.env["pos.order"].sudo().search(
                [("pos_reference", "=", self.pos_reference), ("state", "=", "draft")],
                limit=1,
            )

        return self.env["pos.order"]

    def _get_qpay_payment_method(self, order):
        self.ensure_one()

        if self.payment_method_id and self.payment_method_id.exists():
            payment_method = self.payment_method_id.sudo()
            if not order or payment_method in order.config_id.payment_method_ids:
                return payment_method

        if order:
            return self.env["pos.payment.method"].sudo().search(
                [
                    ("use_payment_terminal", "=", "qpay"),
                    ("config_ids", "in", order.config_id.id),
                ],
                limit=1,
            )

        return self.payment_method_id.sudo()

    def _get_pos_payment_vals(self, order, payment_method):
        self.ensure_one()

        vals = {
            "pos_order_id": order.id,
            "payment_method_id": payment_method.id,
            "amount": self.amount,
            "payment_date": fields.Datetime.now(),
            "payment_status": "done",
            "transaction_id": self.qpay_payment_id or self.qpay_invoice_id or self.name,
            "ticket": _("QPay payment ID: %s")
            % (self.qpay_payment_id or self.qpay_invoice_id or self.name),
        }
        if "qpay_transaction_id" in self.env["pos.payment"]._fields:
            vals["qpay_transaction_id"] = self.id
        return vals

    def _confirm_pos_kiosk_order(self):
        self.ensure_one()

        order = self._get_pos_order_to_confirm()
        if not order:
            _logger.warning("QPay transaction %s has no POS order to confirm", self.name)
            return

        if self.pos_order_id != order:
            self.write({"pos_order_id": order.id})

        payment_method = self._get_qpay_payment_method(order)
        if not payment_method:
            _logger.warning(
                "QPay payment method not found for POS config %s", order.config_id.id
            )
            return

        payment = self.pos_payment_id.sudo()
        if not payment:
            payment = order.payment_ids.filtered(
                lambda record: getattr(record, "qpay_transaction_id", False)
                and record.qpay_transaction_id.id == self.id
            )[:1]
        if not payment and self.qpay_payment_id:
            payment = order.payment_ids.filtered(
                lambda record: record.payment_method_id == payment_method
                and record.transaction_id == self.qpay_payment_id
            )[:1]

        try:
            if payment and order.state not in ("done", "invoiced"):
                payment.sudo().write(self._get_pos_payment_vals(order, payment_method))
            elif order.state not in ("paid", "done", "invoiced"):
                order.add_payment(self._get_pos_payment_vals(order, payment_method))
                payment = order.payment_ids.sorted("id")[-1]

            order._compute_prices()
            if order.state not in ("paid", "done", "invoiced"):
                order._process_saved_order(False)

            update_vals = {"payment_method_id": payment_method.id}
            if payment:
                update_vals["pos_payment_id"] = payment.id
            self.write(update_vals)

            if order.config_id.self_ordering_mode == "kiosk":
                order._send_payment_result("Success")

            _logger.info("QPay POS order %s confirmed successfully", order.name)
        except Exception as exc:
            _logger.error(
                "Failed to confirm QPay POS order %s: %s",
                order.name,
                exc,
            )

    def action_show_qr(self):
        self.ensure_one()
        if not self.qr_image:
            raise UserError(_("QR код байхгүй байна. Эхлээд QPay invoice үүсгэнэ үү."))

        return {
            "type": "ir.actions.act_window",
            "name": _("QPay QR Код"),
            "res_model": "qpay.qr.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_transaction_id": self.id},
        }

    @api.model
    def cron_check_pending_payments(self):
        pending = self.search(
            [("state", "=", "pending"), ("qpay_invoice_id", "!=", False)]
        )
        _logger.info("QPay cron checking %s pending transaction(s)", len(pending))
        for txn in pending:
            try:
                txn.action_check_payment()
            except Exception as exc:
                _logger.warning("QPay cron failed for %s: %s", txn.name, exc)
